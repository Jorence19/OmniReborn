#!/usr/bin/env python3
"""Interactive 1-minute configuration helper for OmniReborn on Vultr."""
import os
import shutil
import subprocess
import sys


def prompt_until_valid(prompt_text, validator, error_text):
    while True:
        try:
            value = input(prompt_text).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nSetup cancelled.")
            sys.exit(1)
        if validator(value):
            return value
        print(f"  [!] {error_text}\n")


def main():
    print("=" * 60)
    print(" OmniReborn Express Setup")
    print("=" * 60)
    print("Answer 4 quick prompts. Just right-click to paste each value.\n")

    # 1. Robinhood RPC
    robinhood_rpc = prompt_until_valid(
        "1. Paste your QuickNode Robinhood RPC URL:\n> ",
        lambda v: v.startswith("http") and "REPLACE_ME" not in v,
        "Please paste the full URL starting with https://",
    )

    # 2. Arc RPC
    arc_rpc = prompt_until_valid(
        "\n2. Paste your Alchemy Arc RPC URL:\n> ",
        lambda v: v.startswith("http") and "REPLACE_ME" not in v,
        "Please paste the full URL starting with https://",
    )

    # 3. Telegram Bot Token
    telegram_token = prompt_until_valid(
        "\n3. Paste your Telegram Bot Token (from @BotFather):\n> ",
        lambda v: ":" in v and len(v) > 20,
        "Please paste the full bot token (example: 123456789:ABCdefGhIJKlm...)",
    )

    # 4. Telegram Chat IDs
    def validate_chats(val):
        parts = [p.strip() for p in val.split(",") if p.strip()]
        if not parts:
            return False
        for p in parts:
            try:
                int(p)
            except ValueError:
                return False
        return True

    telegram_chats = prompt_until_valid(
        "\n4. Paste your Telegram Chat ID(s) (comma-separated for multiple, e.g. 123456789,-100987654321):\n> ",
        validate_chats,
        "Please enter numbers only, separated by comma. (Group IDs start with -)",
    )

    cleaned_chats = ",".join(p.strip() for p in telegram_chats.split(",") if p.strip())

    env_content = f"""# Production environment for OmniReborn Phase 1
CHAIN_ID=4663
ROBIN_ETHERSCAN_API_KEY=V3PFHFXJUWDH7AVUS26E9QUNTTTJ1QE5D4
ETHERSCAN_API_KEY=V3PFHFXJUWDH7AVUS26E9QUNTTTJ1QE5D4
BASESCAN_API_KEY=

# RPC Endpoints
ROBINHOOD_RPC_URL={robinhood_rpc}
ARC_RPC_URL={arc_rpc}
ENABLED_CHAIN_IDS=4663,5042
BASE_RPC_URL=https://mainnet.base.org
ETH_RPC_URL=https://cloudflare-eth.com
ROBINHOOD_POOL_MANAGER=0x8366a39cc670b4001a1121b8f6a443a643e40951

# Paths
FORENSICS_DB_PATH=/opt/omnireborn/data/forensics.db
RUNTIME_DIR=/opt/omnireborn/runtime
REPORT_OUTPUT_PATH=/opt/omnireborn/runtime/phase1_fingerprint_report.json
DASHBOARD_DATA_DIR=/opt/omnireborn/runtime
DASHBOARD_OUTPUT_DIR=/opt/omnireborn/runtime/site-build
DASHBOARD_API_URL=
API_SNAPSHOT_PATH=/opt/omnireborn/runtime/dashboard_candidates.json
API_ALLOWED_ORIGINS=
API_ALLOWED_HOSTS=localhost,127.0.0.1
API_MAX_SNAPSHOT_AGE_SECONDS=3600
BACKUP_DIR=/var/backups/omnireborn

# Collector & Telegram
COLLECTION_INTERVAL_SECONDS=300
TELEGRAM_BOT_TOKEN={telegram_token}
TELEGRAM_ALLOWED_CHAT_IDS={cleaned_chats}
TELEGRAM_TOP_LEADS=5
TELEGRAM_POLL_TIMEOUT_SECONDS=25
TELEGRAM_PUSH_ALERTS=false
TELEGRAM_ALERT_THRESHOLD=65
TELEGRAM_ALERT_EXISTING_ON_START=false
TELEGRAM_DASHBOARD_PATH=/opt/omnireborn/runtime/site-build/index.html
TELEGRAM_HEALTH_MAX_AGE_SECONDS=180
"""

    env_path = "/etc/omnireborn.env"
    print(f"\nWriting configuration to {env_path}...")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(env_content)

    try:
        shutil.chown(env_path, user="root", group="omnireborn")
        os.chmod(env_path, 0o640)
    except Exception:
        pass

    print("[OK] Configuration saved successfully!")
    print("\nStarting service preflight and activation...")
    app_dir = "/opt/omnireborn"
    installer = os.path.join(app_dir, "deploy", "install_vultr_backend.sh")
    result = subprocess.run(["bash", installer], cwd=app_dir)
    if result.returncode == 0:
        print("\n" + "=" * 60)
        print(" ALL DONE! OmniReborn is live!")
        print("=" * 60)
        print("Go to Telegram and send to your bot:")
        print("  /status    -> Check system health & uptime")
        print("  /leads     -> View top developer fingerprint leads")
        print("  /dashboard -> Receive the full HTML dashboard file")
        print("  /dbxlsx    -> Download the Excel database with risk colors")
        print("=" * 60)
    else:
        print(f"\nInstaller exited with code {result.returncode}. Review output above.")


if __name__ == "__main__":
    main()
