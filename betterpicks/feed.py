#!/usr/bin/env python3
"""Sportsbook price feed for the droplet (practice picks only).

The droplet's IP gets no Action Network / Pinnacle rows, so it can't price Kalshi props (3+ books needed).
The project side runs `books.py props`, then this script copies the sportsbook prices for the next 40h into
books_feed.json; the hourly routine commits it and parlays.py on the droplet merges it into its own props.

  python3 feed.py [props.json] [out.json]
"""
import json, os, sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
KEEP_OUT = {"PrizePicks", "Underdog", "Sleeper", "Kalshi", "Polymarket"}   # the droplet fetches these itself
GRADED = os.environ.get("BP_GRADED_BOOK", "/mnt/project-files/betterpicks/book.json")   # the project grades parlays
LEG_KEEP = ("player", "stat", "side", "line", "ticker", "game", "result", "actual")


def verdicts():
    """Ryan 2026-10-18: once a parlay's last game is graded, the droplet posts HIT or LOST in Discord.
    Only the project grades, so the settled parlays ride along in the feed (Discord era, Oct 12 on)."""
    try:
        book = json.load(open(GRADED))
    except Exception:
        return None
    try:   # verdicts the project already posted by hand (Ryan 2026-10-18 catch-up) are skipped on the droplet
        done = set(json.load(open(os.path.join(os.path.dirname(GRADED), "discord_posted.json"))))
    except Exception:
        done = set()
    out = []
    for e in book.get("parlays", []):
        if (e["status"] in ("won", "lost", "void") and e["date"] >= "2026-10-12" and not e.get("test")
                and f"verdict:parlay:{e['date']}:{e['app']}:{e['type']}" not in done):
            out.append({"key": f"parlay:{e['date']}:{e['app']}:{e['type']}", "date": e["date"], "app": e["app"],
                        "type": e["type"], "lock": e.get("lock"), "status": e["status"], "pnl": e["pnl"],
                        "stake": e["stake"], "payout_x": e.get("payout_x"),
                        "legs": [{k: l.get(k) for k in LEG_KEEP} for l in e["legs"]]})
    rule = [e for e in book.get("parlays", []) if e["app"] == "Kalshi" and e["date"] >= "2026-10-13" and e["status"] in ("won", "lost")]
    rec = {"won": sum(e["status"] == "won" for e in rule), "lost": sum(e["status"] == "lost" for e in rule),
           "pnl": round(sum(e["pnl"] for e in rule), 2)}
    return {"parlays": out, "record": rec}


def key(pr):
    return "|".join((pr["league"], pr["player"].lower(), pr["stat"], (pr.get("start") or "")[:10]))


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "props.json")
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "books_feed.json")
    doc = json.load(open(src))
    now = datetime.now(timezone.utc)
    feed = {}
    for pr in doc.get("props", []):
        if not pr.get("start") or pr["league"] in ("nhl", "ncaaf"):
            continue
        start = datetime.fromisoformat(pr["start"].replace("Z", "+00:00"))
        if not (now < start < now + timedelta(hours=40)):
            continue
        books = {n: s for n, s in pr["sources"].items() if n not in KEEP_OUT and s.get("over") is not None}
        if books:
            feed[key(pr)] = books
    v = verdicts()
    json.dump({"generated_at": doc.get("generated_at"), "props": feed, **({"verdicts": v} if v else {})},
              open(out, "w"), separators=(",", ":"))
    print(f"feed: {len(feed)} props, {len(v['parlays']) if v else 0} graded parlays -> {out}")


if __name__ == "__main__":
    main()
