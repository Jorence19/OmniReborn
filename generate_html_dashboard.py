import json
import pandas as pd
from pathlib import Path

# Load candidates and tgscan data
df_cand = pd.read_csv('phase1_fingerprint_report_candidates.csv')
df_tg = pd.read_csv('tgscan_rbh_1789559702282.csv')
df_tg['ca_lower'] = df_tg['ca'].astype(str).str.lower()
df_cand['ca_lower'] = df_cand['ca'].astype(str).str.lower()

merged = pd.merge(
    df_cand,
    df_tg[['ca_lower', 'ath', 'name', 'token_live', 'x', 'website', 'gwei', 'max_gwei', 'priority_gwei', '1st_boost', 'ads']],
    on='ca_lower',
    how='left'
)

# Prepare candidates JSON structure for client-side JavaScript
candidates_data = []
for idx, r in merged.iterrows():
    ca = str(r['ca'])
    symbol = str(r['symbol'])
    name = str(r['name']) if pd.notna(r.get('name')) and str(r.get('name')).strip() != '' else symbol
    conf = str(r['confidence'])
    score = float(r['candidate_score'])
    team = str(r['inferred_team']) if pd.notna(r.get('inferred_team')) else 'unclustered'
    best_match_symbol = str(r['best_match_symbol']) if pd.notna(r.get('best_match_symbol')) else ''
    best_match_ca = str(r['best_match_ca']) if pd.notna(r.get('best_match_ca')) else ''
    ath = float(r['ath']) if pd.notna(r.get('ath')) and float(r['ath']) > 0 else 0.0
    
    # Parse evidence
    ev_raw = r['evidence']
    try:
        ev_list = json.loads(ev_raw) if isinstance(ev_raw, str) else ev_raw
    except Exception:
        ev_list = []
        
    candidates_data.append({
        "ca": ca,
        "symbol": symbol,
        "name": name,
        "confidence": conf,
        "score": score,
        "team": team,
        "best_match_symbol": best_match_symbol,
        "best_match_ca": best_match_ca,
        "ath": ath,
        "evidence": ev_list,
        "token_live": str(r.get('token_live')) if pd.notna(r.get('token_live')) else '',
        "website": str(r.get('website')) if pd.notna(r.get('website')) else '',
        "x": str(r.get('x')) if pd.notna(r.get('x')) else '',
    })

