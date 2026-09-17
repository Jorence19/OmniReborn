# ⚡ OmniReborn: Robinhood Meme Coin Forensics & Developer Team Tracker

> **Chain ID: 4663 (Robinhood Chain)** • Also supports **Base (8453)** & **Ethereum Mainnet (1)**  
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

### 3. Interactive Web Dashboard (`index.html`)
* **Page 1 (Candidate Leads Table)**:
  * Sortable, filterable table of all 140 candidate leads.
  * Displays Confidence Badges (`HIGH_LEAD`, `PROBABLE_LEAD`, `WATCH`, `WEAK`), match scores, inferred team, and **Nearest Sibling Token** (e.g. `$GME`, `$INTEL`, `$PANTHER`, `$DEGENFLY`).
  * Direct browser links to **GMGN**, **DexScreener**, and **RobinScan Blockscout**.
* **Page 2 (Parameter Weights & Tolerance Buffer)**:
  * **3-Column Table**:
    * Column 1: Parameter / Forensic Habit (50 parameters across 6 categories).
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
├── streamer.py                          # Live DexScreener poller & 10-day backfill CLI
├── phase1.py                            # Phase 1 audit & Nearest Duplicate scoring engine
├── forensics.py                         # Master on-chain reverse engineering & opcode extractor
├── database.py                          # SQLite WAL-mode database layer
├── cluster.py                           # 5-pillar similarity functions & rarity scoring
├── branding_scraper.py                  # Favicon mmh3 hash & web hosting classifier
├── schema.sql                           # Normalized relational database schema
├── config.py                            # Chain RPCs & explorer API keys
├── test_phase1.py                       # Regression test suite
├── forensics.db                         # Pre-populated SQLite database with 248 tokens
├── phase1_fingerprint_report_candidates.csv # 140 candidate leads with nearest sibling tokens
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

## 🌐 Deploying to Hostinger (Web Hosting & VPS)

### Method 1: Hostinger Shared Web Hosting (2-Minute Setup)
Because `index.html` is a self-contained Single-Page Application (SPA) with zero runtime dependencies:

1. Log into your **Hostinger hPanel**.
2. Select your website domain or create a subdomain (e.g. `rh-tracker.yourdomain.com`).
3. **Using Hostinger Git Deployment** (Automatic):
   - In hPanel, go to **Git**.
   - Repository URL: `https://github.com/Jorence19/OmniReborn.git`
   - Branch: `main`
   - Install Path: `public_html`
   - Click **Create**. Every time you push to GitHub, Hostinger automatically updates your live site!
4. **Using File Manager** (Manual):
   - Open **File Manager** $\rightarrow$ `public_html/`.
   - Upload `index.html`.
   - Your site is immediately live with free SSL!

### Method 2: Hostinger VPS (24/7 Automated Streaming & Live Updates)
To run the continuous streaming listener and auto-refresh the dashboard around the clock:

1. SSH into your Hostinger Ubuntu VPS:
   ```bash
   git clone https://github.com/Jorence19/OmniReborn.git
   cd OmniReborn
   pip install requests pandas
   ```
2. Copy `index.html` to your Nginx/Apache web root:
   ```bash
   cp index.html /var/www/html/
   ```
3. Run the streamer in background using `systemd` or `tmux`:
   ```bash
   python streamer.py --stream --interval 30
   ```

---

## 🧪 Testing

Run the full automated regression suite:
```bash
python -m unittest test_phase1.py
```

---

## 📜 License
MIT License. Built for advanced on-chain memecoin forensics and developer attribution.
