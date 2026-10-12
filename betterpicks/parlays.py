#!/usr/bin/env python3
"""BetterPicks practice parlays: PrizePicks-style power plays and Kalshi-style combos. PRACTICE ONLY.

Fair odds for each player prop = no-vig price from real sportsbooks at the SAME line (Pinnacle counts double),
blended with the player's recent hit rate from the 5-year stats library (data/*.sqlite).

  python3 parlays.py build   # needs fresh props.json (python3 books.py props) -> adds today's practice entries
  python3 parlays.py grade   # settles entries once the stats library has the box scores
"""
import json, math, os, sqlite3, sys
from datetime import datetime, timedelta, timezone
from itertools import combinations

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
BOOK = os.path.join(HERE, "book.json")
PROPS = os.path.join(HERE, "props.json")
PT = timezone(timedelta(hours=-7))

SHARP_BOOKS = {"Pinnacle": 2.0}
SKIP_BOOKS = {"AN Consensus", "AN Open", "PrizePicks", "Underdog", "Sleeper", "Kalshi", "Polymarket"}
PP_PAYOUT = {int(k): v for k, v in json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.json")))
             .get("prizepicks_power_payouts", {"2": 3.0, "3": 6.0, "4": 10.0}).items()}   # set from Ryan's real app
STAKE = 10.0
MIN_LEG_P = 0.57                                 # 2-pick power breaks even at 57.7%
MIN_KALSHI_EDGE = 0.03

# prop stat -> (league db, table, SQL expression)
STATS = {
    ("nfl", "pass_yds"): "passing_yards", ("nfl", "rush_yds"): "rushing_yards", ("nfl", "rec_yds"): "receiving_yards",
    ("nfl", "receptions"): "receptions", ("nfl", "rush_att"): "carries", ("nfl", "pass_att"): "attempts",
    ("nfl", "pass_comp"): "completions", ("nfl", "pass_tds"): "passing_tds", ("nfl", "pass_ints"): "passing_interceptions",
    ("nfl", "pass_rush_yds"): "passing_yards + rushing_yards", ("nfl", "rush_rec_yds"): "rushing_yards + receiving_yards",
    ("mlb", "pitcher_ks"): ("pitcher_games", "so"), ("mlb", "pitcher_outs"): ("pitcher_games", "outs"),
    ("mlb", "earned_runs"): ("pitcher_games", "er"), ("mlb", "hits_allowed"): ("pitcher_games", "h"),
    ("mlb", "hits"): ("batter_games", "h"), ("mlb", "total_bases"): ("batter_games", "total_bases"),
    ("mlb", "hrr"): ("batter_games", "hits_runs_rbis"), ("mlb", "singles"): ("batter_games", '"1b"'),
    ("mlb", "hr"): ("batter_games", "hr"), ("mlb", "rbi"): ("batter_games", "rbi"), ("mlb", "runs"): ("batter_games", "r"),
    ("mlb", "batter_ks"): ("batter_games", "so"),
    ("nba", "points"): ("player_games", "pts"), ("nba", "rebounds"): ("player_games", "reb"),
    ("nba", "assists"): ("player_games", "ast"), ("nba", "threes"): ("player_games", "fg3m"),
    ("nba", "pra"): ("player_games", "pra"), ("nba", "pts_reb"): ("player_games", "pr"),
    ("nba", "pts_ast"): ("player_games", "pa"), ("nba", "reb_ast"): ("player_games", "ra"),
    ("nba", "steals"): ("player_games", "stl"), ("nba", "blocks"): ("player_games", "blk"),
    ("nba", "turnovers"): ("player_games", "tov"), ("nba", "stocks"): ("player_games", "stocks"),
    ("epl", "shots_on_target"): ("player_matches", "shots_on_target"), ("epl", "shots_attempted"): ("player_matches", "shots"),
    ("epl", "goals"): ("player_matches", "goals"), ("epl", "assists"): ("player_matches", "assists"),
    ("epl", "goals_assists"): ("player_matches", "goals + assists"), ("epl", "saves"): ("player_matches", "saves"),
}


def load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def save(path, data):
    json.dump(data, open(path + ".tmp", "w"), indent=1)
    os.replace(path + ".tmp", path)


def prob(o):
    o = float(o)
    return 100 / (o + 100) if o > 0 else -o / (-o + 100)


