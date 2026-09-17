import json
import math
from collections import Counter
from typing import Dict, Any, Tuple, Optional, List, Mapping

from database import ForensicDatabase


MISSING_TEXT = {"", "none", "null", "nan", "unknown", "n/a", "0x", "00000000"}


def _text(value: Any) -> str:
    value = str(value or "").strip().lower()
    return "" if value in MISSING_TEXT else value


def _method(value: Any) -> str:
    value = _text(value)
    return value[2:] if value.startswith("0x") else value


def _number(value: Any, *, positive: bool = False) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or (positive and number <= 0):
        return None
    return number


def is_005_snipe(val: float) -> bool:
    """Legacy amount habit, evaluated only for a real positive observation."""
    number = _number(val, positive=True)
    if number is None:
        return False
    val_str = f"{number:.9f}".rstrip("0")
    return val_str.endswith("005")


def _close(a: Any, b: Any, *, absolute: float, relative: float = 0.02) -> bool:
    left = _number(a, positive=True)
    right = _number(b, positive=True)
    if left is None or right is None:
        return False
    return abs(left - right) <= max(absolute, min(left, right) * relative)


def build_feature_counts(tokens: List[Dict[str, Any]]) -> Dict[str, Counter]:
    """Count observed values so common launchpad defaults receive little weight."""
    fields = (
        "template_hash", "normalized_bytecode_hash", "method_selector",
        "selectors_hash", "compiler_version", "website_domain", "favicon_hash",
        "tg_handle", "x_handle", "funder_1hop", "funder_2hop",
        "bundler_wallet", "buyer_wallet", "dev_wallet",
    )
    return {
        field: Counter(_text(row.get(field)) for row in tokens if _text(row.get(field)))
        for field in fields
    }


def _rarity_weight(
    field: str,
    value: str,
    counts: Optional[Mapping[str, Counter]],
    population: int,
    minimum: float,
    maximum: float,
) -> float:
    if not counts or population <= 1:
        return maximum
    occurrences = max(1, counts.get(field, Counter()).get(value, 1))
    information = math.log(population / occurrences) / math.log(population)
    return round(minimum + (maximum - minimum) * max(0.0, min(1.0, information)), 2)


def calculate_pair_similarity(
    new_t: Dict[str, Any],
    old_t: Dict[str, Any],
    feature_counts: Optional[Mapping[str, Counter]] = None,
    population_size: int = 0,
) -> Tuple[float, Dict[str, Any]]:
    """Evidence-aware developer similarity.

    Reused wallets and unique branding are identity evidence.  Launchpad-wide
    templates, selectors, zero defaults, and numerical habits are supporting
    evidence only.  Missing observations can never score.
    """
    points: Dict[str, float] = {}
    evidence: Dict[str, Dict[str, Any]] = {}

    def exact(field: str, weight: float, category: str, reason: Optional[str] = None):
        left, right = _text(new_t.get(field)), _text(old_t.get(field))
        if left and left == right:
            key = reason or f"{field}_match"
            points[key] = weight
            evidence[key] = {"category": category, "value": left}

    # Direct identity / operational-wallet reuse.
    exact("dev_wallet", 70.0, "identity")
    exact("bundler_wallet", 40.0, "identity")
    exact("buyer_wallet", 25.0, "identity")
    exact("tg_handle", 35.0, "identity")
    exact("x_handle", 35.0, "identity")
    exact("favicon_hash", 25.0, "identity")
    exact("website_domain", 18.0, "identity")

    new_f1, old_f1 = _text(new_t.get("funder_1hop")), _text(old_t.get("funder_1hop"))
    new_f2, old_f2 = _text(new_t.get("funder_2hop")), _text(old_t.get("funder_2hop"))
    new_label, old_label = _text(new_t.get("funder_label")), _text(old_t.get("funder_label"))
    shared_cex = new_label and old_label and new_label == old_label and new_label not in {"fresh wallet"}
    if new_f1 and new_f1 == old_f1 and not shared_cex:
        points["funder_1hop_match"] = 38.0
        evidence["funder_1hop_match"] = {"category": "identity", "value": new_f1}
    elif new_f2 and new_f2 == old_f2:
        points["funder_2hop_root_match"] = 22.0
        evidence["funder_2hop_root_match"] = {"category": "lineage", "value": new_f2}
    elif new_f1 and old_f2 and new_f1 == old_f2 or old_f1 and new_f2 and old_f1 == new_f2:
        value = new_f1 if new_f1 == old_f2 else old_f1
        points["funder_cross_hop_match"] = 16.0
        evidence["funder_cross_hop_match"] = {"category": "lineage", "value": value}

    # Contract features are rarity-adjusted; common launchpad code is weak.
    for field, low, high in (
        ("normalized_bytecode_hash", 3.0, 16.0),
        ("template_hash", 2.0, 12.0),
        ("selectors_hash", 1.0, 5.0),
        ("method_selector", 0.5, 3.0),
        ("compiler_version", 0.25, 2.0),
    ):
        left, right = _text(new_t.get(field)), _text(old_t.get(field))
        if field == "method_selector":
            left, right = _method(new_t.get(field)), _method(old_t.get(field))
        if left and left == right:
            weight = _rarity_weight(field, left, feature_counts, population_size, low, high)
            key = f"{field}_match"
            points[key] = weight
            evidence[key] = {"category": "contract", "value": left, "rarity_adjusted": True}

    # Repeatable behavior, never scored from zero/missing placeholders.
    if _close(new_t.get("fund_amount"), old_t.get("fund_amount"), absolute=0.0005, relative=0.01):
        points["fund_amount_habit"] = 6.0
        evidence["fund_amount_habit"] = {"category": "behavior"}
    if is_005_snipe(new_t.get("value_eth")) and is_005_snipe(old_t.get("value_eth")):
        points["0005_snipe_habit"] = 4.0
        evidence["0005_snipe_habit"] = {"category": "behavior"}
    elif _close(new_t.get("value_eth"), old_t.get("value_eth"), absolute=0.0001, relative=0.01):
        points["value_eth_habit"] = 5.0
        evidence["value_eth_habit"] = {"category": "behavior"}

    gas_matches = 0
    for field in ("gwei", "max_gwei", "priority_gwei"):
        if _close(new_t.get(field), old_t.get(field), absolute=0.00001, relative=0.01):
            gas_matches += 1
    if gas_matches >= 2:
        points["gas_profile_habit"] = 5.0
        evidence["gas_profile_habit"] = {"category": "behavior", "matching_fields": gas_matches}

    new_nonce, old_nonce = _number(new_t.get("nonce")), _number(old_t.get("nonce"))
    if new_nonce is not None and old_nonce is not None and new_nonce == old_nonce and new_nonce > 0:
        points["nonce_habit"] = 1.0
        evidence["nonce_habit"] = {"category": "behavior", "value": int(new_nonce)}

    raw_score = sum(points.values())
    score = min(round(raw_score, 2), 100.0)
    identity_keys = [k for k, v in evidence.items() if v["category"] == "identity"]
    categories = sorted({v["category"] for v in evidence.values()})
    return score, {
        "points": points,
        "evidence": evidence,
        "evidence_count": len(evidence),
        "independent_categories": categories,
        "identity_evidence": identity_keys,
        "raw_score": round(raw_score, 2),
    }


