#!/bin/bash
# BetterPicks fast loop (Ryan 2026-10-19 06:06Z: refresh every 4 min, not hourly). Runs in the BetterPicks thread session.
#  - every 5s: check branch betterpicks-ask for unanswered /bp questions
#  - every 4 min (in the background): price feed (books.py props + feed.py, push), merge server picks, grade
#  - every 60s (in the background): live wire (Ryan 2026-10-26 "everything live"): injuries, in-game exits, trades,
#    lineups, suspensions, price moves on open picks -> live_wire.json
# Exits (waking Claude) on a flagged premium-chat message (CHAT FLAG), on a new /bp question, on new live-wire items (LIVE ALERT; relay with live_wire.py --unposted), when the city needs a sync (picks merged or graded), or at timeout.
# Practice only. Log: /mnt/project-files/betterpicks/fast_loop.log
REPO=/home/user/archie-bot
BP=/mnt/project-files/betterpicks
LOG=$BP/fast_loop.log
FLAG=$BP/.city_sync_needed
BL=/tmp/bp-book.lock   # Nov 3: every book.json writer takes this lock, so one can't save over another's change
cd $REPO
trap 'trap - TERM; kill 0' TERM   # stopping the loop also stops its refresh, so no orphan saves an old book later
end=$((SECONDS+7000))
next=0
child=
wnext=0
wchild=

sync_code() {
  git -C $REPO fetch -q --no-write-fetch-head origin claude/project-thread-8sdhk6:refs/remotes/origin/claude/project-thread-8sdhk6 &&
    git -C $REPO merge -q --no-edit refs/remotes/origin/claude/project-thread-8sdhk6
}

