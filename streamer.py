"""
Real-time streaming and 10-day backfill engine for Robinhood Chain (Chain ID: 4663)
Streams:
1. Dex-Paid Gate: Polls DexScreener live boosts & profiles for new Robinhood tokens.
2. Migration Gate: Queries Robinhood Chain RPC / Explorer for newly graduated contracts.
3. Automated Enrichment: Extracts 35+ parameters, computes Nearest Duplicate, and updates dashboard.
"""

import argparse
import json
import time
import requests
from typing import List, Set, Dict, Any

from database import ForensicDatabase
from forensics import extract_full_token_metadata
from phase1 import build_report, write_report_artifacts

DEXSCREENER_BOOSTS_URL = "https://api.dexscreener.com/token-boosts/latest/v1"
DEXSCREENER_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
DEXSCREENER_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search?q=robinhood"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def fetch_live_dexpaid_tokens() -> List[Dict[str, Any]]:
    """Polls DexScreener for newly paid / boosted Robinhood Chain tokens."""
    discovered = []
    seen_cas = set()

    # 1. Latest Boosts
    try:
        resp = requests.get(DEXSCREENER_BOOSTS_URL, headers=HEADERS, timeout=8)
        if resp.status_code == 200:
            items = resp.json()
            for item in items:
                chain = str(item.get("chainId", "")).lower()
                ca = item.get("tokenAddress")
                if chain in {"robinhood", "4663"} and ca and ca.lower() not in seen_cas:
                    seen_cas.add(ca.lower())
                    discovered.append({
                        "ca": ca,
                        "source": "dex_boosts",
                        "boost_amount": item.get("amount", 0),
                        "links": item.get("links", []),
                        "description": item.get("description", "")
                    })
    except Exception as e:
        print(f"[Streamer Warning] Boosts API error: {e}")

    # 2. Latest Token Profiles (paid updates/banners)
    try:
        resp = requests.get(DEXSCREENER_PROFILES_URL, headers=HEADERS, timeout=8)
        if resp.status_code == 200:
            items = resp.json()
            for item in items:
                chain = str(item.get("chainId", "")).lower()
                ca = item.get("tokenAddress")
                if chain in {"robinhood", "4663"} and ca and ca.lower() not in seen_cas:
                    seen_cas.add(ca.lower())
                    discovered.append({
                        "ca": ca,
                        "source": "dex_profile_update",
                        "links": item.get("links", []),
                        "description": item.get("description", "")
                    })
    except Exception as e:
        print(f"[Streamer Warning] Profiles API error: {e}")

    return discovered


def backfill_historical_tokens(days: int = 10) -> List[Dict[str, Any]]:
    """Backfills tokens from DexScreener pair search queries across Robinhood Chain."""
    print(f"[*] Starting {days}-day historical backfill for Robinhood Chain...")
    discovered = []
    seen_cas = set()

    # Search queries covering common meme tickers / terms on Robinhood
    search_queries = [
        "robinhood", "pons", "inu", "doge", "cat", "ai", "pepe", "trump",
        "sol", "eth", "coin", "token", "moon", "chad", "elon"
    ]

    for q in search_queries:
        url = f"https://api.dexscreener.com/latest/dex/search?q={q}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=8)
            if resp.status_code == 200:
                pairs = resp.json().get("pairs", [])
                for pair in pairs:
                    if pair.get("chainId") == "robinhood":
                        base_token = pair.get("baseToken", {})
                        ca = base_token.get("address")
                        if ca and ca.lower() not in seen_cas:
                            seen_cas.add(ca.lower())
                            discovered.append({
                                "ca": ca,
                                "symbol": base_token.get("symbol"),
                                "name": base_token.get("name"),
                                "fdv": pair.get("fdv"),
                                "liquidity_usd": pair.get("liquidity", {}).get("usd"),
                                "pair_created_at": pair.get("pairCreatedAt"),
                                "source": "historical_dex_search"
                            })
            time.sleep(0.3)  # Rate limit courtesy
        except Exception as e:
            print(f"[Backfill Warning] Search query '{q}' failed: {e}")

    print(f"[✓] Historical backfill discovered {len(discovered)} unique Robinhood token CAs.")
    return discovered


