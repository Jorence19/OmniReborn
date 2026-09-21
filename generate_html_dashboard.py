import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# Load private source data from the application directory, never the public web root.
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DASHBOARD_DATA_DIR", APP_DIR)).resolve()
OUTPUT_DIR = Path(os.getenv("DASHBOARD_OUTPUT_DIR", APP_DIR)).resolve()
SNAPSHOT_PATH = Path(os.getenv(
    "API_SNAPSHOT_PATH", APP_DIR / "runtime" / "dashboard_candidates.json"
)).resolve()
DASHBOARD_API_URL = os.getenv("DASHBOARD_API_URL", "").strip()
if DASHBOARD_API_URL and (
    not DASHBOARD_API_URL.startswith("https://")
    or not DASHBOARD_API_URL.endswith("/api/candidates")
):
    raise ValueError("DASHBOARD_API_URL must be an HTTPS /api/candidates endpoint")
df_cand = pd.read_csv(DATA_DIR / "phase1_fingerprint_report_candidates.csv")
df_tg = pd.read_csv(APP_DIR / "tgscan_rbh_1789559702282.csv")
CHAIN_NAMES = {4663: "RBH", 5042: "ARC"}
if 'chain_id' not in df_cand.columns:
    df_cand['chain_id'] = 4663
df_cand['chain_id'] = pd.to_numeric(df_cand['chain_id'], errors='coerce').fillna(4663).astype(int)
# The Telegram export is Robinhood-specific; never attach it to an Arc address
# just because the same 20-byte contract address happens to exist on both chains.
df_tg['chain_id'] = 4663
df_tg['ca_lower'] = df_tg['ca'].astype(str).str.lower()
df_cand['ca_lower'] = df_cand['ca'].astype(str).str.lower()
df_tg['_ath_numeric'] = pd.to_numeric(df_tg['ath'], errors='coerce').fillna(0.0)
df_tg = (
    df_tg.sort_values('_ath_numeric')
    .drop_duplicates(subset=['chain_id', 'ca_lower'], keep='last')
    .drop(columns=['_ath_numeric'])
)

merged = pd.merge(
    df_cand,
    df_tg[['chain_id', 'ca_lower', 'ath', 'name', 'token_live', 'x', 'website', 'gwei', 'max_gwei', 'priority_gwei', '1st_boost', 'ads']],
    on=['chain_id', 'ca_lower'],
    how='left'
)

# Prefer the live token registry; the historical Telegram export remains a fallback.
db_path = Path(os.getenv("FORENSICS_DB_PATH", APP_DIR / "forensics.db")).resolve()
if db_path.exists():
    with sqlite3.connect(db_path) as connection:
        df_live = pd.read_sql_query(
            '''SELECT LOWER(ca) AS ca_lower, COALESCE(chain_id, 4663) AS chain_id,
                      chain AS live_chain, name AS live_name,
                      token_live_at AS live_token_live, ath_usd AS live_ath,
                      website AS live_website, x_handle AS live_x
               FROM tokens''',
            connection,
        )
    df_live['live_ath'] = pd.to_numeric(df_live['live_ath'], errors='coerce').fillna(0.0)
    df_live = (
        df_live.sort_values('live_ath')
        .drop_duplicates(subset=['chain_id', 'ca_lower'], keep='last')
    )
    merged = pd.merge(merged, df_live, on=['chain_id', 'ca_lower'], how='left')

    # Ensure all tokens in SQLite (including newly ingested Arc tokens) are included
    existing_pairs = set(zip(merged['chain_id'], merged['ca_lower']))
    with sqlite3.connect(db_path) as connection:
        df_db_all = pd.read_sql_query(
            '''SELECT t.ca, LOWER(t.ca) AS ca_lower, COALESCE(t.chain_id, 4663) AS chain_id,
                      COALESCE(t.chain, CASE WHEN t.chain_id=5042 THEN 'ARC' ELSE 'RBH' END) AS chain,
                      COALESCE(t.symbol, 'TOKEN') AS symbol,
                      COALESCE(t.name, t.symbol, 'Token') AS name,
                      t.token_live_at, t.ath_usd AS ath,
                      t.website, t.x_handle AS x,
                      COALESCE(tm.candidate_team_name, 'unclustered') AS inferred_team,
                      COALESCE(tm.best_match_pct, 20.0) AS candidate_score,
                      COALESCE(tm.best_match_ca, '') AS best_match_ca,
                      CASE
                          WHEN tm.best_match_pct >= 65 THEN 'HIGH_LEAD'
                          WHEN tm.best_match_pct >= 45 THEN 'PROBABLE_LEAD'
                          WHEN tm.best_match_pct >= 30 THEN 'WATCH'
                          ELSE 'TRACKED'
                      END AS confidence,
                      tm.match_reasons AS evidence
               FROM tokens t
               LEFT JOIN token_matches tm ON LOWER(t.ca) = LOWER(tm.ca)''',
            connection,
        )
    extra_rows = []
    for _, row in df_db_all.iterrows():
        cid = int(row['chain_id'])
        cal = str(row['ca_lower'])
        if (cid, cal) not in existing_pairs:
            rec = dict(row)
            ev = rec.get('evidence')
            rec['evidence'] = '[]'
            if isinstance(ev, str) and 'evidence' in ev:
                try:
                    parsed_ev = json.loads(ev).get('evidence', [])
                    rec['evidence'] = json.dumps(parsed_ev)
                except Exception:
                    pass
            extra_rows.append(rec)
    if extra_rows:
        df_extra = pd.DataFrame(extra_rows)
        merged = pd.concat([merged, df_extra], ignore_index=True)
else:
    for column in ('live_chain', 'live_name', 'live_token_live', 'live_ath', 'live_website', 'live_x'):
        merged[column] = None


# One public candidate per chain and case-insensitive EVM contract address.
merged = merged.drop_duplicates(subset=['chain_id', 'ca_lower'], keep='last')


def first_present(*values):
    for value in values:
        if pd.notna(value) and str(value).strip() not in ('', 'nan', 'None'):
            return value
    return None


def supported_chain_id(value, default=4663):
    try:
        chain_id = int(float(value))
    except (TypeError, ValueError):
        return default
    return chain_id if chain_id in CHAIN_NAMES else default


# Prepare candidates JSON structure for client-side JavaScript
candidates_data = []
for idx, r in merged.iterrows():
    ca = str(r['ca'])
    chain_id = supported_chain_id(r.get('chain_id'))
    chain = str(first_present(r.get('live_chain'), r.get('chain'), CHAIN_NAMES.get(chain_id)) or f"CHAIN-{chain_id}")
    symbol = str(r['symbol'])
    name = str(first_present(r.get('live_name'), r.get('name'), symbol))
    conf = str(r['confidence'])
    score = float(r['candidate_score'])
    team = str(r['inferred_team']) if pd.notna(r.get('inferred_team')) else 'unclustered'
    best_match_symbol = str(r['best_match_symbol']) if pd.notna(r.get('best_match_symbol')) else ''
    best_match_ca = str(r['best_match_ca']) if pd.notna(r.get('best_match_ca')) else ''
    best_match_chain_id = supported_chain_id(r.get('best_match_chain_id'), chain_id)
    best_match_chain = str(first_present(r.get('best_match_chain'), CHAIN_NAMES.get(best_match_chain_id)) or f"CHAIN-{best_match_chain_id}")
    live_ath = float(r.get('live_ath') or 0) if pd.notna(r.get('live_ath')) else 0.0
    historical_ath = float(r.get('ath') or 0) if pd.notna(r.get('ath')) else 0.0
    ath = max(live_ath, historical_ath, 0.0)
    token_live = str(first_present(r.get('live_token_live'), r.get('token_live')) or '')
    
    # Rug identification: peak ATH <= $5,000 or zero volume/failed launch
    is_rug = (ath <= 5000.0)
    
    # Parse evidence
    ev_raw = r['evidence']
    try:
        ev_list = json.loads(ev_raw) if isinstance(ev_raw, str) else ev_raw
    except Exception:
        ev_list = []
        
    candidates_data.append({
        "ca": ca,
        "chain_id": chain_id,
        "chain": chain,
        "symbol": symbol,
        "name": name,
        "confidence": conf,
        "score": score,
        "team": team,
        "best_match_symbol": best_match_symbol,
        "best_match_ca": best_match_ca,
        "best_match_chain_id": best_match_chain_id,
        "best_match_chain": best_match_chain,
        "ath": ath,
        "is_rug": is_rug,
        "evidence": ev_list,
        "token_live": token_live,
        "website": str(first_present(r.get('live_website'), r.get('website')) or ''),
        "x": str(first_present(r.get('live_x'), r.get('x')) or ''),
    })

