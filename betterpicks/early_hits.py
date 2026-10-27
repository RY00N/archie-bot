"""Early HITs (Ryan 2026-10-27: "the second it hits say it did ... wait just a sec" in case a stat is taken back).
An open NBA over leg whose live ESPN box score already clears the line is marked won once the box shows it on two
checks at least 60s apart. Then the parlay is graded and the feed pushed right away so BetterBot posts the HIT.
fast_loop.sh runs this every 60s. Practice only."""
import json, os, subprocess, sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import parlays

BP = "/mnt/project-files/betterpicks"
REPO = "/home/user/archie-bot"
CONFIRM_S = 60


def main():
    book = parlays.load(parlays.BOOK, None)
    now = datetime.now(timezone.utc)
    changed, hits = False, []
    for e in book.get("parlays", []):
        if e.get("status") != "open":
            continue
        for l in e["legs"]:
            if l.get("result") is not None or l.get("side") != "over" or l.get("league") != "nba" or not l.get("start"):
                continue
            if now < datetime.fromisoformat(l["start"].replace("Z", "+00:00")):
                continue
            v = parlays.espn_actual(l, live=True)
            if not isinstance(v, (int, float)) or v <= l["line"]:
                if l.pop("early_seen", None):
                    changed = True
                continue
            seen = l.get("early_seen")
            if seen and now - datetime.fromisoformat(seen) >= timedelta(seconds=CONFIRM_S):
                l["result"], l["graded_by"], l["hit_at"] = "won", "live box", now.isoformat(timespec="seconds")
                l.pop("early_seen", None)
                hits.append(f"{l['player']} {l['stat']} over {l['line']:g} ({v:g} so far)")
            elif not seen:
                l["early_seen"] = now.isoformat(timespec="seconds")
            changed = True
    if not changed:
        return
    parlays.save(parlays.BOOK, book)
    if not hits:
        return
    parlays.grade()
    print(now.isoformat(timespec="seconds"), "early HIT:", "; ".join(hits))
    # push the verdict now instead of waiting for the 4-minute refresh
    subprocess.run(["python3", "-I", f"{REPO}/betterpicks/feed.py"], cwd="/tmp", capture_output=True)
    if subprocess.run(["git", "-C", REPO, "diff", "--quiet", "--", "betterpicks/books_feed.json"]).returncode:
        subprocess.run(["git", "-C", REPO, "commit", "-q", "-m", "BetterPicks: early HIT", "--", "betterpicks/books_feed.json"])
        for _ in range(3):
            if subprocess.run(["git", "-C", REPO, "push", "-q", "origin", "claude/project-thread-8sdhk6"],
                              capture_output=True).returncode == 0:
                break
            subprocess.run(["git", "-C", REPO, "pull", "-q", "--no-rebase", "--no-edit", "origin", "claude/project-thread-8sdhk6"],
                           capture_output=True)
    open(os.path.join(BP, ".city_sync_needed"), "w").close()


if __name__ == "__main__":
    main()