def market_p_over(prop, line):
    """Weighted no-vig P(over) from real sportsbooks quoting this exact line."""
    tot = w = 0.0; n = 0
    for name, s in prop["sources"].items():
        if name in SKIP_BOOKS or s.get("line") != line or s.get("over") is None or s.get("under") is None:
            continue
        po, pu = prob(s["over"]), prob(s["under"])
        wt = SHARP_BOOKS.get(name, 1.0)
        tot += wt * po / (po + pu); w += wt; n += 1
    return (tot / w, n) if n >= 3 else (None, n)


def stat_query(league, stat):
    spec = STATS.get((league, stat))
    if spec is None:
        return None
    if league == "nfl":
        return "nfl.sqlite", f"select {spec}, gameday, opponent_team from player_games where player_display_name=? or player_name=? order by gameday desc"
    table, expr = spec
    return ("soccer.sqlite" if league == "epl" else f"{league}.sqlite"), f"select {expr}, date, opp from {table} where player=? or player=? order by date desc"


_CONN, _ROWS = {}, {}


def rows_for(league, stat, player):
    """All of a player's games for this stat, newest first (cached; DBs copied locally once for speed)."""
    q = stat_query(league, stat)
    if not q or not os.path.exists(os.path.join(DATA, q[0])):
        return None
    key = (league, stat, player)
    if key not in _ROWS:
        if q[0] not in _CONN:
            local = os.path.join("/tmp", "betterpicks-" + q[0])
            src = os.path.join(DATA, q[0])
            if not os.path.exists(local) or os.path.getmtime(local) < os.path.getmtime(src):
                import shutil; shutil.copyfile(src, local)
            _CONN[q[0]] = sqlite3.connect(local)
        _ROWS[key] = _CONN[q[0]].execute(q[1], (player, player)).fetchall()
    return _ROWS[key]


def history(league, stat, player, line, opp=None, n=12):
    rows = rows_for(league, stat, player)
    if rows is None:
        return None
    vals = [r[0] for r in rows if r[0] is not None][:n]
    if len(vals) < 6:
        return None
    out = {"games": len(vals), "hit_rate": round(sum(v > line for v in vals) / len(vals), 3),
           "avg": round(sum(vals) / len(vals), 1)}
    if opp:
        vs = [r[0] for r in rows if r[2] == opp and r[0] is not None]
        if vs:
            out["vs_opp"] = f"{sum(v > line for v in vs)}/{len(vs)} over vs {opp}, avg {sum(vs)/len(vs):.1f}"
    return out


def opp_of(prop):
    g = prop.get("game", "").replace(" vs ", " @ ").split(" @ ")
    return next((t for t in g if t != prop.get("team")), None) if len(g) == 2 else None


def fair(prop, line, side):
    mp, nb = market_p_over(prop, line)
    if mp is None:
        return None
    h = history(prop["league"], prop["stat"], prop["player"], line, opp_of(prop))
    p_over = 0.8 * mp + 0.2 * h["hit_rate"] if h else mp
    p = p_over if side == "over" else 1 - p_over
    return {"p": round(p, 3), "market_p": round(mp if side == "over" else 1 - mp, 3), "books": nb, "hist": h}


def describe(prop, side, line, f):
    h = f["hist"]
    s = f"{prop['player']} {side} {line} {prop['stat'].replace('_', ' ')}: {f['books']} books price it {f['market_p']:.0%}"
    if h:
        s += f"; hit {h['hit_rate']:.0%} of last {h['games']} (avg {h['avg']})"
        if h.get("vs_opp"):
            s += f"; {h['vs_opp']}"
    return s + f". Fair {f['p']:.0%}."