class ClusteringEngine:
    def __init__(self, db: ForensicDatabase):
        self.db = db

    def cluster_token(self, new_ca: str, auto_create_team: bool = True) -> Dict[str, Any]:
        token = self.db.get_token(new_ca)
        if not token:
            raise ValueError(f"Token {new_ca} not found in database.")

        historical_tokens = self.db.get_all_historical_tokens(exclude_ca=new_ca)
        if not historical_tokens:
            return {
                "ca": new_ca, "best_match_ca": None, "best_match_symbol": None,
                "best_match_pct": 0.0, "candidate_team": "Genesis Token",
                "confidence": "NONE", "reasons": {},
            }

        population = historical_tokens + [token]
        counts = build_feature_counts(population)
        best_match_token = None
        best_score = 0.0
        best_reasons: Dict[str, Any] = {}
        for previous in historical_tokens:
            score, reasons = calculate_pair_similarity(token, previous, counts, len(population))
            tie_break = reasons.get("evidence_count", 0) > best_reasons.get("evidence_count", 0)
            if score > best_score or (score == best_score and tie_break):
                best_score, best_match_token, best_reasons = score, previous, reasons

        identity = bool(best_reasons.get("identity_evidence"))
        categories = len(best_reasons.get("independent_categories") or [])
        if identity and best_score >= 60:
            confidence = "HIGH_CONFIDENCE"
        elif best_score >= 45 and categories >= 2:
            confidence = "PROBABLE"
        elif best_score >= 20:
            confidence = "WEAK_LEAD"
        else:
            confidence = "UNCORRELATED"

        candidate_team_name = None
        candidate_team_id = None
        if best_match_token and confidence in {"HIGH_CONFIDENCE", "PROBABLE"}:
            previous_team = best_match_token.get("candidate_team")
            if previous_team and previous_team.lower() != "unclustered":
                candidate_team_name = previous_team
                candidate_team_id = best_match_token.get("candidate_team_id")
                if candidate_team_id is None:
                    team = self.db.get_team_by_name(previous_team)
                    candidate_team_id = team.get("team_id") if team else None
            elif auto_create_team and confidence == "HIGH_CONFIDENCE":
                anchor = best_match_token.get("symbol") or token.get("symbol") or "unknown"
                candidate_team_name = f"team {anchor.lower()}"
                candidate_team_id = self.db.upsert_team(
                    team_name=candidate_team_name,
                    representative_template=token.get("normalized_bytecode_hash") or token.get("template_hash"),
                    root_funder=token.get("funder_1hop"),
                )

        self.db.upsert_token_match(
            ca=new_ca,
            best_match_ca=best_match_token["ca"] if best_match_token else None,
            best_match_pct=best_score,
            candidate_team_id=candidate_team_id,
            candidate_team_name=candidate_team_name,
            match_reasons=best_reasons,
        )
        return {
            "ca": new_ca,
            "symbol": token.get("symbol"),
            "best_match_ca": best_match_token["ca"] if best_match_token else None,
            "best_match_symbol": best_match_token.get("symbol") if best_match_token else None,
            "best_match_pct": best_score,
            "candidate_team": candidate_team_name or "Unclustered",
            "confidence": confidence,
            "reasons": best_reasons,
        }

    def recompute_all_matches(self, preserve_ground_truth: bool = True) -> List[Dict[str, Any]]:
        results = []
        for token in self.db.get_all_historical_tokens():
            if preserve_ground_truth and self.db.is_ground_truth_match(token["ca"]):
                continue
            results.append(self.cluster_token(token["ca"], auto_create_team=False))
        return results
