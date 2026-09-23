"""Refresh current DEX market observations without fabricating ATH or rug time."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import CHAIN_METADATA
from database import ForensicDatabase


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def chunks(values: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


def number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_session() -> requests.Session:
    retry = Retry(
        total=5, connect=5, read=5, status=5, backoff_factor=0.8,
        status_forcelist=(408, 425, 429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}), respect_retry_after_header=True,
    )
    session = requests.Session()
    session.headers.update({"User-Agent": "OmniReborn/1.0", "Accept": "application/json"})
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def choose_pairs(payload: Any, requested: set[str]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, list):
        return selected
    for pair in payload:
        if not isinstance(pair, dict):
            continue
        address = str((pair.get("baseToken") or {}).get("address") or "").lower()
        if address not in requested:
            continue
        liquidity = number((pair.get("liquidity") or {}).get("usd")) or 0.0
        previous = selected.get(address)
        previous_liquidity = number((previous.get("liquidity") or {}).get("usd")) if previous else None
        if previous is None or liquidity > (previous_liquidity or 0.0):
            selected[address] = pair
    return selected


def backup_database(db_path: Path, keep: int = 3) -> Path:
    backup_dir = db_path.parent / "runtime" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / f"{db_path.stem}-pre-market-refresh-{stamp}.db"
    source = sqlite3.connect(str(db_path))
    target = sqlite3.connect(str(backup_path))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    backups = sorted(
        backup_dir.glob(f"{db_path.stem}-pre-market-refresh-*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in backups[keep:]:
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass
    return backup_path


def sync_baseline_arc_tokens(db_path: Path):
    """If target DB is missing Arc tokens, sync baseline Arc records from repo forensics.db."""
    repo_db = db_path.parent.parent / "forensics.db" if db_path.parent.name == "data" else db_path.parent / "forensics.db"
    if not repo_db.exists() or repo_db.resolve() == db_path.resolve():
        return
    try:
        with sqlite3.connect(db_path) as dst, sqlite3.connect(repo_db) as src:
            src.row_factory = sqlite3.Row
            count = dst.execute("SELECT COUNT(*) FROM tokens WHERE chain_id=5042").fetchone()[0]
            if count == 0:
                rows = src.execute("SELECT * FROM tokens WHERE chain_id=5042").fetchall()
                for r in rows:
                    keys = list(r.keys())
                    placeholders = ", ".join(["?"] * len(keys))
                    dst.execute(f"INSERT OR IGNORE INTO tokens ({', '.join(keys)}) VALUES ({placeholders})", tuple(r))
                dst.commit()
    except Exception:
        pass


def refresh(db_path: Path, threshold: float | None, batch_size: int, pause: float) -> dict[str, Any]:
    sync_baseline_arc_tokens(db_path)
    db = ForensicDatabase(str(db_path))
    with db.get_connection() as connection:
        rows = [dict(row) for row in connection.execute(
            """SELECT ca,chain_id,symbol,ath_usd,current_market_cap_usd
               FROM tokens
               WHERE chain_id IN (4663,5042)
                 AND (? IS NULL OR current_market_cap_usd IS NULL OR current_market_cap_usd < ?)
               ORDER BY chain_id,created_at,ca""",
            (threshold, threshold),
        )]

    # Collapse legacy checksum/case duplicates without deleting any evidence rows.
    unique: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        unique[(int(row["chain_id"]), str(row["ca"]).lower())] = row
    rows = list(unique.values())

    session = build_session()
    refreshed: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    observed_at = utcnow_iso()

    for chain_id in sorted({int(row["chain_id"]) for row in rows}):
        chain_rows = [row for row in rows if int(row["chain_id"]) == chain_id]
        dex_chain = str(CHAIN_METADATA[chain_id]["dex_chain_id"])
        for batch in chunks(chain_rows, batch_size):
            requested = {str(row["ca"]).lower() for row in batch}
            url = "https://api.dexscreener.com/tokens/v1/" + dex_chain + "/" + ",".join(sorted(requested))
            try:
                response = session.get(url, timeout=(5.0, 30.0))
                response.raise_for_status()
                pairs = choose_pairs(response.json(), requested)
            except Exception as exc:
                errors.append({
                    "chain_id": chain_id,
                    "addresses": sorted(requested),
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue

            for row in batch:
                address = str(row["ca"]).lower()
                pair = pairs.get(address)
                if not pair:
                    missing.append({"chain_id": chain_id, "ca": address, "symbol": row.get("symbol")})
                    continue
                market_cap = number(pair.get("marketCap"))
                fdv = number(pair.get("fdv"))
                liquidity = number((pair.get("liquidity") or {}).get("usd"))
                created_at = None
                if pair.get("pairCreatedAt"):
                    created_at = datetime.fromtimestamp(
                        int(pair["pairCreatedAt"]) / 1000, timezone.utc
                    ).isoformat()
                base = pair.get("baseToken") or {}
                db.upsert_token({
                    "ca": address,
                    "chain_id": chain_id,
                    "chain": CHAIN_METADATA[chain_id]["tag"],
                    "symbol": base.get("symbol") or row.get("symbol"),
                    "name": base.get("name"),
                    "token_live_at": created_at,
                    "current_market_cap_usd": market_cap,
                    "observed_peak_market_cap_usd": market_cap,
                    "fdv_usd": fdv,
                    "current_liquidity_usd": liquidity,
                    "peak_liquidity_usd": liquidity,
                    "market_pair_url": pair.get("url"),
                    "market_data_at": observed_at,
                })
                refreshed.append({
                    "chain_id": chain_id,
                    "ca": address,
                    "symbol": base.get("symbol") or row.get("symbol"),
                    "current_market_cap_usd": market_cap,
                    "fdv_usd": fdv,
                    "current_liquidity_usd": liquidity,
                    "pair_created_at": created_at,
                    "market_pair_url": pair.get("url"),
                })
            if pause:
                time.sleep(pause)

    current_values = [row["current_market_cap_usd"] for row in refreshed]
    return {
        "selected": len(rows),
        "refreshed": len(refreshed),
        "refreshed_current_at_or_above_threshold": sum(
            value is not None and threshold is not None and value >= threshold
            for value in current_values
        ),
        "refreshed_current_below_threshold": sum(
            value is not None and threshold is not None and value < threshold
            for value in current_values
        ),
        "refreshed_without_market_cap": sum(value is None for value in current_values),
        "missing_from_dexscreener": len(missing),
        "batch_errors": errors,
        "observed_at": observed_at,
        "rows": refreshed,
        "missing": missing,
    }


def main() -> int:
    default_db = os.getenv("FORENSICS_DB_PATH")
    if not default_db or not Path(default_db).exists():
        if Path("data/forensics.db").exists():
            default_db = "data/forensics.db"
        else:
            default_db = "forensics.db"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=default_db)
    parser.add_argument(
        "--under-usd", type=float, default=1000.0,
        help="refresh rows whose current market cap is missing or below this value",
    )
    parser.add_argument("--all", action="store_true", help="refresh every supported token")
    parser.add_argument("--batch-size", type=int, default=30)
    parser.add_argument("--pause", type=float, default=0.25)
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--details", action="store_true", help="include every refreshed and missing row")
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 30:
        parser.error("--batch-size must be between 1 and 30")
    db_path = Path(args.db).resolve()
    if not db_path.exists():
        parser.error(f"database not found: {db_path}")
    backup = None if args.no_backup else backup_database(db_path)
    result = refresh(
        db_path,
        threshold=None if args.all else max(0.0, args.under_usd),
        batch_size=args.batch_size,
        pause=max(0.0, args.pause),
    )
    result["backup"] = str(backup) if backup else None
    if not args.details:
        result.pop("rows", None)
        result.pop("missing", None)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["batch_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
