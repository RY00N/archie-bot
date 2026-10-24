#!/bin/bash
# One-time upgrade (Reza 2026-10-24: "i want u to do it"): lets the project run the TikTok robot and read its logs without the console.
#   curl -fsSL https://raw.githubusercontent.com/RY00N/archie-bot/claude/project-thread-npp23h/larp-media/robot/setup2.sh | bash
# - real browser window on a virtual screen (xvfb) + silent sound device (pulseaudio) for TikTok's editor
# - robot runs one-off jobs from larp-media/robot/jobs.json (only "post N" / "test") on its 5-min tick
# - robot pushes its text log (never the cookie) to branch larp-robot-log with a GitHub token Reza pastes here (kept only in /etc/larp-robot.env)
DIR=/opt/larp-robot
apt-get install -y -qq xvfb pulseaudio >/dev/null 2>&1 && echo "xvfb + pulseaudio ok"
git -C $DIR pull -q --ff-only && echo "code updated"
if ! grep -q '^GH_LOG_TOKEN=' /etc/larp-robot.env; then
  read -rsp "Paste the GitHub token for the robot log (archie-bot, Contents read+write), then Enter: " T </dev/tty; echo
  [ -n "$T" ] && (umask 077; echo "GH_LOG_TOKEN=$T" >> /etc/larp-robot.env) && echo "token saved"
fi
sed -i "s#^ExecStart=.*#ExecStart=/usr/bin/xvfb-run -a /opt/larp-robot-venv/bin/python $DIR/larp-media/robot/tiktok_robot.py tick#" /etc/systemd/system/larp-robot.service
grep -q '^Environment=HOME' /etc/systemd/system/larp-robot.service || sed -i '/^\[Service\]/a Environment=HOME=/root' /etc/systemd/system/larp-robot.service
systemctl daemon-reload
systemctl enable --now larp-robot.timer >/dev/null 2>&1
echo "timer: $(systemctl is-active larp-robot.timer)"
systemctl start --no-block larp-robot.service
echo "Done. The robot is running its first job now (d3 to TikTok, ~5-8 min). You can close the console; Claude reads the results."
