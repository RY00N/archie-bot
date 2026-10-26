#!/usr/bin/env bash
# One-time: DM old "BetterPicks (old)" members about the new server, slowly, from the throwaway invite bot.
# Paste into the server console as root:
#   bash <(curl -fsSL https://raw.githubusercontent.com/RY00N/archie-bot/claude/project-thread-lwpe8v/server/invite-dm-setup.sh)
# Pause: touch /home/archie/invite-dm/STOP     Remove later: systemctl disable --now invite-dm
set -euo pipefail
mkdir -p /home/archie/invite-dm
curl -fsSL https://raw.githubusercontent.com/RY00N/archie-bot/claude/project-thread-lwpe8v/invite/invite_dm.py -o /home/archie/invite-dm/invite_dm.py
rm -f /home/archie/invite-dm/STOPPED   # re-running after a stop resumes from sent.json
chown -R archie:archie /home/archie/invite-dm
if [ ! -f /etc/invite-dm.env ]; then
  echo "Paste the INVITE bot's token (BetterPicksAI, not BetterBot). It stays on this server; hidden as you type."
  read -rsp "INVITE_BOT_TOKEN: " T; echo
  printf 'INVITE_BOT_TOKEN=%s\n' "$T" > /etc/invite-dm.env; chmod 600 /etc/invite-dm.env
fi
cat > /etc/systemd/system/invite-dm.service <<UNIT
[Unit]
Description=BetterPicks one-time invite DMs (slow, stops on any Discord pushback)
After=network-online.target
[Service]
User=archie
EnvironmentFile=/etc/betterbot.env
EnvironmentFile=/etc/invite-dm.env
ExecStart=/usr/bin/python3 -I /home/archie/invite-dm/invite_dm.py
Restart=on-failure
RestartSec=300
UNIT
systemctl daemon-reload
sudo -u archie env $(cat /etc/betterbot.env /etc/invite-dm.env | xargs) python3 -I /home/archie/invite-dm/invite_dm.py --dry-run | head -3
systemctl enable -q invite-dm && systemctl restart invite-dm
sleep 5 && journalctl -u invite-dm --no-pager -n 5
echo "Done. One DM about every 90 seconds, any hour."
