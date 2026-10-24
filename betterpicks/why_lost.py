#!/usr/bin/env python3
"""Why a pick lost, in one or two plain sentences, from the game's box score (Ryan 2026-10-24: "put that in the
reasoning for why a pick lost in the discord"). feed.py adds it to lost legs; the droplet prints it under the leg.

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
    return finish(leg, box_actual if actual is None else actual, why, known=actual is not None)


def finish(leg, actual, why, known=False, nba_box=True):
    """known: the card already shows the final number, so don't repeat it."""
    out = []
    if actual is not None:
        close = abs(actual - leg["line"]) <= 1
        out.append(("Missed by one." if close else "") if known else
                   f"Finished with {actual:g}" + (", missing by one." if close else "."))
    if why:
        s = "; ".join(why)
        out.append(s[0].upper() + s[1:] + ".")
    elif actual is not None and nba_box:
        out.append("Nothing unusual in the box score: normal minutes and role, just an off night." if leg["side"] == "over"
                   else "Nothing unusual in the box score: normal minutes and role, he just had a bigger night.")
    return " ".join(x for x in out if x) or None


def explain(leg, league="nba"):
    """Short reason a leg lost, or None. leg needs player, stat, side, line, game, start (+ actual if known)."""
    if not leg.get("game") or not leg.get("start") or league not in ESPN:
        return None
    key = f"{league}|{leg['player']}|{leg['stat']}|{leg['side']}|{leg['line']}|{leg['start'][:10]}"
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
        text = nba(leg, s, actual) if league == "nba" else finish(leg, actual, [], known=actual is not None, nba_box=False)
    except Exception:
        return None
    cache[key] = text
    json.dump(cache, open(CACHE, "w"), indent=1)
    return text


if __name__ == "__main__":
    a = sys.argv[1:]
    print(explain({"player": a[0], "stat": a[1], "side": a[2], "line": float(a[3]), "game": a[4], "start": a[5]},
                  a[6] if len(a) > 6 else "nba"))
