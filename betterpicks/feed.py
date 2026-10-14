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
    json.dump({"generated_at": doc.get("generated_at"), "props": feed}, open(out, "w"), separators=(",", ":"))
    print(f"feed: {len(feed)} props -> {out}")


if __name__ == "__main__":
    main()
