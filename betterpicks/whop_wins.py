"""Keep the BetterPicks VIP Whop page's wins list current (Ryan 2026-10-23: "auto post in the home page every win").

Reads book.json, takes the picks that count toward the record (record.counts), and rewrites the Whop product
description with the record line and every win (newest first). PATCHes only when the text changed.
Key: env WHOP_BP_API. Usage: python3 -I whop_wins.py [--dry]
"""
import hashlib, json, os, sys, urllib.request

BP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BP)
import record  # noqa: E402

PRODUCT = "prod_M44sTTPJ3OFlS"
STATE = os.path.join(BP, ".whop_wins_state")
LIMIT = 1400  # Whop rejects descriptions over 1500 characters (margin for emoji counting); older wins and misses drop off the list to fit

STAT = {"pitcher_ks": "strikeouts", "pra": "pts+reb+ast", "threes": "threes", "receptions": "receptions",
        "assists": "assists", "rebounds": "rebounds", "points": "points"}

HEAD = """BetterPicks is a bot that scans player props around the clock and only posts when it finds a real edge over the price.

RECORD: {won}-{lost} on picks that meet our current rules, {pnl} at $5 a pick ({roi} return). Updates after every game.

EVERY WIN ({won}){more}
{wins}
{losses}
YOU GET
- Live picks in the Discord PICKS channel with win chance + edge
- HIT/LOST results after every game

JOIN: buy a plan, join https://discord.gg/bmVz2WhyZC.

NBA, NFL, MLB, soccer. Not financial advice. 18+ (21+ where required). Not affiliated with Kalshi or any sportsbook."""


def leg_text(l):
    stat = STAT.get(l.get("stat"), (l.get("stat") or "").replace("_", " "))
    line = l.get("line")
    if l.get("side") == "over" and line is not None and float(line) % 1 == 0.5:
        return f"{l['player']} {int(float(line) + 0.5)}+ {stat}"
    return f"{l['player']} {l.get('side', '')} {line} {stat}"


def pick_text(e):
    d = e.get("date", "")[5:].replace("-", "/").lstrip("0")
    return f"{d}: " + " + ".join(leg_text(l) for l in e.get("legs", []))


FEED_URL = "https://raw.githubusercontent.com/RY00N/archie-bot/claude/project-thread-8sdhk6/betterpicks/books_feed.json"


def counted():
    """The graded picks that count, and the record. On the droplet book.json is never graded (grading runs in the
    project), so read the project's graded feed; fall back to the local book.json when the feed has no list."""
    try:
        req = urllib.request.Request(FEED_URL, headers={"User-Agent": "BetterPicks (1.0)", "Cache-Control": "no-cache"})
        v = json.load(urllib.request.urlopen(req, timeout=15)).get("verdicts") or {}
        if "counted" in v and v.get("record"):
            return v["counted"], v["record"]
    except Exception as e:
        print("whop_wins: feed unavailable, using book.json:", e)
    book = json.load(open(os.path.join(BP, "book.json")))
    return [e for e in book["parlays"] if record.counts(e)], record.record(book["parlays"])


def build():
    c, r = counted()
    wins = sorted([e for e in c if e["status"] == "won"], key=lambda e: e.get("date", ""), reverse=True)
    lost = sorted([e for e in c if e["status"] == "lost"], key=lambda e: e.get("date", ""), reverse=True)
    roi = f"{r['pnl'] / r['staked'] * 100:+.0f}%" if r["staked"] else "n/a"
    n_w, n_l = len(wins), min(len(lost), 5)
    while True:
        text = HEAD.format(
            won=r["won"], lost=r["lost"], pnl=f"{'+' if r['pnl'] >= 0 else '-'}${abs(r['pnl']):.2f}", roi=roi,
            more=f", latest {n_w} shown" if n_w < len(wins) else "",
            wins="\n".join("✅ " + pick_text(e) for e in wins[:n_w]) or "(none yet)",
            losses=("\nLATEST MISSES\n" + "\n".join("❌ " + pick_text(e) for e in lost[:n_l]) + "\n") if n_l else "",
        )
        if len(text) <= LIMIT or (n_w <= 1 and n_l <= 1):
            return text[:LIMIT]
        if n_l > 1 and n_l * 3 > n_w:
            n_l -= 1
        else:
            n_w -= 1


def main():
    text = build()
    if "--dry" in sys.argv:
        print(text)
        return
    h = hashlib.sha1(text.encode()).hexdigest()
    if os.path.exists(STATE) and open(STATE).read().strip() == h:
        return
    key = os.environ.get("WHOP_BP_API")
    if not key:
        print("whop_wins: WHOP_BP_API not set, skipped")
        return
    req = urllib.request.Request(f"https://api.whop.com/api/v1/products/{PRODUCT}", method="PATCH",
                                 data=json.dumps({"description": text}).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()
    open(STATE, "w").write(h)
    print("whop_wins: page updated")


if __name__ == "__main__":
    main()
