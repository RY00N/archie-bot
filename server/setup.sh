#!/usr/bin/env bash
# One-time setup for Archie's always-on bot on a fresh Ubuntu server.
# Usage (paste into the server console): bash <(curl -fsSL https://raw.githubusercontent.com/RY00N/archie-bot/main/server/setup.sh) RY00N/archie-bot
set -euo pipefail
REPO="${1:?give OWNER/REPO}"
apt-get update -qq && apt-get install -y -qq git python3 >/dev/null
id archie >/dev/null 2>&1 || useradd -m -s /bin/bash archie
sudo -u archie git clone -q "https://github.com/$REPO.git" /home/archie/bot 2>/dev/null || sudo -u archie git -C /home/archie/bot pull -q
if [ ! -f /etc/archie.env ]; then
  echo "Paste your Alpaca PAPER keys (they stay on this server only)."
  read -rp "APCA_API_KEY_ID: " K; read -rsp "APCA_API_SECRET_KEY: " S; echo
  printf 'APCA_API_KEY_ID=%s\nAPCA_API_SECRET_KEY=%s\n' "$K" "$S" > /etc/archie.env
  chmod 600 /etc/archie.env
fi
cat > /etc/systemd/system/archie.service <<UNIT
[Unit]
Description=Archie live trading bot (paper)
After=network-online.target
[Service]
User=archie
WorkingDirectory=/home/archie/bot
EnvironmentFile=/etc/archie.env
ExecStart=/usr/bin/python3 /home/archie/bot/archie_live.py
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable --now archie
sleep 5 && systemctl --no-pager status archie | head -5
echo "Archie is running. Live log: journalctl -u archie -f"
