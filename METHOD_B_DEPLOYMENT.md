# Method B deployment: Hostinger frontend + Vultr backend

## Current gate

The code is ready for the split architecture after the required values below are supplied. Deployment is intentionally blocked while example domains or public Robinhood/Arc RPCs remain configured.

Required before launch:

- A Hostinger HTTPS origin, such as `https://tracker.your-real-domain.com`.
- A Vultr API hostname, such as `api.your-real-domain.com`, with an A record to the Vultr IPv4 address.
- Private/archive-capable Robinhood Chain and Arc RPCs.
- A Robinhood explorer key in `ROBIN_ETHERSCAN_API_KEY`. Arc uses the public ArcScan-compatible API and does not require an Etherscan key.
- `ENABLED_CHAIN_IDS=4663,5042`.
- Ubuntu packages: `git python3 python3-venv nginx curl certbot python3-certbot-nginx`.
- Vultr firewall rules exposing 22 only from your administration IP and 80/443 publicly. Never expose port 8000.

## Data flow

```text
collector -> SQLite WAL -> atomic candidate snapshot -> FastAPI on 127.0.0.1:8000
                                                        |
Hostinger index.html <- HTTPS JSON/CORS <- Nginx 443 <-+
```

The API is deliberately read-only. It does not expose SQLite, CSV files, reports, environment variables, stack traces, or mutation endpoints. The Hostinger page keeps an embedded snapshot and automatically falls back to it if Vultr is unreachable.

## 1. Prepare Vultr

On a fresh Ubuntu server:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv nginx curl certbot python3-certbot-nginx
sudo git clone https://github.com/Jorence19/OmniReborn.git /opt/omnireborn
cd /opt/omnireborn
sudo bash deploy/install_vultr_method_b.sh
```

The first run creates `/etc/omnireborn.env` and exits. Edit it:

```bash
sudo nano /etc/omnireborn.env
```

Set real values for:

```dotenv
ROBIN_ETHERSCAN_API_KEY=...
ETHERSCAN_API_KEY=...
ARC_EXPLORER_API_URL=https://api.arc-scan.org/api
ROBINHOOD_RPC_URL=https://YOUR_PRIVATE_ROBINHOOD_ARCHIVE_RPC
ARC_RPC_URL=https://YOUR_PRIVATE_ARC_ARCHIVE_RPC
ENABLED_CHAIN_IDS=4663,5042
FORENSICS_DB_PATH=/opt/omnireborn/data/forensics.db
RUNTIME_DIR=/opt/omnireborn/runtime
REPORT_OUTPUT_PATH=/opt/omnireborn/runtime/phase1_fingerprint_report.json
DASHBOARD_DATA_DIR=/opt/omnireborn/runtime
DASHBOARD_OUTPUT_DIR=/var/www/omnireborn
API_SNAPSHOT_PATH=/opt/omnireborn/runtime/dashboard_candidates.json
API_ALLOWED_ORIGINS=https://YOUR_HOSTINGER_FRONTEND_DOMAIN
API_ALLOWED_HOSTS=api.YOUR_REAL_DOMAIN,localhost,127.0.0.1
DASHBOARD_API_URL=https://api.YOUR_REAL_DOMAIN/api/candidates
```

No trailing slash is allowed in `API_ALLOWED_ORIGINS`. CORS is exact-origin; list multiple origins with commas only when each is trusted.

Rerun the installer:

```bash
cd /opt/omnireborn
sudo bash deploy/install_vultr_method_b.sh
```

## 2. Put Nginx and TLS in front of the API

```bash
sudo cp deploy/nginx-omnireborn-api.conf /etc/nginx/sites-available/omnireborn-api
sudo sed -i 's/api.example.com/api.YOUR_REAL_DOMAIN/g' /etc/nginx/sites-available/omnireborn-api
sudo ln -s /etc/nginx/sites-available/omnireborn-api /etc/nginx/sites-enabled/omnireborn-api
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d api.YOUR_REAL_DOMAIN
```

The API process binds only to loopback. Nginx enforces a request/body limit, connection limit, and GET/HEAD/OPTIONS-only boundary. Systemd restarts crashes; the two-minute watchdog restarts a nonresponsive API.

## 3. Build the Hostinger frontend

Build locally with the final HTTPS endpoint embedded:

PowerShell:

```powershell
$env:DASHBOARD_API_URL = "https://api.YOUR_REAL_DOMAIN/api/candidates"
$env:API_SNAPSHOT_PATH = "$PWD/runtime/dashboard_candidates.json"
python generate_html_dashboard.py
```

Upload only these two files to Hostinger `public_html`:

- `index.html`
- `.htaccess`

Do not upload the database, snapshot JSON, reports, CSVs, Python, `.env`, Git files, or service files. The CSP allows HTTPS API connections but still blocks third-party scripts, framing, forms, and directory/file disclosure.

## 4. Deployment gates and smoke tests

On Vultr:

```bash
sudo -u omnireborn /opt/omnireborn/.venv/bin/python /opt/omnireborn/streamer.py --preflight
sudo -u omnireborn /opt/omnireborn/.venv/bin/python /opt/omnireborn/api_server.py --check
sudo systemctl status omnireborn omnireborn-api
sudo systemctl list-timers 'omnireborn*'
curl --fail https://api.YOUR_REAL_DOMAIN/api/live
curl --fail https://api.YOUR_REAL_DOMAIN/api/health
curl --fail -H 'Origin: https://YOUR_HOSTINGER_FRONTEND_DOMAIN' -D - https://api.YOUR_REAL_DOMAIN/api/candidates
```

The preflight must report `rpc_chain_4663=4663`, `rpc_chain_5042=5042`, and both production RPC checks as true. The last response must contain the exact `Access-Control-Allow-Origin`, an `ETag`, and a JSON `candidates` array. Then open the Hostinger page and confirm its source label changes from “Embedded safe snapshot” to “Live API”.

If the API fails, the page stays usable from its embedded snapshot. Failed collector jobs remain in the durable retry queue; stale snapshot state appears at `/api/health`; SQLite and snapshot writes are atomic; collector and API processes restart independently.

## Update procedure

```bash
cd /opt/omnireborn
sudo git pull --ff-only
sudo bash deploy/install_vultr_method_b.sh
sudo nginx -t
sudo systemctl restart omnireborn omnireborn-api
```

Rebuild and re-upload `index.html` only when the frontend code or API URL changes. Candidate data itself updates through the API without another Hostinger upload.
