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
DASHBOARD_API_URL = os.getenv("DASHBOARD_API_URL", "/api/candidates").strip()
if DASHBOARD_API_URL and not (
    DASHBOARD_API_URL.startswith("/")
    or DASHBOARD_API_URL.startswith("http://")
    or DASHBOARD_API_URL.startswith("https://")
):
    raise ValueError("DASHBOARD_API_URL must be an endpoint starting with /, http://, or https://")
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
    df_tg[['chain_id', 'ca_lower', 'ath', 'name', 'token_live', 'time_social_paid', '1st_boost', 'x', 'website', 'gwei', 'max_gwei', 'priority_gwei', 'ads']],
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
                      website AS live_website, x_handle AS live_x,
                      ath_source AS live_ath_source,
                      current_market_cap_usd AS live_market_cap,
                      observed_peak_market_cap_usd AS live_observed_peak_market_cap,
                      fdv_usd AS live_fdv,
                      current_liquidity_usd AS live_liquidity,
                      market_pair_url AS live_market_pair_url,
                      market_data_at AS live_market_data_at,
                      COALESCE(is_qualified, 0) AS live_is_qualified,
                      COALESCE(is_graduated, 0) AS live_is_graduated,
                      COALESCE(is_training_anchor, 0) AS live_is_training_anchor,
                      COALESCE(is_dex_paid, 0) AS live_is_dex_paid
               FROM tokens''',
            connection,
        )
    df_live['live_ath'] = pd.to_numeric(df_live['live_ath'], errors='coerce').fillna(0.0)
    df_live = (
        df_live.sort_values('live_ath')
        .drop_duplicates(subset=['chain_id', 'ca_lower'], keep='last')
    )
    merged = pd.merge(merged, df_live, on=['chain_id', 'ca_lower'], how='left')

    # CSV and Telegram exports are historical context, never an eligibility source.
    # A dashboard candidate must be a trusted anchor or have a currently recorded,
    # chain-specific graduation proof in SQLite. This prevents stale exports from
    # reintroducing records a revalidation pass has demoted.
    live_graduated = pd.to_numeric(merged['live_is_graduated'], errors='coerce').fillna(0).astype(int)
    live_anchor = pd.to_numeric(merged['live_is_training_anchor'], errors='coerce').fillna(0).astype(int)
    merged = merged.loc[(live_graduated == 1) | (live_anchor == 1)].copy()

    # Ensure all tokens in SQLite (including newly ingested Arc tokens) are included.
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
                      tm.match_reasons AS evidence,
                      COALESCE(t.is_qualified, 0) AS live_is_qualified,
                      COALESCE(t.is_graduated, 0) AS live_is_graduated,
                      COALESCE(t.is_training_anchor, 0) AS live_is_training_anchor,
                      COALESCE(t.is_dex_paid, 0) AS live_is_dex_paid,
                      t.ath_source AS live_ath_source,
                      t.current_market_cap_usd AS live_market_cap,
                      t.observed_peak_market_cap_usd AS live_observed_peak_market_cap,
                      t.fdv_usd AS live_fdv,
                      t.current_liquidity_usd AS live_liquidity,
                      t.market_pair_url AS live_market_pair_url,
                      t.market_data_at AS live_market_data_at
               FROM tokens t
               LEFT JOIN token_matches tm ON LOWER(t.ca) = LOWER(tm.ca)
               WHERE COALESCE(t.is_graduated, 0)=1 OR COALESCE(t.is_training_anchor, 0)=1''',
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


def parse_dt(s):
    if not isinstance(s, str) or not s.strip():
        return None
    cleaned = s.replace('PHT', '').strip()
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d, %H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            pass
    return None


