#!/usr/bin/env python3
"""Sportsbook price feed for the droplet (practice picks only).

The droplet's IP gets no Action Network / Pinnacle rows, so it can't price Kalshi props (3+ books needed).
The project side runs `books.py props`, then this script copies the sportsbook prices for the next 40h into
books_feed.json; the hourly routine commits it and parlays.py on the droplet merges it into its own props.

  python3 feed.py [props.json] [out.json]
"""
import json, os, sys, urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
KEEP_OUT = {"PrizePicks", "Underdog", "Sleeper", "Kalshi", "Polymarket"}   # the droplet fetches these itself
GRADED = os.environ.get("BP_GRADED_BOOK", "/mnt/project-files/betterpicks/book.json")   # the project grades parlays
LEG_KEEP = ("player", "stat", "side", "line", "ticker", "game", "result", "actual")
BOARD_KEEP = ("player", "stat", "side", "line", "game", "result", "actual", "start", "league")


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
        if (e["status"] in ("won", "lost", "void") and e["date"] >= "2026-10-12" and not e.get("test") and not e.get("retracted")
                and f"verdict:parlay:{e['date']}:{e['app']}:{e['type']}" not in done):
            out.append({"key": f"parlay:{e['date']}:{e['app']}:{e['type']}", "date": e["date"], "app": e["app"],
                        "type": e["type"], "lock": e.get("lock"), "status": e["status"], "pnl": e["pnl"],
                        "stake": e["stake"], "payout_x": e.get("payout_x"),
                        "legs": [{k: l.get(k) for k in LEG_KEEP} for l in e["legs"]]})
    rule = [e for e in book.get("parlays", []) if e["app"] == "Kalshi" and e["date"] >= "2026-10-13" and e["status"] in ("won", "lost")]
    rec = {"won": sum(e["status"] == "won" for e in rule), "lost": sum(e["status"] == "lost" for e in rule),
           "pnl": round(sum(e["pnl"] for e in rule), 2)}
    posted = [e for e in book.get("parlays", []) if e["date"] >= "2026-10-12" and not e.get("test")
              and f"parlay:{e['date']}:{e['app']}:{e['type']}" in done]
    # Ryan 2026-10-21: picks the project takes back; the droplet deletes the post (if it went out) and says fade it
    retract = [{"key": f"parlay:{e['date']}:{e['app']}:{e['type']}", "app": e["app"], "type": e["type"],
                "lock": e.get("lock"), "why": e.get("retract_why", ""), "legs": [{k: l.get(k) for k in LEG_KEEP} for l in e["legs"]]}
               for e in book.get("parlays", []) if e.get("retracted")]
    live = [e for e in posted if not e.get("retracted")]
    return {"parlays": out, "record": rec, "props": prop_record(live), "delays": delays(live), "board": board(live),
            "retract": retract}


def board(parlays):
    """Ryan 2026-10-21: /bp should answer in seconds. The droplet answers "what hit / what's open" itself
    from this board: every posted pick that is still open or settled in the last 3 days."""
    since = (datetime.now(timezone.utc) - timedelta(days=3, hours=7)).strftime("%Y-%m-%d")
    return [{"date": e["date"], "type": e["type"], "status": e["status"], "pnl": e.get("pnl"), "stake": e.get("stake"),
             "legs": [{k: l.get(k) for k in BOARD_KEEP} for l in e["legs"]]}
            for e in parlays if e["status"] == "open" or e["date"] >= since]


def prop_record(parlays):
    """Ryan 2026-10-18 (/pnl): wins vs losses by player prop (each leg), not by parlay, for picks posted in Discord."""
    legs = [(e["date"], l) for e in parlays for l in e["legs"] if l.get("result") in ("won", "lost")]
    by = {}
    for _, l in legs:
        lg = by.setdefault((l.get("league") or "other").upper(), [0, 0])
        lg[0 if l["result"] == "won" else 1] += 1
    settled = [e for e in parlays if e["status"] in ("won", "lost", "void")]
    return {"won": sum(l["result"] == "won" for _, l in legs), "lost": sum(l["result"] == "lost" for _, l in legs),
            "by_league": by, "parlay_pnl": round(sum(e["pnl"] for e in settled), 2),
            "recent": [{k: l.get(k) for k in LEG_KEEP} | {"date": d} for d, l in sorted(legs, key=lambda x: x[0])[-10:]]}


ESPN = {"mlb": "baseball/mlb", "nfl": "football/nfl", "nba": "basketball/nba"}


def delays(parlays):
    """Ryan 2026-10-18: if a posted pick's game is postponed or delayed, say so in Discord; the pick carries over."""
    out, boards = [], {}
    for e in parlays:
        if e["status"] != "open":
            continue
        for l in e["legs"]:
            if l.get("result") or l.get("league") not in ESPN or not l.get("start"):
                continue
            day = (datetime.fromisoformat(l["start"].replace("Z", "+00:00")) - timedelta(hours=7)).strftime("%Y%m%d")
            k = (l["league"], day)
            if k not in boards:
                try:
                    url = f"https://site.api.espn.com/apis/site/v2/sports/{ESPN[l['league']]}/scoreboard?dates={day}"
                    boards[k] = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "BetterPicks (1.0)"}), timeout=15)).get("events", [])
                except Exception:
                    boards[k] = []
            teams = set(l.get("game", "").replace(" ", "").split("@"))
            for ev in boards[k]:
                abbr = {c["team"]["abbreviation"] for c in ev["competitions"][0]["competitors"]}
                st = ev["status"]["type"]
                if teams and teams <= abbr and (st.get("name") in ("STATUS_POSTPONED", "STATUS_DELAYED", "STATUS_SUSPENDED", "STATUS_RAIN_DELAY")):
                    out.append({"key": f"parlay:{e['date']}:{e['app']}:{e['type']}", "type": e["type"], "date": e["date"],
                                "game": l["game"], "status": st.get("description") or "Postponed",
                                "legs": [{k2: x.get(k2) for k2 in LEG_KEEP} for x in e["legs"]]})
                    break
    return out


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
