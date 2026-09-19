# Hostinger production deployment

## Readiness verdict

The static dashboard can run on Hostinger Web/Cloud hosting. The Python collector requires a Hostinger VPS: Hostinger currently documents Python as VPS-only. Do not deploy the full repository under `public_html`; it contains a database, source datasets, and operational code. Publish only `index.html` and the supplied `.htaccess`, or use the VPS layout below.

Production collection requires private/archive-capable RPCs for both enabled chains. `python streamer.py --preflight` deliberately fails while the public Robinhood or Arc endpoint is configured, even though both public endpoints are useful for smoke tests.

## What the safety layer guarantees

- Every discovered address is durably queued before enrichment.
- `(address, chain)` makes queue insertion idempotent.
- A worker lease prevents duplicate processing and is automatically reclaimed after a crash.
- Failed work retries up to eight times with exponential backoff, then moves to `dead` for inspection.
- `--retry-dead` explicitly requeues dead-letter jobs after the cause is fixed.
- Uniswap v4 scanning uses a persisted block cursor, 20-block finality delay, 50-block overlap, and unique `(chain, transaction, log index)` provenance.
- RPC log chunks shrink automatically when a provider rejects a large range.
- SQLite uses WAL, a 30-second busy timeout, foreign keys, and integrity checks.
- Dashboard HTML and JSON/CSV reports are replaced atomically.
- A process lock prevents two collectors from writing the same database.
- SIGTERM/SIGINT produces a graceful stop; systemd restarts failures.
- A five-minute watchdog restarts a hung/stale service.
- Daily backups use SQLite's online backup API, verify `integrity_check`, write SHA-256 checksums, and keep 14 days.
- Live discoveries set migrated/DEX-paid gates but never automatically become qualified training anchors.
- Chain ID is preserved from queue through enrichment, reporting, API validation, and dashboard links.
- Native-denominated Robinhood ETH values and Arc USDC values are never treated as comparable habits.
- A same-address/different-chain registry collision fails closed instead of silently overwriting forensic evidence.

## Coverage boundary

The Robinhood on-chain backfill scans every Uniswap v4 `Initialize` event emitted by the official Robinhood Chain PoolManager. This is exhaustive only for pools created through that PoolManager, not for contracts that never use it.

Arc is currently discovered through DexScreener boost/profile/ads feeds and dated search, then enriched through Etherscan V2 and Arc RPC. This is production-safe and resumable, but it is not an exhaustive Arc token/pool indexer. Do not describe it as retrieving every Arc contract or every Arc token. Exhaustive Arc coverage requires a verified Arc factory/PoolManager event source or a full contract-creation indexer.

A rare identical 20-byte contract address appearing on both chains is rejected and dead-lettered because the legacy profile tables are address-keyed. This prevents corruption but means literal all-address coverage requires a future composite-key schema migration.

Official PoolManager for chain 4663: `0x8366a39cc670b4001a1121b8f6a443a643e40951`.

## VPS installation

Use Ubuntu on a Hostinger VPS. Keep the application outside the public web root.

```bash
sudo apt update
sudo apt install -y git python3 python3-venv nginx
sudo git clone https://github.com/Jorence19/OmniReborn.git /opt/omnireborn
cd /opt/omnireborn
sudo bash deploy/install_hostinger_vps.sh
```

The first installer run creates `/etc/omnireborn.env` and stops. Edit it:

```bash
sudo nano /etc/omnireborn.env
```

Required values:

- `ROBIN_ETHERSCAN_API_KEY`
- `ROBINHOOD_RPC_URL` pointing to a private/archive-capable endpoint
- `ARC_RPC_URL` pointing to a private/archive-capable Arc endpoint
- `ENABLED_CHAIN_IDS=4663,5042`
- `ETHERSCAN_API_KEY`, or a unified Etherscan V2 key in `ROBIN_ETHERSCAN_API_KEY`
- deployment paths from `.env.example`

Rerun the installer, update the Nginx `server_name`, enable the site, and obtain SSL:

```bash
cd /opt/omnireborn
sudo bash deploy/install_hostinger_vps.sh
sudo cp deploy/nginx-omnireborn.conf /etc/nginx/sites-available/omnireborn
sudo sed -i 's/tracker.example.com/YOUR_DOMAIN/' /etc/nginx/sites-available/omnireborn
sudo ln -s /etc/nginx/sites-available/omnireborn /etc/nginx/sites-enabled/omnireborn
sudo nginx -t
sudo systemctl reload nginx
```

Run the initial ten-day discovery. It processes a bounded batch immediately; the service drains the remaining durable queue safely:

```bash
sudo -u omnireborn /opt/omnireborn/.venv/bin/python /opt/omnireborn/streamer.py --backfill 10 --max-jobs 5
sudo systemctl restart omnireborn
```

## Operations

```bash
# Deployment gate
sudo -u omnireborn /opt/omnireborn/.venv/bin/python /opt/omnireborn/streamer.py --preflight

# Queue state
sudo -u omnireborn /opt/omnireborn/.venv/bin/python /opt/omnireborn/streamer.py --status

# Service and logs
sudo systemctl status omnireborn
sudo journalctl -u omnireborn -f

# Heartbeat
sudo -u omnireborn /opt/omnireborn/.venv/bin/python /opt/omnireborn/streamer.py --health-check

# Requeue dead jobs after correcting their cause
sudo -u omnireborn /opt/omnireborn/.venv/bin/python /opt/omnireborn/streamer.py --retry-dead --drain --max-jobs 10

# Verified manual backup
sudo -u omnireborn /opt/omnireborn/.venv/bin/python /opt/omnireborn/backup_database.py --backup-dir /var/backups/omnireborn
```

Restore is intentionally guarded and creates a pre-restore backup:

```bash
sudo systemctl stop omnireborn
sudo -u omnireborn /opt/omnireborn/.venv/bin/python /opt/omnireborn/backup_database.py   --db /opt/omnireborn/data/forensics.db   --restore /var/backups/omnireborn/forensics-TIMESTAMP.db   --confirm-restore
sudo systemctl start omnireborn
```

For maintenance, disable the watchdog first so it does not undo an intentional stop:

```bash
sudo systemctl stop omnireborn-watchdog.timer
# maintenance
sudo systemctl start omnireborn-watchdog.timer
```

## Shared Web/Cloud hosting

Shared hosting can serve the static dashboard only. Upload `index.html` and `.htaccess`; do not upload `.env`, `forensics.db`, CSV/JSON reports, Python files, or `.git`. Hostinger hPanel cron jobs run in UTC, but Hostinger's current platform documentation says Python itself is supported only on VPS, so cron is not a substitute for this collector on Web/Cloud hosting.

## References

- Hostinger language support: https://www.hostinger.com/support/which-programming-languages-and-frameworks-are-supported-at-hostinger/
- Hostinger cron jobs: https://www.hostinger.com/support/1583465-how-to-set-up-a-cron-job-at-hostinger/
- Robinhood Chain endpoints: https://docs.robinhood.com/chain/connecting/
- Arc network parameters: https://docs.arc.io/arc/references/connect-to-arc
- Arc node providers: https://docs.arc.io/arc/tools/node-providers
- ArcScan and Etherscan API: https://info.etherscan.com/what-is-arcscan/
- Uniswap v4 deployments: https://developers.uniswap.org/docs/protocols/v4/deployments
- DexScreener API limits and endpoints: https://docs.dexscreener.com/api/reference