def calc_lifespan(t_live_str, t_social_str, t_boost_str, ath, chain_id, current_liquidity, market_data_at):
    if chain_id == 5042:
        return True, False, None, "🟢 Still Alive"
    if current_liquidity is not None and current_liquidity >= 400:
        return True, False, None, "🟢 Still Alive"
    if first_present(market_data_at) and (current_liquidity is None or current_liquidity >= 300):
        return True, False, None, "🟢 Still Alive"

    t_live = parse_dt(t_live_str)
    t_social = parse_dt(t_social_str)
    t_boost = parse_dt(t_boost_str)

    last_event = max([t for t in (t_social, t_boost) if t is not None], default=None)
    if t_live and last_event and last_event >= t_live:
        sec = int((last_event - t_live).total_seconds())
    elif t_live and ath <= 5000:
        sec = 180  # ~3 min quick dump
    elif ath >= 1000000:
        sec = 7200  # ~2 hrs for million-dollar runners
    elif ath >= 100000:
        sec = 2100  # ~35 mins for mid runners
    elif ath > 5000:
        sec = 900   # ~15 mins
    else:
        sec = 240   # ~4 mins for failed launches

    mins = round(sec / 60)
    if mins < 1:
        s = "< 1 min"
    elif mins < 60:
        s = f"{mins} mins"
    elif mins < 1440:
        s = f"{sec / 3600:.1f} hrs"
    else:
        s = f"{sec / 86400:.1f} days"

    return False, True, sec, s


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
    ath_source = str(first_present(r.get('live_ath_source'), 'tgscan_archive' if historical_ath > 0 else None) or '')
    ath_known = bool(ath_source and ath > 0)
    market_cap = float(r.get('live_market_cap')) if pd.notna(r.get('live_market_cap')) else None
    observed_peak_market_cap = float(r.get('live_observed_peak_market_cap')) if pd.notna(r.get('live_observed_peak_market_cap')) else None
    fdv = float(r.get('live_fdv')) if pd.notna(r.get('live_fdv')) else None
    current_liquidity = float(r.get('live_liquidity')) if pd.notna(r.get('live_liquidity')) else None
    market_data_at = str(first_present(r.get('live_market_data_at')) or '')
    token_live = str(first_present(r.get('live_token_live'), r.get('token_live')) or '')
    t_social = str(r.get('time_social_paid') or '')
    t_boost = str(r.get('1st_boost') or '')
    is_alive, is_rug, lifespan_sec, lifespan_str = calc_lifespan(
        token_live, t_social, t_boost, ath, chain_id, current_liquidity, market_data_at
    )
    
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
        "is_qualified": bool(int(r.get("live_is_qualified") or 0)),
        "is_training_anchor": bool(int(r.get("live_is_training_anchor") or 0)),
        "is_dex_paid": bool(int(r.get("live_is_dex_paid") or 0)),
        "team": team,
        "best_match_symbol": best_match_symbol,
        "best_match_ca": best_match_ca,
        "best_match_chain_id": best_match_chain_id,
        "best_match_chain": best_match_chain,
        "ath": ath if ath_known else None,
        "ath_known": ath_known,
        "ath_source": ath_source,
        "market_cap": market_cap,
        "observed_peak_market_cap": observed_peak_market_cap,
        "fdv": fdv,
        "current_liquidity": current_liquidity,
        "market_pair_url": str(first_present(r.get("live_market_pair_url")) or ""),
        "market_data_at": market_data_at,
        "is_rug": is_rug,
        "is_alive": is_alive,
        "lifespan_sec": lifespan_sec,
        "lifespan_str": lifespan_str,
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
            width: 100%;
            max-width: 100%;
            margin: 0;
            padding: 20px 28px;
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
            padding: 10px 14px;
            margin-bottom: 16px;
            display: flex;
            flex-wrap: wrap;
            gap: 8px 12px;
            align-items: center;
            justify-content: space-between;
        }}
        .filter-group {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .range-inline-group {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: #090d16;
            border: 1px solid #1e293b;
            border-radius: 8px;
            padding: 5px 12px;
        }}
        .range-inline-label {{
            font-size: 12px;
            font-weight: 700;
            color: #cbd5e1;
            white-space: nowrap;
        }}
        .range-input-box {{
            background: #0b0e14;
            border: 1px solid #334155;
            color: #f8fafc;
            font-family: monospace;
            font-size: 12px;
            font-weight: 600;
            padding: 4px 8px;
            border-radius: 5px;
            width: 74px;
            text-align: center;
            outline: none;
            transition: all 0.2s ease;
        }}
        .range-input-box:focus {{
            border-color: #10b981;
            box-shadow: 0 0 0 1px rgba(16, 185, 129, 0.3);
        }}
        .dual-range-track {{
            position: relative;
            width: 130px;
            height: 20px;
            display: flex;
            align-items: center;
        }}
        .dual-rail-bg {{
            position: absolute;
            width: 100%;
            height: 4px;
            background: #1e293b;
            border-radius: 2px;
            z-index: 1;
        }}
        .dual-rail-fill {{
            position: absolute;
            height: 4px;
            background: #10b981;
            border-radius: 2px;
            z-index: 2;
        }}
        .dual-rail-fill.rug-fill {{
            background: #f59e0b;
        }}
        .dual-range-track input[type="range"] {{
            position: absolute;
            width: 100%;
            height: 20px;
            margin: 0;
            padding: 0;
            background: transparent;
            pointer-events: none;
            -webkit-appearance: none;
            appearance: none;
            z-index: 3;
        }}
        .dual-range-track input[type="range"]::-webkit-slider-thumb {{
            -webkit-appearance: none;
            appearance: none;
            width: 13px;
            height: 13px;
            border-radius: 50%;
            background: #10b981;
            border: 2px solid #090d16;
            cursor: pointer;
            pointer-events: auto;
            box-shadow: 0 0 3px rgba(0,0,0,0.6);
            transition: transform 0.1s ease;
        }}
        .dual-range-track input[type="range"]::-webkit-slider-thumb:hover {{
            transform: scale(1.25);
        }}
        .dual-range-track input[type="range"]::-moz-range-thumb {{
            width: 13px;
            height: 13px;
            border-radius: 50%;
            background: #10b981;
            border: 2px solid #090d16;
            cursor: pointer;
            pointer-events: auto;
            box-shadow: 0 0 3px rgba(0,0,0,0.6);
        }}
        .dual-range-track.rug-track input[type="range"]::-webkit-slider-thumb {{
            background: #f59e0b;
        }}
        .dual-range-track.rug-track input[type="range"]::-moz-range-thumb {{
            background: #f59e0b;
        }}
        .alive-checkbox-label {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            font-size: 11px;
            color: #94a3b8;
            cursor: pointer;
            user-select: none;
            margin-left: 4px;
        }}
        .alive-checkbox-label input {{
            cursor: pointer;
            accent-color: #10b981;
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
        .btn-gmgn-disabled {{
            background: #1e293b;
            color: #64748b;
            padding: 5px 9px;
            font-size: 11px;
            border: 1px solid #334155;
            cursor: not-allowed;
            opacity: 0.65;
            border-radius: 4px;
            display: inline-block;
            text-decoration: none;
        }}
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
        /* Clean row styling - no heavy red overlay */
        tr.rug-row {{
            background-color: transparent;
        }}
        tr.rug-row:hover td {{
            background-color: rgba(255, 255, 255, 0.03) !important;
        }}
        .status-pill {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
            white-space: nowrap;
        }}
        .status-pill.alive {{
            background: rgba(16, 185, 129, 0.15);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.4);
        }}
        .status-pill.rug {{
            background: rgba(148, 163, 184, 0.12);
            color: #cbd5e1;
            border: 1px solid rgba(148, 163, 184, 0.3);
        }}
        .status-pill.quick-rug {{
            background: rgba(239, 68, 68, 0.14);
            color: #fca5a5;
            border: 1px solid rgba(239, 68, 68, 0.35);
        }}
        .mc-val {{
            font-family: monospace;
            font-size: 13px;
            font-weight: 800;
            white-space: nowrap;
        }}
        .mc-high {{
            color: #34d399;
        }}
        .mc-mid {{
            color: #38bdf8;
        }}
        .mc-sub {{
            color: #fbbf24;
        }}
        .mc-low {{
            color: #94a3b8;
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
        .qualification-pill {{
            display: inline-block;
            background: rgba(16, 185, 129, 0.16);
            color: #6ee7b7;
            border: 1px solid rgba(16, 185, 129, 0.45);
            padding: 2px 6px;
            margin-left: 6px;
            border-radius: 999px;
            font-size: 9px;
            font-weight: 900;
            letter-spacing: 0.04em;
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
        .chain-pill.arc {{
            background: rgba(56, 189, 248, 0.16);
            color: #7dd3fc;
            border-color: rgba(56, 189, 248, 0.4);
        }}
        .team-indicator-badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(56, 189, 248, 0.12);
            border: 1px solid rgba(56, 189, 248, 0.3);
            color: #38bdf8;
            padding: 7px 12px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 500;
        }}
        .team-indicator-badge b {{
            color: #ffffff;
            font-weight: 700;
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
            max-width: 550px;
        }}
        @media (min-width: 1600px) {{
            .evidence-tags {{
                max-width: 750px;
            }}
        }}
        @media (min-width: 2200px) {{
            .evidence-tags {{
                max-width: 1100px;
            }}
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
        .btn-fetch-single {{
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 4px;
            color: #cbd5e1;
            cursor: pointer;
            font-size: 11px;
            padding: 1px 5px;
            margin-left: 5px;
            vertical-align: middle;
            transition: all 0.2s;
            line-height: 1.2;
        }}
        .btn-fetch-single:hover {{
            background: rgba(59, 130, 246, 0.35);
            border-color: #3b82f6;
            color: #ffffff;
            transform: scale(1.1);
        }}
        @keyframes spin-anim {{
            from {{ transform: rotate(0deg); }}
            to {{ transform: rotate(360deg); }}
        }}
        .btn-fetch-single.spin {{
            animation: spin-anim 0.8s linear infinite;
            pointer-events: none;
            opacity: 0.6;
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
                <div class="brand-subtitle">Robinhood & Arc Chains • Pre-Launch Sniping vs Post-Launch Holding Intelligence</div>
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
                    <span>🟣 Robinhood</span>
                    <span class="chain-badge" id="top-badge-4663">{len([c for c in candidates_data if c['chain_id'] == 4663])}</span>
                </button>
                <button class="chain-btn chain-arc" id="top-chain-5042" onclick="selectTopChain('5042')">
                    <span>🔷 Arc</span>
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
            <div class="stat-card" style="--stat-accent: #f59e0b;">
                <div class="title">Live Market Coverage</div>
                <div class="val" id="stat-rug-leads" style="color: #fbbf24;">0</div>
                <div class="subtext" id="stat-rug-sub">Tokens refreshed from live pairs</div>
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
                    <input type="text" id="leads-search" class="search-input" placeholder="Search Symbol, Name, CA, or Sibling..." oninput="filterLeadsTable()">
                    <select id="leads-tier-filter" class="select-input" onchange="filterLeadsTable()">
                        <option value="ALL">All Confidence Tiers</option>
                        <option value="HIGH_LEAD">High Leads Only</option>
                        <option value="PROBABLE_LEAD">Probable Leads Only</option>
                        <option value="WATCH">Watch Leads Only</option>
                        <option value="WEAK">Weak Leads Only</option>
                    </select>
                    <select id="leads-team-filter" class="select-input" onchange="filterLeadsTable()">
                        <option value="ALL">All Teams</option>
                    </select>
                    <select id="leads-rug-filter" class="select-input" onchange="filterLeadsTable()">
                        <option value="ALL">All Statuses</option>
                        <option value="ALIVE_ONLY">🟢 Still Alive Only</option>
                        <option value="RUG_ONLY">Confirmed Rugs Only</option>
                    </select>
                    <span class="team-indicator-badge" id="team-indicator-badge" title="Identified dev teams in this view">👥 <b id="team-found-count">0</b> Teams Identified</span>

                    <!-- ATH: Min Box - Slider - Max Box -->
                    <div class="range-inline-group">
                        <span class="range-inline-label">💰 ATH:</span>
                        <input type="text" id="ath-min-input" class="range-input-box" value="$0" placeholder="Min" title="Type min ATH (e.g. 0, 10k, 100k, 1m)" onchange="onAthBoxChange('min', this.value)">
                        <div class="dual-range-track" id="ath-track-wrap">
                            <div class="dual-rail-bg"></div>
                            <div class="dual-rail-fill" id="ath-rail-fill"></div>
                            <input type="range" id="slider-ath-min" min="0" max="20" step="1" value="0" oninput="onAthSliderDual('min')">
                            <input type="range" id="slider-ath-max" min="0" max="20" step="1" value="20" oninput="onAthSliderDual('max')">
                        </div>
                        <input type="text" id="ath-max-input" class="range-input-box" value="Max" placeholder="Max" title="Type max ATH (e.g. 50k, 500k, 1m, max)" onchange="onAthBoxChange('max', this.value)">
                    </div>

                    <!-- Time to Rug: Min Box - Slider - Max Box -->
                    <div class="range-inline-group">
                        <span class="range-inline-label">⏱️ Time to Rug:</span>
                        <input type="text" id="rug-min-input" class="range-input-box" value="0m" placeholder="Min" title="Type min time (e.g. 0m, 5m, 30m, 1h)" onchange="onRugBoxChange('min', this.value)">
                        <div class="dual-range-track rug-track" id="rug-track-wrap">
                            <div class="dual-rail-bg"></div>
                            <div class="dual-rail-fill rug-fill" id="rug-rail-fill"></div>
                            <input type="range" id="slider-rug-min" min="0" max="20" step="1" value="0" oninput="onRugSliderDual('min')">
                            <input type="range" id="slider-rug-max" min="0" max="20" step="1" value="20" oninput="onRugSliderDual('max')">
                        </div>
                        <input type="text" id="rug-max-input" class="range-input-box" value="Max" placeholder="Max" title="Type max time (e.g. 5m, 25m, 2h, max)" onchange="onRugBoxChange('max', this.value)">
                        <label class="alive-checkbox-label" title="Keep alive tokens visible when filtering rug times">
                            <input type="checkbox" id="rug-include-alive" onchange="filterLeadsTable()"> Alive
                        </label>
                    </div>
                </div>

                <div class="filter-group" style="align-items: center; gap: 8px;">
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span id="data-source-status" style="font-size: 12px; color: var(--text-muted);">Embedded safe snapshot</span>
                        <button class="btn btn-secondary" id="btn-manual-sync" onclick="loadRemoteCandidates()" style="padding: 3px 8px; font-size: 12px; display: inline-flex; align-items: center; gap: 4px;" title="Refresh live data now">🔄 Sync</button>
                    </div>
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
                            <th class="sortable" onclick="sortLeads('ath')" style="cursor: pointer;">Current MC / Sourced ATH ↕</th>
                            <th class="sortable" onclick="sortLeads('lifespan')" style="cursor: pointer;">Time to Rug ↕</th>
                            <th class="sortable" onclick="sortLeads('date')" style="cursor: pointer;">Launch Date (UTC) ↕</th>
                            <th>Top Matching Evidence (Proximity)</th>
                            <th>Live Charts / Actions</th>
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
            Robinhood + Arc Forensic Tracking Engine • Data verified from RobinScan Multichain V2, Blockscout, and Telegram Scans
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
            const optionalNumber = value => {{
                if (value === null || value === undefined || value === '') return null;
                const parsed = Number(value);
                return Number.isFinite(parsed) ? Math.max(0, parsed) : null;
            }};
            const ath = optionalNumber(raw.ath);
            const marketCap = optionalNumber(raw.market_cap);
            const observedPeakMarketCap = optionalNumber(raw.observed_peak_market_cap);
            const fdv = optionalNumber(raw.fdv);
            const currentLiquidity = optionalNumber(raw.current_liquidity);
            const isAlive = Boolean(raw.is_alive);
            const lifespanSec = optionalNumber(raw.lifespan_sec);
            const lifespanStr = clean(raw.lifespan_str, 32) || (isAlive ? 'Market observed' : 'Unknown');
            return {{
                ca, chain_id: chainId, chain: chainName,
                symbol: clean(raw.symbol, 80), name: clean(raw.name, 200),
                confidence, score: Math.max(0, Math.min(100, number(raw.score))),
                is_qualified: Boolean(raw.is_qualified),
                is_training_anchor: Boolean(raw.is_training_anchor),
                is_dex_paid: Boolean(raw.is_dex_paid),
                team: clean(raw.team || 'unclustered', 120),
                best_match_symbol: clean(raw.best_match_symbol, 80),
                best_match_ca: /^0x[0-9a-fA-F]{{40}}$/.test(bestCa) ? bestCa : '',
                best_match_chain_id: bestMatchChainId, best_match_chain: bestMatchChainName,
                ath, ath_known: Boolean(raw.ath_known) && ath !== null,
                ath_source: clean(raw.ath_source, 80),
                market_cap: marketCap, observed_peak_market_cap: observedPeakMarketCap,
                fdv, current_liquidity: currentLiquidity,
                market_data_at: clean(raw.market_data_at, 64),
                is_rug: Boolean(raw.is_rug), is_alive: isAlive,
                lifespan_sec: lifespanSec, lifespan_str: lifespanStr,
                evidence,
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
            const syncBtn = document.getElementById('btn-manual-sync');
            if (syncBtn) syncBtn.classList.add('spin');
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 8000);
            try {{
                if (status && status.textContent === 'Embedded safe snapshot') status.textContent = 'Connecting...';
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
                const timeStr = payload.generated_at ? new Date(payload.generated_at).toLocaleTimeString() : new Date().toLocaleTimeString();
                if (status) status.innerHTML = `<span style="color: var(--accent-green);">🟢 Live</span> • ${{timeStr}}`;
                rescoreAllCandidates();
            }} catch (error) {{
                if (status) status.innerHTML = `<span style="color: var(--text-muted);">Offline snapshot</span>`;
                console.warn('Candidate API unavailable; using embedded snapshot.', error);
            }} finally {{
                clearTimeout(timeout);
                if (syncBtn) syncBtn.classList.remove('spin');
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
                    desc.innerHTML = '🟣 Viewing Robinhood Chain • Native Gas/Value: ETH';
                }} else if (chainId === '5042') {{
                    desc.innerHTML = '🔷 Viewing Arc Chain • Native Gas/Value: USDC';
                }}
            }}

            // Update Page 2 parameter banner
            const paramBanner = document.getElementById('param-chain-banner');
            if (paramBanner) {{
                if (chainId === 'ALL') {{
                    paramBanner.innerHTML = '<span>⚡ Active Tuning Context: <b>🌐 All Chains</b></span><span style=\"font-size: 11px; opacity: 0.85;\">Tuning buffer & weights across both RBH & ARC</span>';
                }} else if (chainId === '4663') {{
                    paramBanner.innerHTML = '<span>⚡ Active Tuning Context: <b>🟣 Robinhood</b></span><span style=\"font-size: 11px; opacity: 0.85;\">Denominated in ETH • Pons/Uniswap v4 metrics</span>';
                }} else if (chainId === '5042') {{
                    paramBanner.innerHTML = '<span>⚡ Active Tuning Context: <b>🔷 Arc</b></span><span style=\"font-size: 11px; opacity: 0.85;\">Denominated in USDC • Arc DEX metrics</span>';
                }}
            }}

            rescoreAllCandidates();
        }}

        // Dynamic team filter builder & counter
        function updateTeamFilter() {{
            const teamSelect = document.getElementById('leads-team-filter');
            const badge = document.getElementById('team-found-count');
            if (!teamSelect) return;

            const selectedTeam = teamSelect.value || 'ALL';

            // Tally teams from currentCandidates for the active chain
            const teamCounts = {{}};
            currentCandidates.forEach(c => {{
                if (activeChainFilter !== 'ALL' && String(c.chain_id) !== activeChainFilter) return;
                const t = (c.team || 'unclustered').trim();
                teamCounts[t] = (teamCounts[t] || 0) + 1;
            }});

            // Find all identified teams (excluding unclustered)
            const identifiedTeams = Object.keys(teamCounts).filter(t => t.toLowerCase() !== 'unclustered' && t.length > 0);
            identifiedTeams.sort((a, b) => teamCounts[b] - teamCounts[a]);

            if (badge) {{
                badge.textContent = identifiedTeams.length;
            }}

            // Build select options
            let optionsHtml = `<option value="ALL">All Teams (${{identifiedTeams.length}} Identified)</option>`;
            identifiedTeams.forEach(t => {{
                const display = t.replace(/\\b\\w/g, ch => ch.toUpperCase());
                optionsHtml += `<option value="${{escapeHtml(t.toLowerCase())}}">${{escapeHtml(display)}} (${{teamCounts[t]}} leads)</option>`;
            }});

            if (teamCounts['unclustered']) {{
                optionsHtml += `<option value="unclustered">Unclustered (${{teamCounts['unclustered']}} tokens)</option>`;
            }}

            teamSelect.innerHTML = optionsHtml;

            // Retain user's selection if still present in options
            if (selectedTeam === 'ALL' || teamCounts[selectedTeam] !== undefined) {{
                teamSelect.value = selectedTeam;
            }} else {{
                teamSelect.value = 'ALL';
            }}
        }}

        // Rescore all candidates with active weights & calculate chain-filtered stats
        function rescoreAllCandidates() {{
            let highCount = 0;
            let probCount = 0;
            let watchCount = 0;
            let marketObservedCount = 0;
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
                    if (cand.is_alive) marketObservedCount++;
                    if (cand.confidence === 'HIGH_LEAD') highCount++;
                    else if (cand.confidence === 'PROBABLE_LEAD') probCount++;
                    else if (cand.confidence === 'WATCH') watchCount++;

                    if (cand.ath_known && cand.ath > maxAth) {{
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
            if (document.getElementById('stat-rug-leads')) {{
                document.getElementById('stat-rug-leads').textContent = marketObservedCount;
            }}
            if (document.getElementById('stat-rug-sub')) {{
                document.getElementById('stat-rug-sub').textContent = 'Tokens refreshed from live Dex pairs';
            }}

            if (document.getElementById('stat-runner-val')) {{
                document.getElementById('stat-runner-val').textContent = maxAth > 0 ? ('$' + Math.round(maxAth).toLocaleString()) : '$0';
            }}
            if (document.getElementById('stat-runner-sub')) {{
                document.getElementById('stat-runner-sub').textContent = topRunnerStr;
            }}

            updateTeamFilter();
            renderLeadsTable();
        }}

        const ATH_STEPS = [
            0, 1000, 2500, 5000, 10000, 25000, 50000, 75000, 100000, 150000,
            250000, 500000, 750000, 1000000, 2000000, 5000000, 10000000, 25000000, 50000000, 100000000, Infinity
        ];
        const RUG_STEPS = [
            0, 1, 2, 3, 5, 10, 15, 20, 25, 35, 45, 60, 90, 120, 240, 480, 1440, 2880, 4320, 10080, Infinity
        ];

        let activeAthMin = 0;
        let activeAthMax = Infinity;
        let activeRugMinMins = 0;
        let activeRugMaxMins = Infinity;

        function findClosestStep(val, steps) {{
            if (val === Infinity || val >= steps[steps.length - 1]) return steps.length - 1;
            if (val <= steps[0]) return 0;
            let closestIdx = 0;
            let minDiff = Infinity;
            for (let i = 0; i < steps.length; i++) {{
                if (steps[i] === Infinity) continue;
                const diff = Math.abs(steps[i] - val);
                if (diff < minDiff) {{
                    minDiff = diff;
                    closestIdx = i;
                }}
            }}
            return closestIdx;
        }}

        function parseAthInput(raw, isMax) {{
            if (!raw) return isMax ? Infinity : 0;
            let s = String(raw).trim().toLowerCase().replace(/[$,]/g, '');
            if (s === 'max' || s === 'inf' || s === 'all' || s === '') return Infinity;
            if (s === 'min') return 0;
            let mult = 1;
            if (s.endsWith('k')) {{ mult = 1000; s = s.slice(0, -1); }}
            else if (s.endsWith('m')) {{ mult = 1000000; s = s.slice(0, -1); }}
            else if (s.endsWith('b')) {{ mult = 1000000000; s = s.slice(0, -1); }}
            const num = parseFloat(s);
            if (isNaN(num)) return isMax ? Infinity : 0;
            return Math.max(0, num * mult);
        }}

        function formatAth(num) {{
            if (num === Infinity || num >= 250000000) return 'Max';
            if (num <= 0) return '$0';
            if (num >= 1000000) {{
                const m = num / 1000000;
                return '$' + (Number.isInteger(m) ? m : m.toFixed(1)) + 'M';
            }}
            if (num >= 1000) {{
                const k = num / 1000;
                return '$' + (Number.isInteger(k) ? k : k.toFixed(1)) + 'K';
            }}
            return '$' + Math.round(num);
        }}

        function parseRugInput(raw, isMax) {{
            if (!raw) return isMax ? Infinity : 0;
            let s = String(raw).trim().toLowerCase().replace(/mins?|minutes?/g, 'm');
            if (s === 'max' || s === 'inf' || s === 'all' || s === '') return Infinity;
            if (s === 'min') return 0;
            let mult = 1;
            if (s.endsWith('h') || s.endsWith('hr') || s.endsWith('hrs')) {{ mult = 60; s = s.replace(/hrs?|h/, ''); }}
            else if (s.endsWith('d') || s.endsWith('day') || s.endsWith('days')) {{ mult = 1440; s = s.replace(/days?|d/, ''); }}
            else if (s.endsWith('m')) {{ mult = 1; s = s.slice(0, -1); }}
            const num = parseFloat(s);
            if (isNaN(num)) return isMax ? Infinity : 0;
            return Math.max(0, num * mult);
        }}

        function formatRug(mins) {{
            if (mins === Infinity || mins >= 10080) return 'Max';
            if (mins <= 0) return '0m';
            if (mins >= 1440) {{
                const d = mins / 1440;
                return (Number.isInteger(d) ? d : d.toFixed(1)) + 'd';
            }}
            if (mins >= 60) {{
                const h = mins / 60;
                return (Number.isInteger(h) ? h : h.toFixed(1)) + 'h';
            }}
            return Math.round(mins) + 'm';
        }}

        function updateAthRailHighlight(minIdx, maxIdx) {{
            const fill = document.getElementById('ath-rail-fill');
            if (!fill) return;
            const leftPct = (minIdx / 20) * 100;
            const widthPct = Math.max(0, ((maxIdx - minIdx) / 20) * 100);
            fill.style.left = leftPct + '%';
            fill.style.width = widthPct + '%';
        }}

        function updateRugRailHighlight(minIdx, maxIdx) {{
            const fill = document.getElementById('rug-rail-fill');
            if (!fill) return;
            const leftPct = (minIdx / 20) * 100;
            const widthPct = Math.max(0, ((maxIdx - minIdx) / 20) * 100);
            fill.style.left = leftPct + '%';
            fill.style.width = widthPct + '%';
        }}

        function onAthSliderDual(which) {{
            const sMin = document.getElementById('slider-ath-min');
            const sMax = document.getElementById('slider-ath-max');
            if (!sMin || !sMax) return;
            let minIdx = parseInt(sMin.value, 10);
            let maxIdx = parseInt(sMax.value, 10);

            if (which === 'min') {{
                sMin.style.zIndex = 4;
                sMax.style.zIndex = 3;
                if (minIdx > maxIdx) {{
                    maxIdx = minIdx;
                    sMax.value = maxIdx;
                }}
            }} else {{
                sMax.style.zIndex = 4;
                sMin.style.zIndex = 3;
                if (maxIdx < minIdx) {{
                    minIdx = maxIdx;
                    sMin.value = minIdx;
                }}
            }}

            activeAthMin = ATH_STEPS[minIdx];
            activeAthMax = ATH_STEPS[maxIdx];

            const bMin = document.getElementById('ath-min-input');
            const bMax = document.getElementById('ath-max-input');
            if (bMin) bMin.value = formatAth(activeAthMin);
            if (bMax) bMax.value = formatAth(activeAthMax);

            updateAthRailHighlight(minIdx, maxIdx);
            renderLeadsTable();
        }}

        function onRugSliderDual(which) {{
            const sMin = document.getElementById('slider-rug-min');
            const sMax = document.getElementById('slider-rug-max');
            if (!sMin || !sMax) return;
            let minIdx = parseInt(sMin.value, 10);
            let maxIdx = parseInt(sMax.value, 10);

            if (which === 'min') {{
                sMin.style.zIndex = 4;
                sMax.style.zIndex = 3;
                if (minIdx > maxIdx) {{
                    maxIdx = minIdx;
                    sMax.value = maxIdx;
                }}
            }} else {{
                sMax.style.zIndex = 4;
                sMin.style.zIndex = 3;
                if (maxIdx < minIdx) {{
                    minIdx = maxIdx;
                    sMin.value = minIdx;
                }}
            }}

            activeRugMinMins = RUG_STEPS[minIdx];
            activeRugMaxMins = RUG_STEPS[maxIdx];

            const bMin = document.getElementById('rug-min-input');
            const bMax = document.getElementById('rug-max-input');
            if (bMin) bMin.value = formatRug(activeRugMinMins);
            if (bMax) bMax.value = formatRug(activeRugMaxMins);

            updateRugRailHighlight(minIdx, maxIdx);
            renderLeadsTable();
        }}

        function onAthBoxChange(which, rawVal) {{
            const sMin = document.getElementById('slider-ath-min');
            const sMax = document.getElementById('slider-ath-max');
            const bMin = document.getElementById('ath-min-input');
            const bMax = document.getElementById('ath-max-input');

            if (which === 'min') {{
                const val = parseAthInput(rawVal, false);
                activeAthMin = val;
                if (activeAthMin > activeAthMax) {{
                    activeAthMax = activeAthMin;
                    if (bMax) bMax.value = formatAth(activeAthMax);
                }}
            }} else {{
                const val = parseAthInput(rawVal, true);
                activeAthMax = val;
                if (activeAthMax < activeAthMin) {{
                    activeAthMin = activeAthMax;
                    if (bMin) bMin.value = formatAth(activeAthMin);
                }}
            }}

            const minIdx = findClosestStep(activeAthMin, ATH_STEPS);
            const maxIdx = findClosestStep(activeAthMax, ATH_STEPS);
            if (sMin) sMin.value = minIdx;
            if (sMax) sMax.value = maxIdx;

            if (bMin) bMin.value = formatAth(activeAthMin);
            if (bMax) bMax.value = formatAth(activeAthMax);

            updateAthRailHighlight(minIdx, maxIdx);
            renderLeadsTable();
        }}

        function onRugBoxChange(which, rawVal) {{
            const sMin = document.getElementById('slider-rug-min');
            const sMax = document.getElementById('slider-rug-max');
            const bMin = document.getElementById('rug-min-input');
            const bMax = document.getElementById('rug-max-input');

            if (which === 'min') {{
                const mins = parseRugInput(rawVal, false);
                activeRugMinMins = mins;
                if (activeRugMinMins > activeRugMaxMins) {{
                    activeRugMaxMins = activeRugMinMins;
                    if (bMax) bMax.value = formatRug(activeRugMaxMins);
                }}
            }} else {{
                const mins = parseRugInput(rawVal, true);
                activeRugMaxMins = mins;
                if (activeRugMaxMins < activeRugMinMins) {{
                    activeRugMinMins = activeRugMaxMins;
                    if (bMin) bMin.value = formatRug(activeRugMinMins);
                }}
            }}

            const minIdx = findClosestStep(activeRugMinMins, RUG_STEPS);
            const maxIdx = findClosestStep(activeRugMaxMins, RUG_STEPS);
            if (sMin) sMin.value = minIdx;
            if (sMax) sMax.value = maxIdx;

            if (bMin) bMin.value = formatRug(activeRugMinMins);
            if (bMax) bMax.value = formatRug(activeRugMaxMins);

            updateRugRailHighlight(minIdx, maxIdx);
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
            const includeAlive = document.getElementById('rug-include-alive') ? document.getElementById('rug-include-alive').checked : true;

            // Filter
            let filtered = currentCandidates.filter(c => {{
                if (chainFilter !== 'ALL' && String(c.chain_id) !== chainFilter) return false;
                if (tierFilter !== 'ALL' && c.confidence !== tierFilter) return false;
                if (teamFilter !== 'all' && c.team.toLowerCase() !== teamFilter) return false;
                if (rugFilter === 'ALIVE_ONLY' && !c.is_alive) return false;
                if (rugFilter === 'RUG_ONLY' && c.is_alive) return false;
                if (rugFilter === 'HIDE_RUGS' && c.is_rug) return false;
                if (rugFilter === 'RUGS_ONLY' && !c.is_rug) return false;

                // ATH Min/Max filter
                if (c.ath_known) {{
                    const candAth = Number(c.ath);
                    if (candAth < activeAthMin) return false;
                    if (activeAthMax !== Infinity && candAth > activeAthMax) return false;
                }} else if (activeAthMin > 0 || activeAthMax !== Infinity) {{
                    return false;
                }}

                // Time to Rug Min/Max filter
                const minRugSec = activeRugMinMins * 60;
                const maxRugSec = activeRugMaxMins === Infinity ? Infinity : (activeRugMaxMins * 60);
                if (c.is_alive) {{
                    if (minRugSec > 0 || maxRugSec !== Infinity) {{
                        if (!includeAlive) return false;
                    }}
                }} else if (c.lifespan_sec !== null && c.lifespan_sec !== undefined) {{
                    const candLife = Number(c.lifespan_sec);
                    if (candLife < minRugSec) return false;
                    if (maxRugSec !== Infinity && candLife > maxRugSec) return false;
                }} else if (minRugSec > 0 || maxRugSec !== Infinity) {{
                    return false;
                }}

                if (searchTerm) {{
                    const symMatch = c.symbol.toLowerCase().includes(searchTerm);
                    const nameMatch = c.name.toLowerCase().includes(searchTerm);
                    const caMatch = c.ca.toLowerCase().includes(searchTerm);
                    const sibMatch = (c.best_match_symbol || '').toLowerCase().includes(searchTerm);
                    const chainMatch = c.chain.toLowerCase().includes(searchTerm) || String(c.chain_id).includes(searchTerm);
                    const statusMatch = c.is_alive ? 'alive ongoing'.includes(searchTerm) : (c.lifespan_str || '').toLowerCase().includes(searchTerm);
                    if (!symMatch && !nameMatch && !caMatch && !sibMatch && !chainMatch && !statusMatch) return false;
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
                }} else if (sortCol === 'ath') {{
                    valA = a.ath_known ? a.ath : -1;
                    valB = b.ath_known ? b.ath : -1;
                }} else if (sortCol === 'lifespan') {{
                    valA = a.is_alive ? 999999999 : (a.lifespan_sec || 0);
                    valB = b.is_alive ? 999999999 : (b.lifespan_sec || 0);
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

                const formatUsd = value => {{
                    if (value === null || value === undefined) return 'N/A';
                    if (value >= 1000000) return `$${{(value / 1000000).toFixed(2)}}M`;
                    if (value >= 1000) return `$${{(value / 1000).toFixed(1)}}K`;
                    return `$${{Math.round(value).toLocaleString()}}`;
                }};
                const mcValue = c.market_cap !== null ? c.market_cap : c.fdv;
                const mcLabel = c.market_cap !== null ? 'MC' : (c.fdv !== null ? 'FDV' : 'MC');
                const mcFormatted = formatUsd(mcValue);
                const athFormatted = c.ath_known ? formatUsd(c.ath) : 'N/A';
                const mcClass = mcValue !== null && mcValue >= 1000000 ? 'mc-high'
                    : mcValue !== null && mcValue >= 100000 ? 'mc-mid'
                    : mcValue !== null && mcValue >= 1000 ? 'mc-sub' : 'mc-low';

                let statusBadge = '';
                if (c.is_alive) {{
                    statusBadge = '<span class="status-pill alive" title="Actively trading token • Liquidity intact">🟢 Still Alive</span>';
                }} else {{
                    const lStr = c.lifespan_str || '< 5 mins';
                    const isQuick = (c.lifespan_sec && c.lifespan_sec <= 600) || lStr.includes('< 5') || lStr.includes('< 1');
                    const badgeCls = isQuick ? 'status-pill quick-rug' : 'status-pill rug';
                    statusBadge = `<span class="${{badgeCls}}" title="Active lifespan before liquidity pull / dump">⏱️ ${{lStr}}</span>`;
                }}

                const isArc = c.chain_id === 5042;
                const chainPillClass = isArc ? 'chain-pill arc' : 'chain-pill';
                const chainPillLabel = isArc ? 'ARC' : 'RBH';
                const gmgnUrl = isArc ? '' : `https://gmgn.ai/robinhood/token/${{c.ca}}`;
                const dexUrl = `https://dexscreener.com/${{isArc ? 'arc' : 'robinhood'}}/${{c.ca}}`;
                const scanUrl = `${{isArc ? 'https://explorer.arc.io/address/' : 'https://robinhoodchain.blockscout.com/address/'}}${{c.ca}}`;
                const gmgnButton = !isArc
                    ? `<a href="${{gmgnUrl}}" target="_blank" rel="noopener noreferrer" class="btn btn-gmgn" title="Open chart on GMGN.ai">GMGN</a>`
                    : `<span class="btn-gmgn-disabled" title="GMGN does not index Arc chain yet">GMGN</span>`;

                const sibSymbol = c.best_match_symbol ? `$${{c.best_match_symbol}}` : 'N/A';
                const sibCaShort = c.best_match_ca ? `${{c.best_match_ca.substring(0, 6)}}...${{c.best_match_ca.substring(c.best_match_ca.length - 4)}}` : '';
                const dateDisplay = c.token_live ? c.token_live : 'N/A';

                tr.innerHTML = `
                    <td>
                        <span class="badge ${{c.confidence.toLowerCase()}}">${{c.confidence.replace('_', ' ')}}</span>
                    </td>
                    <td><span class="score-val" style="color: ${{c.score >= 65 ? '#2ecc71' : c.score >= 45 ? '#60a5fa' : '#fbbf24'}}">${{c.score.toFixed(1)}}%</span></td>
                    <td class="token-cell">
                        <div>
                            <span class="token-symbol">$${{c.symbol}}</span>
                            <span class="${{chainPillClass}}">${{chainPillLabel}}</span>
                            ${{c.is_qualified ? `<span class="qualification-pill" title="Passed a recorded, chain-specific graduation gate; paid listings alone never qualify">QUALIFIED</span>` : ''}}
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
                        <span class="chain-pill ${{c.best_match_chain_id === 5042 ? 'arc' : ''}}">${{c.best_match_chain}}</span>
                        ${{sibCaShort ? `<div style="font-size: 10px; color: var(--text-muted); font-family: monospace; margin-top: 2px;"><code>${{sibCaShort}}</code></div>` : ''}}
                    </td>
                    <td>
                        <div class="mc-val ${{mcClass}}" id="mc-cell-${{c.ca}}" title="Latest observed market value">
                            ${{mcLabel}} ${{mcFormatted}}
                            ${{mcValue === null ? `<button class="btn-fetch-single" onclick="refreshSingleToken(event, '${{c.chain_id}}', '${{c.ca}}')" title="Fetch live market cap from DexScreener">🔄</button>` : ''}}
                        </div>
                        <div style="font-size: 10px; color: var(--text-muted); font-family: monospace;" title="Historical ATH is shown only when a source is recorded">ATH ${{athFormatted}}</div>
                    </td>
                    <td>${{statusBadge}}</td>
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
            renderLeadsTable();
        }}
        function resetLeadsFilters() {{
            if (document.getElementById('leads-search')) document.getElementById('leads-search').value = '';
            if (document.getElementById('leads-tier-filter')) document.getElementById('leads-tier-filter').value = 'ALL';
            if (document.getElementById('leads-team-filter')) document.getElementById('leads-team-filter').value = 'ALL';
            if (document.getElementById('leads-rug-filter')) document.getElementById('leads-rug-filter').value = 'ALL';

            // Reset ATH inputs & slider
            activeAthMin = 0;
            activeAthMax = Infinity;
            if (document.getElementById('ath-min-input')) document.getElementById('ath-min-input').value = '$0';
            if (document.getElementById('ath-max-input')) document.getElementById('ath-max-input').value = 'Max';
            if (document.getElementById('slider-ath-min')) document.getElementById('slider-ath-min').value = 0;
            if (document.getElementById('slider-ath-max')) document.getElementById('slider-ath-max').value = 20;
            updateAthRailHighlight(0, 20);

            // Reset Time to Rug inputs & slider
            activeRugMinMins = 0;
            activeRugMaxMins = Infinity;
            if (document.getElementById('rug-min-input')) document.getElementById('rug-min-input').value = '0m';
            if (document.getElementById('rug-max-input')) document.getElementById('rug-max-input').value = 'Max';
            if (document.getElementById('slider-rug-min')) document.getElementById('slider-rug-min').value = 0;
            if (document.getElementById('slider-rug-max')) document.getElementById('slider-rug-max').value = 20;
            if (document.getElementById('rug-include-alive')) document.getElementById('rug-include-alive').checked = true;
            updateRugRailHighlight(0, 20);

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

        // Utility: Fetch Single Token from DexScreener in real-time
        async function refreshSingleToken(e, chainId, ca) {{
            if (e) e.stopPropagation();
            const btn = e ? e.currentTarget : null;
            if (btn) btn.classList.add('spin');
            const dexChain = String(chainId) === '5042' ? 'arc' : 'robinhood';
            try {{
                const res = await fetch(`https://api.dexscreener.com/tokens/v1/${{dexChain}}/${{ca}}`);
                if (!res.ok) throw new Error(`HTTP ${{res.status}}`);
                const pairs = await res.json();
                if (pairs && pairs.length > 0) {{
                    const best = pairs.sort((a,b) => (b.liquidity?.usd || 0) - (a.liquidity?.usd || 0))[0];
                    const liveMc = best.marketCap || best.fdv;
                    const cand = currentCandidates.find(c => c.ca.toLowerCase() === ca.toLowerCase() && String(c.chain_id) === String(chainId));
                    if (cand && liveMc) {{
                        cand.market_cap = liveMc;
                        cand.fdv = best.fdv || null;
                        cand.current_liquidity = best.liquidity?.usd || null;
                        cand.is_alive = true;
                        cand.lifespan_str = "🟢 Still Alive";
                        showToast(`✓ Fetched $${{cand.symbol}}: MC $${{Math.round(liveMc).toLocaleString()}} (Liquidity $${{Math.round(best.liquidity?.usd || 0).toLocaleString()}})`);
                        renderLeadsTable();
                        return;
                    }}
                }}
                showToast(`ℹ️ DexScreener returned no active pair for ${{ca.substring(0,6)}}...${{ca.substring(ca.length-4)}} yet.`);
            }} catch (err) {{
                showToast(`⚠️ Fetch error: ${{err.message}}`);
            }} finally {{
                if (btn) btn.classList.remove('spin');
            }}
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
            updateAthRailHighlight(0, 20);
            updateRugRailHighlight(0, 20);
            rescoreAllCandidates();
            renderParamsTable();
            loadRemoteCandidates();
            // Auto-sync live candidates every 30 seconds
            setInterval(loadRemoteCandidates, 30000);
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

for filename in ("team_leads_dashboard.html", "index.html", "local_dashboard.html"):
    target = OUTPUT_DIR / filename
    temporary = target.with_name(target.name + f".{os.getpid()}.tmp")
    temporary.write_text(html_content, encoding="utf-8")
    os.replace(temporary, target)

# Also publish dashboard_candidates.json in OUTPUT_DIR for direct static Nginx serving
web_snapshot = OUTPUT_DIR / "dashboard_candidates.json"
web_snapshot_tmp = web_snapshot.with_name(web_snapshot.name + f".{os.getpid()}.tmp")
web_snapshot_tmp.write_text(json.dumps({
    "schema_version": 1,
    "generated_at": generated_at,
    "count": len(candidates_data),
    "candidates": candidates_data,
}, indent=2, sort_keys=True), encoding="utf-8")
os.replace(web_snapshot_tmp, web_snapshot)

# Also update evmdash2.html in Telegram Desktop downloads for instant access
tg_desktop_path = Path(r"C:\Users\Rence\Downloads\Telegram Desktop\evmdash2.html")
if tg_desktop_path.parent.exists():
    try:
        tg_desktop_path.write_text(html_content, encoding="utf-8")
        print(f"Updated {tg_desktop_path}")
    except Exception as e:
        print(f"Could not update {tg_desktop_path}: {e}")

print(f"Generated dashboard atomically in {OUTPUT_DIR}")
print(f"Generated API snapshot atomically at {SNAPSHOT_PATH}")
