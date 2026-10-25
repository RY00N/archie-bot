#!/usr/bin/env python3
"""Daily trade check (Ryan 2026-10-25, after a traded-away Ingram moved a Scottie Barnes pick).

Compares every NBA player's team in the stats DB (his latest game this season) with today's ESPN rosters and
writes trades.json: who changed teams. news.context() already ignores any injured player who isn't on today's
roster; this list is the daily record of moves, and the loop prints it so a new trade shows up in the log.

  python3 trades.py        check now, write trades.json, print the moves
"""
import json, os, sqlite3, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import news                     # noqa: E402
from parlays import NBA_CODE    # noqa: E402

OUT = os.path.join(HERE, "trades.json")
DB = os.path.join(HERE, "data", "nba.sqlite")


def rosters(league, c):
    """{player name lower: team} from today's ESPN rosters for every team."""
    out = {}
    for team in sorted(set(news.team_abbrs(league, c).values())):
        for name in news.roster_pos(league, team, c):
            out[name] = news.norm(team)
    return out


def db_teams(since):
    """{player name lower: (name, team, last date)} from his latest game since `since`."""
    con = sqlite3.connect(DB)
    rows = con.execute("select player, team, max(date) from player_games where date >= ? group by player",
                       (since,)).fetchall()
    return {p.lower(): (p, news.norm(NBA_CODE.get(t, t)), d) for p, t, d in rows if p and t}


def main():
    c = news._cache()
    now = rosters("nba", c)
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 150 * 86400))
    moves = []
    for key, (name, was, last) in db_teams(since).items():
        cur = now.get(key)
        if cur and cur != was:
            moves.append({"player": name, "was": was, "now": cur, "last_game_for_old_team": last})
    moves.sort(key=lambda m: m["player"])
    json.dump({"checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rostered": len(now),
               "nba": moves}, open(OUT, "w"), indent=1)
    json.dump(c, open(news.CACHE, "w"))
    print(f"Trades: {len(moves)} NBA players on a new team vs our stats ({len(now)} rostered)"
          + (": " + ", ".join(f"{m['player']} {m['was']}->{m['now']}" for m in moves[:12]) if moves else ""))


if __name__ == "__main__":
    main()
