#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR=/opt/omnireborn
APP_USER=omnireborn
WEB_DIR=/var/www/omnireborn
BACKUP_DIR=/var/backups/omnireborn

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/install_hostinger_vps.sh" >&2
  exit 1
fi
if [[ "$(pwd)" != "$APP_DIR" ]]; then
  echo "Clone the repository to $APP_DIR and run this script there." >&2
  exit 1
fi
id "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
install -d -o "$APP_USER" -g "$APP_USER" -m 0750 "$APP_DIR/data" "$APP_DIR/runtime" "$BACKUP_DIR"
install -d -o "$APP_USER" -g www-data -m 2770 "$WEB_DIR"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
if [[ ! -f "$APP_DIR/data/forensics.db" && -f "$APP_DIR/forensics.db" ]]; then
  install -o "$APP_USER" -g "$APP_USER" -m 0640 "$APP_DIR/forensics.db" "$APP_DIR/data/forensics.db"
fi
if [[ ! -f /etc/omnireborn.env ]]; then
  install -o root -g "$APP_USER" -m 0640 "$APP_DIR/.env.example" /etc/omnireborn.env
  echo "Created /etc/omnireborn.env. Fill the explorer key and production RPC, then rerun." >&2
  exit 2
fi
install -o root -g root -m 0644 "$APP_DIR/deploy/omnireborn.service" /etc/systemd/system/
install -o root -g root -m 0644 "$APP_DIR/deploy/omnireborn-watchdog.service" /etc/systemd/system/
install -o root -g root -m 0644 "$APP_DIR/deploy/omnireborn-watchdog.timer" /etc/systemd/system/
install -o root -g root -m 0644 "$APP_DIR/deploy/omnireborn-backup.service" /etc/systemd/system/
install -o root -g root -m 0644 "$APP_DIR/deploy/omnireborn-backup.timer" /etc/systemd/system/
chown -R "$APP_USER:$APP_USER" "$APP_DIR/data" "$APP_DIR/runtime" "$BACKUP_DIR"
systemctl daemon-reload
sudo -u "$APP_USER" /bin/bash -c 'set -a; source /etc/omnireborn.env; set +a; exec /opt/omnireborn/.venv/bin/python /opt/omnireborn/streamer.py --preflight'
systemctl enable --now omnireborn.service omnireborn-watchdog.timer omnireborn-backup.timer
echo "Installed. Check: systemctl status omnireborn && journalctl -u omnireborn -f"