def build():
    book = load(BOOK, None)
    props = load(PROPS, {}).get("props", [])
    now = datetime.now(timezone.utc)
    today = now.astimezone(PT).date().isoformat()
    book.setdefault("parlays", [])
    if any(e["date"] == today for e in book["parlays"]):
        print("already built today"); return
    legs = []
    for pr in props:
        if (pr["league"], pr["stat"]) not in STATS:
            continue
        if not pr.get("start"):
            continue
        start = datetime.fromisoformat(pr["start"].replace("Z", "+00:00"))
        if not (now < start < now + timedelta(hours=40)):
            continue
        pp = pr["sources"].get("PrizePicks")
        if pp and pp.get("line") is not None:
            for side in ("over", "under"):
                f = fair(pr, pp["line"], side)
                if f and f["p"] >= MIN_LEG_P:
                    legs.append({"app": "PrizePicks", "prop": pr, "side": side, "line": pp["line"], **f})
        for rung in (pr["sources"].get("Kalshi") or {}).get("ladder", []):
            for side, ask in (("over", rung.get("yes_ask")), ("under", rung.get("no_ask"))):
                if not ask or ask <= 0.02 or ask >= 0.95:
                    continue
                f = fair(pr, rung["line"], side)
                fee = 0.07 * ask * (1 - ask)
                if f and f["p"] - ask - fee >= MIN_KALSHI_EDGE:
                    legs.append({"app": "Kalshi", "prop": pr, "side": side, "line": rung["line"], "price": ask,
                                 "ticker": rung.get("ticker"), "edge": round(f["p"] - ask - fee, 3), **f})
    # Ryan's rule: no parlay under the win chance in rules.json (55% since 2026-10-12, was 65%) (rules.json parlay_min_win_p / parlay_mode)
    r = load(os.path.join(HERE, "rules.json"), {})
    min_win, mode = r.get("parlay_min_win_p", 0.65), r.get("parlay_mode", "whole")
    ok = (lambda c: math.prod(l["p"] for l in c) >= min_win) if mode == "whole" else (lambda c: min(l["p"] for l in c) >= min_win)
    entries = []
    pp_legs = sorted([l for l in legs if l["app"] == "PrizePicks"], key=lambda l: -l["p"])[:12]
    for size in (2, 3):
        best = None
        for combo in combinations(pp_legs, size):
            if len({l["prop"]["game"] for l in combo}) < size or not ok(combo):   # one leg per game; min win-chance rule
                continue
            ev = math.prod(l["p"] for l in combo) * PP_PAYOUT[size] - 1
            if best is None or ev > best[0]:
                best = (ev, combo)
        if best and best[0] > 0:
            entries.append(make_entry(today, "PrizePicks", f"{size}-pick power play ({PP_PAYOUT[size]:g}x)",
                                      best[1], PP_PAYOUT[size], best[0]))
    k_legs = sorted([l for l in legs if l["app"] == "Kalshi"], key=lambda l: -l["p"])
    seen, k_pick = set(), []
    for l in k_legs:
        if l["prop"]["game"] not in seen and ok(k_pick + [l]):
            k_pick.append(l); seen.add(l["prop"]["game"])
        if len(k_pick) == 3:
            break
    if len(k_pick) >= 2:
        # Ryan 2026-10-08: don't list a Kalshi multiplier we don't know; he sends the real one later
        # (python3 parlays.py payout <date> <mult>). The estimate is used only to check the combo is worth it.
        est = kalshi_combo_mult([l["price"] for l in k_pick])
        if math.prod(l["p"] for l in k_pick) * est > 1:
            entries.append(make_entry(today, "Kalshi", f"{len(k_pick)}-leg combo", k_pick, None, None))
    book["parlays"] += entries
    save(BOOK, book)
    for e in entries:
        print(e["app"], e["type"], f"pays {e['payout_x']}x, EV {e['ev_pct']}%" if e["payout_x"] else "payout: waiting on Ryan")
        for l in e["legs"]:
            print("   ", l["why"])
    if not entries:
        print("no +EV practice entries today")


def recent_case(league, stat, player, side, line, n=5):
    """Plain-words recent form: last n results and how many beat the line on our side."""
    rows = rows_for(league, stat, player) or []
    vals = [r[0] for r in rows if r[0] is not None][:n]
    if not vals:
        return ""
    hits = sum((v > line) if side == "over" else (v < line) for v in vals)
    return f"Last {len(vals)} games: {', '.join(str(int(v)) for v in vals)} ({hits} of {len(vals)} {side} {line})."


def kalshi_combo_mult(prices):
    """Kalshi combo payout. Calibrated from a real Kalshi slip (2026-10-07: legs 46/49/58% -> 7.09x):
    combo price = product of leg prices x markup (rules.json kalshi_combo_markup, ~1.079)."""
    markup = load(os.path.join(HERE, "rules.json"), {}).get("kalshi_combo_markup", 1.079)
    return round(1 / (math.prod(prices) * markup), 2)