refresh() {
  {
    echo "== $(date -u +%FT%TZ) refresh"
    sync_code
    (cd /tmp && timeout 180 python3 -I $REPO/betterpicks/books.py props >/dev/null) || echo "props failed"
    # Ryan 2026-10-25: re-check lineups/injuries for picks tipping off within 90 min, take back any that changed;
    # trade check (ESPN rosters vs our stats) every 6h
    (cd /tmp && flock $BL timeout 150 python3 -I $BP/pregame.py) || echo "pregame failed"
    if [ -z "$(find $BP/trades.json -mmin -360 2>/dev/null)" ]; then
      (cd /tmp && timeout 300 python3 -I $BP/trades.py) || echo "trade check failed"
    fi
    (cd /tmp && python3 -I $REPO/betterpicks/feed.py) || echo "feed failed"
    if ! git -C $REPO diff --quiet -- betterpicks/books_feed.json; then
      for i in 1 2 3; do
        git -C $REPO commit -q -m "BetterPicks: price feed" -- betterpicks/books_feed.json && break; sleep 3
      done
      for i in 1 2 3 4; do
        git -C $REPO push -q origin claude/project-thread-8sdhk6 2>/dev/null && break
        sync_code; sleep $((2**i))
      done
    fi
    # NBA box scores incl. preseason (Ryan 2026-10-21), at most every 3h; slim stats DBs for the droplet once a day
    if [ -z "$(find $BP/data/nba.sqlite -mmin -180 2>/dev/null)" ]; then
      (cd /tmp && timeout 300 python3 -I $BP/data/load_nba.py --seasons 2027 --start $(date -u -d '3 days ago' +%F) 2>&1 | tail -1)
    fi
    if [ "$(git -C $REPO log -1 --format=%ct -- betterpicks/data/nba.sqlite.gz)" -lt $(( $(date +%s) - 72000 )) ]; then
      (cd /tmp && nice python3 -I $REPO/betterpicks/slim_db.py >/dev/null) &&
        git -C $REPO commit -q -m "BetterPicks: daily stats refresh" -- betterpicks/data/*.sqlite.gz &&
        for i in 1 2 3 4; do git -C $REPO push -q origin claude/project-thread-8sdhk6 2>/dev/null && break; sync_code; sleep $((2**i)); done
    fi
    # Ryan 2026-10-28 background tests (no posts): line sniper every cycle, tennis vs Pinnacle every 30 min
    (cd /tmp && timeout 120 python3 -I $REPO/betterpicks/sniper.py) || echo "sniper failed"
    [ -z "$(find $BP/tennis_shadow.json -mmin -30 2>/dev/null)" ] && { (cd /tmp && timeout 240 python3 -I $REPO/betterpicks/tennis_shadow.py) || echo "tennis failed"; }
    (cd /tmp && flock $BL python3 -I $BP/book_heal.py)
    (cd /tmp && flock $BL python3 -I $BP/merge_server.py)
    (cd /tmp && flock $BL python3 -I $BP/parlays.py grade >/dev/null) || echo "parlay grade failed"
    (cd /tmp && flock $BL python3 -I $BP/betterpicks.py grade && python3 -I $BP/betterpicks.py report >/dev/null)
    (cd /tmp && flock $BL python3 -I $BP/book_heal.py)
    after=$(python3 -I -c 'import json;b=json.load(open("'$BP'/book.json"));print(len(b["parlays"]),[e["status"] for e in b["parlays"]],b["bankroll"])')
    # compare with the last state we synced, so changes made by other routines between cycles count too
    [ "$after" != "$(cat $BP/.fast_loop_state 2>/dev/null)" ] && echo "$after" > $BP/.fast_loop_state && touch $FLAG && echo "book changed -> city sync"
    # Nov 3: keep a good copy of the book (it was wiped once by a write collision); one per hour, last 48 kept
    python3 -I -c 'import json,sys;b=json.load(open(sys.argv[1]));sys.exit(0 if b.get("parlays") else 1)' $BP/book.json &&
      cp $BP/book.json $BP/backups/book-$(( $(date +%s) / 3600 % 48 )).json
    date -u +%FT%TZ > $BP/fast_loop.heartbeat
  } >> $LOG 2>&1
}

while [ $SECONDS -lt $end ]; do
  if [ $SECONDS -ge $next ] && { [ -z "$child" ] || ! kill -0 $child 2>/dev/null; }; then
    refresh & child=$!
    next=$((SECONDS+240))
  fi
  if [ $SECONDS -ge $wnext ] && { [ -z "$wchild" ] || ! kill -0 $wchild 2>/dev/null; }; then
    (cd /tmp && timeout 120 python3 -I $BP/live_wire.py >> $BP/live_wire.log 2>&1; flock $BL timeout 90 python3 -I $BP/early_hits.py >> $BP/live_wire.log 2>&1) & wchild=$!   # early HITs: Ryan 2026-10-27
    wnext=$((SECONDS+60))
  fi
  # premium CHAT watch every 5s (Ryan 2026-10-30): flags go to chat_flags.json, the thread tells Ryan
  if [ -z "$cchild" ] || ! kill -0 $cchild 2>/dev/null; then
    (cd /tmp && timeout 20 python3 -I $BP/chat_watch.py >> $BP/chat_watch.log 2>&1) & cchild=$!
  fi
  # Members/Premium counter backstop every 10 min (Ryan 2026-10-31); file stamp so loop restarts don't reset it
  if [ $(( $(date +%s) - $(stat -c %Y $BP/.counters_at 2>/dev/null || echo 0) )) -ge 600 ]; then
    touch $BP/.counters_at; (cd /tmp && timeout 60 python3 -I $BP/counters_check.py >> $BP/counters.log 2>&1) &
  fi
  # cash-out alerts every 60s (Ryan 2026-11-01): posts in PICKS itself, rarely (leg near dead late or player out)
  if [ $(( $(date +%s) - $(stat -c %Y $BP/.cashout_at 2>/dev/null || echo 0) )) -ge 60 ]; then
    touch $BP/.cashout_at; (cd /tmp && timeout 50 python3 -I $BP/cashout.py >> $BP/cashout.log 2>&1) &
  fi
  if [ -e $BP/.chat_flag ]; then echo "CHAT FLAG: run python3 -I $BP/chat_watch.py --unposted, tell Ryan (he decides on deletes), restart the loop"; exit 0; fi
  if [ -e $BP/.live_alert ]; then echo "LIVE ALERT: run python3 -I $BP/live_wire.py --unposted, post them, restart the loop"; exit 0; fi
  if git fetch -q --no-write-fetch-head origin betterpicks-ask:refs/remotes/origin/betterpicks-ask 2>/dev/null; then
    out=$(git show origin/betterpicks-ask:questions.json 2>/dev/null | python3 -I -c '
import json,sys
try: qs=json.load(sys.stdin)
except Exception: qs=[]
try: done={a["id"] for a in json.load(open("/home/user/archie-bot/betterpicks/bp_answers.json"))}
except Exception: done=set()
for q in qs:
    if q["id"] not in done and not q.get("auto"): print(json.dumps(q))   # auto = BetterBot already answered it')
    if [ -n "$out" ]; then echo "NEW /bp QUESTION(S):"; echo "$out"; exit 0; fi
  fi
  if [ -e $FLAG ]; then rm -f $FLAG; echo "CITY SYNC NEEDED (book changed); restart the loop after syncing"; exit 0; fi
  sleep 5
done
echo "fast loop timeout; restart it"
