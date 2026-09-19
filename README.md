# ⚡ OmniReborn: Robinhood Meme Coin Forensics & Developer Team Tracker

> **Chain IDs: 4663 (Robinhood Chain) + 5042 (Arc)** • Also supports **Base (8453)** & **Ethereum Mainnet (1)**
> Autonomous forensic clustering, developer team identification, and live candidate leads dashboard for meme coin snipers.

---

## 🚀 Overview

**OmniReborn** is a quantitative on-chain intelligence and developer forensics system built to identify repeat meme coin creators ("serial dev teams") on Robinhood Chain and EVM Layer 2s.

Instead of evaluating newly launched tokens in isolation or relying on generic launchpad heuristics, OmniReborn extracts **35+ deep parameters** (bytecode normalized SHA-256, opcode function dispatchers, 2-hop funder lineage, dev initial snipe habits, favicon hashes, and website hosting infrastructure) and cross-examines candidate tokens against known anchor teams (such as `team astro`, `team gigalon`, and `team intel`).

---

## 🌟 Core Innovations

### 1. Nearest Duplicate (Pairwise Sibling) Cross-Examination
Unlike traditional clustering that relies on global centroid averages (which can be overwhelmed by one large cartel), OmniReborn cross-examines every candidate token pairwise against every qualified anchor token in the database.
* Identifies the exact **closest sibling token** ($A^*$).
* Smaller dev teams with 2–4 tokens (`team intel`, `team nchip`, `team robinary`) receive direct attribution without being drowned out by larger teams.

### 2. $\pm 15\%$ Continuous Numerical Proximity Buffer
Replaces rigid binary string matching with relative proximity scoring:
$$\Delta = \frac{|V_{\text{cand}} - V_{\text{ref}}|}{\max(|V_{\text{ref}}|, 10^{-9})}$$
* If $\Delta \le 0.15$ ($\pm 15\%$ tolerance), the metric scores with continuous proximity credit:
  $$\text{Proximity} = 1.0 - \frac{\Delta}{0.15}$$
* Eliminates the penalty on natural blockchain fluctuations (gas base fees, priority tips, minor funding rounding, and `.0005` dev snipe variations).

### 3. Pre-Launch vs. Post-Launch Parameter Pipeline
* **Pre-Launch Sniping Parameters (Block 0 Decision)**:
  * Bytecode normalized SHA-256, opcode dispatcher hashes, compiler version, deployer wallet, 1-hop and 2-hop funder lineage, initial dev snipe amount (e.g. `.0005` ETH), nonce, creation gas used, gas base fee, and priority gwei.
  * Allows instantaneous scoring before or at the moment of liquidity addition to make automated snipe decisions.
* **Post-Launch Holding & Confirmation Parameters**:
  * Bundler wallet cluster, bundle ETH ratio, buyer distribution, dev holding vs. sold ratio, top 10 holder concentration, marketing boost speed (first/second boost delay), website tech stack, and Telegram/X handle naming patterns.
  * Used to confirm whether to hold the token or take early profit.

### 4. Rug Token Identification & Visual Highlighting
* Tokens flagged with an All-Time High $\le \$5,000$ are categorized as **Rugs**.
* Automatically highlighted in the leads table with a **40% Red Opacity background fill** (`rgba(239, 68, 68, 0.40)`) and an unmistakable `[🚨 RUG]` badge.
* Includes a quick filter toggle to view All Tokens, Hide Rugs, or view Rugs Only.

### 5. Interactive Web Dashboard (`index.html`)
* **Page 1 (Candidate Leads Table)**:
  * **Launch Date (UTC)** column with bidirectional sorting (newest/oldest).
  * Sortable by Score, Token Symbol, Inferred Team, and Launch Date.
  * Displays Confidence Badges (`HIGH_LEAD`, `PROBABLE_LEAD`, `WATCH`, `WEAK`), match scores, inferred team, and **Nearest Sibling Token** (e.g. `$GME`, `$INTEL`, `$PANTHER`, `$DEGENFLY`).
  * Direct browser links to **GMGN**, **DexScreener**, and **RobinScan Blockscout**.