def make_entry(today, app, kind, combo, mult, ev):
    return {"date": today, "app": app, "type": kind, "stake": STAKE, "payout_x": round(mult, 2) if mult else None,
            "win_p": round(math.prod(l["p"] for l in combo), 3), "ev_pct": round(100 * ev, 1) if ev is not None else None,
            "status": "open", "pnl": 0,
            "legs": [{"league": l["prop"]["league"], "player": l["prop"]["player"], "team": l["prop"].get("team"),
                      "stat": l["prop"]["stat"], "side": l["side"], "line": l["line"], "price": l.get("price"),
                      "ticker": l.get("ticker"), "game": l["prop"]["game"], "start": l["prop"]["start"],
                      "p": l["p"], "why": describe(l["prop"], l["side"], l["line"], l),
                      "case": recent_case(l["prop"]["league"], l["prop"]["stat"], l["prop"]["player"], l["side"], l["line"]),
                      "result": None} for l in combo]}


def actual(leg):
    rows = rows_for(leg["league"], leg["stat"], leg["player"])
    if rows is None:
        return "nodata"
    d = datetime.fromisoformat(leg["start"].replace("Z", "+00:00")).astimezone(PT).date()
    for v, day, _ in rows:
        if day and abs((datetime.fromisoformat(day[:10]).date() - d).days) <= 1:
            return v
    return "void" if datetime.now(PT).date() - d > timedelta(days=3) else None


def grade():
    book = load(BOOK, None)
    for e in book.get("parlays", []):
        if e["status"] != "open":
            continue
        for l in e["legs"]:
            if l["result"] is None:
                v = actual(l)
                if v is not None:
                    l["actual"] = v if isinstance(v, (int, float)) else None
                    l["result"] = "void" if v in ("void", "nodata") else (
                        "push" if v == l["line"] else ("won" if (v > l["line"]) == (l["side"] == "over") else "lost"))
        res = [l["result"] for l in e["legs"]]
        if None in res:
            continue
        live = [l for l in e["legs"] if l["result"] not in ("void", "push")]
        if any(l["result"] == "lost" for l in live):
            e["status"], e["pnl"] = "lost", -e["stake"]
        elif len(live) < 2:
            e["status"], e["pnl"] = "void", 0
        else:
            mult = (PP_PAYOUT.get(len(live), e["payout_x"]) if e["app"] == "PrizePicks"
                    else (e["payout_x"] if len(live) == len(e["legs"]) else kalshi_combo_mult([l["price"] for l in live])))
            if not mult or (e["app"] == "Kalshi" and len(live) < len(e["legs"]) and e.get("payout_x") is None):
                e["status"], e["pnl"], e["payout_pending"] = "won", 0, True   # counted once Ryan sends the multiplier
            else:
                e["status"], e["pnl"] = "won", round(e["stake"] * (mult - 1), 2)
    book["bankroll"] = round(book["start_bankroll"] + sum(p["pnl"] for p in book["picks"])
                             + sum(e["pnl"] for e in book.get("parlays", [])), 2)
    save(BOOK, book)
    done = [e for e in book.get("parlays", []) if e["status"] in ("won", "lost")]
    print("parlays settled", len(done), "won", sum(e["status"] == "won" for e in done), "bankroll", book["bankroll"])


def payout(date, mult, app="Kalshi"):
    """Set the real multiplier Ryan reports for that day's combo (and settle it if it already won)."""
    book = load(BOOK, None)
    for e in book.get("parlays", []):
        if e["app"] == app and e["date"] == date and e.get("payout_x") is None:
            e["payout_x"] = float(mult)
            e["ev_pct"] = round(100 * (e["win_p"] * e["payout_x"] - 1), 1)
            if e.get("payout_pending"):
                e["pnl"] = round(e["stake"] * (e["payout_x"] - 1), 2); e.pop("payout_pending")
            print("set", app, date, e["type"], e["payout_x"], "x")
    book["bankroll"] = round(book["start_bankroll"] + sum(p["pnl"] for p in book["picks"])
                             + sum(e["pnl"] for e in book.get("parlays", [])), 2)
    save(BOOK, book)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "payout":
        payout(*sys.argv[2:]); sys.exit()
    {"build": build, "grade": grade}[sys.argv[1] if len(sys.argv) > 1 else "build"]()
