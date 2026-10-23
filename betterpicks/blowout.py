"""Blowout chance and how teams sit their starters (Ryan 2026-10-23).

Ryan: "calculate the percentage on a blowout happening that game ... study which teams take out their starters for
benchwarmers and how often they do it for a blowout". Study only for now: nothing here changes a pick yet.

- p_blowout(spread): chance the final margin is 20+ either way, from the pregame spread. Final margin vs the
  spread is close to normal; its spread (sigma) is fit from every regular-season game since 2020-21 with a line.
- Team study: for each team, starters' average minutes in games decided by 20+ (split by winning and losing side)
  vs games decided by 10 or less, over the last two regular seasons. "Cut" = share of minutes starters lose.
- expected_minutes(player_minutes, team, spread): blends normal minutes and blowout minutes by the blowout chance,
  which is what a prop model would use later.

Usage: python3 -I blowout.py study            (writes data/blowout_study.json, prints the table)
       python3 -I blowout.py chance <spread>  (e.g. -8.5)
       python3 -I blowout.py game <team> <spread> <player minutes>
"""
import json, math, os, sqlite3, sys
from statistics import mean, pstdev

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "nba.sqlite")
OUT = os.path.join(HERE, "data", "blowout_study.json")
BLOWOUT, CLOSE = 20, 10
SEASONS = ("2024-25", "2025-26")


def _db():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def _cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def fit_sigma(c=None):
    """Std dev of (home margin + home spread) over regular-season games with a line."""
    c = c or _db()
    rows = c.execute("select home_score - away_score + spread_home from games where season_type='regular' "
                     "and spread_home is not null and home_score > 0").fetchall()
    return pstdev([r[0] for r in rows]), len(rows)


def p_blowout(spread, sigma, margin=BLOWOUT):
    """spread = the favorite's or home line as books write it (-8.5 = favored by 8.5). Returns
    (either side, by the favored side, by the other side)."""
    mu = -spread
    win_big = 1 - _cdf((margin - 0.5 - mu) / sigma)
    lose_big = _cdf((-margin + 0.5 - mu) / sigma)
    return win_big + lose_big, win_big, lose_big


def check_calibration(c, sigma):
    """Predicted vs actual blowout rate by spread bucket, so the model is checked against real games."""
    rows = c.execute("select spread_home, home_score - away_score from games where season_type='regular' "
                     "and spread_home is not null and home_score > 0").fetchall()
    out = []
    for lo, hi in ((0, 3), (3, 6), (6, 9), (9, 12), (12, 30)):
        g = [(s, m) for s, m in rows if lo <= abs(s) < hi]
        if g:
            pred = mean(p_blowout(s, sigma)[0] for s, _ in g)
            act = mean(abs(m) >= BLOWOUT for _, m in g)
            out.append({"spread": f"{lo}-{hi}", "games": len(g), "predicted": round(pred, 3), "actual": round(act, 3)})
    return out


def team_study(c):
    tg = {(g, t): m for g, t, m in c.execute(
        f"select game_id, team, margin from team_games where season_type='regular' and season in {SEASONS}")}
    starters = c.execute(f"select game_id, team, player, minutes from player_games where season_type='regular' "
                         f"and season in {SEASONS} and starter=1 and minutes > 0").fetchall()
    by = {}
    for g, t, p, mins in starters:
        m = tg.get((g, t))
        if m is None:
            continue
        kind = "won_big" if m >= BLOWOUT else "lost_big" if m <= -BLOWOUT else "close" if abs(m) <= CLOSE else None
        if kind:
            by.setdefault(t, {}).setdefault(kind, {}).setdefault(g, []).append(mins)
    teams = {}
    for t, k in by.items():
        avg = lambda kind: mean(mean(v) for v in k.get(kind, {}).values()) if k.get(kind) else None
        close, won, lost = avg("close"), avg("won_big"), avg("lost_big")
        games = c.execute(f"select count(*), sum(abs(margin) >= {BLOWOUT}) from team_games where team=? and "
                          f"season_type='regular' and season in {SEASONS}", (t,)).fetchone()
        cut = lambda x: round(1 - x / close, 3) if x and close else None
        teams[t] = {"games": games[0], "blowouts": games[1], "blowout_rate": round(games[1] / games[0], 3),
                    "starter_min_close": round(close, 1) if close else None,
                    "starter_min_won_big": round(won, 1) if won else None, "won_big_games": len(k.get("won_big", {})),
                    "starter_min_lost_big": round(lost, 1) if lost else None, "lost_big_games": len(k.get("lost_big", {})),
                    "cut_when_winning_big": cut(won), "cut_when_losing_big": cut(lost)}
    return teams


def study():
    c = _db()
    sigma, n = fit_sigma(c)
    doc = {"blowout_margin": BLOWOUT, "close_margin": CLOSE, "seasons": SEASONS, "sigma": round(sigma, 2),
           "games_fit": n, "calibration": check_calibration(c, sigma), "teams": team_study(c)}
    json.dump(doc, open(OUT, "w"), indent=1)
    return doc


def load():
    return json.load(open(OUT)) if os.path.exists(OUT) else study()


def expected_minutes(player_min, team, spread, doc=None):
    """Player's expected minutes once blowouts are priced in. spread is the team's own line (-8.5 = favored)."""
    doc = doc or load()
    _, big_win, big_loss = p_blowout(spread, doc["sigma"])
    t = doc["teams"].get(team, {})
    cw, cl = t.get("cut_when_winning_big") or 0, t.get("cut_when_losing_big") or 0
    return player_min * (1 - big_win * cw - big_loss * cl), big_win, big_loss


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "study"
    if cmd == "study":
        d = study()
        print(f"sigma {d['sigma']} pts over {d['games_fit']} games")
        for r in d["calibration"]:
            print(f"  spread {r['spread']:>5}: predicted {r['predicted']:.0%} blowouts, actual {r['actual']:.0%} ({r['games']} games)")
        rows = sorted(d["teams"].items(), key=lambda kv: -(kv[1]["cut_when_winning_big"] or 0))
        print("team  blowout%  starters close / won big / lost big   cut win / cut loss")
        for t, v in rows:
            print(f"{t:<5} {v['blowout_rate']:>7.0%}   {v['starter_min_close']} / {v['starter_min_won_big']} / {v['starter_min_lost_big']}"
                  f"   {v['cut_when_winning_big']} / {v['cut_when_losing_big']}")
    elif cmd == "chance":
        d = load(); e, w, l = p_blowout(float(sys.argv[2]), d["sigma"])
        print(f"blowout (20+) chance {e:.0%}: favored side by 20+ {w:.0%}, other side by 20+ {l:.0%}")
    elif cmd == "game":
        m, w, l = expected_minutes(float(sys.argv[4]), sys.argv[2].upper(), float(sys.argv[3]))
        print(f"expected minutes {m:.1f} (win big {w:.0%}, lose big {l:.0%})")
