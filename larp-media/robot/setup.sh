#!/bin/bash
# One-time droplet setup for the @audemarai TikTok robot (Reza OK'd 2026-10-24). Run as root in the droplet console:
#   curl -fsSL https://raw.githubusercontent.com/RY00N/archie-bot/claude/project-thread-npp23h/larp-media/robot/setup.sh | bash
set -e
DIR=/opt/larp-robot
if [ ! -d $DIR/.git ]; then
  git clone -q --single-branch -b claude/project-thread-npp23h https://github.com/RY00N/archie-bot.git $DIR
else
  git -C $DIR pull -q --ff-only
fi
apt-get install -y -qq python3-venv >/dev/null
python3 -m venv /opt/larp-robot-venv
/opt/larp-robot-venv/bin/pip install -q playwright
/opt/larp-robot-venv/bin/python -m playwright install --with-deps chromium >/dev/null
if ! grep -q TIKTOK_SESSIONID /etc/larp-robot.env 2>/dev/null; then
  read -rsp "Paste the TikTok sessionid for @audemarai, then press Enter: " SID </dev/tty; echo
  (umask 077; echo "TIKTOK_SESSIONID=$SID" > /etc/larp-robot.env)
fi
cat > /etc/systemd/system/larp-robot.service <<UNIT
[Unit]
Description=TikTok larp robot tick (@audemarai)
[Service]
Type=oneshot
EnvironmentFile=/etc/larp-robot.env
Nice=10
TimeoutStartSec=900
ExecStart=/opt/larp-robot-venv/bin/python $DIR/larp-media/robot/tiktok_robot.py tick
UNIT
cat > /etc/systemd/system/larp-robot.timer <<UNIT
[Unit]
Description=Run the TikTok larp robot every 5 minutes
[Timer]
OnCalendar=*:0/5
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now larp-robot.timer
set -a; . /etc/larp-robot.env; set +a
/opt/larp-robot-venv/bin/python $DIR/larp-media/robot/tiktok_robot.py test
echo "Done. Robot checks every 5 min and posts at 12:00, 3:00, 6:30 PT. Logs: journalctl -u larp-robot -n 30"
