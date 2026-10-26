#!/usr/bin/env python3
"""Why a pick lost, from the game's box score (Ryan 2026-10-24: "put that in the reasoning for why a pick lost in
the discord"; 2026-10-26: "EVERY LOSS gets a detailed explanation"). feed.py adds it to lost legs; the droplet prints
it under the leg. Detailed = how far off it was, his full box line, the final score, what went wrong, what we
expected going in (win chance + reason) and the takeaway.

NBA: minutes vs his usual, bench vs starting, foul trouble, a blowout, shooting, the team losing the glass, and
how close it came. Other leagues: how close it came and a blowout. Results are cached (why_lost.json) so each
lost leg hits ESPN once. Practice picks only.
  python3 -I why_lost.py <player> <stat> <side> <line> <game "AWAY @ HOME"> <start ISO> [league]
"""
import json, os, sqlite3, sys, urllib.request
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("BP_DATA", "/mnt/project-files/betterpicks/data")
CACHE = os.path.join(HERE, "why_lost.json")
ESPN = {"nba": "basketball/nba", "nfl": "football/nfl", "mlb": "baseball/mlb"}
CODE = {"GSW": "GS", "NYK": "NY", "NOP": "NO", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH"}
# stat -> box-score parts that add up to it (NBA)
PARTS = {"points": ("PTS",), "rebounds": ("REB",), "assists": ("AST",), "threes": ("3PT",), "steals": ("STL",),
         "blocks": ("BLK",), "pra": ("PTS", "REB", "AST"), "pts_reb": ("PTS", "REB"), "pts_ast": ("PTS", "AST"),
         "reb_ast": ("REB", "AST")}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "BetterPicks (1.0)"})
    return json.load(urllib.request.urlopen(req, timeout=20))


def _event(league, game, start):
    """ESPN event id for 'AWAY @ HOME' on the game's US date."""
    day = (datetime.fromisoformat(start.replace("Z", "+00:00")) - timedelta(hours=7)).strftime("%Y%m%d")
    want = [CODE.get(t, t) for t in game.split(" @ ")]
    for e in _get(f"https://site.api.espn.com/apis/site/v2/sports/{ESPN[league]}/scoreboard?dates={day}").get("events", []):
        if [CODE.get(t, t) for t in e["shortName"].split(" @ ")] == want:
            return e["id"]
    return None


NBA_KEEP = ("MIN", "PTS", "FG", "3PT", "REB", "AST", "PF")
SKIP_LABELS = {"ERA", "PC-ST", "AVG", "OBP", "SLG", "OPS", "+/-", "QBR", "RTG"}


def box_line(summary, player):
    """'5.0 IP, 6 H, 3 R, 0 ER, 2 BB, 3 K, 82 PC' (every stat group he shows up in), his team, started or not."""
    parts, team, started = [], None, None
    for t in summary.get("boxscore", {}).get("players", []):
        for g in t.get("statistics", []):
            for a in g.get("athletes", []):
                if a["athlete"]["displayName"].lower() == player.lower() and a.get("stats"):
                    team, started = t["team"]["abbreviation"], a.get("starter", started)
                    kind = g.get("name") or g.get("type") or ""
                    line = ", ".join(f"{v} {k}" for k, v in zip(g["labels"], a["stats"])
                                     if k not in SKIP_LABELS and ("PTS" not in g["labels"] or k in NBA_KEEP))
                    parts.append((f"{kind}: " if kind and len(t["statistics"]) > 1 and kind != "pitching" else "") + line)
    return "; ".join(parts), team, started


def final_score(summary):
    comp = summary["header"]["competitions"][0]["competitors"]
    away = next((c for c in comp if c.get("homeAway") == "away"), comp[0])
    home = next((c for c in comp if c is not away), comp[-1])
    return f"Final: {away['team']['abbreviation']} {away.get('score')}, {home['team']['abbreviation']} {home.get('score')}."


