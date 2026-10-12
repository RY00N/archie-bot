#!/usr/bin/env bash
# One-time setup so BetterPicks' Discord bot (BetterBot) shows online 24/7 on this server.
# Usage (paste into the server console as root):
#   bash <(curl -fsSL https://raw.githubusercontent.com/RY00N/archie-bot/claude/project-thread-8sdhk6/server/betterbot-setup.sh)
set -euo pipefail
BRANCH="${1:-claude/project-thread-8sdhk6}"
id archie >/dev/null 2>&1 || useradd -m -s /bin/bash archie
mkdir -p /home/archie/betterbot
curl -fsSL "https://raw.githubusercontent.com/RY00N/archie-bot/$BRANCH/betterbot_presence.py" -o /home/archie/betterbot/betterbot_presence.py
chown -R archie:archie /home/archie/betterbot
if [ ! -f /etc/betterbot.env ]; then
  echo "Paste your Discord bot token (it stays on this server only; typing is hidden)."
  read -rsp "DISCORD_BOT_TOKEN: " T; echo
  printf 'DISCORD_BOT_TOKEN=%s\n' "$T" > /etc/betterbot.env
  chmod 600 /etc/betterbot.env
fi
cat > /etc/systemd/system/betterbot.service <<UNIT
[Unit]
Description=BetterBot Discord presence (shows the bot online)
After=network-online.target
[Service]
User=archie
EnvironmentFile=/etc/betterbot.env
ExecStart=/usr/bin/python3 -I /home/archie/betterbot/betterbot_presence.py
Restart=always
RestartSec=15
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable --now betterbot
sleep 6 && journalctl -u betterbot --no-pager -n 3
echo "BetterBot should now show online in Discord."
