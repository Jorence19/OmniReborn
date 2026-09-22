"""Phase 1: collect and rank developer fingerprints from qualified tokens."""

import argparse
import csv
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

from database import ForensicDatabase
from forensics import extract_full_token_metadata


CATEGORICAL_FEATURES = (
    "dev_wallet", "funder_1hop", "funder_2hop", "funder_label",
    "method_selector", "contract_factory", "template_hash",
    "normalized_bytecode_hash", "selectors_hash", "compiler_version",
    "bundler_wallet", "buyer_wallet", "website_domain", "website_host_type",
    "favicon_hash", "tg_handle", "tg_naming_pattern", "x_handle",
    "x_naming_pattern", "launchpad",
)

NUMERIC_HABITS = (
    "fund_amount", "value_eth", "gwei", "max_gwei", "priority_gwei", "nonce",
    "setup_time_seconds", "wallet_age_at_deploy_seconds",
    "funding_count_before_deploy", "funding_total_eth_before_deploy",
    "creation_gas_used", "creation_tx_fee_eth", "initial_snipe_tokens",
    "bundle_eth", "dev_eth", "buyer_eth", "bundle_ratio", "bundle_wallets_count",
    "dev_holding_ratio", "dev_sold_ratio", "top_10_ratio",
)

NATIVE_DENOMINATED_HABITS = {
    "fund_amount", "value_eth", "gwei", "max_gwei", "priority_gwei",
    "funding_total_eth_before_deploy", "creation_tx_fee_eth",
    "bundle_eth", "dev_eth", "buyer_eth",
}

MISSING = {"", "none", "null", "nan", "unknown", "n/a", "0x", "00000000"}


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    result = str(value).strip()
    return None if result.lower() in MISSING else result.lower()


def _float(value: Any, positive: bool = False) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or (positive and result <= 0):
        return None
    return result


def _bucket_seconds(value: Any) -> Optional[str]:
    seconds = _float(value)
    if seconds is None or seconds < 0:
        return None
    for upper, label in (
        (60, "<=1m"), (300, "1-5m"), (1800, "5-30m"),
        (7200, "30m-2h"), (86400, "2-24h"), (604800, "1-7d"),
    ):
        if seconds <= upper:
            return label
    return ">7d"


def _numeric_signature(feature: str, value: Any) -> Optional[str]:
    number = _float(value)
    if number is None:
        return None
    if feature in {"setup_time_seconds", "wallet_age_at_deploy_seconds"}:
        return _bucket_seconds(number)
    if feature in {"funding_count_before_deploy", "nonce"}:
        return str(int(number))
    if feature == "creation_gas_used":
        return f"~{round(number / 1000) * 1000:.0f}"
    magnitude = abs(number)
    decimals = 8 if magnitude < 0.01 else 6 if magnitude < 1 else 4
    return f"{number:.{decimals}f}".rstrip("0").rstrip(".")


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None or str(value).strip() == "":
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def token_fingerprints(row: Dict[str, Any]) -> Dict[str, str]:
    """Flatten every usable categorical and behavior clue into comparable values."""
    result: Dict[str, str] = {}
    for feature in CATEGORICAL_FEATURES:
        value = _text(row.get(feature))
        if value:
            result[feature] = value
    for feature in NUMERIC_HABITS:
        signature = _numeric_signature(feature, row.get(feature))
        if signature is not None:
            # Legacy imports used zero for missing values, so zero is not evidence.
            if signature != "0":
                if feature in NATIVE_DENOMINATED_HABITS:
                    chain_id = int(row.get("chain_id") or 4663)
                    result[feature] = f"{chain_id}:{signature}"
                else:
                    result[feature] = signature
    website = row.get("website_url") or row.get("token_website")
    if website and not result.get("website_domain"):
        candidate = str(website).strip()
        parsed = urlparse(candidate if "://" in candidate else "https://" + candidate)
        if parsed.netloc:
            result["website_domain"] = parsed.netloc.lower().removeprefix("www.")

    description = str(row.get("description") or "").strip()
    if description:
        result["description_length_bucket"] = f"{(len(description) // 50) * 50}-{(len(description) // 50 + 1) * 50 - 1}"
        result["description_hashtag_count"] = str(description.count("#"))
        result["description_mention_count"] = str(description.count("@"))

    for field in ("first_boost", "second_boost", "ads_paid"):
        if _text(row.get(field)):
            result[f"{field}_used"] = "yes"

    timestamp = _parse_timestamp(row.get("creation_timestamp") or row.get("token_live_at"))
    if timestamp:
        result["launch_hour_utc"] = f"{timestamp.hour:02d}"
        result["launch_quarter_hour_utc"] = f"{timestamp.hour:02d}:{(timestamp.minute // 15) * 15:02d}"
        result["launch_weekday_utc"] = timestamp.strftime("%A").lower()
    return result