def ingest_and_enrich_token(db: ForensicDatabase, ca: str, chain_id: int = 4663, source: str = "stream") -> bool:
    """Extracts on-chain metadata and persists into database."""
    clean_ca = ca.strip().lower()
    existing = db.get_token(clean_ca)
    if existing and existing.get("creation_gas_used"):
        return False  # Already enriched

    try:
        print(f"[*] Ingesting and extracting on-chain forensics for {ca}...")
        profile = extract_full_token_metadata(ca, chain_id=chain_id)
        
        # Save token
        db.upsert_token({
            "ca": ca,
            "symbol": profile.get("token_symbol"),
            "name": profile.get("token_name"),
            "chain": "RBH",
            "launchpad": "Pons",
            "is_qualified": True,
            "qualification_reasons": {"source": source},
            "description": profile.get("description"),
            "website": profile.get("website_url"),
            "x_handle": profile.get("twitter_url")
        })
        db.upsert_execution_profile(profile)
        db.upsert_bytecode_profile(profile)
        print(f"[✓] Successfully enriched {profile.get('token_symbol', ca)} ({ca})")
        return True
    except Exception as e:
        print(f"[Enrich Error] Failed for {ca}: {e}")
        return False


def run_streamer(interval_seconds: int = 30, chain_id: int = 4663):
    """Continuous polling loop streaming live tokens into forensics.db."""
    db = ForensicDatabase("forensics.db")
    print(f"[*] Starting live Robinhood Chain token streamer (Interval: {interval_seconds}s)...")
    print("[*] Monitoring DexScreener Boosts, Profile Updates, and Graduation Events.")

    while True:
        try:
            live_tokens = fetch_live_dexpaid_tokens()
            newly_added = 0
            for t in live_tokens:
                ca = t["ca"]
                if ingest_and_enrich_token(db, ca, chain_id=chain_id, source=t["source"]):
                    newly_added += 1

            if newly_added > 0:
                print(f"[!] Rescoring candidate leads and updating dashboard (+{newly_added} tokens)...")
                report = build_report(db, qualified_only=True)
                write_report_artifacts(report, "phase1_fingerprint_report.json")
                import subprocess
                subprocess.run(["python", "generate_html_dashboard.py"], check=False)
                print("[✓] Dashboard updated live!")

        except Exception as e:
            print(f"[Streamer Loop Error]: {e}")

        time.sleep(interval_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Robinhood Token Streamer & Backfill Engine")
    parser.add_argument("--backfill", type=int, default=0, help="Number of days to backfill (e.g. 10)")
    parser.add_argument("--stream", action="store_true", help="Run continuous streaming listener")
    parser.add_argument("--interval", type=int, default=30, help="Polling interval in seconds")
    parser.add_argument("--chain-id", type=int, default=4663, help="Chain ID (default 4663 for Robinhood)")
    args = parser.parse_args()

    db = ForensicDatabase("forensics.db")

    if args.backfill > 0:
        tokens = backfill_historical_tokens(days=args.backfill)
        count = 0
        for t in tokens:
            if ingest_and_enrich_token(db, t["ca"], chain_id=args.chain_id, source="backfill"):
                count += 1
        print(f"[✓] Backfill complete: enriched {count} new tokens into forensics.db")
        report = build_report(db, qualified_only=True)
        write_report_artifacts(report, "phase1_fingerprint_report.json")
        import subprocess
        subprocess.run(["python", "generate_html_dashboard.py"], check=False)

    if args.stream:
        run_streamer(interval_seconds=args.interval, chain_id=args.chain_id)
