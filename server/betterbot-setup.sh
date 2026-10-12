#!/usr/bin/env bash
# One-time setup on the server for BetterPicks' Discord bot (BetterBot), practice picks only:
#   1. betterbot.service     keeps BetterBot showing online 24/7
#   2. betterpicks.service   scans Kalshi, PrizePicks and the sportsbooks every 3 minutes and posts locks
# Usage (paste into the server console as root):
#   bash <(curl -fsSL https://raw.githubusercontent.com/RY00N/archie-bot/claude/project-thread-8sdhk6/server/betterbot-setup.sh)
set -euo pipefail
BRANCH="${1:-claude/project-thread-8sdhk6}"
apt-get install -y -qq git python3 >/dev/null 2>&1 || true
id archie >/dev/null 2>&1 || useradd -m -s /bin/bash archie
if [ -d /home/archie/bp-repo/.git ]; then
  sudo -u archie git -C /home/archie/bp-repo pull -q
else
  sudo -u archie git clone -q -b "$BRANCH" --single-branch https://github.com/RY00N/archie-bot.git /home/archie/bp-repo
fi
if [ ! -f /etc/betterbot.env ]; then
  echo "Paste your Discord bot details (they stay on this server only; tokens are hidden as you type)."
  read -rsp "DISCORD_BOT_TOKEN: " T; echo
  read -rp  "DISCORD_CHANNEL_ID: " C
  echo "Optional: a GitHub token for this repo (Contents read/write) so server picks sync back to the project. Press Enter to skip."
  read -rsp "GITHUB_TOKEN: " G; echo
  { printf 'DISCORD_BOT_TOKEN=%s\nDISCORD_CHANNEL_ID=%s\n' "$T" "$C"; if [ -n "$G" ]; then printf 'GITHUB_TOKEN=%s\n' "$G"; fi; } > /etc/betterbot.env
  chmod 600 /etc/betterbot.env
fi
unit() {  # name, description, command
cat > "/etc/systemd/system/$1.service" <<UNIT
[Unit]
Description=$2
After=network-online.target
[Service]
User=archie
EnvironmentFile=/etc/betterbot.env
ExecStart=$3
Restart=always
RestartSec=15
[Install]
WantedBy=multi-user.target
UNIT
}
unit betterbot "BetterBot Discord presence (shows the bot online)" "/usr/bin/python3 -I /home/archie/bp-repo/betterbot_presence.py"
unit betterpicks "BetterPicks lock scanner (practice picks, every 3 minutes)" "/usr/bin/python3 -I /home/archie/bp-repo/betterpicks/scan_server.py"
systemctl daemon-reload && systemctl enable -q --now betterbot betterpicks
systemctl restart betterbot betterpicks
sleep 8 && journalctl -u betterbot -u betterpicks --no-pager -n 6
echo "Done. BetterBot should show online, and locks will post to Discord as they appear."
