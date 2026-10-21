#!/usr/bin/env python3
"""Rebuild the slim stats DBs the droplet uses (betterpicks/data/*.sqlite.gz) from the project's full research DBs.
Only the columns parlays.py and news.py read are kept, so the droplet stays within its limits. Practice only.
  python3 slim_db.py [full_data_dir]      (default /mnt/project-files/betterpicks/data)
"""
import gzip, os, shutil, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else "/mnt/project-files/betterpicks/data"
KEEP = {
    "nba": {"player_games": ["date", "season_type", "player", "team", "opp", "minutes", "pts", "reb", "ast", "fg3m", "pra", "pr",
                             "pa", "ra", "stl", "blk", "tov", "stocks"]},   # team + season_type: Ryan 2026-10-21 (moved players)
    "mlb": {"pitcher_games": ["date", "player", "opp", "so", "outs", "er", "h"],
            "batter_games": ["date", "player", "opp", "h", "1b", "r", "rbi", "hr", "so", "total_bases", "hits_runs_rbis"]},
    "nfl": {"player_games": ["gameday", "season_type", "team", "player_name", "player_display_name", "opponent_team",
                             "def_tackles_solo", "def_tackle_assists", "def_pass_defended", "passing_yards",
                             "passing_tds", "passing_interceptions", "completions", "attempts", "carries", "rushing_yards",
                             "receptions", "receiving_yards"]},
    "soccer": {"player_matches": ["date", "player", "opp", "goals", "assists", "shots", "shots_on_target", "saves"]},
}


def main():
    for db, tables in KEEP.items():
        src = os.path.join(SRC, db + ".sqlite")
        out = os.path.join(HERE, "data", db + ".sqlite")
        if os.path.exists(out):
            os.remove(out)
        con = sqlite3.connect(out)
        con.execute("ATTACH ? AS full", (src,))
        for t, cols in tables.items():
            q = ", ".join(f'"{c}"' for c in cols)
            con.execute(f'CREATE TABLE "{t}" AS SELECT {q} FROM full."{t}"')
            n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            print(db, t, n, "rows")
        con.commit(); con.execute("DETACH full"); con.execute("VACUUM"); con.close()
        with open(out, "rb") as fi, gzip.GzipFile(out + ".gz", "wb", compresslevel=9, mtime=0) as fo:
            shutil.copyfileobj(fi, fo)
        os.remove(out)


if __name__ == "__main__":
    main()