def _team_name(row: Dict[str, Any]) -> str:
    name = _text(row.get("candidate_team"))
    return name if name and name != "unclustered" else "unclustered"


def discover_fingerprints(
    rows: List[Dict[str, Any]],
    universe_rows: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Rank fingerprints learned from qualified rows against the whole universe."""
    universe_rows = universe_rows or rows
    anchor_observations: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    universe_counts: Counter = Counter()
    for row in rows:
        for feature, value in token_fingerprints(row).items():
            anchor_observations[(feature, value)].append(row)
    for row in universe_rows:
        universe_counts.update(token_fingerprints(row).items())

    catalog = []
    universe_size = max(1, len(universe_rows))
    for (feature, value), matched in anchor_observations.items():
        if len(matched) < 2:
            continue
        teams = Counter(_team_name(row) for row in matched)
        labeled = Counter({k: v for k, v in teams.items() if k != "unclustered"})
        leading_team, leading_count = labeled.most_common(1)[0] if labeled else (None, 0)
        purity = leading_count / len(matched) if leading_team else 0.0
        global_count = universe_counts[(feature, value)]
        qualified_precision = len(matched) / max(1, global_count)
        rarity = math.log((universe_size + 1) / (global_count + 1)) / math.log(universe_size + 1)
        recurrence = min(1.0, len(matched) / 5)
        score = round(100 * (
            0.40 * purity + 0.25 * qualified_precision + 0.20 * rarity + 0.15 * recurrence
        ), 2)
        catalog.append({
            "feature": feature, "value": value,
            "qualified_token_count": len(matched),
            "global_token_count": global_count,
            "unqualified_token_count": max(0, global_count - len(matched)),
            "global_prevalence_pct": round(100 * global_count / universe_size, 2),
            "qualified_precision_pct": round(100 * qualified_precision, 2),
            "leading_team": leading_team,
            "team_purity_pct": round(100 * purity, 2),
            "fingerprint_strength": score,
            "tokens": [row.get("ca") for row in matched],
            "symbols": [row.get("symbol") for row in matched],
        })
    return sorted(catalog, key=lambda item: (-item["fingerprint_strength"], -item["qualified_token_count"], item["feature"]))


def _feature_reliability(feature: str) -> float:
    if feature in {"dev_wallet", "bundler_wallet", "buyer_wallet", "tg_handle", "x_handle", "favicon_hash"}:
        return 1.0
    if feature in {"funder_1hop", "funder_2hop", "website_domain"}:
        return 0.85
    if feature in {"normalized_bytecode_hash", "contract_factory"}:
        return 0.55
    if feature in {"template_hash", "selectors_hash"}:
        return 0.30
    if feature in {"method_selector", "compiler_version", "launchpad", "funder_label"}:
        return 0.08
    return 0.22


def numeric_proximity(a: Any, b: Any, buffer_pct: float = 0.15) -> float:
    """Returns 1.0 for exact match, decaying linearly to 0.0 at buffer_pct difference."""
    n1 = _float(a, positive=True)
    n2 = _float(b, positive=True)
    if n1 is None or n2 is None:
        return 0.0
    denom = max(n1, n2)
    if denom <= 0:
        return 0.0
    diff_pct = abs(n1 - n2) / denom
    if diff_pct > buffer_pct:
        return 0.0
    return max(0.0, 1.0 - (diff_pct / buffer_pct))


def calculate_nearest_duplicate(
    candidate: Dict[str, Any],
    anchor: Dict[str, Any],
    buffer_pct: float = 0.15,
) -> Tuple[float, List[Dict[str, Any]]]:
    """Pairwise cross-examination between a candidate token and a qualified anchor token."""
    miss_probability = 1.0
    matched = []

    # 1. Categorical features (exact match)
    for feature in CATEGORICAL_FEATURES:
        v1 = _text(candidate.get(feature))
        v2 = _text(anchor.get(feature))
        if v1 and v2 and v1 == v2:
            reliability = _feature_reliability(feature)
            miss_probability *= (1.0 - min(0.95, reliability))
            matched.append({
                "feature": feature,
                "value": v1,
                "anchor_value": v2,
                "type": "categorical",
                "proximity": 1.0,
                "reliability": reliability,
                "contribution": round(reliability, 4),
            })

    # 2. Numerical habits (with buffer_pct relative proximity). Native-value
    # fields are incomparable across ETH-gas Robinhood and USDC-gas Arc.
    cross_chain = int(candidate.get("chain_id") or 4663) != int(anchor.get("chain_id") or 4663)
    for feature in NUMERIC_HABITS:
        if cross_chain and feature in NATIVE_DENOMINATED_HABITS:
            continue
        v1 = candidate.get(feature)
        v2 = anchor.get(feature)
        if v1 is not None and v2 is not None:
            if feature == "nonce":
                try:
                    n1 = int(float(v1))
                    n2 = int(float(v2))
                    if n1 > 0 and n2 > 0:
                        diff = abs(n1 - n2)
                        prox = 1.0 if diff == 0 else 0.6 if diff == 1 else 0.0
                        if prox > 0:
                            rel = _feature_reliability(feature)
                            contrib = rel * prox
                            miss_probability *= (1.0 - min(0.95, contrib))
                            matched.append({
                                "feature": feature,
                                "value": str(n1),
                                "anchor_value": str(n2),
                                "type": "numeric",
                                "proximity": round(prox, 2),
                                "reliability": rel,
                                "contribution": round(contrib, 4),
                            })
                except (ValueError, TypeError):
                    pass
                continue

            prox = numeric_proximity(v1, v2, buffer_pct=buffer_pct)
            if prox > 0:
                rel = _feature_reliability(feature)
                contrib = rel * (0.7 + 0.3 * prox)
                miss_probability *= (1.0 - min(0.95, contrib))
                f1 = _float(v1)
                f2 = _float(v2)
                val_str = f"{f1:.4f}" if f1 is not None else str(v1)
                ref_str = f"{f2:.4f}" if f2 is not None else str(v2)
                matched.append({
                    "feature": feature,
                    "value": f"{val_str} (ref: {ref_str})",
                    "anchor_value": ref_str,
                    "type": "numeric",
                    "proximity": round(prox, 2),
                    "reliability": rel,
                    "contribution": round(contrib, 4),
                })

    score = round(100 * (1.0 - miss_probability), 2)
    matched.sort(key=lambda item: -item["contribution"])
    return score, matched


def scan_candidate_tokens(
    qualified_rows: List[Dict[str, Any]],
    universe_rows: List[Dict[str, Any]],
    catalog: Optional[List[Dict[str, Any]]] = None,
    buffer_pct: float = 0.15,
) -> List[Dict[str, Any]]:
    """Cross-examine candidate tokens against qualified anchors to find their nearest duplicate."""
    qualified_cas = {
        (int(row.get("chain_id") or 4663), _text(row.get("ca")))
        for row in qualified_rows
    }
    candidates = []

    for row in universe_rows:
        ca = _text(row.get("ca"))
        token_key = (int(row.get("chain_id") or 4663), ca)
        if token_key in qualified_cas:
            continue

        best_score = 0.0
        best_anchor = None
        best_evidence = []

        for anchor in qualified_rows:
            pair_score, pair_evidence = calculate_nearest_duplicate(row, anchor, buffer_pct=buffer_pct)
            if pair_score > best_score or (pair_score == best_score and len(pair_evidence) > len(best_evidence)):
                best_score = pair_score
                best_anchor = anchor
                best_evidence = pair_evidence

        if best_score < 15 or not best_anchor:
            continue

        inferred_team = _team_name(best_anchor)
        strong_evidence = sum(item["reliability"] >= 0.55 for item in best_evidence)

        if best_score >= 65 and strong_evidence:
            confidence = "HIGH_LEAD"
        elif best_score >= 45 and (strong_evidence or len(best_evidence) >= 2):
            confidence = "PROBABLE_LEAD"
        elif best_score >= 30:
            confidence = "WATCH"
        else:
            confidence = "WEAK"

        candidates.append({
            "ca": row.get("ca"),
            "chain_id": int(row.get("chain_id") or 4663),
            "chain": row.get("chain") or "RBH",
            "symbol": row.get("symbol"),
            "candidate_score": best_score,
            "confidence": confidence,
            "inferred_team": inferred_team,
            "best_match_symbol": best_anchor.get("symbol"),
            "best_match_ca": best_anchor.get("ca"),
            "best_match_chain_id": int(best_anchor.get("chain_id") or 4663),
            "best_match_chain": best_anchor.get("chain") or "RBH",
            "evidence": best_evidence,
        })

    return sorted(candidates, key=lambda item: (-item["candidate_score"], item.get("symbol") or ""))

def build_team_habits(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        team = _team_name(row)
        if team != "unclustered":
            grouped[team].append(row)

    result = []
    for team, members in grouped.items():
        counts: Counter = Counter()
        for member in members:
            counts.update(token_fingerprints(member).items())
        habits = []
        for (feature, value), count in counts.items():
            if count < 2:
                continue
            habits.append({
                "feature": feature, "value": value, "count": count,
                "coverage_pct": round(100 * count / len(members), 2),
            })
        habits.sort(key=lambda item: (-item["coverage_pct"], -item["count"], item["feature"]))
        result.append({
            "team": team,
            "token_count": len(members),
            "tokens": [{"ca": row.get("ca"), "symbol": row.get("symbol")} for row in members],
            "recurring_habits": habits,
        })
    return sorted(result, key=lambda item: (-item["token_count"], item["team"]))


def evidence_quality(profile: Dict[str, Any]) -> Dict[str, Any]:
    fields = {
        "creator": profile.get("deployer_address"),
        "launch_funder": profile.get("funder_address"),
        "funding_lineage": profile.get("funding_lineage"),
        "creation_transaction": profile.get("creation_tx_hash"),
        "raw_bytecode": profile.get("bytecode_sha256"),
        "normalized_bytecode": profile.get("normalized_bytecode_sha256"),
        "selectors": profile.get("function_selectors"),
        "compiler": profile.get("compiler_version"),
        "launch_execution": profile.get("creation_method"),
        "storage_candidates": profile.get("storage_address_candidates"),
    }
    present = [name for name, value in fields.items() if value not in (None, "", [], {})]
    return {
        "present": present,
        "missing": [name for name in fields if name not in present],
        "coverage_pct": round(100 * len(present) / len(fields), 2),
    }


def persist_profile(db: ForensicDatabase, profile: Dict[str, Any], *, qualified: bool = False) -> str:
    """Persist both indexed fields and a lossless Phase 1 fingerprint payload."""
    chain_id = int(profile.get("chain_id") or 4663)
    chain_tag = {4663: "RBH", 5042: "ARC", 8453: "BASE", 1: "ETH"}.get(
        chain_id, str(chain_id)
    )
    canonical_ca = db.upsert_token({
        "ca": profile["ca"], "chain_id": chain_id, "chain": chain_tag,
        "symbol": profile.get("token_symbol"),
        "name": profile.get("token_name"), "is_qualified": qualified,
        "is_training_anchor": qualified,
        "qualification_reasons": {"source": "phase1_enrich"} if qualified else None,
        "description": profile.get("description"), "website": profile.get("website_url"),
        "x_handle": profile.get("twitter_url"),
    })
    stored = dict(profile)
    stored["ca"] = canonical_ca
    db.upsert_execution_profile(stored)
    db.upsert_bytecode_profile(stored)
    db.upsert_fingerprint_profile({
        "ca": canonical_ca,
        "wallet_lineage_json": profile.get("funding_lineage"),
        "funding_habits_json": {k: profile.get(k) for k in (
            "funder_address", "funder_hop2_address", "eth_received",
            "funding_count_before_deploy", "funding_total_eth_before_deploy",
            "setup_time_seconds", "wallet_age_at_deploy_seconds",
        )} | {"chain_id": chain_id, "native_symbol": "USDC" if chain_id == 5042 else "ETH"},
        "launch_habits_json": {k: profile.get(k) for k in (
            "creation_method", "creation_nonce", "gas_price_gwei", "max_fee_gwei",
            "priority_fee_gwei", "creation_gas_used", "creation_tx_fee",
            "deployer_initial_capital", "deployer_initial_snipe",
        )},
        "contract_habits_json": {k: profile.get(k) for k in (
            "normalized_bytecode_sha256", "bytecode_sha256", "metadata_hash",
            "compiler_version", "method_ids_hash", "function_selectors",
            "hardcoded_addresses", "storage_address_candidates", "embedded_strings_hash",
            "printable_constructor_strings",
        )},
        "social_habits_json": {k: profile.get(k) for k in (
            "ipfs_metadata", "twitter_url", "website_url", "description",
        )},
        "evidence_quality_json": evidence_quality(profile),
    })
    return canonical_ca


def build_report(db: ForensicDatabase, qualified_only: bool = True) -> Dict[str, Any]:
    all_rows = db.get_all_historical_tokens()
    # Trusted labeled anchors remain available to teach fingerprints. Every non-anchor must
    # carry verified graduation evidence before it can influence the Phase 1 universe.
    universe = [
        row for row in all_rows
        if int(row.get("is_training_anchor") or 0) == 1
        or int(row.get("is_graduated") or 0) == 1
    ] if qualified_only else all_rows
    rows = [row for row in universe if int(row.get("is_training_anchor") or 0) == 1] if qualified_only else universe
    catalog = discover_fingerprints(rows, universe)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "training_anchors_to_graduated_tokens" if qualified_only else "all_tokens",
        "token_count": len(rows),
        "universe_token_count": len(universe),
        "excluded_ungraduated_token_count": len(all_rows) - len(universe),
        "fingerprint_catalog": catalog,
        "team_habits": build_team_habits(rows),
        "candidate_tokens": scan_candidate_tokens(rows, universe, catalog) if qualified_only else [],
    }
def write_report_artifacts(report: Dict[str, Any], output_path: str) -> List[str]:
    """Write JSON and CSVs atomically so readers never observe partial files."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    stem = output.with_suffix("")
    fingerprint_path = Path(str(stem) + "_fingerprints.csv")
    candidate_path = Path(str(stem) + "_candidates.csv")
    habits_path = Path(str(stem) + "_team_habits.csv")
    targets = [output, fingerprint_path, candidate_path, habits_path]
    temps = [path.with_name(path.name + f".{os.getpid()}.tmp") for path in targets]
    json_tmp, fingerprint_tmp, candidate_tmp, habits_tmp = temps
    json_tmp.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    fingerprint_fields = [
        "feature", "value", "qualified_token_count", "global_token_count",
        "unqualified_token_count", "global_prevalence_pct", "qualified_precision_pct",
        "leading_team", "team_purity_pct", "fingerprint_strength", "symbols", "tokens",
    ]
    with fingerprint_tmp.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fingerprint_fields)
        writer.writeheader()
        for item in report["fingerprint_catalog"]:
            row = dict(item)
            row["symbols"] = "|".join(str(v or "") for v in row["symbols"])
            row["tokens"] = "|".join(str(v or "") for v in row["tokens"])
            writer.writerow({key: row.get(key) for key in fingerprint_fields})

    candidate_fields = [
        "candidate_score", "confidence", "inferred_team", "chain_id", "chain",
        "symbol", "ca", "best_match_symbol", "best_match_ca",
        "best_match_chain_id", "best_match_chain", "evidence",
    ]
    with candidate_tmp.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=candidate_fields)
        writer.writeheader()
        for item in report["candidate_tokens"]:
            row = dict(item)
            row["evidence"] = json.dumps(row["evidence"], sort_keys=True)
            writer.writerow({key: row.get(key) for key in candidate_fields})

    habit_fields = ["team", "team_token_count", "feature", "value", "count", "coverage_pct"]
    with habits_tmp.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=habit_fields)
        writer.writeheader()
        for team in report["team_habits"]:
            for habit in team["recurring_habits"]:
                writer.writerow({"team": team["team"], "team_token_count": team["token_count"], **habit})

    try:
        for temp, target in zip(temps, targets):
            os.replace(temp, target)
    finally:
        for temp in temps:
            if temp.exists():
                temp.unlink()
    return [str(path) for path in targets]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit", help="build a fingerprint and habit report")
    audit.add_argument("--db", default="forensics.db")
    audit.add_argument("--output", default="phase1_fingerprint_report.json")
    audit.add_argument("--all-tokens", action="store_true")
    enrich = sub.add_parser("enrich", help="extract and persist one token from chain data")
    enrich.add_argument("ca")
    enrich.add_argument("--chain-id", type=int, default=4663)
    enrich.add_argument("--db", default="forensics.db")
    enrich.add_argument("--qualified", action="store_true")
    args = parser.parse_args()

    db = ForensicDatabase(args.db)
    if args.command == "audit":
        report = build_report(db, qualified_only=not args.all_tokens)
        paths = write_report_artifacts(report, args.output)
        for item in report.get("candidate_tokens", []):
            db.upsert_token_match(
                ca=item["ca"],
                best_match_ca=item.get("best_match_ca"),
                best_match_pct=item.get("candidate_score", 0.0),
                candidate_team_id=None,
                candidate_team_name=item.get("inferred_team"),
                match_reasons={"confidence": item.get("confidence"), "evidence": item.get("evidence")},
            )
        print(f"Wrote {', '.join(paths)} and synced {len(report['candidate_tokens'])} candidate matches to database: {report['token_count']} tokens, {len(report['fingerprint_catalog'])} recurring fingerprints, {len(report['candidate_tokens'])} candidates")
    else:
        profile = extract_full_token_metadata(args.ca, args.chain_id)
        persist_profile(db, profile, qualified=args.qualified)
        print(json.dumps({"ca": profile["ca"], "evidence_quality": evidence_quality(profile)}, indent=2))


if __name__ == "__main__":
    main()
