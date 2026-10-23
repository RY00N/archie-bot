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
MAX_LIST = 40  # Whop description stays readable; older wins roll into the count

STAT = {"pitcher_ks": "strikeouts", "pra": "pts+reb+ast", "threes": "threes", "receptions": "receptions",
        "assists": "assists", "rebounds": "rebounds", "points": "points"}

HEAD = """BetterPicks is a picks bot that scans player props around the clock and only posts when its model finds a real edge over the market price. No hype picks, no filler.

RECORD: {won}-{lost} on picks that meet our current rules, {pnl} at $5 a pick ({roi} return on money risked). Updated automatically after every game.

EVERY WIN ({won}){more}
{wins}
{losses}
HOW IT PICKS
- Every pick needs a 60%+ win chance AND a 5%+ edge over the price, or it doesn't post
- Injuries and matchups checked before every pick (teammate out, defender out, minutes trends)
- Combos max 2 legs, every leg 65%+
- If news turns a pick bad, the bot pulls it and tells you to fade

WHAT YOU GET
- Every pick posted live in the Discord PICKS channel, any hour, with win chance and edge shown
- HIT / LOST results posted after every game
- /bp: ask BetterBot about any pick and get an answer in seconds
- A running record tracked in the open

Sports: NBA, NFL, MLB, soccer (in season).

Past results don't guarantee future results. Picks are information, not financial advice. 18+ (21+ where required). Bet responsibly. Not affiliated with Kalshi or any sportsbook."""


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
    shown = wins[:MAX_LIST]
    return HEAD.format(
        won=r["won"], lost=r["lost"], pnl=f"{'+' if r['pnl'] >= 0 else '-'}${abs(r['pnl']):.2f}", roi=roi,
        more=f", latest {MAX_LIST} shown" if len(wins) > MAX_LIST else "",
        wins="\n".join("✅ " + pick_text(e) for e in shown) or "(none yet)",
        losses=("\nMISSES (we post the losses too)\n" + "\n".join("❌ " + pick_text(e) for e in lost[:10]) + "\n") if lost else "",
    )


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