def expected(leg):
    """What we thought going in: win chance and the reason we posted it."""
    p = leg.get("p")
    why = (leg.get("case") or leg.get("why") or "").strip().rstrip(".")
    if len(why) > 220:
        why = why[:220].rsplit(" ", 1)[0] + "..."
    if not p and not why:
        return ""
    s = f"We had it at {p * 100:.0f}%" if isinstance(p, (int, float)) else "Going in"
    return s + (f": {why[0].lower() + why[1:]}." if why else ".")


def _num(x):
    try:
        return float(str(x).split("-")[0])
    except ValueError:
        return None


def _usual(player, before):
    """His median minutes and how often he started, last 10 regular-season games before this one (full stats DB;
    playoff minutes run long)."""
    try:
        c = sqlite3.connect(f"file:{os.path.join(DATA, 'nba.sqlite')}?mode=ro", uri=True)
        rows = c.execute("select minutes, starter from player_games where player=? and date<? and season_type='regular' "
                         "and minutes>0 order by date desc limit 10", (player, before)).fetchall()
    except sqlite3.Error:
        return None, None
    if not rows:
        return None, None
    m = sorted(r[0] for r in rows)
    return m[len(m) // 2], sum(r[1] or 0 for r in rows) / len(rows)


def nba(leg, summary, actual):
    team_rows, me, my_team = summary["boxscore"]["players"], None, None
    for t in team_rows:
        st = t["statistics"][0]
        for a in st["athletes"]:
            if a["athlete"]["displayName"].lower() == leg["player"].lower() and a.get("stats"):
                me, my_team = (dict(zip(st["labels"], a["stats"])), a.get("starter")), t["team"]["abbreviation"]
    if not me:
        return "He didn't play." if leg["side"] == "over" else None
    box, started = me
    box_actual = sum(_num(box.get(p)) or 0 for p in PARTS.get(leg["stat"], ()))
    why = []
    mins = _num(box.get("MIN"))
    date = (datetime.fromisoformat(leg["start"].replace("Z", "+00:00")) - timedelta(hours=7)).strftime("%Y-%m-%d")
    usual, start_rate = _usual(leg["player"], date)
    if start_rate is not None and start_rate >= 0.6 and not started:
        why.append("he came off the bench after starting most of his recent games")
    if mins is not None and usual and mins <= 0.8 * usual:
        why.append(f"he played only {mins:g} minutes, well under his usual {usual:g}")
    elif mins is not None and usual and leg["side"] == "under" and mins >= 1.15 * usual:
        why.append(f"he played {mins:g} minutes, more than his usual {usual:g}")
    if (_num(box.get("PF")) or 0) >= 5 and leg["side"] == "over":
        why.append(f"he was in foul trouble ({box.get('PF')} fouls)")
    comp = summary["header"]["competitions"][0]["competitors"]
    score = {c["team"]["abbreviation"]: _num(c.get("score")) for c in comp}
    mine = score.get(my_team)
    other = next((v for k, v in score.items() if k != my_team), None)
    if mine is not None and other is not None and abs(mine - other) >= 20 and leg["side"] == "over":
        why.append(f"the game was a blowout ({mine:g}-{other:g}), so starters sat late")
    stat = leg["stat"]
    if stat == "threes" and "3PT" in box:
        why.append(f"he shot {box['3PT'].replace('-', ' of ')} from three")
    elif stat in ("points", "pra", "pts_reb", "pts_ast") and "FG" in box and leg["side"] == "over":
        made, att = (_num(x) for x in box["FG"].split("-"))
        if att and made / att < 0.4:
            why.append(f"he shot {box['FG'].replace('-', ' of ')} from the field")
    if stat in ("rebounds", "pra", "pts_reb", "reb_ast"):
        reb = {t["team"]["abbreviation"]: _num({x["name"]: x["displayValue"] for x in t["statistics"]}.get("totalRebounds"))
               for t in summary["boxscore"]["teams"]}
        theirs = next((v for k, v in reb.items() if k != my_team), None)
        if reb.get(my_team) and theirs and theirs - reb[my_team] >= 10 and leg["side"] == "over":
            why.append(f"the other team won the boards {theirs:g}-{reb[my_team]:g}")
    line, _, _ = box_line(summary, leg["player"])
    role = ("started" if started else "off the bench") + (f", usual {usual:g} min" if usual else "")
    return finish(leg, box_actual if actual is None else actual, why, box=f"{line} ({role})", score=final_score(summary))


def finish(leg, actual, why, nba_box=True, box="", score=""):
    """Detailed loss note: how far off, box line, final score, what went wrong, what we expected, takeaway."""
    out, need = [], (f"{leg['line'] + 0.5:g}+" if leg["side"] == "over" else f"{leg['line'] - 0.5:g} or fewer")
    if actual is not None:
        off = (leg["line"] + 0.5 - actual) if leg["side"] == "over" else (actual - (leg["line"] - 0.5))
        out.append(f"Needed {need}, got {actual:g}" + (" (missed by one)." if off <= 1 else f" (missed by {off:g})."))
    if box:
        out.append(f"Box: {box}" + ("" if box.endswith(".") else "."))
    if score:
        out.append(score)
    if why:
        s = "; ".join(why)
        out.append("What went wrong: " + s[0].lower() + s[1:] + ".")
    exp = expected(leg)
    if exp:
        out.append(exp)
    if not why:
        p = leg.get("p")
        if nba_box and actual is not None:
            out.append("Takeaway: normal minutes and role, nothing we missed, just an off night." if leg["side"] == "over"
                       else "Takeaway: normal minutes and role, nothing we missed, he just had a bigger night.")
        else:
            out.append("Takeaway: no red flag in the box score, this one was variance." +
                       (f" A {p * 100:.0f}% pick still loses about {100 - p * 100:.0f} times in 100."
                        if isinstance(p, (int, float)) else ""))
    else:
        out.append("Takeaway: his role or minutes changed during the game, which no pregame number showed. "
                   "The pregame check re-confirms role and lineup before every pick." if any("bench" in w or "minutes" in w for w in why)
                   else "Takeaway: game flow went against us, not a bad read on the player.")
    return " ".join(x for x in out if x) or None


def explain(leg, league="nba"):
    """Short reason a leg lost, or None. leg needs player, stat, side, line, game, start (+ actual if known)."""
    if not leg.get("game") or not leg.get("start") or league not in ESPN:
        return None
    key = f"v2|{league}|{leg['player']}|{leg['stat']}|{leg['side']}|{leg['line']}|{leg['start'][:10]}"
    try:
        cache = json.load(open(CACHE))
    except (OSError, ValueError):
        cache = {}
    if key in cache:
        return cache[key]
    try:
        ev = _event(league, leg["game"], leg["start"])
        if not ev:
            return None
        s = _get(f"https://site.api.espn.com/apis/site/v2/sports/{ESPN[league]}/summary?event={ev}")
        if s["header"]["competitions"][0]["status"]["type"].get("completed") is not True:
            return None
        actual = leg.get("actual") if isinstance(leg.get("actual"), (int, float)) else None
        if league == "nba":
            text = nba(leg, s, actual)
        else:
            line, team, _ = box_line(s, leg["player"])
            why = [] if line or leg["side"] != "over" else ["he didn't record a stat line (scratched or left early)"]
            text = finish(leg, actual, why, nba_box=False, box=line, score=final_score(s))
    except Exception:
        return None
    cache[key] = text
    json.dump(cache, open(CACHE, "w"), indent=1)
    return text


if __name__ == "__main__":
    a = sys.argv[1:]
    print(explain({"player": a[0], "stat": a[1], "side": a[2], "line": float(a[3]), "game": a[4], "start": a[5]},
                  a[6] if len(a) > 6 else "nba"))
