import os
import glob
import pandas as pd
from database import ForensicDatabase

def clean_ca(val):
    if pd.isna(val):
        return None
    s = str(val).strip()
    return s if s.startswith("0x") and len(s) == 42 else None

def clean_str(val):
    if pd.isna(val):
        return None
    s = str(val).strip()
    return s if s and s.lower() != "nan" else None

def clean_float(val, default=None):
    try:
        if pd.isna(val):
            return default
        return float(str(val).replace(",", "").strip())
    except Exception:
        return default

def clean_int(val, default=None):
    try:
        if pd.isna(val):
            return default
        return int(float(str(val).replace(",", "").strip()))
    except Exception:
        return default

def import_all():
    base_dir = os.path.dirname(__file__)
    db = ForensicDatabase(os.path.join(base_dir, "forensics.db"))
    print("Database initialized.")

    # 1. Ingest ca_matching_RBH CSV
    ca_match_files = glob.glob(os.path.join(base_dir, "ca_matching_RBH_*.csv"))
    teams_map = {} # team_name -> team_id

    if ca_match_files:
        path = ca_match_files[0]
        print(f"Ingesting matching ground truth: {os.path.basename(path)}")
        df_match = pd.read_csv(path)
        for _, row in df_match.iterrows():
            ca = clean_ca(row.get("ca"))
            if not ca:
                continue

            # Upsert Token
            db.upsert_token({
                "ca": ca,
                "symbol": clean_str(row.get("symbol")),
                "name": clean_str(row.get("name")),
                "launchpad": clean_str(row.get("launchpad")) or "Pons",
                "chain": "RBH",
                "is_qualified": True,
                "qualification_reasons": {"source": "ca_matching_ground_truth"}
            })

            # Upsert Execution Profile
            db.upsert_execution_profile({
                "ca": ca,
                "dev_wallet": clean_str(row.get("dev_wallet")),
                "funder_1hop": clean_str(row.get("funder")),
                "funder_label": clean_str(row.get("funder_label")),
                "fund_amount": clean_float(row.get("fund_amount")),
                "funded_at": clean_str(row.get("funded_at")),
                "value_eth": clean_float(row.get("value_eth")),
                "gwei": clean_float(row.get("gwei")),
                "max_gwei": clean_float(row.get("max_gwei")),
                "priority_gwei": clean_float(row.get("priority_gwei")),
                "nonce": clean_int(row.get("nonce")),
                "method_selector": clean_str(row.get("method")),
                "proxy_impl": clean_str(row.get("proxy_impl")),
                "proxy_kind": clean_str(row.get("proxy_kind"))
            })

            # Upsert Bytecode Profile
            db.upsert_bytecode_profile({
                "ca": ca,
                "template_hash": clean_str(row.get("template"))
            })

            # Register Team and Match Result
            team_name = clean_str(row.get("candidate_team"))
            team_id = None
            if team_name:
                if team_name not in teams_map:
                    team_id = db.upsert_team(
                        team_name=team_name,
                        representative_template=clean_str(row.get("template")),
                        root_funder=clean_str(row.get("funder"))
                    )
                    teams_map[team_name] = team_id
                else:
                    team_id = teams_map[team_name]

            best_match_ca = clean_str(row.get("best_match"))
            best_match_pct = clean_float(row.get("best_match_pct")) or 0.0

            db.upsert_token_match(
                ca=ca,
                best_match_ca=best_match_ca,
                best_match_pct=best_match_pct,
                candidate_team_id=team_id,
                candidate_team_name=team_name,
                match_reasons={"ground_truth": True, "legacy_match": best_match_ca}
            )

    # 2. Ingest tgscan_rbh CSV
    tgscan_files = glob.glob(os.path.join(base_dir, "tgscan_rbh_*.csv"))
    if tgscan_files:
        path = tgscan_files[0]
        print(f"Ingesting Telegram scan archive: {os.path.basename(path)}")
        df_tg = pd.read_csv(path)
        for _, row in df_tg.iterrows():
            ca = clean_ca(row.get("ca"))
            if not ca:
                continue

            db.upsert_token({
                "ca": ca,
                "symbol": clean_str(row.get("symbol")),
                "name": clean_str(row.get("name")),
                "chain": clean_str(row.get("chain")) or "RBH",
                "launchpad": clean_str(row.get("launchpad")) or "Pons",
                "token_live_at": clean_str(row.get("token_live")),
                "ath_usd": clean_float(row.get("ath")),
                "x_handle": clean_str(row.get("x")),
                "website": clean_str(row.get("website")),
                "description": clean_str(row.get("description"))
            })

            db.upsert_execution_profile({
                "ca": ca,
                "dev_wallet": clean_str(row.get("dev_wallet")),
                "funder_1hop": clean_str(row.get("funder")),
                "fund_amount": clean_float(row.get("dw_funded")),
                "funded_at": clean_str(row.get("dw_date_funded")),
                "value_eth": clean_float(row.get("value_eth")),
                "gwei": clean_float(row.get("gwei")),
                "max_gwei": clean_float(row.get("max_gwei")),
                "priority_gwei": clean_float(row.get("priority_gwei")),
                "nonce": clean_int(row.get("nonce")),
                "method_selector": clean_str(row.get("method"))
            })

            db.upsert_bytecode_profile({
                "ca": ca,
                "template_hash": clean_str(row.get("template"))
            })

            db.upsert_bundle_analytics({
                "ca": ca,
                "time_social_paid": clean_str(row.get("time_social_paid")),
                "first_boost": clean_str(row.get("1st_boost")),
                "hm_1st_boost": clean_str(row.get("hm_1st_boost")),
                "second_boost": clean_str(row.get("2nd_boost")),
                "hm_2nd_boost": clean_str(row.get("hm_2nd_boost")),
                "ads_paid": clean_str(row.get("ads"))
            })

    # 3. Ingest Bundle D Team CSV
    bundle_files = glob.glob(os.path.join(base_dir, "*Bundle D Team*.csv"))
    if bundle_files:
        path = bundle_files[0]
        print(f"Ingesting Bundle D analytics: {os.path.basename(path)}")
        df_bundle = pd.read_csv(path)
        for _, row in df_bundle.iterrows():
            ca = clean_ca(row.get("ca"))
            if not ca:
                continue

            # Ensure token exists in tokens table for foreign key constraint
            db.upsert_token({
                "ca": ca,
                "symbol": clean_str(row.get("symbol")),
                "name": clean_str(row.get("symbol")),
                "chain": "RBH"
            })

            db.upsert_bundle_analytics({
                "ca": ca,
                "dev_eth": clean_float(row.get("dev_eth")),
                "buyer_wallet": clean_str(row.get("buyer")),
                "buyer_eth": clean_float(row.get("buyer_eth")),
                "bundle_eth": clean_float(row.get("bundle_eth")),
                "bundler_wallet": clean_str(row.get("bundler_wallet"))
            })

    # Update summary counts
    print("\n--- INGESTION COMPLETE ---")
    tokens = db.get_all_historical_tokens()
    teams = db.get_team_summary()
    print(f"Total tokens in database: {len(tokens)}")
    print(f"Identified Teams ({len(teams)}):")
    for t in teams:
        print(f" • {t['team']}: {t['token_count']} tokens (Peak ATH: ${t['peak_ath_usd']:,.2f} | Avg Snipe: {t['avg_dev_snipe_eth']} ETH)")

if __name__ == "__main__":
    import_all()