* **Page 2 (Parameter Weights & Tolerance Buffer)**:
  * **3-Column Table**:
    * Column 1: Parameter / Forensic Habit (50 parameters tagged as `⚡ PRE-LAUNCH (SNIPE)` or `🛡️ POST-LAUNCH (HOLD)`).
    * Column 2: Weighted (Active Applied Value).
    * Column 3: Customize Weight (interactive input + slider).
  * Interactive **Numerical Habit Tolerance Buffer** slider (default $\pm 15\%$).
  * **[ 💾 Apply Custom Weights & Rescore ]**: Overwrites Column 2, saves to `localStorage`, and recalculates all candidate match scores client-side in real-time!

---

## 📂 Project Structure

```
├── index.html                           # Root dashboard (ready for Hostinger deployment)
├── team_leads_dashboard.html            # Standalone forensic leads dashboard
├── generate_html_dashboard.py           # Dashboard generator & data serializer
├── streamer.py                          # Durable dual-chain queue, RBH v4 scanner, live market collector
├── phase1.py                            # Phase 1 audit & Nearest Duplicate scoring engine
├── forensics.py                         # Master on-chain reverse engineering & opcode extractor
├── database.py                          # SQLite WAL-mode database layer
├── cluster.py                           # 5-pillar similarity functions & rarity scoring
├── branding_scraper.py                  # Favicon mmh3 hash & web hosting classifier
├── schema.sql                           # Normalized relational database schema
├── config.py                            # Chain RPCs & explorer API keys
├── test_phase1.py                       # Regression test suite
├── forensics.db                         # Seed database; production copy lives outside web root
├── phase1_fingerprint_report_candidates.csv # Current candidate leads with nearest sibling tokens
├── phase1_fingerprint_report.json       # Full audit JSON report
└── README.md                            # Documentation
```

---

## 🛠️ Quick Start (Local Setup)

### 1. Prerequisites
- Python 3.10+
- `pip install -r requirements.txt` (or `pip install requests pandas`)

### 2. View the Dashboard
Simply double-click `index.html` (or `team_leads_dashboard.html`) to open it directly in any browser—no web server required!

### 3. Run the Phase 1 Audit & Nearest Duplicate Engine
```bash
python phase1.py audit --db forensics.db --output phase1_fingerprint_report.json
```

### 4. Run the Live Streamer (DexScreener Boosts & Profiles)
```bash
python streamer.py --stream --interval 30
```

### 5. Backfill Historical Tokens (e.g. 10 Days)
```bash
python streamer.py --backfill 10
```

---

## 🌐 Deploying to Hostinger

The dashboard is static, but the collector is Python. Hostinger currently supports Python on **VPS hosting only**. The production deployment now includes a durable SQLite queue, resumable Uniswap v4 event scanner, retries/backoff, health heartbeat, systemd restart policy, stale-worker watchdog, atomic publication, and verified daily backups.

Do not deploy this entire repository into `public_html`. For static Web/Cloud hosting, upload only `index.html` plus `.htaccess`. For live collection, use the isolated VPS layout in [HOSTINGER_DEPLOYMENT.md](HOSTINGER_DEPLOYMENT.md).

```bash
# Must pass before enabling the service
python streamer.py --preflight

# True on-chain + market ten-day discovery; queued work resumes after interruption
python streamer.py --backfill 10 --max-jobs 5

# Continuous collector
python streamer.py --stream --interval 60 --max-jobs 5

# Operational state
python streamer.py --status
python streamer.py --health-check
```

Private/archive-capable Robinhood and Arc RPCs are required when both chains are enabled. Public endpoints remain useful for smoke tests but are rejected by the deployment preflight. Arc market discovery is currently DexScreener-based rather than an exhaustive on-chain pool index; see [ARC_SUPPORT.md](ARC_SUPPORT.md).

## 🧪 Testing

Run the full automated regression suite:
```bash
python -m unittest discover -v
```

---

## 📜 License
MIT License. Built for advanced on-chain memecoin forensics and developer attribution.