# Define the 50 parameters with Pre-Launch vs Post-Launch phase tags and default weights
PARAMETERS = [
    # ==================== ⚡ PRE-LAUNCH (SNIPE DECISIONS) ====================
    # 1. Wallets & Lineage (Pre-Launch)
    {
        "id": "dev_wallet",
        "name": "Deployer Wallet (dev_wallet)",
        "phase": "PRE_LAUNCH",
        "category": "Wallets & Lineage",
        "default": 1.00,
        "description": "Direct deployer wallet reused across tokens (immediate 100% attribution)."
    },
    {
        "id": "funder_1hop",
        "name": "1-Hop Funder Address (funder_1hop)",
        "phase": "PRE_LAUNCH",
        "category": "Wallets & Lineage",
        "default": 0.85,
        "description": "Immediate upstream funding source transferring ETH to the dev wallet (e.g. Astro Treasury)."
    },
    {
        "id": "funder_2hop",
        "name": "2-Hop Funder Address (funder_2hop)",
        "phase": "PRE_LAUNCH",
        "category": "Wallets & Lineage",
        "default": 0.85,
        "description": "Grandparent funding root or distribution hub two hops upstream."
    },
    {
        "id": "funder_label",
        "name": "Funder Label / Exchange Tag (funder_label)",
        "phase": "PRE_LAUNCH",
        "category": "Wallets & Lineage",
        "default": 0.08,
        "description": "Recognized CEX hot wallet or bridge tag (e.g. Binance Hot Wallet, FixedFloat)."
    },

    # 2. Bytecode & Smart Contract Architecture (Pre-Launch)
    {
        "id": "normalized_bytecode_hash",
        "name": "Normalized Bytecode Hash",
        "phase": "PRE_LAUNCH",
        "category": "Bytecode & Architecture",
        "default": 0.55,
        "description": "Exact SHA-256 bytecode match after stripping CBOR compiler metadata and constructor addresses."
    },
    {
        "id": "contract_factory",
        "name": "Contract Factory / Proxy",
        "phase": "PRE_LAUNCH",
        "category": "Bytecode & Architecture",
        "default": 0.55,
        "description": "Factory or proxy deployer contract address used to spawn token contracts."
    },
    {
        "id": "template_hash",
        "name": "Template Structural Hash",
        "phase": "PRE_LAUNCH",
        "category": "Bytecode & Architecture",
        "default": 0.35,
        "description": "Structural opcode flow hash representing token contract implementation framework."
    },
    {
        "id": "selectors_hash",
        "name": "Function Selectors Hash",
        "phase": "PRE_LAUNCH",
        "category": "Bytecode & Architecture",
        "default": 0.30,
        "description": "Combined hash of all public ABI 4-byte function selectors supported by the token."
    },
    {
        "id": "method_selector",
        "name": "Creation Method Selector",
        "phase": "PRE_LAUNCH",
        "category": "Bytecode & Architecture",
        "default": 0.15,
        "description": "4-byte function selector used to deploy the token (e.g. 0xf85f8e41 launchAndBuy)."
    },
    {
        "id": "compiler_version",
        "name": "Solidity Compiler Version",
        "phase": "PRE_LAUNCH",
        "category": "Bytecode & Architecture",
        "default": 0.10,
        "description": "Exact solc compiler version extracted from bytecode metadata (e.g. 0.8.28)."
    },
    {
        "id": "launchpad",
        "name": "Launchpad Platform",
        "phase": "PRE_LAUNCH",
        "category": "Bytecode & Architecture",
        "default": 0.10,
        "description": "Platform or bonding curve mechanism used (e.g. pons, uniswap_v4)."
    },

    # 3. Execution, Setup & Gas Habits (Pre-Launch)
    {
        "id": "setup_time_seconds",
        "name": "Setup Time (Funding to Deploy)",
        "phase": "PRE_LAUNCH",
        "category": "Execution & Gas",
        "default": 0.20,
        "description": "Time elapsed between funder ETH arrival and token deployment (±15% buffer)."
    },
    {
        "id": "wallet_age_at_deploy_seconds",
        "name": "Wallet Age at Deploy Bucket",
        "phase": "PRE_LAUNCH",
        "category": "Execution & Gas",
        "default": 0.20,
        "description": "Total age of deployer address from its very first tx on chain (±15% buffer)."
    },
    {
        "id": "nonce",
        "name": "Deployer Nonce at Launch",
        "phase": "PRE_LAUNCH",
        "category": "Execution & Gas",
        "default": 0.20,
        "description": "Wallet nonce count when issuing the token creation transaction."
    },
    {
        "id": "value_eth",
        "name": "Deployment ETH Value (Snipe / Liquidity)",
        "phase": "PRE_LAUNCH",
        "category": "Execution & Gas",
        "default": 0.25,
        "description": "Exact native ETH sent alongside token creation call (e.g. .0005 snipe habit, ±15% buffer)."
    },
    {
        "id": "fund_amount",
        "name": "Funder Transfer Amount",
        "phase": "PRE_LAUNCH",
        "category": "Execution & Gas",
        "default": 0.25,
        "description": "ETH amount sent by upstream funder to seed the deployer (±15% buffer)."
    },
    {
        "id": "funding_count_before_deploy",
        "name": "Funding Tx Count Before Deploy",
        "phase": "PRE_LAUNCH",
        "category": "Execution & Gas",
        "default": 0.20,
        "description": "Number of incoming funding transactions into deployer before creation."
    },
    {
        "id": "funding_total_eth_before_deploy",
        "name": "Total ETH Seed Capital",
        "phase": "PRE_LAUNCH",
        "category": "Execution & Gas",
        "default": 0.20,
        "description": "Cumulative ETH received by deployer before token deployment (±15% buffer)."
    },
    {
        "id": "creation_gas_used",
        "name": "Creation Gas Used",
        "phase": "PRE_LAUNCH",
        "category": "Execution & Gas",
        "default": 0.15,
        "description": "Gas units consumed by token deployment transaction (±15% buffer)."
    },
    {
        "id": "creation_tx_fee_eth",
        "name": "Creation Tx Fee (ETH)",
        "phase": "PRE_LAUNCH",
        "category": "Execution & Gas",
        "default": 0.15,
        "description": "Total transaction fee paid to block builder in ETH (±15% buffer)."
    },
    {
        "id": "gwei",
        "name": "Gas Base Fee (Gwei)",
        "phase": "PRE_LAUNCH",
        "category": "Execution & Gas",
        "default": 0.15,
        "description": "Effective gas price chosen by dev during deployment (±15% buffer)."
    },
    {
        "id": "max_gwei",
        "name": "Max Fee Per Gas (Max Gwei)",
        "phase": "PRE_LAUNCH",
        "category": "Execution & Gas",
        "default": 0.15,
        "description": "EIP-1559 maxFeePerGas setting configured in launch transaction (±15% buffer)."
    },
    {
        "id": "priority_gwei",
        "name": "Priority Tip Fee (Priority Gwei)",
        "phase": "PRE_LAUNCH",
        "category": "Execution & Gas",
        "default": 0.20,
        "description": "EIP-1559 maxPriorityFeePerGas habit (e.g. 0.1 Gwei, ±15% buffer)."
    },
    {
        "id": "initial_snipe_tokens",
        "name": "Dev Initial Snipe Token Amount",
        "phase": "PRE_LAUNCH",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "Number of tokens bought by dev in the deployment transaction (±15% buffer)."
    },

    # ==================== 🛡️ POST-LAUNCH (HOLDING & EXIT DECISIONS) ====================
    # 4. Bundle & Insider Dynamics (Post-Launch)
    {
        "id": "bundler_wallet",
        "name": "Bundler Wallet (bundler_wallet)",
        "phase": "POST_LAUNCH",
        "category": "Wallets & Lineage",
        "default": 1.00,
        "description": "Wallet coordinating or funding launch bundles at block 0."
    },
    {
        "id": "buyer_wallet",
        "name": "Insider Buyer Wallet (buyer_wallet)",
        "phase": "POST_LAUNCH",
        "category": "Wallets & Lineage",
        "default": 1.00,
        "description": "Repeat sniper or internal buyer wallet detected across multiple team coins."
    },
    {
        "id": "bundle_eth",
        "name": "Total Bundle ETH Spent",
        "phase": "POST_LAUNCH",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "Aggregate ETH spent across all coordinated block 0 bundle transactions (±15% buffer)."
    },
    {
        "id": "dev_eth",
        "name": "Dev Snipe ETH Amount",
        "phase": "POST_LAUNCH",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "Exact ETH capital committed by the dev address at launch (±15% buffer)."
    },
    {
        "id": "buyer_eth",
        "name": "Top Insider Buyer ETH",
        "phase": "POST_LAUNCH",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "ETH capital deployed by top coordinated insider buyer (±15% buffer)."
    },
    {
        "id": "bundle_ratio",
        "name": "Bundle Supply Ratio (%)",
        "phase": "POST_LAUNCH",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "Percentage of total token supply sniped in the launch bundle (±15% buffer)."
    },
    {
        "id": "bundle_wallets_count",
        "name": "Bundle Wallets Count",
        "phase": "POST_LAUNCH",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "Number of coordinated buyer wallets participating in the launch bundle."
    },
    {
        "id": "dev_holding_ratio",
        "name": "Dev Supply Retention Ratio",
        "phase": "POST_LAUNCH",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "Percentage of total supply retained by dev post-graduation (±15% buffer)."
    },
    {
        "id": "dev_sold_ratio",
        "name": "Dev Supply Sold Ratio",
        "phase": "POST_LAUNCH",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "Percentage of dev holdings sold into the market (±15% buffer)."
    },
    {
        "id": "top_10_ratio",
        "name": "Top 10 Holders Supply Concentration",
        "phase": "POST_LAUNCH",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "Percentage of supply controlled by the top 10 holders combined (±15% buffer)."
    },

    # 5. Marketing Speed & Boost Purchases (Post-Launch)
    {
        "id": "first_boost_used",
        "name": "Dex Paid 1st Boost Purchase",
        "phase": "POST_LAUNCH",
        "category": "Marketing & Timing",
        "default": 0.22,
        "description": "Whether dev bought DexScreener/DexTools 1st marketing boost."
    },
    {
        "id": "second_boost_used",
        "name": "Dex Paid 2nd Boost Purchase",
        "phase": "POST_LAUNCH",
        "category": "Marketing & Timing",
        "default": 0.22,
        "description": "Whether dev followed up with 2nd marketing boost."
    },
    {
        "id": "ads_paid_used",
        "name": "Banner Ads Paid at Launch",
        "phase": "POST_LAUNCH",
        "category": "Marketing & Timing",
        "default": 0.22,
        "description": "Whether dev paid for sponsored banner advertisements on DexScreener."
    },
    {
        "id": "launch_hour_utc",
        "name": "Launch Hour (UTC)",
        "phase": "POST_LAUNCH",
        "category": "Marketing & Timing",
        "default": 0.22,
        "description": "UTC hour of day when dev team habitually deploys."
    },
    {
        "id": "launch_quarter_hour_utc",
        "name": "Launch 15-Minute Window (UTC)",
        "phase": "POST_LAUNCH",
        "category": "Marketing & Timing",
        "default": 0.22,
        "description": "Specific 15-minute window habit within the hour."
    },
    {
        "id": "launch_weekday_utc",
        "name": "Launch Day of Week (UTC)",
        "phase": "POST_LAUNCH",
        "category": "Marketing & Timing",
        "default": 0.22,
        "description": "Day of the week preferred by team for deployments."
    },

    # 6. Branding, Socials & Web Infrastructure (Post-Launch)
    {
        "id": "favicon_hash",
        "name": "Favicon MMH3 / SHA-256 Hash",
        "phase": "POST_LAUNCH",
        "category": "Branding & Socials",
        "default": 1.00,
        "description": "Exact cryptographic hash of website favicon icon asset."
    },
    {
        "id": "tg_handle",
        "name": "Telegram Channel / Group Handle",
        "phase": "POST_LAUNCH",
        "category": "Branding & Socials",
        "default": 1.00,
        "description": "Exact Telegram username or invite link reused by team."
    },
    {
        "id": "x_handle",
        "name": "Twitter / X Profile Handle",
        "phase": "POST_LAUNCH",
        "category": "Branding & Socials",
        "default": 1.00,
        "description": "Exact X / Twitter profile handle linked to project."
    },
    {
        "id": "website_domain",
        "name": "Website Root Domain",
        "phase": "POST_LAUNCH",
        "category": "Branding & Socials",
        "default": 0.85,
        "description": "Base domain name hosting the token's landing page."
    },
    {
        "id": "website_host_type",
        "name": "Website Hosting Infrastructure",
        "phase": "POST_LAUNCH",
        "category": "Branding & Socials",
        "default": 0.22,
        "description": "Hosting provider signature (Vercel, Carrd, Netlify, Cloudflare Pages)."
    },
    {
        "id": "tg_naming_pattern",
        "name": "Telegram Naming Regex Pattern",
        "phase": "POST_LAUNCH",
        "category": "Branding & Socials",
        "default": 0.22,
        "description": "Syntax habits in TG handle construction (e.g. _portal, _sol, _erc20)."
    },
    {
        "id": "x_naming_pattern",
        "name": "Twitter / X Naming Pattern",
        "phase": "POST_LAUNCH",
        "category": "Branding & Socials",
        "default": 0.22,
        "description": "Syntax habits in X handle naming conventions (e.g. _coin, real_token)."
    },
    {
        "id": "description_length_bucket",
        "name": "Description Length Bucket",
        "phase": "POST_LAUNCH",
        "category": "Branding & Socials",
        "default": 0.22,
        "description": "50-character binned length of token description copy."
    },
    {
        "id": "description_hashtag_count",
        "name": "Description Hashtag Count",
        "phase": "POST_LAUNCH",
        "category": "Branding & Socials",
        "default": 0.22,
        "description": "Number of '#' hashtags included in description."
    },
    {
        "id": "description_mention_count",
        "name": "Description Mention Count",
        "phase": "POST_LAUNCH",
        "category": "Branding & Socials",
        "default": 0.22,
        "description": "Number of '@' handles tagged in description."
    }
]

