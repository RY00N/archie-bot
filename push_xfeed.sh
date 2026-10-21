#!/usr/bin/env bash
# Cloud-only: read Ryan's followed X accounts (cookies auth_token/ct0 from the cloud env, never committed) and push
# feeds/qqq-x.json to the archie-bot repo; the droplet git-pulls every 5 minutes and intel.py picks it up.
set -e
R=$HOME/archie-push
[ -d "$R/.git" ] || git clone -q https://github.com/RY00N/archie-bot "$R"
cd "$R" && git pull -q --ff-only origin main
python3 xfeed.py
git add feeds/qqq-x.json
if git diff --cached --quiet; then echo "x feed unchanged"; exit 0; fi
git -c user.name="Archie (Claude)" -c user.email="noreply@anthropic.com" commit -q -m "Archie: X feed $(date -u +%FT%H:%MZ)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012xExvG5RsZrejTMwyrhgco"
for i in 1 2 3; do git push -q origin main && echo "pushed x feed" && exit 0; git pull -q --rebase origin main; done
exit 1
