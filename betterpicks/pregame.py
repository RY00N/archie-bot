#!/usr/bin/env python3
"""Pregame check (Ryan 2026-10-25: "make sure what the roster is going to be before the game ... people on the
bench coming in for starters, anyone injured").

Runs every loop cycle. For each open pick whose game starts in the next 90 minutes it re-checks, with fresh data:
  - the player's own injury status (Out, Doubtful or Questionable = take it back)
  - NBA starting lineup once ESPN posts it (~30 min before tip): a usual starter who's on the bench, or a bench
    player starting on an under = take it back
  - the injury/matchup adjustment: teammates or opponents counted as out who are now playing (or were traded),
    or new absences, re-run through news.context(); if the win chance falls under our bar = take it back
A taken-back pick is marked retracted in book.json; the droplet deletes its Discord post and tells people to fade
it (discord_post.retractions). Each pick is checked until tip; nothing here places bets.

  python3 pregame.py          check and retract
  python3 pregame.py --dry    print what would happen
"""
import json, os, re, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import news      # noqa: E402
import parlays   # noqa: E402
news.ROWS = parlays.rows_for
news.SQL = parlays.sql
news.STAT_COL = lambda league, stat: (parlays.STATS.get((league, stat)) or (None, None))[1]
news.DB_TEAM = parlays.DB_CODE.get("nba", {})

BOOK = os.path.join(HERE, "book.json")
RULES = os.path.join(HERE, "rules.json")
WINDOW = 90 * 60
BAD = ("out", "doubtful", "questionable", "injured reserve", "suspension")


def _ts(s):
    return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone


def own_status(league, player, c):
    """'Out' / 'Questionable' / ... or '' from ESPN's league injury report (status + comment)."""
    s = news.injuries(league, c).get(player.lower(), "")
    low = s.lower()
    if low.startswith("day-to-day"):   # NBA day-to-day: the comment says how likely he is to play
        return next((w.title() for w in ("out", "doubtful", "questionable", "game-time")
                     if re.search(rf"\b{w}\b", low)), "")
    return next((w.title() for w in BAD if low.startswith(w)), "")


def starters(league, game, start, c):
    """{player name lower: True/False starter} once ESPN shows lineups for the game, else {}."""
    path = news.ESPN.get(league)
    if league != "nba" or not path:
        return {}
    teams = [news.norm(t) for t in game.replace(" vs ", " @ ").split(" @ ")]
    try:
        sb = news._get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={start[:10].replace('-', '')}")
        ev = next(e for e in sb.get("events", []) if
                  {news.norm(x["team"]["abbreviation"]) for x in e["competitions"][0]["competitors"]} == set(teams))
        d = news._get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/summary?event={ev['id']}")
    except Exception:
        return {}
    out = {}
    for t in (d.get("boxscore") or {}).get("players", []):
        for g in t.get("statistics", []):
            for a in g.get("athletes", []):
                name = (a.get("athlete") or {}).get("displayName")
                if name and "starter" in a:
                    out[name.lower()] = bool(a["starter"])
    return out if any(out.values()) else {}


def usual_role(league, stat, player):
    if league != "nba":
        return None
    parlays.rows_for(league, stat, player)
    return parlays._ROLE.get((league, player))


def fresh_p(leg, c):
    """Win chance with today's injury/matchup picture, or None when the old adjustment isn't known."""
    pr = {k: leg.get(k) for k in ("league", "player", "team", "stat", "game", "start")}
    old = leg.get("ctx_dp")
    if old is None:
        return None
    dp, _ = news.context({"prop": pr, "side": leg["side"], "line": leg["line"]}, c)
    bdp, _ = news.blowout_adj({"prop": pr, "side": leg["side"], "line": leg["line"]}, c)
    pdp, _ = news.pos_defense({"prop": pr, "side": leg["side"], "line": leg["line"]}, c)
    hdp, _ = news.hot_star({"prop": pr, "side": leg["side"], "line": leg["line"]}, c)
    mdp, _ = news.minutes_trend({"prop": pr, "side": leg["side"], "line": leg["line"]}, c)
    return round(leg["p"] - old + dp + bdp + pdp + hdp + mdp, 3), dp + bdp + pdp + hdp + mdp


def gone_mates(leg, c):
    """Players the posted note counted as out who are now active or off both teams."""
    league = leg["league"]
    teams = [news.norm(t) for t in (leg.get("game") or "").replace(" vs ", " @ ").split(" @ ")]
    inj = news.injury_list(league, c)
    known = {i["name"].lower(): i for i in inj}
    rost = {t: news.roster_pos(league, t, c) for t in teams}
    names = set(known) | {n for r in rost.values() if r for n in r}
    back = []
    for chunk in re.findall(r"((?:\S+ ){1,4})(?:\([A-Z]+\) )?is out", leg.get("news") or ""):
        words = chunk.split()
        name = next((" ".join(words[-k:]) for k in (2, 3, 4) if " ".join(words[-k:]).lower() in names), None)
        if not name:
            continue
        i, on_team = known.get(name.lower()), any(name.lower() in r for r in rost.values() if r)
        if not on_team:
            back.append(f"{name} isn't on either team's roster anymore")
        elif not i or not news.is_out(i["status"]):
            back.append(f"{name} is playing")
    return back


def check(leg, rules, c):
    """-> reason to take the pick back, or None."""
    league, player = leg["league"], leg["player"]
    st = own_status(league, player, c)
    if st:
        return f"{player} is now listed {st.lower()} on the injury report."
    lineup = starters(league, leg.get("game") or "", leg.get("start") or "", c)
    if lineup and player.lower() in lineup:
        role = usual_role(league, leg["stat"], player)
        if role == 1 and not lineup[player.lower()]:
            return f"{player} isn't in tonight's starting lineup after starting most games this season."
        if role == 0 and lineup[player.lower()] and leg["side"] == "under":
            return f"{player} is starting tonight, so his role is bigger than the under assumed."
    bar = max(rules.get("parlay_min_win_p", 0.6), rules.get("under_min_p", 0) if leg["side"] == "under" else 0)
    fp = fresh_p(leg, c)
    if fp is not None:
        if fp[0] < bar:
            return f"With tonight's injury news our win chance drops to {fp[0]:.0%}, under our {bar:.0%} bar."
    else:
        back = gone_mates(leg, c)
        if back:
            return "The injury edge we counted is gone: " + "; ".join(back) + "."
    return None


def main(dry=False):
    book, rules, c = json.load(open(BOOK)), json.load(open(RULES)), news._cache()
    now, changed = time.time(), False
    for e in book.get("parlays", []):
        if e.get("app") != "Kalshi" or e["status"] != "open" or e.get("retracted") or e.get("test"):
            continue
        for leg in e["legs"]:
            if not leg.get("start") or leg.get("league") not in ("nba", "nfl"):
                continue
            t = _ts(leg["start"])
            if not (now < t <= now + WINDOW):
                continue
            try:
                why = check(leg, rules, c)
            except Exception as ex:
                print("pregame check skipped:", leg.get("player"), ex)
                continue
            if why:
                print(f"PREGAME take back {e['date']} {e['type']}: {why}")
                if not dry:
                    e.update(retracted=True, status="void", retract_pregame=True,
                             retract_why=f"Pregame check: {why}")
                    changed = True
                break
    try:
        json.dump(c, open(news.CACHE, "w"))
    except Exception:
        pass
    if changed:
        json.dump(book, open(BOOK, "w"), indent=1)


if __name__ == "__main__":
    main("--dry" in sys.argv)
