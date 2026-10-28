"""Daily NBA defender-matchup pull, run on Ryan's PC (home connection; stats.nba.com blocks cloud/datacenter IPs).
Ryan OK'd 2026-10-28. Pulls each finished game's boxscorematchupsv3 (who guarded whom, possessions, points allowed)
for the given PT dates (default: yesterday) and writes one JSON per date to OUT. Honest User-Agent, no header spoofing:
if the site refuses, it says so and stops. Standard library only.
  python nba_matchups.py [YYYY-MM-DD ...] [--out D:\\BetterPicks\\nba_matchups] [--test]"""
import json, os, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://stats.nba.com/stats/"
UA = {"User-Agent": "BetterPicks matchup pull (personal use)", "Accept": "application/json"}


def get(path):
    req = urllib.request.Request(BASE + path, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def game_ids(date):
    sb = get(f"scoreboardv3?GameDate={date}&LeagueID=00")
    return [(g["gameId"], g.get("gameStatus")) for g in sb.get("scoreboard", {}).get("games", [])]


def rows(gid):
    """Flatten boxscorematchupsv3 into one row per offensive player x defender."""
    bm = get(f"boxscorematchupsv3?GameID={gid}").get("boxScoreMatchups", {})
    out = []
    for side in ("homeTeam", "awayTeam"):
        t = bm.get(side) or {}
        for p in t.get("players", []):
            for m in p.get("matchups", []):
                s = m.get("statistics", {})
                out.append({"game": gid, "team": t.get("teamTricode"), "player": f"{p.get('firstName','')} {p.get('familyName','')}".strip(),
                            "player_id": p.get("personId"), "defender": f"{m.get('firstName','')} {m.get('familyName','')}".strip(),
                            "defender_id": m.get("personId"), **{k: s.get(k) for k in (
                                "matchupMinutesSort", "partialPossessions", "playerPoints", "matchupFieldGoalsMade",
                                "matchupFieldGoalsAttempted", "matchupThreePointersMade", "matchupThreePointersAttempted",
                                "matchupAssists", "matchupTurnovers", "switchesOn", "helpBlocks")}})
    return out


def main(argv):
    out_dir = r"D:\BetterPicks\nba_matchups"
    if "--out" in argv:
        out_dir = argv[argv.index("--out") + 1]
        del argv[argv.index("--out"):argv.index("--out") + 2]
    test = "--test" in argv
    dates = [a for a in argv if a[:2] == "20"] or [(datetime.now(timezone(timedelta(hours=-7))) - timedelta(days=1)).strftime("%Y-%m-%d")]
    if test:
        t = time.time()
        try:
            n = len(get("boxscorematchupsv3?GameID=0022600001").get("boxScoreMatchups", {}).get("homeTeam", {}).get("players", []))
            print(f"OK: stats.nba.com reachable from here ({time.time()-t:.1f}s, {n} players in test game)")
        except Exception as e:
            print(f"BLOCKED: {e} ({time.time()-t:.1f}s)")
        return
    os.makedirs(out_dir, exist_ok=True)
    for d in dates:
        allrows, done = [], 0
        for gid, status in game_ids(d):
            if status != 3:          # 3 = final
                continue
            allrows += rows(gid)
            done += 1
            time.sleep(2)            # be polite
        path = os.path.join(out_dir, f"{d}.json")
        with open(path, "w") as f:
            json.dump({"date": d, "games": done, "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows": allrows}, f)
        print(f"{d}: {done} games, {len(allrows)} matchup rows -> {path}")


if __name__ == "__main__":
    main(sys.argv[1:])