# Define the 50 parameters with default Phase 1 weights and forensic descriptions
PARAMETERS = [
    # 1. Wallets & Lineage (High Reliability)
    {
        "id": "dev_wallet",
        "name": "Deployer Wallet (dev_wallet)",
        "category": "Wallets & Lineage",
        "default": 1.00,
        "description": "Direct deployer wallet reused across tokens (immediate 100% attribution)."
    },
    {
        "id": "bundler_wallet",
        "name": "Bundler Wallet (bundler_wallet)",
        "category": "Wallets & Lineage",
        "default": 1.00,
        "description": "Wallet coordinating or funding launch bundles at block 0."
    },
    {
        "id": "buyer_wallet",
        "name": "Insider Buyer Wallet (buyer_wallet)",
        "category": "Wallets & Lineage",
        "default": 1.00,
        "description": "Repeat sniper or internal buyer wallet detected across multiple team coins."
    },
    {
        "id": "funder_1hop",
        "name": "1-Hop Funder Address (funder_1hop)",
        "category": "Wallets & Lineage",
        "default": 0.85,
        "description": "Immediate upstream funding source transferring ETH to the dev wallet (e.g. Astro Treasury)."
    },
    {
        "id": "funder_2hop",
        "name": "2-Hop Funder Address (funder_2hop)",
        "category": "Wallets & Lineage",
        "default": 0.85,
        "description": "Grandparent funding root or distribution hub two hops upstream."
    },
    {
        "id": "funder_label",
        "name": "Funder Label / Exchange Tag (funder_label)",
        "category": "Wallets & Lineage",
        "default": 0.08,
        "description": "Recognized CEX hot wallet or bridge tag (e.g. Binance Hot Wallet, FixedFloat)."
    },

    # 2. Bytecode & Smart Contract Architecture
    {
        "id": "normalized_bytecode_hash",
        "name": "Normalized Bytecode Hash",
        "category": "Bytecode & Architecture",
        "default": 0.55,
        "description": "Exact SHA-256 bytecode match after stripping CBOR compiler metadata and constructor addresses."
    },
    {
        "id": "contract_factory",
        "name": "Contract Factory / Proxy",
        "category": "Bytecode & Architecture",
        "default": 0.55,
        "description": "Factory or proxy deployer contract address used to spawn token contracts."
    },
    {
        "id": "template_hash",
        "name": "Template Structural Hash",
        "category": "Bytecode & Architecture",
        "default": 0.35,
        "description": "Structural opcode flow hash representing token contract implementation framework."
    },
    {
        "id": "selectors_hash",
        "name": "Function Selectors Hash",
        "category": "Bytecode & Architecture",
        "default": 0.30,
        "description": "Combined hash of all public ABI 4-byte function selectors supported by the token."
    },
    {
        "id": "method_selector",
        "name": "Creation Method Selector",
        "category": "Bytecode & Architecture",
        "default": 0.15,
        "description": "4-byte function selector used to deploy the token (e.g. 0xf85f8e41 launchAndBuy)."
    },
    {
        "id": "compiler_version",
        "name": "Solidity Compiler Version",
        "category": "Bytecode & Architecture",
        "default": 0.10,
        "description": "Exact solc compiler version extracted from bytecode metadata (e.g. 0.8.28)."
    },
    {
        "id": "launchpad",
        "name": "Launchpad Platform",
        "category": "Bytecode & Architecture",
        "default": 0.10,
        "description": "Platform or bonding curve mechanism used (e.g. pons, uniswap_v4)."
    },

    # 3. Branding, Socials & Web Infrastructure
    {
        "id": "favicon_hash",
        "name": "Favicon MMH3 / SHA-256 Hash",
        "category": "Branding & Socials",
        "default": 1.00,
        "description": "Exact cryptographic hash of website favicon icon asset."
    },
    {
        "id": "tg_handle",
        "name": "Telegram Channel / Group Handle",
        "category": "Branding & Socials",
        "default": 1.00,
        "description": "Exact Telegram username or invite link reused by team."
    },
    {
        "id": "x_handle",
        "name": "Twitter / X Profile Handle",
        "category": "Branding & Socials",
        "default": 1.00,
        "description": "Exact X / Twitter profile handle linked to project."
    },
    {
        "id": "website_domain",
        "name": "Website Root Domain",
        "category": "Branding & Socials",
        "default": 0.85,
        "description": "Base domain name hosting the token's landing page."
    },
    {
        "id": "website_host_type",
        "name": "Website Hosting Infrastructure",
        "category": "Branding & Socials",
        "default": 0.22,
        "description": "Hosting provider signature (Vercel, Carrd, Netlify, Cloudflare Pages)."
    },
    {
        "id": "tg_naming_pattern",
        "name": "Telegram Naming Regex Pattern",
        "category": "Branding & Socials",
        "default": 0.22,
        "description": "Syntax habits in TG handle construction (e.g. _portal, _sol, _erc20)."
    },
    {
        "id": "x_naming_pattern",
        "name": "Twitter / X Naming Pattern",
        "category": "Branding & Socials",
        "default": 0.22,
        "description": "Syntax habits in X handle naming conventions (e.g. _coin, real_token)."
    },
    {
        "id": "description_length_bucket",
        "name": "Description Length Bucket",
        "category": "Branding & Socials",
        "default": 0.22,
        "description": "50-character binned length of token description copy."
    },
    {
        "id": "description_hashtag_count",
        "name": "Description Hashtag Count",
        "category": "Branding & Socials",
        "default": 0.22,
        "description": "Number of '#' hashtags included in description."
    },
    {
        "id": "description_mention_count",
        "name": "Description Mention Count",
        "category": "Branding & Socials",
        "default": 0.22,
        "description": "Number of '@' handles tagged in description."
    },

    # 4. Execution, Setup & Gas Habits
    {
        "id": "setup_time_seconds",
        "name": "Setup Time (Funding to Deploy)",
        "category": "Execution & Gas",
        "default": 0.20,
        "description": "Time elapsed between funder ETH arrival and token deployment (±15% buffer)."
    },
    {
        "id": "wallet_age_at_deploy_seconds",
        "name": "Wallet Age at Deploy Bucket",
        "category": "Execution & Gas",
        "default": 0.20,
        "description": "Total age of deployer address from its very first tx on chain (±15% buffer)."
    },
    {
        "id": "nonce",
        "name": "Deployer Nonce at Launch",
        "category": "Execution & Gas",
        "default": 0.20,
        "description": "Wallet nonce count when issuing the token creation transaction."
    },
    {
        "id": "value_eth",
        "name": "Deployment ETH Value (Snipe / Liquidity)",
        "category": "Execution & Gas",
        "default": 0.25,
        "description": "Exact native ETH sent alongside token creation call (±15% buffer)."
    },
    {
        "id": "fund_amount",
        "name": "Funder Transfer Amount",
        "category": "Execution & Gas",
        "default": 0.25,
        "description": "ETH amount sent by upstream funder to seed the deployer (±15% buffer)."
    },
    {
        "id": "funding_count_before_deploy",
        "name": "Funding Tx Count Before Deploy",
        "category": "Execution & Gas",
        "default": 0.20,
        "description": "Number of incoming funding transactions into deployer before creation."
    },
    {
        "id": "funding_total_eth_before_deploy",
        "name": "Total ETH Seed Capital",
        "category": "Execution & Gas",
        "default": 0.20,
        "description": "Cumulative ETH received by deployer before token deployment (±15% buffer)."
    },
    {
        "id": "creation_gas_used",
        "name": "Creation Gas Used",
        "category": "Execution & Gas",
        "default": 0.15,
        "description": "Gas units consumed by token deployment transaction (±15% buffer)."
    },
    {
        "id": "creation_tx_fee_eth",
        "name": "Creation Tx Fee (ETH)",
        "category": "Execution & Gas",
        "default": 0.15,
        "description": "Total transaction fee paid to block builder in ETH (±15% buffer)."
    },
    {
        "id": "gwei",
        "name": "Gas Base Fee (Gwei)",
        "category": "Execution & Gas",
        "default": 0.15,
        "description": "Effective gas price chosen by dev during deployment (±15% buffer)."
    },
    {
        "id": "max_gwei",
        "name": "Max Fee Per Gas (Max Gwei)",
        "category": "Execution & Gas",
        "default": 0.15,
        "description": "EIP-1559 maxFeePerGas setting configured in launch transaction (±15% buffer)."
    },
    {
        "id": "priority_gwei",
        "name": "Priority Tip Fee (Priority Gwei)",
        "category": "Execution & Gas",
        "default": 0.20,
        "description": "EIP-1559 maxPriorityFeePerGas habit (e.g. 0.1 Gwei, ±15% buffer)."
    },

    # 5. Launch Economics & Bundle Dynamics
    {
        "id": "initial_snipe_tokens",
        "name": "Dev Initial Snipe Token Amount",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "Number of tokens bought by dev in the deployment transaction (±15% buffer)."
    },
    {
        "id": "bundle_eth",
        "name": "Total Bundle ETH Spent",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "Aggregate ETH spent across all coordinated block 0 bundle transactions (±15% buffer)."
    },
    {
        "id": "dev_eth",
        "name": "Dev Snipe ETH Amount",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "Exact ETH capital committed by the dev address at launch (±15% buffer)."
    },
    {
        "id": "buyer_eth",
        "name": "Top Insider Buyer ETH",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "ETH capital deployed by top coordinated insider buyer (±15% buffer)."
    },
    {
        "id": "bundle_ratio",
        "name": "Bundle Supply Ratio (%)",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "Percentage of total token supply sniped in the launch bundle (±15% buffer)."
    },
    {
        "id": "bundle_wallets_count",
        "name": "Bundle Wallets Count",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "Number of coordinated buyer wallets participating in the launch bundle."
    },
    {
        "id": "dev_holding_ratio",
        "name": "Dev Supply Retention Ratio",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "Percentage of total supply retained by dev post-graduation (±15% buffer)."
    },
    {
        "id": "dev_sold_ratio",
        "name": "Dev Supply Sold Ratio",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "Percentage of dev holdings sold into the market (±15% buffer)."
    },
    {
        "id": "top_10_ratio",
        "name": "Top 10 Holders Supply Concentration",
        "category": "Economics & Bundles",
        "default": 0.20,
        "description": "Percentage of supply controlled by the top 10 holders combined (±15% buffer)."
    },

    # 6. Marketing & Launch Timing
    {
        "id": "first_boost_used",
        "name": "Dex Paid 1st Boost Purchase",
        "category": "Marketing & Timing",
        "default": 0.22,
        "description": "Whether dev bought DexScreener/DexTools 1st marketing boost."
    },
    {
        "id": "second_boost_used",
        "name": "Dex Paid 2nd Boost Purchase",
        "category": "Marketing & Timing",
        "default": 0.22,
        "description": "Whether dev followed up with 2nd marketing boost."
    },
    {
        "id": "ads_paid_used",
        "name": "Banner Ads Paid at Launch",
        "category": "Marketing & Timing",
        "default": 0.22,
        "description": "Whether dev paid for sponsored banner advertisements on DexScreener."
    },
    {
        "id": "launch_hour_utc",
        "name": "Launch Hour (UTC)",
        "category": "Marketing & Timing",
        "default": 0.22,
        "description": "UTC hour of day when dev team habitually deploys."
    },
    {
        "id": "launch_quarter_hour_utc",
        "name": "Launch 15-Minute Window (UTC)",
        "category": "Marketing & Timing",
        "default": 0.22,
        "description": "Specific 15-minute window habit within the hour."
    },
    {
        "id": "launch_weekday_utc",
        "name": "Launch Day of Week (UTC)",
        "category": "Marketing & Timing",
        "default": 0.22,
        "description": "Day of the week preferred by team for deployments."
    }
]

