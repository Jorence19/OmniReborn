-- Forensic Database Schema for Developer Team Clustering & Real-Time Sniping
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

-- 1. Master Token Registry (With Graduation & Ingestion Filter Gates)
CREATE TABLE IF NOT EXISTS tokens (
    ca TEXT PRIMARY KEY,
    chain TEXT DEFAULT 'RBH',
    symbol TEXT,
    name TEXT,
    launchpad TEXT,
    token_live_at TEXT,
    migrated_at TEXT,               -- Graduation timestamp (Pons -> Uni v4)
    time_to_graduate_sec INTEGER,   -- Graduation Velocity (fast < 3m = bundle cabal)
    is_migrated INTEGER DEFAULT 0,  -- Gate A
    is_dex_paid INTEGER DEFAULT 0,  -- Gate B
    is_qualified INTEGER DEFAULT 0, -- Explicit Phase 1 inclusion, never inferred from missing data
    qualification_reasons TEXT,     -- JSON evidence for why this token qualified
    ath_usd REAL DEFAULT 0,
    peak_liquidity_usd REAL DEFAULT 0,
    x_handle TEXT,
    website TEXT,
    description TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- 2. Blockchain Execution & Deployment DNA
CREATE TABLE IF NOT EXISTS execution_profiles (
    ca TEXT PRIMARY KEY REFERENCES tokens(ca) ON DELETE CASCADE,
    dev_wallet TEXT,
    funder_1hop TEXT,
    funder_2hop TEXT,
    funder_label TEXT,
    fund_amount REAL,
    funded_at TEXT,
    value_eth REAL,
    is_005_pattern INTEGER DEFAULT 0, -- Programmatic .0005 snipe flag
    gwei REAL,
    max_gwei REAL,
    priority_gwei REAL,
    nonce INTEGER,
    method_selector TEXT,
    proxy_impl TEXT,
    proxy_kind TEXT,
    creation_tx_hash TEXT,
    creation_block INTEGER,
    creation_timestamp TEXT,
    creation_gas_used INTEGER,
    creation_tx_fee_eth REAL,
    setup_time_seconds INTEGER,
    wallet_age_at_deploy_seconds INTEGER,
    funding_tx_hash TEXT,
    first_funder TEXT,
    first_funded_at TEXT,
    funding_count_before_deploy INTEGER,
    funding_total_eth_before_deploy REAL,
    funding_lineage_json TEXT,
    initial_snipe_tokens REAL,
    initial_snipe_raw TEXT,
    contract_factory TEXT,
    deployer_balance_eth REAL
);

-- 3. Smart Contract Bytecode Fingerprints
CREATE TABLE IF NOT EXISTS bytecode_profiles (
    ca TEXT PRIMARY KEY REFERENCES tokens(ca) ON DELETE CASCADE,
    template_hash TEXT,             -- Cleaned runtime bytecode hash
    raw_bytecode_hash TEXT,
    compiler_version TEXT,
    selectors_hash TEXT,            -- Hash of function dispatchers
    selectors_list TEXT,            -- JSON array of function selectors
    normalized_bytecode_hash TEXT,  -- Runtime hash after Solidity metadata removal
    bytecode_size_bytes INTEGER,
    metadata_hash TEXT,
    metadata_length_bytes INTEGER,
    selector_count INTEGER
);

-- 4. Bundler Activity & Market Promotions
CREATE TABLE IF NOT EXISTS bundle_analytics (
    ca TEXT PRIMARY KEY REFERENCES tokens(ca) ON DELETE CASCADE,
    bundler_wallet TEXT,
    bundle_eth REAL,
    dev_eth REAL,
    buyer_wallet TEXT,
    buyer_eth REAL,
    bundle_ratio REAL DEFAULT 0,     -- % sniped in block 0/1 (GMGN)
    bundle_wallets_count INTEGER DEFAULT 0,
    dev_holding_ratio REAL DEFAULT 0,
    dev_sold_ratio REAL DEFAULT 0,
    top_10_ratio REAL DEFAULT 0,
    time_social_paid TEXT,
    first_boost TEXT,
    hm_1st_boost TEXT,
    second_boost TEXT,
    hm_2nd_boost TEXT,
    ads_paid TEXT
);

-- 5. Deep Branding & Socials Fingerprints (The 5th Forensic Pillar)
CREATE TABLE IF NOT EXISTS branding_profiles (
    ca TEXT PRIMARY KEY REFERENCES tokens(ca) ON DELETE CASCADE,
    website_url TEXT,
    website_domain TEXT,
    website_tld TEXT,               -- .app, .xyz, .fun, etc.
    website_host_type TEXT,         -- Vercel, Netlify, Carrd, Custom VPS
    favicon_hash TEXT,              -- MurmurHash3 or SHA256 of favicon
    website_title_hash TEXT,        -- Hash of title & meta description
    tg_url TEXT,
    tg_handle TEXT,
    tg_naming_pattern TEXT,         -- portal, rbh, official, etc.
    tg_chat_id_bracket TEXT,        -- Sequential ID group check (recycled rug detection)
    x_handle TEXT,
    x_naming_pattern TEXT,          -- app, coin, token suffix
    marketing_speed_sec INTEGER     -- Delay from launch to first paid boost
);

-- 6. Lossless Phase 1 fingerprint payloads.  Columns above support fast matching;
-- this table retains every observed clue and its extraction quality for later research.
CREATE TABLE IF NOT EXISTS fingerprint_profiles (
    ca TEXT PRIMARY KEY REFERENCES tokens(ca) ON DELETE CASCADE,
    wallet_lineage_json TEXT,
    funding_habits_json TEXT,
    launch_habits_json TEXT,
    contract_habits_json TEXT,
    social_habits_json TEXT,
    evidence_quality_json TEXT,
    extracted_at TEXT DEFAULT (datetime('now'))
);

-- 6. Clustered Developer Teams Registry

CREATE TABLE IF NOT EXISTS teams (
    team_id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_name TEXT UNIQUE,          -- e.g. 'team astro', 'team gigalon', 'team NCHIP'
    team_tier TEXT DEFAULT 'TIER_A',-- TIER_S_RUNNER, TIER_A, TIER_F_RUGGER
    representative_template TEXT,
    root_funder TEXT,
    avg_ath_usd REAL DEFAULT 0,
    peak_ath_usd REAL DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    rug_rate_pct REAL DEFAULT 0,
    first_seen_at TEXT,
    notes TEXT
);

-- 7. Match Results & Similarity Lineage
CREATE TABLE IF NOT EXISTS token_matches (
    ca TEXT PRIMARY KEY REFERENCES tokens(ca) ON DELETE CASCADE,
    best_match_ca TEXT,
    best_match_pct REAL,            -- 0 to 100
    candidate_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
    candidate_team_name TEXT,
    match_reasons TEXT,             -- JSON breakdown of scoring points
    matched_at TEXT DEFAULT (datetime('now'))
);

-- 8. Real-Time Snipe Watchlists (Powered by Audit Intelligence)
CREATE TABLE IF NOT EXISTS snipe_watchlists (
    watchlist_id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,      -- 'FUNDER', 'BUNDLER', 'TEMPLATE', 'DEV'
    target_value TEXT NOT NULL UNIQUE,
    associated_team_name TEXT,
    team_tier TEXT DEFAULT 'TIER_S_RUNNER',
    avg_ath_usd REAL DEFAULT 0,
    action_trigger TEXT DEFAULT 'SNIPE_ON_GRADUATION',
    created_at TEXT DEFAULT (datetime('now'))
);

-- High Performance Indexes
CREATE INDEX IF NOT EXISTS idx_exec_dev ON execution_profiles(dev_wallet);
CREATE INDEX IF NOT EXISTS idx_exec_funder1 ON execution_profiles(funder_1hop);
CREATE INDEX IF NOT EXISTS idx_exec_funder2 ON execution_profiles(funder_2hop);
CREATE INDEX IF NOT EXISTS idx_bytecode_template ON bytecode_profiles(template_hash);
CREATE INDEX IF NOT EXISTS idx_bytecode_normalized ON bytecode_profiles(normalized_bytecode_hash);
CREATE INDEX IF NOT EXISTS idx_tokens_qualified ON tokens(is_qualified);
CREATE INDEX IF NOT EXISTS idx_matches_team ON token_matches(candidate_team_id);
CREATE INDEX IF NOT EXISTS idx_bundle_bundler ON bundle_analytics(bundler_wallet);
CREATE INDEX IF NOT EXISTS idx_branding_favicon ON branding_profiles(favicon_hash);
CREATE INDEX IF NOT EXISTS idx_tokens_migrated ON tokens(is_migrated);
CREATE INDEX IF NOT EXISTS idx_watchlist_target ON snipe_watchlists(target_value);

-- Unified Master Profile View
DROP VIEW IF EXISTS v_full_forensic_profile;
CREATE VIEW v_full_forensic_profile AS
SELECT 
    t.ca,
    t.symbol,
    t.name,
    t.chain,
    t.launchpad,
    t.token_live_at,
    t.migrated_at,
    t.time_to_graduate_sec,
    t.is_migrated,
    t.is_dex_paid,
    t.is_qualified,
    t.qualification_reasons,
    t.ath_usd,
    t.peak_liquidity_usd,
    COALESCE(bp.x_handle, t.x_handle) AS x_handle,
    t.website AS token_website,
    t.description,
    e.dev_wallet,
    e.funder_1hop,
    e.funder_2hop,
    e.funder_label,
    e.fund_amount,
    e.funded_at,
    e.setup_time_seconds,
    e.wallet_age_at_deploy_seconds,
    e.funding_count_before_deploy,
    e.funding_total_eth_before_deploy,
    e.funding_lineage_json,
    e.value_eth,
    e.is_005_pattern,
    e.gwei,
    e.max_gwei,
    e.priority_gwei,
    e.nonce,
    e.method_selector,
    e.creation_block,
    e.creation_timestamp,
    e.creation_gas_used,
    e.creation_tx_fee_eth,
    e.initial_snipe_tokens,
    e.contract_factory,
    b.template_hash,
    b.raw_bytecode_hash,
    b.normalized_bytecode_hash,
    b.compiler_version,
    b.selectors_hash,
    b.selectors_list,
    b.selector_count,
    a.bundler_wallet,
    a.buyer_wallet,
    a.bundle_eth,
    a.dev_eth,
    a.buyer_eth,
    a.bundle_ratio,
    a.bundle_wallets_count,
    a.dev_holding_ratio,
    a.dev_sold_ratio,
    a.top_10_ratio,
    a.time_social_paid,
    a.first_boost,
    a.hm_1st_boost,
    a.second_boost,
    a.hm_2nd_boost,
    a.ads_paid,
    bp.website_url,
    bp.website_domain,
    bp.website_host_type,
    bp.favicon_hash,
    bp.tg_url,
    bp.tg_handle,
    bp.tg_naming_pattern,
    bp.x_handle AS branding_x_handle,
    bp.x_naming_pattern,
    m.best_match_ca,
    m.candidate_team_id,
    m.best_match_pct,
    COALESCE(tm.team_name, m.candidate_team_name, 'Unclustered') AS candidate_team,
    COALESCE(tm.team_tier, 'UNRATED') AS team_tier,
    m.match_reasons
FROM tokens t
LEFT JOIN execution_profiles e ON t.ca = e.ca
LEFT JOIN bytecode_profiles b ON t.ca = b.ca
LEFT JOIN bundle_analytics a ON t.ca = a.ca
LEFT JOIN branding_profiles bp ON t.ca = bp.ca
LEFT JOIN token_matches m ON t.ca = m.ca
LEFT JOIN teams tm ON m.candidate_team_id = tm.team_id;

-- Team Summary & Runner Performance View
DROP VIEW IF EXISTS v_team_summary;
CREATE VIEW v_team_summary AS
SELECT 
    COALESCE(tm.team_name, 'Unclustered') AS team,
    COALESCE(tm.team_tier, 'UNRATED') AS tier,
    COUNT(t.ca) AS token_count,
    GROUP_CONCAT(t.symbol, ', ') AS tokens_launched,
    ROUND(AVG(t.ath_usd), 2) AS avg_ath_usd,
    MAX(t.ath_usd) AS peak_ath_usd,
    ROUND(AVG(e.value_eth), 4) AS avg_dev_snipe_eth,
    e.funder_1hop AS primary_funder,
    b.template_hash AS common_template
FROM tokens t
JOIN token_matches m ON t.ca = m.ca
LEFT JOIN teams tm ON m.candidate_team_id = tm.team_id
LEFT JOIN execution_profiles e ON t.ca = e.ca
LEFT JOIN bytecode_profiles b ON t.ca = b.ca
GROUP BY COALESCE(tm.team_name, 'Unclustered');

-- Active Snipe Watchlist View
DROP VIEW IF EXISTS v_active_snipe_targets;
CREATE VIEW v_active_snipe_targets AS
SELECT 
    w.watchlist_id,
    w.target_type,
    w.target_value,
    w.associated_team_name,
    w.team_tier,
    w.avg_ath_usd,
    w.action_trigger
FROM snipe_watchlists w
ORDER BY w.avg_ath_usd DESC;
