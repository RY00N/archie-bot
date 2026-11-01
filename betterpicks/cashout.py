"""Cash-out alerts (Ryan 2026-11-01: "if this is in your parlay cash it out, pick doesn't look good anymore ... very
rarely tell people they have to cash out, but if it looks like they have to then say it").

fast_loop runs this every 60s. For each open posted pick whose game is live, it posts ONE warning in PICKS only when
the leg is close to dead:
  - our side trades at CUT or less on Kalshi (the market gives it ~10% or less), and
  - most of the game is gone (LATE share of regulation played), or the player has left the game hurt.
Never before the game, never after it ends, never twice for the same pick. --dry prints instead of posting."""
import calendar, json, os, re, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BP = "/mnt/project-files/betterpicks"
BOOK = os.path.join(BP, "book.json")
STATE = os.path.join(BP, "cashout_state.json")
WIRE = os.path.join(BP, "live_wire.json")
PICKS_CH = "1559075934030659596"
CUT, LATE = 0.10, 0.60
SPORT = {"nba": "basketball/nba", "nfl": "football/nfl", "mlb": "baseball/mlb"}
# stat -> (ESPN box group, column) for the "has X so far" line; unknown stats just skip that line
BOX = {"nba": {"points": (None, "PTS"), "rebounds": (None, "REB"), "assists": (None, "AST"), "threes": (None, "3PT")},
       "nfl": {"receptions": ("receiving", "REC"), "rec_yds": ("receiving", "YDS"), "pass_tds": ("passing", "TD"),
               "pass_yds": ("passing", "YDS"), "rush_yds": ("rushing", "YDS"), "rush_tds": ("rushing", "TD")}}
STAT_WORDS = {"threes": "threes", "receptions": "catches", "rec_yds": "receiving yards", "pass_tds": "passing TDs",
              "pass_yds": "passing yards", "rush_yds": "rushing yards", "rush_tds": "rushing TDs"}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def load(p, d):
    try:
        return json.load(open(p))
    except Exception:
        return d


def side_price(leg):
    m = get(f"https://api.elections.kalshi.com/trade-api/v2/markets/{leg['ticker']}")["market"]
    side = "no" if leg.get("side") == "under" else "yes"
    v = m.get(f"{side}_bid_dollars")
    return float(v) if v not in (None, "") else None


def game(leg, boards):
    lg = leg.get("league", "").lower()
    date = time.strftime("%Y%m%d", time.gmtime(calendar.timegm(time.strptime(leg["start"][:19], "%Y-%m-%dT%H:%M:%S")) - 8 * 3600))
    key = (lg, date)
    if key not in boards:
        boards[key] = get(f"https://site.api.espn.com/apis/site/v2/sports/{SPORT[lg]}/scoreboard?dates={date}")["events"]
    teams = set(re.findall(r"[A-Z]{2,4}", leg.get("game", "")))
    for e in boards[key]:
        if teams and teams <= set(re.findall(r"[A-Z]{2,4}", e["shortName"])):
            return e
    return None


def played(lg, st):
    """share of regulation played (0..1)"""
    per, clock = st.get("period", 0), st.get("displayClock", "0:00")
    try:
        mm, ss = (int(x) for x in clock.split(":")[:2])
    except ValueError:
        mm, ss = 0, 0
    if lg == "mlb":
        return min(1, per / 9)
    q = 12 if lg == "nba" else 15
    return min(1, ((per - 1) * q + (q - mm - ss / 60)) / (4 * q)) if per else 0


def so_far(lg, ev_id, leg):
    col = BOX.get(lg, {}).get(leg.get("stat"))
    if not col:
        return None
    s = get(f"https://site.api.espn.com/apis/site/v2/sports/{SPORT[lg]}/summary?event={ev_id}")
    last = leg["player"].split()[-1].lower()
    for t in s.get("boxscore", {}).get("players", []):
        for g in t.get("statistics", []):
            if col[0] and g.get("name") != col[0]:
                continue
            if col[1] not in g.get("labels", []):
                continue
            i = g["labels"].index(col[1])
            for a in g.get("athletes", []):
                if a["athlete"]["displayName"].lower() == leg["player"].lower() or a["athlete"]["displayName"].lower().endswith(" " + last) and \
                        a["athlete"]["displayName"][0] == leg["player"][0]:
                    v = a["stats"][i] if a.get("stats") else None
                    return v.split("-")[0] if v else None
    return "0"


def hurt(player):
    """the player left the game hurt and hasn't come back (live_wire in-game notes, last 4 hours)"""
    last = player.split()[-1]
    seen = None
    cutoff = time.strftime("%Y-%m-%dT%H:%M", time.gmtime(time.time() - 4 * 3600))
    for w in load(WIRE, []):
        if w.get("at", "") < cutoff:
            continue
        t = w.get("text", "")
        if w.get("player") == player and re.search(r"won't return|ruled out|\bOut\b", t):
            seen = True
        elif re.search(rf"-\w*\.?\s*{re.escape(last)}\b", t) or (player in t):
            if "was injured" in t:
                seen = seen or "maybe"
            elif "has returned" in t:
                seen = None
    return seen is True


def main(dry=False):
    st = load(STATE, {})
    book = load(BOOK, {})
    boards, out = {}, []
    for e in book.get("parlays", []):
        if e.get("status") != "open" or e.get("retracted") or e.get("test"):
            continue
        for leg in e.get("legs", []):
            k = f"{e['date']}|{e['type']}|{leg.get('player')}"
            if k in st or not leg.get("ticker") or not leg.get("start") or leg.get("result"):
                continue
            lg = leg.get("league", "").lower()
            if lg not in SPORT or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) < leg["start"]:
                continue
            try:
                ev = game(leg, boards)
                if not ev or ev["status"]["type"]["state"] != "in":
                    continue
                px = side_price(leg)
            except Exception as ex:
                print("cashout:", leg.get("player"), ex)
                continue
            if px is None or px > CUT:
                continue
            share = played(lg, ev["status"])
            gone = hurt(leg["player"])
            if share < LATE and not gone:
                continue
            try:
                have = so_far(lg, ev["id"], leg)
            except Exception:
                have = None
            word = STAT_WORDS.get(leg.get("stat"), leg.get("stat", "").replace("_", " "))
            want = (f"under {leg['line']}" if leg.get("side") == "under" else f"{int(float(leg['line']) + 0.5)}+") + f" {word}"
            why = "He left the game hurt and isn't coming back." if gone else (
                f"He has {have} so far with {ev['status'].get('displayClock', '')} left in the {ordinal(ev['status'].get('period'))}." if have is not None
                else "He's way off pace this late in the game.")
            msg = (f"⚠️ **Cash-out alert: {leg['player']} {want}** ({leg.get('game', '')})\n{why} "
                   f"This one doesn't look like it's hitting. If it's in your parlay, cash it out now.")
            out.append(msg)
            if dry:
                print(msg, f"\n  [price {px:.2f}, {share:.0%} played, hurt={gone}]\n")
                continue
            sys.path.insert(0, HERE)
            import discord_post as dp
            dp.send(msg, channel=PICKS_CH)
            st[k] = time.time()
            json.dump(st, open(STATE, "w"), indent=1)
            print("cash-out alert posted:", k)
    return out


def ordinal(n):
    n = n or 0
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th") if n <= 4 else "OT"


if __name__ == "__main__":
    main("--dry" in sys.argv)