candidates_json = json.dumps(candidates_data)
parameters_json = json.dumps(PARAMETERS)

html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Robinhood Meme Coin Forensics & Team Leads Dashboard</title>
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
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
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
            min-width: 260px;
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
        }}
        td {{
            padding: 14px 16px;
            border-bottom: 1px solid var(--border-color);
            vertical-align: middle;
        }}
        tr:hover td {{
            background-color: rgba(255, 255, 255, 0.02);
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
        .ath-val {{
            color: #34d399;
            font-weight: 800;
            font-family: monospace;
            font-size: 14px;
        }}
        .token-cell {{
            min-width: 180px;
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
        .evidence-tags {{
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
            max-width: 380px;
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
    </style>
</head>
<body>

    <!-- Top Navigation Bar -->
    <div class="navbar">
        <div class="navbar-brand">
            <span class="logo-icon">⚡</span>
            <div>
                <div class="brand-title">Robinhood Meme Coin Forensics</div>
                <div class="brand-subtitle">Chain ID: 4663 • Nearest Duplicate Sibling Matching & Dev Clustering</div>
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

    <div class="container">

        <!-- Global Toast Notification -->
        <div class="toast-banner" id="toast-banner">
            <span id="toast-msg">Custom weights applied! Candidate leads rescored live.</span>
            <button class="btn btn-secondary" style="padding: 3px 8px; font-size: 11px;" onclick="closeToast()">✕ Dismiss</button>
        </div>

        <!-- Global Stats Grid (Auto-updated on weight changes) -->
        <div class="stats-grid">
            <div class="stat-card" style="--stat-accent: #64748b;">
                <div class="title">Universe Tokens</div>
                <div class="val">248</div>
                <div class="subtext">75 qualified anchor coins</div>
            </div>
            <div class="stat-card" style="--stat-accent: #2ecc71;">
                <div class="title">High Leads (Score ≥65)</div>
                <div class="val" id="stat-high-leads" style="color: #2ecc71;">24</div>
                <div class="subtext">Nearest sibling match ≥65%</div>
            </div>
            <div class="stat-card" style="--stat-accent: #3b82f6;">
                <div class="title">Probable Leads (Score ≥45)</div>
                <div class="val" id="stat-prob-leads" style="color: #60a5fa;">24</div>
                <div class="subtext">Multi-parameter relative match</div>
            </div>
            <div class="stat-card" style="--stat-accent: #f59e0b;">
                <div class="title">Watch Candidates</div>
                <div class="val" id="stat-watch-leads" style="color: #fbbf24;">69</div>
                <div class="subtext">Shared habit & bytecode traces</div>
            </div>
            <div class="stat-card" style="--stat-accent: #10b981;">
                <div class="title">Top Runner Peak ATH</div>
                <div class="val" style="color: #34d399;">$18,099,822</div>
                <div class="subtext">$MANCER • team astro</div>
            </div>
        </div>

        <!-- ==================== PAGE 1: CANDIDATE LEADS TABLE ==================== -->
        <div class="page-content active" id="page-leads">
            <div class="filter-bar">
                <div class="filter-group">
                    <input type="text" id="leads-search" class="search-input" placeholder="Search Symbol, Name, CA, or Sibling Token..." oninput="filterLeadsTable()">
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
                </div>
                <div class="filter-group">
                    <span id="filtered-count-display" style="font-size: 12px; color: var(--text-secondary);">Showing {len(candidates_data)} of {len(candidates_data)} candidate leads</span>
                    <button class="btn btn-secondary" onclick="resetLeadsFilters()">Reset Filters</button>
                </div>
            </div>

            <div class="table-container">
                <table id="leads-table">
                    <thead>
                        <tr>
                            <th onclick="sortLeads('confidence')" style="cursor: pointer;">Confidence ↕</th>
                            <th onclick="sortLeads('score')" style="cursor: pointer;">Score ↕</th>
                            <th>Token / Contract Address</th>
                            <th>Inferred Team</th>
                            <th onclick="sortLeads('best_match_symbol')" style="cursor: pointer;">Nearest Sibling Token ↕</th>
                            <th onclick="sortLeads('ath')" style="cursor: pointer;">Peak ATH ↕</th>
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

            <!-- Numerical Habit Tolerance Buffer Setting -->
            <div class="buffer-card">
                <div>
                    <div style="font-weight: 700; font-size: 14px; color: #ffffff;">
                        🎯 Numerical Habit Tolerance Buffer: <span id="buffer-val-disp" style="color: #10b981; font-family: monospace;">±15%</span>
                    </div>
                    <div style="font-size: 12px; color: var(--text-secondary); margin-top: 4px;">
                        Tolerance applied to continuous numerical parameters (snipe value, fund amount, gas gwei, priority tip, setup duration, gas units). Values within this relative threshold receive proximity credit rather than dropping to 0 immediately.
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
                    <select id="params-cat-filter" class="select-input" onchange="filterParamsTable()">
                        <option value="ALL">All Categories ({len(PARAMETERS)})</option>
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
                            <th style="width: 45%;">1. Parameter / Forensic Habit</th>
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
            Robinhood Chain (Chain ID: 4663) Forensic Tracking Engine • Data verified from RobinScan Multichain V2, Blockscout, and Telegram Scans
        </div>

    </div>

    <!-- Client-Side Scoring & Table Engine -->
    <script>
        // Initial Dataset injected from Python
        const CANDIDATES = {candidates_json};
        const PARAMETERS = {parameters_json};

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

        // Active candidates state
        let currentCandidates = JSON.parse(JSON.stringify(CANDIDATES));
        let sortCol = 'score';
        let sortAsc = false;

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
        function rescoreAllCandidates() {{
            let highCount = 0;
            let probCount = 0;
            let watchCount = 0;

            currentCandidates.forEach(cand => {{
                const res = calculateTokenScore(cand, activeWeights, activeBufferPct);
                cand.score = res.score;
                cand.confidence = res.confidence;

                if (cand.confidence === 'HIGH_LEAD') highCount++;
                else if (cand.confidence === 'PROBABLE_LEAD') probCount++;
                else if (cand.confidence === 'WATCH') watchCount++;
            }});

            // Update stats grid
            if (document.getElementById('stat-high-leads')) document.getElementById('stat-high-leads').textContent = highCount;
            if (document.getElementById('stat-prob-leads')) document.getElementById('stat-prob-leads').textContent = probCount;
            if (document.getElementById('stat-watch-leads')) document.getElementById('stat-watch-leads').textContent = watchCount;

            renderLeadsTable();
        }}

        // Render Page 1: Leads Table
        function renderLeadsTable() {{
            const tbody = document.getElementById('leads-tbody');
            if (!tbody) return;
            tbody.innerHTML = '';

            const searchTerm = (document.getElementById('leads-search') ? document.getElementById('leads-search').value.toLowerCase().trim() : '');
            const tierFilter = (document.getElementById('leads-tier-filter') ? document.getElementById('leads-tier-filter').value : 'ALL');
            const teamFilter = (document.getElementById('leads-team-filter') ? document.getElementById('leads-team-filter').value.toLowerCase() : 'all');

            // Filter
            let filtered = currentCandidates.filter(c => {{
                if (tierFilter !== 'ALL' && c.confidence !== tierFilter) return false;
                if (teamFilter !== 'all' && c.team.toLowerCase() !== teamFilter) return false;
                if (searchTerm) {{
                    const symMatch = c.symbol.toLowerCase().includes(searchTerm);
                    const nameMatch = c.name.toLowerCase().includes(searchTerm);
                    const caMatch = c.ca.toLowerCase().includes(searchTerm);
                    const sibMatch = (c.best_match_symbol || '').toLowerCase().includes(searchTerm);
                    if (!symMatch && !nameMatch && !caMatch && !sibMatch) return false;
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

                const gmgnUrl = `https://gmgn.ai/robinhood/token/${{c.ca}}`;
                const dexUrl = `https://dexscreener.com/robinhood/${{c.ca}}`;
                const scanUrl = `https://robinhoodchain.blockscout.com/address/${{c.ca}}`;

                const sibSymbol = c.best_match_symbol ? `$${{c.best_match_symbol}}` : 'N/A';
                const sibCaShort = c.best_match_ca ? `${{c.best_match_ca.substring(0, 6)}}...${{c.best_match_ca.substring(c.best_match_ca.length - 4)}}` : '';

                tr.innerHTML = `
                    <td><span class="badge ${{c.confidence.toLowerCase()}}">${{c.confidence.replace('_', ' ')}}</span></td>
                    <td><span class="score-val" style="color: ${{c.score >= 65 ? '#2ecc71' : c.score >= 45 ? '#60a5fa' : '#fbbf24'}}">${{c.score.toFixed(1)}}%</span></td>
                    <td class="token-cell">
                        <div>
                            <span class="token-symbol">$${{c.symbol}}</span>
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
                        ${{sibCaShort ? `<div style="font-size: 10px; color: var(--text-muted); font-family: monospace; margin-top: 2px;"><code>${{sibCaShort}}</code></div>` : ''}}
                    </td>
                    <td><span class="${{athClass}}">${{athFormatted}}</span></td>
                    <td>${{evHtml}}</td>
                    <td class="actions-cell">
                        <a href="${{gmgnUrl}}" target="_blank" class="btn btn-gmgn">GMGN</a>
                        <a href="${{dexUrl}}" target="_blank" class="btn btn-dex">DEX</a>
                        <a href="${{scanUrl}}" target="_blank" class="btn btn-scan">SCAN</a>
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
            const catFilter = (document.getElementById('params-cat-filter') ? document.getElementById('params-cat-filter').value : 'ALL');

            PARAMETERS.forEach(p => {{
                if (catFilter !== 'ALL' && p.category !== catFilter) return;
                if (searchTerm) {{
                    const matchName = p.name.toLowerCase().includes(searchTerm);
                    const matchId = p.id.toLowerCase().includes(searchTerm);
                    const matchDesc = p.description.toLowerCase().includes(searchTerm);
                    if (!matchName && !matchId && !matchDesc) return;
                }}

                const curWeight = activeWeights[p.id] !== undefined ? activeWeights[p.id] : p.default;

                const tr = document.createElement('tr');
                tr.id = `param-row-${{p.id}}`;

                tr.innerHTML = `
                    <td>
                        <span class="category-chip">${{p.category}}</span>
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
            showToast(`✓ Successfully applied customized weights across ${{updatedCount}} parameters with ±${{Math.round(activeBufferPct*100)}}% buffer! Rescored all ${{currentCandidates.length}} candidate leads.`);
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
            renderLeadsTable();
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
        }});
    </script>
</body>
</html>
"""

# Write out the generated HTML file (both team_leads_dashboard.html and index.html for Hostinger)
with open("team_leads_dashboard.html", "w", encoding="utf-8") as f:
    f.write(html_content)

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_content)

print("Generated both team_leads_dashboard.html and index.html successfully for Hostinger deployment!")
