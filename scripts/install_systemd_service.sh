#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-dexscreener-lead-bot}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="${APP_USER:-${SUDO_USER:-$(id -un)}}"
PYTHON_BIN="${PYTHON_BIN:-$APP_DIR/.venv/bin/python}"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root (recommended): sudo ./scripts/install_systemd_service.sh"
    exit 1
fi

if ! id -u "$APP_USER" >/dev/null 2>&1; then
    echo "Service user does not exist: $APP_USER"
    exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Python executable not found or not executable: $PYTHON_BIN"
    echo "Create your venv first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR/data"

cat >"$SERVICE_PATH" <<EOF
[Unit]
Description=Dexscreener Lead Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$PYTHON_BIN -m src.main
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=10
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

echo "Installed and started: $SERVICE_NAME"
echo "Status: sudo systemctl status $SERVICE_NAME"
echo "Logs:   sudo journalctl -u $SERVICE_NAME -f"
