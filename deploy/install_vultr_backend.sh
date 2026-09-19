#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR=/opt/omnireborn
APP_USER=omnireborn
BACKUP_DIR=/var/backups/omnireborn

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/install_vultr_backend.sh" >&2
  exit 1
fi
if [[ "$(pwd)" != "$APP_DIR" ]]; then
  echo "Clone the repository to $APP_DIR and run this script there." >&2
  exit 1
fi

id "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
install -d -o "$APP_USER" -g "$APP_USER" -m 0750 "$APP_DIR/data" "$APP_DIR/runtime" "$APP_DIR/runtime/site-build" "$BACKUP_DIR"

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ ! -f "$APP_DIR/data/forensics.db" && -f "$APP_DIR/forensics.db" ]]; then
  install -o "$APP_USER" -g "$APP_USER" -m 0640 "$APP_DIR/forensics.db" "$APP_DIR/data/forensics.db"
fi
if [[ ! -f /etc/omnireborn.env ]]; then
  install -o root -g "$APP_USER" -m 0640 "$APP_DIR/.env.example" /etc/omnireborn.env
  echo "Created /etc/omnireborn.env. Add private RPCs and Telegram values, then rerun." >&2
  exit 2
fi

units=(
  omnireborn.service
  omnireborn-watchdog.service
  omnireborn-watchdog.timer
  omnireborn-backup.service
  omnireborn-backup.timer
  omnireborn-telegram.service
  omnireborn-telegram-watchdog.service
  omnireborn-telegram-watchdog.timer
)
for unit in "${units[@]}"; do
  install -o root -g root -m 0644 "$APP_DIR/deploy/$unit" "/etc/systemd/system/$unit"
done

chown -R "$APP_USER:$APP_USER" "$APP_DIR/data" "$APP_DIR/runtime" "$BACKUP_DIR"
systemctl daemon-reload

as_app() {
  sudo -u "$APP_USER" /bin/bash -c "set -a; source /etc/omnireborn.env; set +a; $1"
}

as_app 'exec /opt/omnireborn/.venv/bin/python /opt/omnireborn/phase1.py audit --db "$FORENSICS_DB_PATH" --output "$REPORT_OUTPUT_PATH"'
as_app 'exec /opt/omnireborn/.venv/bin/python /opt/omnireborn/generate_html_dashboard.py'
as_app 'exec /opt/omnireborn/.venv/bin/python /opt/omnireborn/streamer.py --preflight'
as_app 'exec /opt/omnireborn/.venv/bin/python /opt/omnireborn/telegram_bot.py --check'

enabled_units=(
  omnireborn.service
  omnireborn-telegram.service
  omnireborn-watchdog.timer
  omnireborn-telegram-watchdog.timer
  omnireborn-backup.timer
)
systemctl enable --now "${enabled_units[@]}"

echo "Pure Vultr + Telegram Phase 1 deployment installed."
echo "Collector cadence: 10 minutes. Phase 2 push alerts remain disabled by default."