def script_json(value):
    # Prevent user-controlled token metadata from terminating the inline script.
    return json.dumps(value, separators=(",", ":")).replace("<", "\u003c").replace(
        ">", "\u003e"
    ).replace("&", "\u0026").replace(" ", "\u2028").replace(" ", "\u2029")


candidates_json = script_json(candidates_data)
parameters_json = script_json(PARAMETERS)
api_url_json = script_json(DASHBOARD_API_URL)
try:
    report_meta = json.loads((DATA_DIR / "phase1_fingerprint_report.json").read_text(encoding="utf-8"))
    generated_at = str(report_meta.get("generated_at") or "")
except (OSError, json.JSONDecodeError):
    generated_at = ""
if not generated_at:
    generated_at = datetime.now(timezone.utc).isoformat()

html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Robinhood + Arc Meme Coin Forensics & Team Leads Dashboard</title>
    <style>
        :root {{
            --bg-main: #0b0e14;
            --bg-card: #151922;
            --bg-card-hover: #1c2230;
            --border-color: #262c3b;
            --border-active: #3b82f6;
            --text-primary: #e6edf3;
            --text-secondary: #8b949e;
            --text-muted: #64748b;
            --accent-green: #2ecc71;
            --accent-blue: #3b82f6;
            --accent-cyan: #06b6d4;
            --accent-purple: #a855f7;
            --accent-orange: #f59e0b;
            --accent-red: #ef4444;
            --badge-high: #2ecc71;
            --badge-prob: #3b82f6;
            --badge-watch: #f59e0b;
            --badge-weak: #64748b;
        }}
        * {{
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, "Helvetica Neue", sans-serif;
            background-color: var(--bg-main);
            color: var(--text-primary);
            margin: 0;
            padding: 0;
            line-height: 1.5;
        }}
        .navbar {{
            background: #11151f;
            border-bottom: 1px solid var(--border-color);
            padding: 14px 28px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: sticky;
            top: 0;
            z-index: 100;
        }}
        .navbar-brand {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .logo-icon {{
            font-size: 22px;
            background: linear-gradient(135deg, #10b981, #3b82f6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-weight: 900;
        }}
        .brand-title {{
            font-size: 17px;
            font-weight: 700;
            letter-spacing: 0.5px;
            color: #ffffff;
        }}
        .brand-subtitle {{
            font-size: 12px;
            color: var(--text-secondary);
        }}
        .nav-tabs {{
            display: flex;
            gap: 8px;
            background: #0b0e14;
            padding: 4px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
        }}
        .nav-tab {{
            background: transparent;
            border: none;
            color: var(--text-secondary);
            font-size: 13px;
            font-weight: 600;
            padding: 8px 18px;
            border-radius: 6px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 8px;
            transition: all 0.2s ease;
        }}
        .nav-tab:hover {{
            color: #ffffff;
            background: rgba(255, 255, 255, 0.05);
        }}
        .nav-tab.active {{
            background: #2563eb;
            color: #ffffff;
            box-shadow: 0 2px 8px rgba(37, 99, 235, 0.4);
        }}
        .tab-count {{
            background: rgba(255, 255, 255, 0.2);
            padding: 2px 7px;
            border-radius: 12px;
            font-size: 11px;
        }}
        .container {{
            max-width: 1440px;
            margin: 0 auto;
            padding: 24px;
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .stat-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 16px 20px;
            position: relative;
            overflow: hidden;
        }}
        .stat-card::before {{
            content: "";
            position: absolute;
            top: 0;
            left: 0;
            width: 4px;
            height: 100%;
            background: var(--stat-accent, #3b82f6);
        }}
        .stat-card .title {{
            font-size: 11px;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.8px;
            font-weight: 600;
        }}
        .stat-card .val {{
            font-size: 26px;
            font-weight: 800;
            margin-top: 6px;
            color: #ffffff;
            font-family: monospace;
        }}
        .stat-card .subtext {{
            font-size: 11px;
            color: var(--text-muted);
            margin-top: 4px;
        }}
        .page-content {{
            display: none;
        }}
        .page-content.active {{
            display: block;
        }}
        .filter-bar {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 14px 18px;
            margin-bottom: 18px;
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            align-items: center;
            justify-content: space-between;
        }}
        .filter-group {{
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }}
        .search-input, .select-input {{
            background: #0b0e14;
            border: 1px solid var(--border-color);
            color: #ffffff;
            padding: 8px 14px;
            border-radius: 6px;
            font-size: 13px;
            outline: none;
            transition: border 0.2s;
        }}
        .search-input:focus, .select-input:focus {{
            border-color: var(--border-active);
        }}
        .search-input {{
            min-width: 250px;
        }}
        .btn {{
            border: none;
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s ease;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            text-decoration: none;
        }}
        .btn-primary {{
            background: #10b981;
            color: #ffffff;
        }}
        .btn-primary:hover {{
            background: #059669;
            box-shadow: 0 2px 10px rgba(16, 185, 129, 0.4);
        }}
        .btn-secondary {{
            background: #334155;
            color: #f1f5f9;
        }}
        .btn-secondary:hover {{
            background: #475569;
        }}
        .btn-blue {{
            background: #2563eb;
            color: #ffffff;
        }}
        .btn-blue:hover {{
            background: #1d4ed8;
        }}
        .btn-gmgn {{
            background: #0ea5e9;
            color: #ffffff;
            padding: 5px 9px;
            font-size: 11px;
        }}
        .btn-gmgn:hover {{ background: #0284c7; }}
        .btn-dex {{
            background: #10b981;
            color: #ffffff;
            padding: 5px 9px;
            font-size: 11px;
        }}
        .btn-dex:hover {{ background: #059669; }}
        .btn-scan {{
            background: #475569;
            color: #cbd5e1;
            padding: 5px 9px;
            font-size: 11px;
        }}
        .btn-scan:hover {{ background: #64748b; }}
        .table-container {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            overflow-x: auto;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 13px;
        }}
        th {{
            background-color: #111622;
            color: var(--text-secondary);
            font-weight: 700;
            padding: 14px 16px;
            border-bottom: 1px solid var(--border-color);
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 0.6px;
            white-space: nowrap;
            user-select: none;
        }}
        th.sortable:hover {{
            color: #ffffff;
            background-color: #1a2233;
        }}
        td {{
            padding: 14px 16px;
            border-bottom: 1px solid var(--border-color);
            vertical-align: middle;
        }}
        tr:hover td {{
            background-color: rgba(255, 255, 255, 0.02);
        }}
        /* 40% Red Opacity for Rug Tokens */
        tr.rug-row {{
            background-color: rgba(239, 68, 68, 0.40) !important;
        }}
        tr.rug-row:hover td {{
            background-color: rgba(239, 68, 68, 0.50) !important;
        }}
        .badge {{
            padding: 4px 9px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
            display: inline-block;
            letter-spacing: 0.3px;
            text-transform: uppercase;
        }}
        .badge.high_lead {{
            background: rgba(46, 204, 113, 0.15);
            color: #2ecc71;
            border: 1px solid rgba(46, 204, 113, 0.4);
        }}
        .badge.probable_lead {{
            background: rgba(59, 130, 246, 0.15);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.4);
        }}
        .badge.watch {{
            background: rgba(245, 158, 11, 0.15);
            color: #fbbf24;
            border: 1px solid rgba(245, 158, 11, 0.4);
        }}
        .badge.weak {{
            background: rgba(100, 116, 139, 0.15);
            color: #94a3b8;
            border: 1px solid rgba(100, 116, 139, 0.4);
        }}
        .badge.rug-badge {{
            background: #ef4444;
            color: #ffffff;
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 4px;
            font-weight: 800;
            margin-left: 6px;
            vertical-align: middle;
        }}
        .score-val {{
            font-size: 15px;
            font-weight: 800;
            font-family: monospace;
        }}
        .team-tag {{
            background: #1e293b;
            color: #93c5fd;
            padding: 4px 10px;
            border-radius: 6px;
            font-weight: 700;
            font-size: 11px;
            border: 1px solid rgba(147, 197, 253, 0.2);
            white-space: nowrap;
        }}
        .chain-pill {{
            display: inline-block;
            background: rgba(168, 85, 247, 0.16);
            color: #d8b4fe;
            border: 1px solid rgba(168, 85, 247, 0.4);
            padding: 2px 6px;
            margin-left: 6px;
            border-radius: 999px;
            font-size: 10px;
            font-weight: 800;
        }}
        .ath-val {{
            color: #34d399;
            font-weight: 800;
            font-family: monospace;
            font-size: 14px;
        }}
        .token-cell {{
            min-width: 170px;
        }}
        .token-symbol {{
            font-size: 15px;
            font-weight: 800;
            color: #ffffff;
        }}
        .token-name {{
            color: var(--text-secondary);
            font-size: 12px;
            margin-left: 6px;
            font-weight: normal;
        }}
        .token-ca {{
            margin-top: 4px;
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .token-ca code {{
            color: #94a3b8;
            font-size: 11px;
            background: #090c12;
            padding: 2px 6px;
            border-radius: 4px;
            font-family: monospace;
        }}
        .copy-btn {{
            background: transparent;
            border: none;
            color: var(--text-muted);
            cursor: pointer;
            font-size: 11px;
            padding: 1px 4px;
            border-radius: 3px;
        }}
        .copy-btn:hover {{
            color: #ffffff;
            background: #1e293b;
        }}
        .sibling-badge {{
            background: #0f172a;
            border: 1px solid #3b82f6;
            color: #60a5fa;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
            display: inline-block;
        }}
        .date-cell {{
            font-family: monospace;
            font-size: 12px;
            color: #cbd5e1;
            white-space: nowrap;
        }}
        .evidence-tags {{
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
            max-width: 360px;
        }}
        .ev-tag {{
            background: #0f172a;
            border: 1px solid #1e293b;
            padding: 2px 7px;
            border-radius: 4px;
            font-size: 11px;
            color: #cbd5e1;
            white-space: nowrap;
        }}
        .ev-tag b {{
            color: #60a5fa;
        }}
        .actions-cell {{
            display: flex;
            gap: 6px;
            white-space: nowrap;
        }}
        .toast-banner {{
            background: linear-gradient(90deg, rgba(16, 185, 129, 0.2), rgba(59, 130, 246, 0.2));
            border: 1px solid #10b981;
            color: #34d399;
            padding: 12px 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: none;
            align-items: center;
            justify-content: space-between;
            font-size: 13px;
            font-weight: 600;
        }}
        .phase-pill {{
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 10px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            display: inline-block;
            margin-right: 6px;
        }}
        .phase-pill.pre {{
            background: rgba(6, 182, 212, 0.2);
            color: #22d3ee;
            border: 1px solid rgba(6, 182, 212, 0.4);
        }}
        .phase-pill.post {{
            background: rgba(168, 85, 247, 0.2);
            color: #c084fc;
            border: 1px solid rgba(168, 85, 247, 0.4);
        }}
        .category-chip {{
            background: #1e293b;
            color: #cbd5e1;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            display: inline-block;
            margin-bottom: 4px;
        }}
        .param-desc {{
            font-size: 12px;
            color: var(--text-secondary);
            line-height: 1.4;
            max-width: 480px;
        }}
        .weight-display {{
            font-family: monospace;
            font-size: 15px;
            font-weight: 800;
            color: #38bdf8;
            background: #0b1120;
            padding: 4px 10px;
            border-radius: 6px;
            border: 1px solid #1e293b;
            display: inline-block;
            min-width: 54px;
            text-align: center;
        }}
        .weight-input-box {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .weight-num-input {{
            background: #090c12;
            border: 1px solid #334155;
            color: #ffffff;
            padding: 6px 10px;
            border-radius: 6px;
            width: 75px;
            font-size: 13px;
            font-family: monospace;
            font-weight: bold;
            text-align: center;
            outline: none;
        }}
        .weight-num-input:focus {{
            border-color: #3b82f6;
            box-shadow: 0 0 6px rgba(59, 130, 246, 0.4);
        }}
        .weight-slider {{
            width: 130px;
            cursor: pointer;
            accent-color: #10b981;
        }}
        .buffer-card {{
            background: #111827;
            border: 1px solid #1f2937;
            border-radius: 8px;
            padding: 14px 20px;
            margin-bottom: 18px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 20px;
        }}
        .highlight-update {{
            animation: pulse-green 1s ease;
        }}
        @keyframes pulse-green {{
            0% {{ background-color: rgba(16, 185, 129, 0.4); }}
            100% {{ background-color: transparent; }}
        }}
        .footer {{
            text-align: center;
            padding: 30px;
            color: var(--text-muted);
            font-size: 12px;
            border-top: 1px solid var(--border-color);
            margin-top: 40px;
        }}
        .chain-header-bar {{
            background: #0f131d;
            border-bottom: 1px solid var(--border-color);
            padding: 12px 28px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 12px;
        }}
        .chain-header-left {{
            display: flex;
            align-items: center;
            gap: 14px;
        }}
        .chain-selector-title {{
            font-size: 11px;
            font-weight: 800;
            color: var(--text-secondary);
            letter-spacing: 1px;
            text-transform: uppercase;
        }}
        .chain-pill-group {{
            display: inline-flex;
            background: #07090e;
            padding: 4px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
            gap: 4px;
        }}
        .chain-btn {{
            background: transparent;
            border: none;
            color: var(--text-secondary);
            font-size: 13px;
            font-weight: 700;
            padding: 7px 16px;
            border-radius: 6px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: all 0.2s ease;
        }}
        .chain-btn:hover {{
            color: #ffffff;
            background: rgba(255, 255, 255, 0.06);
        }}
        .chain-btn.active.chain-all {{
            background: #2563eb;
            color: #ffffff;
            box-shadow: 0 0 12px rgba(37, 99, 235, 0.5);
        }}
        .chain-btn.active.chain-rbh {{
            background: #9333ea;
            color: #ffffff;
            box-shadow: 0 0 12px rgba(147, 51, 234, 0.5);
        }}
        .chain-btn.active.chain-arc {{
            background: #0284c7;
            color: #ffffff;
            box-shadow: 0 0 12px rgba(2, 132, 199, 0.5);
        }}
        .chain-badge {{
            background: rgba(0, 0, 0, 0.35);
            padding: 2px 7px;
            border-radius: 10px;
            font-size: 11px;
        }}
        .chain-context-indicator {{
            font-size: 12px;
            color: var(--text-secondary);
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .param-chain-banner {{
            background: #131b2e;
            border: 1px solid #1e3a8a;
            border-radius: 8px;
            padding: 12px 18px;
            margin-bottom: 18px;
            font-size: 13px;
            color: #93c5fd;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
    </style>
</head>
<body>

    <!-- Top Navigation Bar -->
    <div class="navbar">
        <div class="navbar-brand">
            <span class="logo-icon">⚡</span>
            <div>
                <div class="brand-title">Robinhood + Arc Meme Coin Forensics</div>
                <div class="brand-subtitle">Chain IDs: 4663 (RBH) / 5042 (Arc) • Pre-Launch Sniping vs Post-Launch Holding Intelligence</div>
            </div>
        </div>

        <div class="nav-tabs">
            <button class="nav-tab active" id="tab-btn-leads" onclick="switchTab('leads')">
                <span>📊 Candidate Leads</span>
                <span class="tab-count" id="badge-leads-count">{len(candidates_data)}</span>
            </button>
            <button class="nav-tab" id="tab-btn-params" onclick="switchTab('params')">
                <span>⚙️ Parameter Weights & Buffer</span>
                <span class="tab-count">{len(PARAMETERS)}</span>
            </button>
        </div>
    </div>

    <!-- Prominent Top-Level Chain Hierarchy Switcher -->
    <div class="chain-header-bar">
        <div class="chain-header-left">
            <span class="chain-selector-title">ACTIVE FORENSICS CHAIN:</span>
            <div class="chain-pill-group">
                <button class="chain-btn active chain-all" id="top-chain-all" onclick="selectTopChain('ALL')">
                    <span>🌐 All Chains</span>
                    <span class="chain-badge" id="top-badge-all">{len(candidates_data)}</span>
                </button>
                <button class="chain-btn chain-rbh" id="top-chain-4663" onclick="selectTopChain('4663')">
                    <span>🟣 Robinhood (4663)</span>
                    <span class="chain-badge" id="top-badge-4663">{len([c for c in candidates_data if c['chain_id'] == 4663])}</span>
                </button>
                <button class="chain-btn chain-arc" id="top-chain-5042" onclick="selectTopChain('5042')">
                    <span>🔷 Arc (5042)</span>
                    <span class="chain-badge" id="top-badge-5042">{len([c for c in candidates_data if c['chain_id'] == 5042])}</span>
                </button>
            </div>
        </div>
        <div class="chain-context-indicator" id="top-chain-desc">
            🌐 Viewing combined dual-chain cross-forensic evidence
        </div>
    </div>

    <div class="container">

        <!-- Global Toast Notification -->
        <div class="toast-banner" id="toast-banner">
            <span id="toast-msg">Custom weights applied! Candidate leads rescored live.</span>
            <button class="btn btn-secondary" style="padding: 3px 8px; font-size: 11px;" onclick="closeToast()">✕ Dismiss</button>
        </div>

        <!-- Global Stats Grid (Auto-updated on chain selection and weight changes) -->
        <div class="stats-grid">
            <div class="stat-card" style="--stat-accent: #64748b;">
                <div class="title" id="stat-universe-title">Universe Tokens</div>
                <div class="val" id="stat-universe-val">{len(candidates_data)}</div>
                <div class="subtext" id="stat-universe-sub">Candidate & registry coins</div>
            </div>
            <div class="stat-card" style="--stat-accent: #2ecc71;">
                <div class="title">High Leads (Score ≥65)</div>
                <div class="val" id="stat-high-leads" style="color: #2ecc71;">0</div>
                <div class="subtext">Nearest sibling match ≥65%</div>
            </div>
            <div class="stat-card" style="--stat-accent: #3b82f6;">
                <div class="title">Probable Leads (Score ≥45)</div>
                <div class="val" id="stat-prob-leads" style="color: #60a5fa;">0</div>
                <div class="subtext">Multi-parameter relative match</div>
            </div>
            <div class="stat-card" style="--stat-accent: #f59e0b;">
                <div class="title">Watch Candidates</div>
                <div class="val" id="stat-watch-leads" style="color: #fbbf24;">0</div>
                <div class="subtext">Shared habit & bytecode traces</div>
            </div>
            <div class="stat-card" style="--stat-accent: #ef4444;">
                <div class="title">Flagged Rugs (ATH ≤ $5k)</div>
                <div class="val" id="stat-rug-leads" style="color: #ef4444;">0</div>
                <div class="subtext">Highlighted in 40% red</div>
            </div>
            <div class="stat-card" style="--stat-accent: #10b981;">
                <div class="title" id="stat-runner-title">Top Runner Peak ATH</div>
                <div class="val" id="stat-runner-val" style="color: #34d399;">$0</div>
                <div class="subtext" id="stat-runner-sub">-</div>
            </div>
        </div>

        <!-- ==================== PAGE 1: CANDIDATE LEADS TABLE ==================== -->
        <div class="page-content active" id="page-leads">
            <div class="filter-bar">
                <div class="filter-group">
                    <input type="text" id="leads-search" class="search-input" placeholder="Search Symbol, Name, CA, or Sibling Token..." oninput="filterLeadsTable()">
                    <select id="leads-chain-filter" class="select-input" onchange="filterLeadsTable()">
                        <option value="ALL">All Chains</option>
                        <option value="4663">Robinhood (4663)</option>
                        <option value="5042">Arc (5042)</option>
                    </select>
                    <select id="leads-tier-filter" class="select-input" onchange="filterLeadsTable()">
                        <option value="ALL">All Confidence Tiers</option>
                        <option value="HIGH_LEAD">High Leads Only</option>
                        <option value="PROBABLE_LEAD">Probable Leads Only</option>
                        <option value="WATCH">Watch Leads Only</option>
                        <option value="WEAK">Weak Leads Only</option>
                    </select>
                    <select id="leads-team-filter" class="select-input" onchange="filterLeadsTable()">
                        <option value="ALL">All Teams</option>
                        <option value="team astro">team astro</option>
                        <option value="team gigalon">team gigalon</option>
                        <option value="team intel">team intel</option>
                        <option value="team nchip">team nchip</option>
                        <option value="team robinary">team robinary</option>
                        <option value="unclustered">unclustered</option>
                    </select>
                    <select id="leads-rug-filter" class="select-input" onchange="filterLeadsTable()">
                        <option value="ALL">Show All (Including Rugs)</option>
                        <option value="HIDE_RUGS">Hide Rug Tokens</option>
                        <option value="RUGS_ONLY">Rugs Only (Red Rows)</option>
                    </select>
                </div>
                <div class="filter-group">
                    <span id="data-source-status" style="font-size: 12px; color: var(--text-muted);">Embedded safe snapshot</span>
                    <span id="filtered-count-display" style="font-size: 12px; color: var(--text-secondary);">Showing {len(candidates_data)} of {len(candidates_data)} candidate leads</span>
                    <button class="btn btn-secondary" onclick="resetLeadsFilters()">Reset Filters</button>
                </div>
            </div>

            <div class="table-container">
                <table id="leads-table">
                    <thead>
                        <tr>
                            <th class="sortable" onclick="sortLeads('confidence')" style="cursor: pointer;">Confidence ↕</th>
                            <th class="sortable" onclick="sortLeads('score')" style="cursor: pointer;">Score ↕</th>
                            <th class="sortable" onclick="sortLeads('token')" style="cursor: pointer;">Token / Contract ↕</th>
                            <th class="sortable" onclick="sortLeads('team')" style="cursor: pointer;">Inferred Team ↕</th>
                            <th class="sortable" onclick="sortLeads('best_match_symbol')" style="cursor: pointer;">Nearest Sibling Token ↕</th>
                            <th class="sortable" onclick="sortLeads('ath')" style="cursor: pointer;">Peak ATH ↕</th>
                            <th class="sortable" onclick="sortLeads('date')" style="cursor: pointer;">Launch Date (UTC) ↕</th>
                            <th>Top Matching Evidence (Proximity)</th>
                            <th>Live Charts / Exploration</th>
                        </tr>
                    </thead>
                    <tbody id="leads-tbody">
                        <!-- Populated by JavaScript -->
                    </tbody>
                </table>
            </div>
        </div>

        <!-- ==================== PAGE 2: PARAMETER WEIGHTS & SCORING ==================== -->
        <div class="page-content" id="page-params">

            <!-- Active Chain Context for Tuning -->
            <div class="param-chain-banner" id="param-chain-banner">
                <span>⚡ Active Tuning Context: <b>🌐 All Chains</b></span>
                <span style="font-size: 11px; opacity: 0.85;">Switch chain using the top bar to inspect chain-specific habit denominations</span>
            </div>

            <!-- Numerical Habit Tolerance Buffer Setting -->
            <div class="buffer-card">
                <div>
                    <div style="font-weight: 700; font-size: 14px; color: #ffffff;">
                        🎯 Numerical Habit Tolerance Buffer: <span id="buffer-val-disp" style="color: #10b981; font-family: monospace;">±15%</span>
                    </div>
                    <div style="font-size: 12px; color: var(--text-secondary); margin-top: 4px;">
                        Relative tolerance applied to continuous numerical parameters (snipe value, fund amount, gas gwei, priority tip, setup duration, gas units). Values within this relative threshold receive proximity credit rather than dropping to 0 immediately.
                    </div>
                </div>
                <div style="display: flex; align-items: center; gap: 12px;">
                    <input type="range" id="buffer-slider" min="0" max="30" step="1" value="15" class="weight-slider" oninput="updateBufferDisplay(this.value)">
                    <button class="btn btn-secondary" onclick="resetBufferDefault()">Reset (±15%)</button>
                </div>
            </div>

            <div class="filter-bar">
                <div class="filter-group">
                    <button class="btn btn-primary" onclick="applyCustomWeights()">
                        <span>💾 Apply Custom Weights & Rescore</span>
                    </button>
                    <button class="btn btn-secondary" onclick="resetToDefaultWeights()">
                        <span>↺ Reset to Defaults</span>
                    </button>
                    <button class="btn btn-blue" onclick="exportWeightsJson()">
                        <span>📋 Export Weights JSON</span>
                    </button>
                </div>
                <div class="filter-group">
                    <input type="text" id="params-search" class="search-input" placeholder="Search parameters..." oninput="filterParamsTable()">
                    <select id="params-phase-filter" class="select-input" onchange="filterParamsTable()">
                        <option value="ALL">All Phases (50 Parameters)</option>
                        <option value="PRE_LAUNCH">⚡ Pre-Launch Only (Sniping Decisions)</option>
                        <option value="POST_LAUNCH">🛡️ Post-Launch Only (Holding Decisions)</option>
                    </select>
                    <select id="params-cat-filter" class="select-input" onchange="filterParamsTable()">
                        <option value="ALL">All Categories</option>
                        <option value="Wallets & Lineage">Wallets & Lineage</option>
                        <option value="Bytecode & Architecture">Bytecode & Architecture</option>
                        <option value="Branding & Socials">Branding & Socials</option>
                        <option value="Execution & Gas">Execution & Gas</option>
                        <option value="Economics & Bundles">Economics & Bundles</option>
                        <option value="Marketing & Timing">Marketing & Timing</option>
                    </select>
                </div>
            </div>

            <div class="table-container">
                <table id="params-table">
                    <thead>
                        <tr>
                            <th style="width: 45%;">1. Parameter / Forensic Habit & Phase</th>
                            <th style="width: 20%; text-align: center;">2. Weighted (Active)</th>
                            <th style="width: 35%;">3. Customize Weight (Overwrite On Apply)</th>
                        </tr>
                    </thead>
                    <tbody id="params-tbody">
                        <!-- Populated by JavaScript -->
                    </tbody>
                </table>
            </div>
        </div>

        <div class="footer">
            Robinhood + Arc (Chain IDs: 4663 / 5042) Forensic Tracking Engine • Data verified from RobinScan Multichain V2, Blockscout, and Telegram Scans
        </div>

    </div>

    <!-- Client-Side Scoring & Table Engine -->
    <script>
        // Initial Dataset injected from Python
        const CANDIDATES = {candidates_json};
        const PARAMETERS = {parameters_json};
        const CANDIDATES_API_URL = {api_url_json};

        // Active weights and numerical buffer
        let activeWeights = {{}};
        let activeBufferPct = 0.15;
        const defaultWeights = {{}};
        PARAMETERS.forEach(p => {{
            defaultWeights[p.id] = p.default;
        }});

        function initWeights() {{
            const saved = localStorage.getItem("rbh_custom_weights");
            const savedBuffer = localStorage.getItem("rbh_custom_buffer");
            if (savedBuffer) {{
                activeBufferPct = parseFloat(savedBuffer) || 0.15;
                const bSlider = document.getElementById('buffer-slider');
                const bDisp = document.getElementById('buffer-val-disp');
                if (bSlider) bSlider.value = Math.round(activeBufferPct * 100);
                if (bDisp) bDisp.textContent = `±${{Math.round(activeBufferPct * 100)}}%`;
            }}
            if (saved) {{
                try {{
                    const parsed = JSON.parse(saved);
                    PARAMETERS.forEach(p => {{
                        activeWeights[p.id] = (parsed[p.id] !== undefined) ? Number(parsed[p.id]) : p.default;
                    }});
                }} catch (e) {{
                    activeWeights = Object.assign({{}}, defaultWeights);
                }}
            }} else {{
                activeWeights = Object.assign({{}}, defaultWeights);
            }}
        }}
        initWeights();

        function escapeHtml(value) {{
            return String(value).replace(/[&<>"']/g, ch => ({{
                '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
            }}[ch]));
        }}

        function normalizeCandidate(raw) {{
            if (!raw || typeof raw !== 'object') return null;
            const ca = String(raw.ca || '');
            if (!/^0x[0-9a-fA-F]{{40}}$/.test(ca)) return null;
            const bestCa = String(raw.best_match_ca || '');
            const parsedChainId = Number(raw.chain_id);
            const chainId = [4663, 5042].includes(parsedChainId) ? parsedChainId : 4663;
            const chainName = chainId === 5042 ? 'ARC' : 'RBH';
            const parsedBestChainId = Number(raw.best_match_chain_id);
            const bestMatchChainId = [4663, 5042].includes(parsedBestChainId) ? parsedBestChainId : chainId;
            const bestMatchChainName = bestMatchChainId === 5042 ? 'ARC' : 'RBH';
            const confidence = ['HIGH_LEAD', 'PROBABLE_LEAD', 'WATCH', 'WEAK'].includes(raw.confidence)
                ? raw.confidence : 'WEAK';
            const number = (value, fallback = 0) => {{
                const parsed = Number(value);
                return Number.isFinite(parsed) ? parsed : fallback;
            }};
            const clean = (value, limit) => escapeHtml(String(value || '').slice(0, limit));
            const evidence = Array.isArray(raw.evidence) ? raw.evidence.slice(0, 50)
                .filter(item => item && typeof item === 'object')
                .map(item => ({{
                    feature: clean(item.feature, 80),
                    value: clean(item.value, 500),
                    type: ['numeric', 'categorical', 'feature'].includes(item.type) ? item.type : 'feature',
                    reliability: Math.max(0, Math.min(1, number(item.reliability))),
                    proximity: Math.max(0, Math.min(1, number(item.proximity, 1)))
                }})) : [];
            const ath = Math.max(0, number(raw.ath));
            return {{
                ca, chain_id: chainId, chain: chainName,
                symbol: clean(raw.symbol, 80), name: clean(raw.name, 200),
                confidence, score: Math.max(0, Math.min(100, number(raw.score))),
                team: clean(raw.team || 'unclustered', 120),
                best_match_symbol: clean(raw.best_match_symbol, 80),
                best_match_ca: /^0x[0-9a-fA-F]{{40}}$/.test(bestCa) ? bestCa : '',
                best_match_chain_id: bestMatchChainId, best_match_chain: bestMatchChainName,
                ath, is_rug: Boolean(raw.is_rug), evidence,
                token_live: clean(raw.token_live, 64),
                website: '', x: ''
            }};
        }}

        // Active candidates state. Embedded data remains a fail-safe if Vultr is unavailable.
        let currentCandidates = CANDIDATES.map(normalizeCandidate).filter(Boolean);
        let sortCol = 'score';
        let sortAsc = false;

        async function loadRemoteCandidates() {{
            if (!CANDIDATES_API_URL) return;
            const status = document.getElementById('data-source-status');
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 8000);
            try {{
                if (status) status.textContent = 'Loading Vultr API...';
                const response = await fetch(CANDIDATES_API_URL, {{
                    method: 'GET',
                    headers: {{'Accept': 'application/json'}},
                    cache: 'no-store',
                    mode: 'cors',
                    signal: controller.signal
                }});
                if (!response.ok) throw new Error('API returned ' + response.status);
                const payload = await response.json();
                if (!payload || !Array.isArray(payload.candidates) || payload.candidates.length > 20000) {{
                    throw new Error('invalid candidate payload');
                }}
                const validated = payload.candidates.map(normalizeCandidate).filter(Boolean);
                if (!validated.length && payload.candidates.length) throw new Error('no valid candidates');
                currentCandidates = validated;
                const badge = document.getElementById('badge-leads-count');
                if (badge) badge.textContent = currentCandidates.length;
                if (status) status.textContent = 'Live API • ' + (payload.generated_at || 'current snapshot');
                rescoreAllCandidates();
            }} catch (error) {{
                if (status) status.textContent = 'API unavailable • embedded snapshot active';
                console.warn('Candidate API unavailable; using embedded snapshot.', error);
            }} finally {{
                clearTimeout(timeout);
            }}
        }}

        // Recalculate token score against its nearest duplicate using custom weights & buffer
        function calculateTokenScore(candidate, weights, bufferPct) {{
            let missProbability = 1.0;
            let strongEvidenceCount = 0;

            if (!candidate.evidence || candidate.evidence.length === 0) {{
                return {{ score: candidate.score, confidence: candidate.confidence }};
            }}

            candidate.evidence.forEach(ev => {{
                const feature = ev.feature;
                const weight = (weights[feature] !== undefined) ? weights[feature] : (Number(ev.reliability) || 0.22);
                let prox = (ev.proximity !== undefined) ? Number(ev.proximity) : 1.0;
                
                let contrib = 0.0;
                if (ev.type === 'numeric') {{
                    contrib = weight * (0.7 + 0.3 * prox);
                }} else {{
                    contrib = weight;
                }}
                
                if (contrib > 0) {{
                    missProbability *= (1.0 - Math.min(0.95, contrib));
                }}
                if (weight >= 0.55) {{
                    strongEvidenceCount++;
                }}
            }});

            const newScore = Math.round((1.0 - missProbability) * 10000) / 100;

            // Confidence tier
            let confidence = "WEAK";
            if (newScore >= 65 && strongEvidenceCount > 0) {{
                confidence = "HIGH_LEAD";
            }} else if (newScore >= 45 && (strongEvidenceCount > 0 || candidate.evidence.length >= 2)) {{
                confidence = "PROBABLE_LEAD";
            }} else if (newScore >= 30) {{
                confidence = "WATCH";
            }}

            return {{ score: newScore, confidence: confidence }};
        }}

        // Rescore all candidates with active weights
        let activeChainFilter = 'ALL';

        function selectTopChain(chainId) {{
            activeChainFilter = chainId;

            // Update top button active states
            const keys = ['all', '4663', '5042'];
            keys.forEach(k => {{
                const btn = document.getElementById('top-chain-' + k);
                if (!btn) return;
                const matches = (k === 'all' && chainId === 'ALL') || (k === chainId);
                if (matches) {{
                    btn.className = 'chain-btn active ' + (k === 'all' ? 'chain-all' : k === '4663' ? 'chain-rbh' : 'chain-arc');
                }} else {{
                    btn.className = 'chain-btn';
                }}
            }});

            // Update context descriptor
            const desc = document.getElementById('top-chain-desc');
            if (desc) {{
                if (chainId === 'ALL') {{
                    desc.innerHTML = '🌐 Viewing combined dual-chain cross-forensic evidence';
                }} else if (chainId === '4663') {{
                    desc.innerHTML = '🟣 Viewing Robinhood Chain (4663) • Native Gas/Value: ETH';
                }} else if (chainId === '5042') {{
                    desc.innerHTML = '🔷 Viewing Arc Chain (5042) • Native Gas/Value: USDC';
                }}
            }}

            // Sync with table dropdown filter
            const chainSelect = document.getElementById('leads-chain-filter');
            if (chainSelect && chainSelect.value !== chainId) {{
                chainSelect.value = chainId;
            }}

            // Update Page 2 parameter banner
            const paramBanner = document.getElementById('param-chain-banner');
            if (paramBanner) {{
                if (chainId === 'ALL') {{
                    paramBanner.innerHTML = '<span>⚡ Active Tuning Context: <b>🌐 All Chains</b></span><span style=\"font-size: 11px; opacity: 0.85;\">Tuning buffer & weights across both RBH & ARC</span>';
                }} else if (chainId === '4663') {{
                    paramBanner.innerHTML = '<span>⚡ Active Tuning Context: <b>🟣 Robinhood (4663)</b></span><span style=\"font-size: 11px; opacity: 0.85;\">Denominated in ETH • Pons/Uniswap v4 metrics</span>';
                }} else if (chainId === '5042') {{
                    paramBanner.innerHTML = '<span>⚡ Active Tuning Context: <b>🔷 Arc (5042)</b></span><span style=\"font-size: 11px; opacity: 0.85;\">Denominated in USDC • Arc DEX metrics</span>';
                }}
            }}

            rescoreAllCandidates();
        }}

        // Rescore all candidates with active weights & calculate chain-filtered stats
        function rescoreAllCandidates() {{
            let highCount = 0;
            let probCount = 0;
            let watchCount = 0;
            let rugCount = 0;
            let universeCount = 0;
            let maxAth = 0.0;
            let topRunnerStr = '-';

            // Update top pill badges
            const totalAll = currentCandidates.length;
            const totalRbh = currentCandidates.filter(c => c.chain_id === 4663).length;
            const totalArc = currentCandidates.filter(c => c.chain_id === 5042).length;

            if (document.getElementById('top-badge-all')) document.getElementById('top-badge-all').textContent = totalAll;
            if (document.getElementById('top-badge-4663')) document.getElementById('top-badge-4663').textContent = totalRbh;
            if (document.getElementById('top-badge-5042')) document.getElementById('top-badge-5042').textContent = totalArc;

            currentCandidates.forEach(cand => {{
                const res = calculateTokenScore(cand, activeWeights, activeBufferPct);
                cand.score = res.score;
                cand.confidence = res.confidence;

                // Chain-aware stats calculation
                const matchesChain = (activeChainFilter === 'ALL' || String(cand.chain_id) === activeChainFilter);
                if (matchesChain) {{
                    universeCount++;
                    if (cand.is_rug) rugCount++;
                    if (cand.confidence === 'HIGH_LEAD') highCount++;
                    else if (cand.confidence === 'PROBABLE_LEAD') probCount++;
                    else if (cand.confidence === 'WATCH') watchCount++;

                    if (cand.ath > maxAth) {{
                        maxAth = cand.ath;
                        topRunnerStr = `$${{cand.symbol || 'TOKEN'}} • ${{cand.team || 'unclustered'}}`;
                    }}
                }}
            }});

            // Update stats grid
            if (document.getElementById('stat-universe-val')) document.getElementById('stat-universe-val').textContent = universeCount;
            if (document.getElementById('stat-universe-title')) {{
                document.getElementById('stat-universe-title').textContent = (activeChainFilter === 'ALL') ? 'Universe Tokens' : (activeChainFilter === '4663' ? 'RBH Tokens' : 'Arc Tokens');
            }}
            if (document.getElementById('stat-high-leads')) document.getElementById('stat-high-leads').textContent = highCount;
            if (document.getElementById('stat-prob-leads')) document.getElementById('stat-prob-leads').textContent = probCount;
            if (document.getElementById('stat-watch-leads')) document.getElementById('stat-watch-leads').textContent = watchCount;
            if (document.getElementById('stat-rug-leads')) document.getElementById('stat-rug-leads').textContent = rugCount;

            if (document.getElementById('stat-runner-val')) {{
                document.getElementById('stat-runner-val').textContent = maxAth > 0 ? ('$' + Math.round(maxAth).toLocaleString()) : '$0';
            }}
            if (document.getElementById('stat-runner-sub')) {{
                document.getElementById('stat-runner-sub').textContent = topRunnerStr;
            }}

            renderLeadsTable();
        }}

        // Render Page 1: Leads Table
        function renderLeadsTable() {{
            const tbody = document.getElementById('leads-tbody');
            if (!tbody) return;
            tbody.innerHTML = '';

            const searchTerm = (document.getElementById('leads-search') ? document.getElementById('leads-search').value.toLowerCase().trim() : '');
            const chainFilter = activeChainFilter;
            const tierFilter = (document.getElementById('leads-tier-filter') ? document.getElementById('leads-tier-filter').value : 'ALL');
            const teamFilter = (document.getElementById('leads-team-filter') ? document.getElementById('leads-team-filter').value.toLowerCase() : 'all');
            const rugFilter = (document.getElementById('leads-rug-filter') ? document.getElementById('leads-rug-filter').value : 'ALL');

            // Filter
            let filtered = currentCandidates.filter(c => {{
                if (chainFilter !== 'ALL' && String(c.chain_id) !== chainFilter) return false;
                if (tierFilter !== 'ALL' && c.confidence !== tierFilter) return false;
                if (teamFilter !== 'all' && c.team.toLowerCase() !== teamFilter) return false;
                if (rugFilter === 'HIDE_RUGS' && c.is_rug) return false;
                if (rugFilter === 'RUGS_ONLY' && !c.is_rug) return false;
                if (searchTerm) {{
                    const symMatch = c.symbol.toLowerCase().includes(searchTerm);
                    const nameMatch = c.name.toLowerCase().includes(searchTerm);
                    const caMatch = c.ca.toLowerCase().includes(searchTerm);
                    const sibMatch = (c.best_match_symbol || '').toLowerCase().includes(searchTerm);
                    const chainMatch = c.chain.toLowerCase().includes(searchTerm) || String(c.chain_id).includes(searchTerm);
                    if (!symMatch && !nameMatch && !caMatch && !sibMatch && !chainMatch) return false;
                }}
                return true;
            }});

            // Sort
            filtered.sort((a, b) => {{
                let valA = a[sortCol];
                let valB = b[sortCol];
                if (sortCol === 'confidence') {{
                    const order = {{ 'HIGH_LEAD': 4, 'PROBABLE_LEAD': 3, 'WATCH': 2, 'WEAK': 1 }};
                    valA = order[valA] || 0;
                    valB = order[valB] || 0;
                }} else if (sortCol === 'date') {{
                    valA = a.token_live ? new Date(a.token_live.replace(' ', 'T') + ':00Z').getTime() : 0;
                    valB = b.token_live ? new Date(b.token_live.replace(' ', 'T') + ':00Z').getTime() : 0;
                }} else if (sortCol === 'token') {{
                    valA = a.symbol.toLowerCase();
                    valB = b.symbol.toLowerCase();
                }} else if (sortCol === 'team') {{
                    valA = a.team.toLowerCase();
                    valB = b.team.toLowerCase();
                }}
                if (valA < valB) return sortAsc ? -1 : 1;
                if (valA > valB) return sortAsc ? 1 : -1;
                return 0;
            }});

            if (document.getElementById('filtered-count-display')) {{
                document.getElementById('filtered-count-display').textContent = `Showing ${{filtered.length}} of ${{currentCandidates.length}} candidate leads`;
            }}

            filtered.forEach(c => {{
                const tr = document.createElement('tr');
                if (c.is_rug) {{
                    tr.classList.add('rug-row');
                }}
                
                // Format evidence chips with proximity display
                let evHtml = '';
                if (c.evidence && c.evidence.length > 0) {{
                    const topEv = c.evidence.slice(0, 3);
                    evHtml = '<div class="evidence-tags">' + topEv.map(e => {{
                        const curW = activeWeights[e.feature] !== undefined ? activeWeights[e.feature] : e.reliability;
                        const proxText = (e.type === 'numeric' && e.proximity !== undefined) ? ` (${{Math.round(e.proximity * 100)}}% prox)` : '';
                        return `<span class="ev-tag" title="Type: ${{e.type || 'feature'}} • Proximity: ${{e.proximity !== undefined ? e.proximity : 1.0}} • Weight: ${{curW}}"><b>${{e.feature}}:</b> ${{e.value}}${{proxText}}</span>`;
                    }}).join('') + '</div>';
                }} else {{
                    evHtml = '<span style="color: var(--text-muted); font-size: 11px;">No discrete evidence</span>';
                }}

                const athFormatted = c.ath > 0 ? `$${{c.ath.toLocaleString('en-US', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }})}}` : '$0.00';
                const athClass = c.ath > 0 ? 'ath-val' : 'text-muted';

                const isArc = c.chain_id === 5042;
                const gmgnUrl = isArc ? '' : `https://gmgn.ai/robinhood/token/${{c.ca}}`;
                const dexUrl = `https://dexscreener.com/${{isArc ? 'arc' : 'robinhood'}}/${{c.ca}}`;
                const scanUrl = `${{isArc ? 'https://explorer.arc.io/address/' : 'https://robinhoodchain.blockscout.com/address/'}}${{c.ca}}`;
                const gmgnButton = gmgnUrl ? `<a href="${{gmgnUrl}}" target="_blank" rel="noopener noreferrer" class="btn btn-gmgn">GMGN</a>` : '';

                const sibSymbol = c.best_match_symbol ? `$${{c.best_match_symbol}}` : 'N/A';
                const sibCaShort = c.best_match_ca ? `${{c.best_match_ca.substring(0, 6)}}...${{c.best_match_ca.substring(c.best_match_ca.length - 4)}}` : '';
                const dateDisplay = c.token_live ? c.token_live : 'N/A';

                tr.innerHTML = `
                    <td>
                        <span class="badge ${{c.confidence.toLowerCase()}}">${{c.confidence.replace('_', ' ')}}</span>
                        ${{c.is_rug ? '<span class="badge rug-badge">🚨 RUG</span>' : ''}}
                    </td>
                    <td><span class="score-val" style="color: ${{c.score >= 65 ? '#2ecc71' : c.score >= 45 ? '#60a5fa' : '#fbbf24'}}">${{c.score.toFixed(1)}}%</span></td>
                    <td class="token-cell">
                        <div>
                            <span class="token-symbol">$${{c.symbol}}</span>
                            <span class="chain-pill">${{c.chain}} ${{c.chain_id}}</span>
                            <span class="token-name">${{c.name !== c.symbol ? c.name : ''}}</span>
                        </div>
                        <div class="token-ca">
                            <code>${{c.ca.substring(0, 8)}}...${{c.ca.substring(c.ca.length - 6)}}</code>
                            <button class="copy-btn" onclick="copyToClipboard('${{c.ca}}', this)" title="Copy full Contract Address">📋</button>
                        </div>
                    </td>
                    <td><span class="team-tag">${{c.team}}</span></td>
                    <td>
                        <span class="sibling-badge">${{sibSymbol}}</span>
                        <span class="chain-pill">${{c.best_match_chain}}</span>
                        ${{sibCaShort ? `<div style="font-size: 10px; color: var(--text-muted); font-family: monospace; margin-top: 2px;"><code>${{sibCaShort}}</code></div>` : ''}}
                    </td>
                    <td><span class="${{athClass}}">${{athFormatted}}</span></td>
                    <td class="date-cell">${{dateDisplay}}</td>
                    <td>${{evHtml}}</td>
                    <td class="actions-cell">
                        ${{gmgnButton}}
                        <a href="${{dexUrl}}" target="_blank" rel="noopener noreferrer" class="btn btn-dex">DEX</a>
                        <a href="${{scanUrl}}" target="_blank" rel="noopener noreferrer" class="btn btn-scan">SCAN</a>
                    </td>
                `;
                tbody.appendChild(tr);
            }});
        }}

        // Render Page 2: Parameter Weights Table
        function renderParamsTable() {{
            const tbody = document.getElementById('params-tbody');
            if (!tbody) return;
            tbody.innerHTML = '';

            const searchTerm = (document.getElementById('params-search') ? document.getElementById('params-search').value.toLowerCase().trim() : '');
            const phaseFilter = (document.getElementById('params-phase-filter') ? document.getElementById('params-phase-filter').value : 'ALL');
            const catFilter = (document.getElementById('params-cat-filter') ? document.getElementById('params-cat-filter').value : 'ALL');

            PARAMETERS.forEach(p => {{
                if (phaseFilter !== 'ALL' && p.phase !== phaseFilter) return;
                if (catFilter !== 'ALL' && p.category !== catFilter) return;
                if (searchTerm) {{
                    const matchName = p.name.toLowerCase().includes(searchTerm);
                    const matchId = p.id.toLowerCase().includes(searchTerm);
                    const matchDesc = p.description.toLowerCase().includes(searchTerm);
                    if (!matchName && !matchId && !matchDesc) return;
                }}

                const curWeight = activeWeights[p.id] !== undefined ? activeWeights[p.id] : p.default;
                const isPre = p.phase === 'PRE_LAUNCH';
                const phaseBadge = isPre 
                    ? '<span class="phase-pill pre">⚡ PRE-LAUNCH (SNIPE)</span>' 
                    : '<span class="phase-pill post">🛡️ POST-LAUNCH (HOLD)</span>';

                const tr = document.createElement('tr');
                tr.id = `param-row-${{p.id}}`;

                tr.innerHTML = `
                    <td>
                        <div style="margin-bottom: 4px;">
                            ${{phaseBadge}}
                            <span class="category-chip">${{p.category}}</span>
                        </div>
                        <div style="font-weight: 700; font-size: 14px; color: #ffffff; margin-bottom: 2px;">
                            ${{p.name}}
                        </div>
                        <div class="param-desc">${{p.description}}</div>
                    </td>
                    <td style="text-align: center;">
                        <span class="weight-display" id="weight-disp-${{p.id}}">${{curWeight.toFixed(2)}}</span>
                    </td>
                    <td>
                        <div class="weight-input-box">
                            <input type="number" step="0.01" min="0.00" max="1.00" 
                                class="weight-num-input" id="num-input-${{p.id}}" 
                                value="${{curWeight.toFixed(2)}}"
                                oninput="syncWeightSlider('${{p.id}}')">
                            <input type="range" step="0.01" min="0.00" max="1.00" 
                                class="weight-slider" id="slider-${{p.id}}" 
                                value="${{curWeight}}"
                                oninput="syncWeightNumber('${{p.id}}')">
                            <button class="btn btn-secondary" style="padding: 4px 8px; font-size: 11px;" 
                                onclick="setParamDefault('${{p.id}}', ${{p.default}})" title="Restore baseline default (${{p.default}})">
                                Baseline (${{p.default}})
                            </button>
                        </div>
                    </td>
                `;
                tbody.appendChild(tr);
            }});
        }}

        function syncWeightSlider(paramId) {{
            const numInput = document.getElementById(`num-input-${{paramId}}`);
            const slider = document.getElementById(`slider-${{paramId}}`);
            if (numInput && slider) {{
                let val = parseFloat(numInput.value);
                if (isNaN(val)) val = 0.0;
                if (val < 0) val = 0.0;
                if (val > 1.0) val = 1.0;
                slider.value = val;
            }}
        }}

        function syncWeightNumber(paramId) {{
            const numInput = document.getElementById(`num-input-${{paramId}}`);
            const slider = document.getElementById(`slider-${{paramId}}`);
            if (numInput && slider) {{
                numInput.value = parseFloat(slider.value).toFixed(2);
            }}
        }}

        function setParamDefault(paramId, defVal) {{
            const numInput = document.getElementById(`num-input-${{paramId}}`);
            const slider = document.getElementById(`slider-${{paramId}}`);
            if (numInput && slider) {{
                numInput.value = defVal.toFixed(2);
                slider.value = defVal;
            }}
        }}

        function updateBufferDisplay(val) {{
            activeBufferPct = parseFloat(val) / 100.0;
            const disp = document.getElementById('buffer-val-disp');
            if (disp) disp.textContent = `±${{val}}%`;
        }}

        function resetBufferDefault() {{
            const slider = document.getElementById('buffer-slider');
            if (slider) slider.value = 15;
            updateBufferDisplay(15);
        }}

        // Action: Apply Custom Weights & Tolerance Buffer
        function applyCustomWeights() {{
            let updatedCount = 0;
            PARAMETERS.forEach(p => {{
                const numInput = document.getElementById(`num-input-${{p.id}}`);
                if (numInput) {{
                    let val = parseFloat(numInput.value);
                    if (isNaN(val)) val = p.default;
                    val = Math.max(0.0, Math.min(1.0, val));
                    activeWeights[p.id] = val;

                    // Overwrite Column 2 ("Weighted") display
                    const disp = document.getElementById(`weight-disp-${{p.id}}`);
                    if (disp) {{
                        disp.textContent = val.toFixed(2);
                        disp.classList.add('highlight-update');
                        setTimeout(() => disp.classList.remove('highlight-update'), 1000);
                    }}
                    updatedCount++;
                }}
            }});

            const bSlider = document.getElementById('buffer-slider');
            if (bSlider) {{
                activeBufferPct = parseFloat(bSlider.value) / 100.0;
                localStorage.setItem("rbh_custom_buffer", activeBufferPct);
            }}

            // Save to localStorage
            localStorage.setItem("rbh_custom_weights", JSON.stringify(activeWeights));

            // Rescore and update leads table
            rescoreAllCandidates();

            // Show confirmation toast
            showToast(`✓ Successfully applied custom weights across ${{updatedCount}} parameters with ±${{Math.round(activeBufferPct*100)}}% buffer! Rescored all ${{currentCandidates.length}} candidate leads.`);
        }}

        // Action: Reset to Defaults
        function resetToDefaultWeights() {{
            PARAMETERS.forEach(p => {{
                activeWeights[p.id] = p.default;
                const numInput = document.getElementById(`num-input-${{p.id}}`);
                const slider = document.getElementById(`slider-${{p.id}}`);
                const disp = document.getElementById(`weight-disp-${{p.id}}`);
                if (numInput) numInput.value = p.default.toFixed(2);
                if (slider) slider.value = p.default;
                if (disp) {{
                    disp.textContent = p.default.toFixed(2);
                    disp.classList.add('highlight-update');
                    setTimeout(() => disp.classList.remove('highlight-update'), 1000);
                }}
            }});

            resetBufferDefault();
            localStorage.removeItem("rbh_custom_weights");
            localStorage.removeItem("rbh_custom_buffer");
            rescoreAllCandidates();
            showToast(`✓ Reset all 50 parameters & tolerance buffer back to Phase 1 defaults.`);
        }}

        // Action: Export Weights as JSON
        function exportWeightsJson() {{
            const exportPayload = {{
                buffer_pct: activeBufferPct,
                weights: activeWeights
            }};
            const jsonStr = JSON.stringify(exportPayload, null, 2);
            navigator.clipboard.writeText(jsonStr).then(() => {{
                showToast(`📋 Copied custom weights & buffer JSON configuration to clipboard!`);
            }}).catch(() => {{
                showToast(`Weights JSON: ` + jsonStr);
            }});
        }}

        // Tab Switching
        function switchTab(tab) {{
            document.querySelectorAll('.nav-tab').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.page-content').forEach(p => p.classList.remove('active'));

            if (tab === 'leads') {{
                document.getElementById('tab-btn-leads').classList.add('active');
                document.getElementById('page-leads').classList.add('active');
                renderLeadsTable();
            }} else if (tab === 'params') {{
                document.getElementById('tab-btn-params').classList.add('active');
                document.getElementById('page-params').classList.add('active');
                renderParamsTable();
            }}
        }}

        // Search & Filter callbacks
        function filterLeadsTable() {{
            const dropdown = document.getElementById('leads-chain-filter');
            if (dropdown && dropdown.value !== activeChainFilter) {{
                selectTopChain(dropdown.value);
                return;
            }}
            renderLeadsTable();
        }}
        function resetLeadsFilters() {{
            if (document.getElementById('leads-search')) document.getElementById('leads-search').value = '';
            if (document.getElementById('leads-tier-filter')) document.getElementById('leads-tier-filter').value = 'ALL';
            if (document.getElementById('leads-team-filter')) document.getElementById('leads-team-filter').value = 'ALL';
            if (document.getElementById('leads-rug-filter')) document.getElementById('leads-rug-filter').value = 'ALL';
            selectTopChain('ALL');
        }}
        function filterParamsTable() {{
            renderParamsTable();
        }}

        // Sort table
        function sortLeads(col) {{
            if (sortCol === col) {{
                sortAsc = !sortAsc;
            }} else {{
                sortCol = col;
                sortAsc = false;
            }}
            renderLeadsTable();
        }}

        // Utility: Toast Banner
        function showToast(msg) {{
            const banner = document.getElementById('toast-banner');
            const msgEl = document.getElementById('toast-msg');
            msgEl.textContent = msg;
            banner.style.display = 'flex';
            setTimeout(() => {{
                banner.style.display = 'none';
            }}, 5000);
        }}
        function closeToast() {{
            document.getElementById('toast-banner').style.display = 'none';
        }}

        // Utility: Copy to Clipboard
        function copyToClipboard(text, btn) {{
            navigator.clipboard.writeText(text).then(() => {{
                const orig = btn.textContent;
                btn.textContent = '✓';
                setTimeout(() => {{ btn.textContent = orig; }}, 1500);
            }});
        }}

        // Initial setup
        window.addEventListener('DOMContentLoaded', () => {{
            rescoreAllCandidates();
            renderParamsTable();
            loadRemoteCandidates();
        }});
    </script>
</body>
</html>
"""

# Atomic publication: readers see either the old or the complete new artifact.
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
snapshot_temp = SNAPSHOT_PATH.with_name(SNAPSHOT_PATH.name + f".{os.getpid()}.tmp")
snapshot_temp.write_text(json.dumps({
    "schema_version": 1,
    "generated_at": generated_at,
    "count": len(candidates_data),
    "candidates": candidates_data,
}, indent=2, sort_keys=True), encoding="utf-8")
os.replace(snapshot_temp, SNAPSHOT_PATH)

for filename in ("team_leads_dashboard.html", "index.html"):
    target = OUTPUT_DIR / filename
    temporary = target.with_name(target.name + f".{os.getpid()}.tmp")
    temporary.write_text(html_content, encoding="utf-8")
    os.replace(temporary, target)

print(f"Generated dashboard atomically in {OUTPUT_DIR}")
print(f"Generated API snapshot atomically at {SNAPSHOT_PATH}")
