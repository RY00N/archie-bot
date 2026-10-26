#!/usr/bin/env python3
"""BetterPicks practice parlays: PrizePicks-style power plays and Kalshi-style combos. PRACTICE ONLY.

Fair odds for each player prop = no-vig price from real sportsbooks at the SAME line (Pinnacle counts double),
blended with the player's recent hit rate from the 5-year stats library (data/*.sqlite).

  python3 parlays.py build   # needs fresh props.json (python3 books.py props) -> adds today's practice entries
  python3 parlays.py grade   # settles entries once the stats library has the box scores
"""
import json, math, os, sqlite3, sys, time
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
STAKE = float(json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.json"))).get("parlay_stake", 10.0))   # Ryan 2026-10-12: 1 unit = $5
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


_CONN, _ROWS, _TEAM, _MIN, _ROLE = {}, {}, {}, {}, {}
# ESPN codes in the stats DB vs the codes on Kalshi/sportsbook props
NBA_CODE = {"GS": "GSW", "NY": "NYK", "NO": "NOP", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}


def rows_for(league, stat, player):
    """All of a player's games for this stat, newest first (cached; DBs copied locally once for speed).
    NBA (Ryan 2026-10-21, lots of players changed teams): only games for his current team count, and
    preseason games are in the DB, so a moved player's history starts with his new team."""
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
        rows = None
        if league == "nba":
            try:
                rows = _CONN[q[0]].execute(q[1].replace(", opp from", ", opp, team, minutes, season_type, starter from"),
                                           (player, player)).fetchall()
            except sqlite3.OperationalError:   # older slim DB without starter (or team/minutes)
                try:
                    rows = [r + (None, None) for r in _CONN[q[0]].execute(
                        q[1].replace(", opp from", ", opp, team, minutes from"), (player, player)).fetchall()]
                except sqlite3.OperationalError:
                    rows = None
            if rows is None:
                try:
                    rows = [r + (None, None, None) for r in _CONN[q[0]].execute(q[1].replace(", opp from", ", opp, team from"),
                                                                    (player, player)).fetchall()]
                except sqlite3.OperationalError:
                    rows = None
            if rows:
                _TEAM[(league, player)] = NBA_CODE.get(rows[0][3], rows[0][3])
                # Ryan 2026-10-21 (Trae Young): skip limited-minutes games (preseason cameos, injury ramp-ups);
                # "normal" = his median minutes over his last 60 games on any team
                mins = sorted(r[4] for r in rows[:60] if r[4])
                norm = mins[len(mins) // 2] if mins else 0
                rows = [r for r in rows if r[3] == rows[0][3]]
                # Ryan 2026-10-24 (Brook Lopez: started last season, comes off the bench now): only games in the role he
                # has this season count, starter or bench, so last season's starter games don't prop up a bench player
                role = this_role(rows)
                _ROLE[(league, player)] = role   # pregame.py: is he a usual starter?
                if role is not None:
                    rows = [r for r in rows if r[6] == role]
                    mins = sorted(r[4] for r in rows[:60] if r[4])
                    norm = mins[len(mins) // 2] if mins else 0
                _MIN[(league, player)] = {(r[1], r[2]): r[4] for r in rows if r[4]}   # for news.minutes_split
                rows = [r[:3] for r in rows if not norm or r[4] is None or r[4] >= 0.7 * norm]
        _ROWS[key] = rows if rows is not None else _CONN[q[0]].execute(q[1], (player, player)).fetchall()
    return _ROWS[key]


def this_role(rows):
    """1 (starter) or 0 (bench) from this season's regular-season games on his team, else None. rows are newest
    first (value, date, opp, team, minutes, season_type, starter); this season = within 150 days of the newest game."""
    if not rows or rows[0][6] is None:
        return None
    cut = time.strftime("%Y-%m-%d", time.gmtime(time.mktime(time.strptime(str(rows[0][1])[:10], "%Y-%m-%d")) - 150 * 86400))
    flags = [r[6] for r in rows if r[5] == "regular" and r[6] is not None and str(r[1])[:10] >= cut]
    return (1 if 2 * sum(flags) >= len(flags) else 0) if flags else None


def moved(league, player, team):
    """True when the prop has the player on a team he has no games for yet (just traded or signed)."""
    t = _TEAM.get((league, player))
    return bool(team and t and t != team)


def history(league, stat, player, line, opp=None, n=12, team=None):
    rows = rows_for(league, stat, player)
    if rows is None or moved(league, player, team):
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


_GAMES, _TDATES, _ROT = {}, {}, {}
DB_CODE = {"nba": {v: k for k, v in NBA_CODE.items()}, "nfl": {"LAR": "LA", "JAC": "JAX", "WSH": "WAS"}}
# per league: date column, regular/playoff types, missed team games that make a "back" spot, rotation size + stat
SIT = {"nba": {"date": "date", "real": ("regular", "playoffs", "play-in"), "missed": 3, "size": 8, "rot": "pra"},
       "nfl": {"date": "gameday", "real": ("REG", "POST"), "missed": 1, "size": 11,
               "rot": "coalesce(def_tackles_solo,0) + coalesce(def_tackle_assists,0) + 2 * coalesce(def_pass_defended,0)"}}


def career_games(league, stat, player):
    """Every real game (no preseason) for this stat, oldest first: (value, date, opp, team, season_type)."""
    key = (league, stat, player)
    if key not in _GAMES:
        q = stat_query(league, stat)
        _GAMES[key] = []
        if q and rows_for(league, stat, player) is not None:
            col = "opponent_team from" if league == "nfl" else "opp from"
            try:
                rows = _CONN[q[0]].execute(q[1].replace(", " + col, f", {col[:-5]}, team, season_type from"),
                                           (player, player)).fetchall()
                _GAMES[key] = [r for r in reversed(rows) if r[0] is not None and r[4] in SIT[league]["real"]]
            except sqlite3.OperationalError:   # older slim DB without team/season_type
                pass
    return _GAMES[key]


def team_dates(league, team):
    """Dates the team played a real game, oldest first."""
    if (league, team) not in _TDATES:
        c, d = _CONN.get(f"{league}.sqlite"), SIT[league]["date"]
        try:
            ph = ",".join("?" * len(SIT[league]["real"]))
            _TDATES[(league, team)] = [r[0][:10] for r in c.execute(
                f"select distinct {d} from player_games where team=? and season_type in ({ph}) order by 1",
                (team, *SIT[league]["real"]))] if c else []
        except sqlite3.OperationalError:
            _TDATES[(league, team)] = []
    return _TDATES[(league, team)]


def missed(league, team, after, before):
    """How many real games the team played strictly between two dates (games the player sat out)."""
    return sum(after < d < before for d in team_dates(league, team))


def situations(prop, line):
    """Ryan 2026-10-21: how a player does in the spot he's in tonight (NBA, NFL).
    debut = first real game for a new team; back = first game after missing team games (injury, illness, rest:
    3+ NBA games, 1+ NFL game); vs = career games against tonight's opponent, each weighted by how much of that
    night's opposing rotation is still in its current one. Returns [(name, n, avg, over_rate, lineup_share)]."""
    lg = prop.get("league")
    if lg not in SIT or not prop.get("start"):
        return []
    spots = _spots(lg, prop["stat"], prop["player"], prop["start"][:10], prop.get("team"), prop.get("game", ""))
    res = []
    for n, rs, ws, share in spots:
        rate = sum(w * (r[0] > line) for w, r in zip(ws, rs)) / sum(ws)
        res.append((n, len(rs), round(sum(r[0] for r in rs) / len(rs), 1), rate, share))
    return res


_SPOTS = {}


def _spots(lg, stat, player, day, prop_team, game):
    """The line-independent part of situations(), cached: [(name, games, weights, lineup_share)]."""
    key = (lg, stat, player, day, prop_team, game)
    if key not in _SPOTS:
        _SPOTS[key] = _find_spots(lg, stat, player, day, prop_team, game)
    return _SPOTS[key]


def _find_spots(lg, stat, player, day, prop_team, game):
    g = [r for r in career_games(lg, stat, player) if r[1][:10] < day]   # only games before tonight
    if len(g) < 6:
        return []
    db = lambda t: DB_CODE[lg].get(t, t)
    team = db(prop_team) if prop_team else g[-1][3]
    sides = [db(t) for t in game.replace(" vs ", " @ ").split(" @ ")]
    opp = next((t for t in sides if t != team), None) if len(sides) == 2 and team in sides else None
    debuts = [g[i] for i in range(1, len(g)) if g[i][3] != g[i - 1][3]]
    need = SIT[lg]["missed"]
    backs = [g[i] for i in range(1, len(g)) if g[i][3] == g[i - 1][3]
             and missed(lg, g[i][3], g[i - 1][1][:10], g[i][1][:10]) >= need]
    out = []
    if prop_team and g[-1][3] != team and debuts:
        out.append(("debut", debuts))
    if g[-1][3] == team and backs and missed(lg, team, g[-1][1][:10], day) >= need:
        out.append(("back", backs))
    res = [(n, rs, [1.0] * len(rs), None) for n, rs in out]
    vs = [r for r in g if opp and r[2] == opp]
    if len(vs) >= 3:
        # Ryan 2026-10-21: the opponent's lineup changes, so each past game counts by how much of that night's
        # rotation is still in tonight's rotation (a fully new lineup counts for nothing)
        now = rotation(lg, opp, day, current=True)
        ws = [len(now & rotation(lg, opp, r[1][:10])) / SIT[lg]["size"] if now else 1.0 for r in vs]
        if sum(ws) >= 1.5:
            res.append(("vs " + opp, vs, ws, round(sum(ws) / len(ws), 2) if now else None))
    return res


def rotation(league, team, day, current=False):
    """A team's core: NBA top 8 by PRA, NFL top 11 defenders (tackles + passes defended), in its game on `day`,
    or (current) across its last 3 games before `day`."""
    key = (league, team, day, current)
    if key not in _ROT:
        con, d, cfg = _CONN.get(f"{league}.sqlite"), SIT[league]["date"], SIT[league]
        name = "player_display_name" if league == "nfl" else "player"
        _ROT[key] = set()
        if con is not None:
            try:
                days = ([x for x in team_dates(league, team) if x < day][-3:] if current else [day])
                if days:
                    rows = con.execute(f"select {name}, avg({cfg['rot']}) from player_games where team=? and "
                                       f"substr({d},1,10) in ({','.join('?' * len(days))}) group by 1 order by 2 desc "
                                       f"limit {cfg['size']}", (team, *days)).fetchall()
                    _ROT[key] = {r[0] for r in rows}
            except sqlite3.OperationalError:
                pass
    return _ROT[key]


SIT_WORDS = {"debut": "first games with a new team", "back": "first games back after missing games"}


def situation_text(sits, line):
    out = []
    for n, k, avg, rate, same in sits:
        t = f"In {SIT_WORDS.get(n, 'games ' + n)}: {avg:g} avg over {k} games"
        if same is not None:
            t += f", {same:.0%} of that rotation is still there, so the matchup counts as {rate:.0%} over {line}"
        else:
            t += f", {round(rate * k)} of {k} over {line}"
        out.append(t + ".")
    return " ".join(out)


def opp_of(prop):
    g = prop.get("game", "").replace(" vs ", " @ ").split(" @ ")
    return next((t for t in g if t != prop.get("team")), None) if len(g) == 2 else None


def fair(prop, line, side):
    mp, nb = market_p_over(prop, line)
    if mp is None:
        return None
    h = history(prop["league"], prop["stat"], prop["player"], line, opp_of(prop), team=prop.get("team"))
    sits = [x for x in situations(prop, line) if x[1] >= 3]
    base = 0.8 * mp + 0.2 * h["hit_rate"] if h else mp
    if sits:   # tonight's spot gets up to a 10% say; a matchup vs a mostly new lineup gets less
        conf = [1.0 if x[4] is None else x[4] for x in sits]
        sit_w = 0.1 * sum(conf) / len(conf)
        sit = sum(c * x[3] for c, x in zip(conf, sits)) / sum(conf) if sum(conf) else base
        p_over = (1 - sit_w) * base + sit_w * sit
    else:
        p_over = base
    p = p_over if side == "over" else 1 - p_over
    return {"p": round(p, 3), "market_p": round(mp if side == "over" else 1 - mp, 3), "books": nb, "hist": h,
            "spots": situation_text(sits, line)}


def describe(prop, side, line, f):
    h = f["hist"]
    s = f"{prop['player']} {side} {line} {prop['stat'].replace('_', ' ')}: {f['books']} books price it {f['market_p']:.0%}"
    if h:
        s += f"; hit {h['hit_rate']:.0%} of last {h['games']} (avg {h['avg']})"
        if h.get("vs_opp"):
            s += f"; {h['vs_opp']}"
    if f.get("spots"):
        s += ". " + f["spots"].rstrip(".")
    return s + f". Fair {f['p']:.0%}."


def collect_legs(props, now, horizon_h=40, apps=("PrizePicks", "Kalshi"), min_edge=MIN_KALSHI_EDGE):
    legs = []
    for pr in props:
        if (pr["league"], pr["stat"]) not in STATS:
            continue
        if not pr.get("start"):
            continue
        start = datetime.fromisoformat(pr["start"].replace("Z", "+00:00"))
        if not (now < start < now + timedelta(hours=horizon_h)):
            continue
        pp = pr["sources"].get("PrizePicks")
        if "PrizePicks" in apps and pp and pp.get("line") is not None:
            for side in ("over", "under"):
                f = fair(pr, pp["line"], side)
                if f and f["p"] >= MIN_LEG_P:
                    legs.append({"app": "PrizePicks", "prop": pr, "side": side, "line": pp["line"], **f})
        for rung in ((pr["sources"].get("Kalshi") or {}).get("ladder", []) if "Kalshi" in apps else []):
            for side, ask in (("over", rung.get("yes_ask")), ("under", rung.get("no_ask"))):
                if not ask or ask <= 0.02 or ask >= 0.95:
                    continue
                f = fair(pr, rung["line"], side)
                fee = 0.07 * ask * (1 - ask)
                if f and f["p"] - ask - fee >= min_edge:
                    legs.append({"app": "Kalshi", "prop": pr, "side": side, "line": rung["line"], "price": ask,
                                 "ticker": rung.get("ticker"), "edge": round(f["p"] - ask - fee, 3), **f})
    # Ryan 2026-10-13 20:00Z: real-life context (injuries, illness, personal news) before a leg is used
    try:
        if not os.path.exists(os.path.join(HERE, "news.py")):   # older droplet scanner doesn't copy news.py yet
            src = os.path.join(os.environ.get("BP_REPO", "/home/archie/bp-repo"), "betterpicks", "news.py")
            if os.path.exists(src):
                open(os.path.join(HERE, "news.py"), "w").write(open(src).read())
        sys.path.insert(0, HERE)   # python3 -I leaves the script's folder off sys.path
        import news
        news.ROWS = rows_for   # with/without-teammate splits from the stats DBs
        news.MINUTES = lambda league, player: (rows_for(league, "points", player), _MIN.get((league, player)))[1]
        legs = [l for l in news.adjust(legs) if l["app"] != "Kalshi" or l["edge"] >= min_edge]
    except Exception as ex:
        print("news check skipped:", ex)
    return legs


FEED_URL = "https://raw.githubusercontent.com/RY00N/archie-bot/claude/project-thread-8sdhk6/betterpicks/books_feed.json"


def merge_feed(props):
    """2026-10-14: the droplet gets no Action Network/Pinnacle rows, so add the project's hourly sportsbook prices
    (books_feed.json, made by feed.py) to any prop that lacks them. Ignored if missing or over 3h old."""
    doc = None
    try:
        import urllib.request
        with urllib.request.urlopen(FEED_URL, timeout=15) as resp:
            doc = json.loads(resp.read())
    except Exception:
        for p in (os.path.join(HERE, "books_feed.json"),
                  os.path.join(os.environ.get("BP_REPO", "/home/archie/bp-repo"), "betterpicks", "books_feed.json")):
            if os.path.exists(p):
                doc = load(p, None); break
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(doc["generated_at"].replace("Z", "+00:00"))
    except Exception:
        return 0
    if age > timedelta(hours=3):
        return 0
    feed, n = doc.get("props", {}), 0
    for pr in props:
        books = feed.get("|".join((pr["league"], pr["player"].lower(), pr["stat"], (pr.get("start") or "")[:10])))
        for name, s in (books or {}).items():
            if name not in pr["sources"]:
                pr["sources"][name] = s; n += 1
    return n


def build():
    # Ryan 2026-10-12 06:25Z rules (rules.json): Kalshi only for now; parlays of at most 3 legs (1-2 fine);
    # at most 3 parlays per slate day. Ryan 2026-10-14 01:08Z: no posting window, keep checking Kalshi and post whenever.
    # A can't-pass-up leg (lock_single_edge) goes out any time as a 1-leg pick and doesn't count toward the 3.
    book = load(BOOK, None)
    pdoc = load(PROPS, {})
    props = pdoc.get("props", [])
    merge_feed(props)
    r = load(os.path.join(HERE, "rules.json"), {})
    now = datetime.now(timezone.utc)
    now_pt = now.astimezone(PT)
    apps = tuple(r.get("apps", ["PrizePicks", "Kalshi"]))
    max_legs, max_par = r.get("max_legs", 3), r.get("max_parlays_per_day", 3)
    slate = now_pt.date().isoformat()
    book.setdefault("parlays", [])
    legs = collect_legs(props, now, apps=apps)
    # Ryan's banned player/stat pairs (rules.json "banned"), never picked in any form
    ban = {(b["player"].lower(), st) for b in r.get("banned", []) for st in b.get("stats", [])}
    legs = [l for l in legs if (l["prop"]["player"].lower(), l["prop"]["stat"]) not in ban]
    min_win, mode = r.get("parlay_min_win_p", 0.65), r.get("parlay_mode", "whole")
    under_p = r.get("under_min_p", 0.0)   # Ryan 2026-10-21: unders need more margin
    # Ryan 2026-10-26: lean YES (people can combo them); a NO only goes out when it's a genuine lock
    no_p, no_edge = r.get("no_lock_min_p", 0.0), r.get("no_lock_min_edge", 0.0)
    leg_ok = lambda l, bar: (l["p"] >= max(bar, under_p, no_p) and l["edge"] >= no_edge) if l["side"] == "under" else l["p"] >= bar
    combo_bar = r.get("combo_min_leg_p", min_win)
    ok = (lambda c: math.prod(l["p"] for l in c) >= min_win) if mode == "whole" else (lambda c: all(leg_ok(l, combo_bar) for l in c))
    # still-open picks from earlier days count too: a postponed game's pick carries over, never re-posted (Ryan 2026-10-19)
    shadow(book, legs, now_pt)
    used = {(l["player"], l["stat"]) for e in book["parlays"]
            if e["date"] >= now_pt.date().isoformat() or e.get("status") == "open" for l in e["legs"]}
    entries = []
    # 1) locks: any time, single leg, big Kalshi edge
    today = now_pt.date().isoformat()
    lock_edge, lock_p = r.get("lock_single_edge", 0.10), r.get("lock_single_min_p", 0.60)
    n_locks = sum(1 for e in book["parlays"] if e["date"] == today and e.get("lock"))
    for l in sorted([l for l in legs if l["app"] == "Kalshi" and l["edge"] >= lock_edge and leg_ok(l, lock_p)], key=lambda l: -l["edge"]):
        if n_locks >= r.get("max_locks_per_day", 3):
            break
        if (l["prop"]["player"], l["prop"]["stat"]) in used or l["prop"]["player"] in {u[0] for u in used}:
            continue   # one pick per player (Ryan 2026-10-21 rules: no stacking the same player)
        e = make_entry(today, "Kalshi", f"1-leg lock: {l['prop']['player']} {l['side']} {l['line']} {l['prop']['stat'].replace('_', ' ')}",
                       [l], round(1 / l["price"], 2), None)
        e["lock"] = True; e["ev_pct"] = round(100 * (l["p"] / l["price"] - 1), 1)
        entries.append(e); used.add((l["prop"]["player"], l["prop"]["stat"])); n_locks += 1
    # 2) up to max_par parlays per slate day (Ryan: every game in a parlay is played that same day), 2..max_legs legs, one leg per game, no reused players.
    # Ryan 2026-10-14 01:08Z: no cutoff. Always hunt today's remaining games and tomorrow's (tomorrow's post once its day starts).
    slates = [today, (now_pt.date() + timedelta(days=1)).isoformat()]
    for sd in slates:
        day_legs = [l for l in legs if l["app"] == "Kalshi" and datetime.fromisoformat(l["prop"]["start"].replace("Z", "+00:00")).astimezone(PT).date().isoformat() == sd]
        have = [e for e in book["parlays"] if e["date"] == sd and not e.get("lock")]
        made = lambda: sum(1 for e in entries if e["date"] == sd and not e.get("lock"))
        pool = sorted([l for l in day_legs if (l["prop"]["player"], l["prop"]["stat"]) not in used and combo_ok(l)],
                      key=lambda l: -l["p"])
        while len(have) + made() < max_par:
            pick, games = [], set()
            for l in pool:
                if l["prop"]["game"] not in games and ok(pick + [l]):
                    pick.append(l); games.add(l["prop"]["game"])
                if len(pick) == max_legs:
                    break
            if len(pick) < 2 or math.prod(l["p"] for l in pick) * kalshi_combo_mult([l["price"] for l in pick]) <= 1:
                break
            n = len(have) + made() + 1
            entries.append(make_entry(sd, "Kalshi", f"{len(pick)}-leg combo #{n}", pick, None, None))
            ids = {id(l) for l in pick}
            pool = [l for l in pool if id(l) not in ids and l["prop"]["player"] not in {p["prop"]["player"] for p in pick}]
            used.update((l["prop"]["player"], l["prop"]["stat"]) for l in pick)
    # 2026-10-14: the noon "no Kalshi games left" note went out while MIL@LAD props were open, so only trust an
    # empty pool when props.json was written in the last 15 min, and leave a small hourly trace for the project side.
    try:
        props_at = datetime.fromisoformat(pdoc.get("generated_at", "").replace("Z", "+00:00"))
        props_fresh = (now - props_at).total_seconds() < 900
    except ValueError:
        props_at, props_fresh = None, False
    is_today = lambda pr: bool(pr.get("start")) and datetime.fromisoformat(pr["start"].replace("Z", "+00:00")).astimezone(PT).date().isoformat() == today
    k_today = [pr for pr in props if is_today(pr) and (pr["sources"].get("Kalshi") or {}).get("ladder")]
    book["diag"] = {"props_at": props_at.astimezone(PT).strftime("%Y-%m-%d %H:00 PT") if props_at else None,
                    "props_today": sum(1 for pr in props if is_today(pr)), "kalshi_props_today": len(k_today),
                    "kalshi_priced_today": sum(1 for pr in k_today if (pr["league"], pr["stat"]) in STATS
                                               and fair(pr, pr["sources"]["Kalshi"]["ladder"][0]["line"], "over")),
                    "books_on_kalshi_props": dict(sorted(__import__("collections").Counter(
                        n for pr in k_today for n, s in pr["sources"].items() if s.get("over") is not None).items())),
                    "data_files": sorted(f for f in os.listdir(DATA) if f.endswith(".sqlite")) if os.path.isdir(DATA) else [],
                    "kalshi_legs_today": sum(1 for l in legs if l["app"] == "Kalshi" and
                        datetime.fromisoformat(l["prop"]["start"].replace("Z", "+00:00")).astimezone(PT).date().isoformat() == today)}
    # 3) Ryan 2026-10-13 18:04Z: at least one 1-3 leg pick every day. If noon PT passes with nothing for today,
    # post the best Kalshi combo left in today's games (best expected return, at least a 40% win chance if possible),
    # even under the usual 55% bar. Anything beyond that one still has to clear the normal rules.
    if now_pt.hour >= r.get("daily_pick_after_pt", 12) and not any(e["date"] == today for e in book["parlays"] + entries):
        pool = sorted([l for l in collect_legs(props, now, apps=("Kalshi",), min_edge=0.0)
                       if datetime.fromisoformat(l["prop"]["start"].replace("Z", "+00:00")).astimezone(PT).date().isoformat() == today
                       and (l["prop"]["player"], l["prop"]["stat"]) not in used
                       and (l["side"] != "under" or (l["p"] >= no_p and l["edge"] >= no_edge))], key=lambda l: -l["p"])[:15]
        cands = []
        for size in range(1, max_legs + 1):
            for c in combinations(pool, size):
                if len({l["prop"]["game"] for l in c}) < size or len({l["prop"]["player"] for l in c}) < size:
                    continue
                if size > 1 and not all(combo_ok(l) for l in c):
                    continue
                wp = math.prod(l["p"] for l in c)
                mult = 1 / c[0]["price"] if size == 1 else kalshi_combo_mult([l["price"] for l in c])
                cands.append((wp >= r.get("daily_pick_min_p", 0.40), wp * mult, wp, c, mult))
        if cands:
            _, ev, wp, c, mult = max(cands, key=lambda t: (t[0], t[1] if t[0] else t[2]))
            e = make_entry(today, "Kalshi", f"pick of the day ({len(c)}-leg)", list(c), mult if len(c) == 1 else None, ev - 1)
            e["daily"] = True
            entries.append(e)
            used.update((l["prop"]["player"], l["prop"]["stat"]) for l in c)
        elif not pool and props_fresh:
            book["no_games_left"] = today   # discord_post.py only sends the bad-day note when this is set
    if "PrizePicks" in apps and not any(e["date"] == slate and e["app"] == "PrizePicks" for e in book["parlays"]):
        pp_legs = sorted([l for l in legs if l["app"] == "PrizePicks"], key=lambda l: -l["p"])[:12]
        for size in range(2, max_legs + 1):
            best = None
            for combo in combinations(pp_legs, size):
                if len({l["prop"]["game"] for l in combo}) < size or not ok(combo):
                    continue
                ev = math.prod(l["p"] for l in combo) * PP_PAYOUT[size] - 1
                if best is None or ev > best[0]:
                    best = (ev, combo)
            if best and best[0] > 0:
                entries.append(make_entry(slate, "PrizePicks", f"{size}-pick power play ({PP_PAYOUT[size]:g}x)",
                                          best[1], PP_PAYOUT[size], best[0]))
    book["parlays"] += entries
    save(BOOK, book)
    for e in entries:
        print(e["app"], e["type"], f"pays {e['payout_x']}x" if e["payout_x"] else "payout: check Kalshi")
        for l in e["legs"]:
            print("   ", l["why"])
    if not entries:
        print("no new practice entries")


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


def shadow(book, legs, now_pt, keep_days=14):
    """Log every Kalshi leg that cleared pricing + the injury/matchup check, posted or not, so review.py can grade
    the leg bar on far more legs than we post (idea borrowed from the open-source trader Ryan OK'd 2026-10-19).
    Latest pre-game snapshot per ticker/side wins. Paper only."""
    sh = {(x["ticker"], x["side"]): x for x in book.get("shadow", [])}
    for l in legs:
        if l["app"] != "Kalshi" or not l.get("ticker"):
            continue
        old = sh.get((l["ticker"], l["side"]), {})
        sh[(l["ticker"], l["side"])] = {
            "date": datetime.fromisoformat(l["prop"]["start"].replace("Z", "+00:00")).astimezone(PT).date().isoformat(),
            "ticker": l["ticker"], "league": l["prop"]["league"], "player": l["prop"]["player"], "stat": l["prop"]["stat"],
            "side": l["side"], "line": l["line"], "start": l["prop"]["start"], "p": round(l["p"], 3), "price": l["price"],
            "edge": l.get("edge"), "news": l.get("news"), "result": old.get("result")}
    cut = (now_pt.date() - timedelta(days=keep_days)).isoformat()
    book["shadow"] = sorted((x for x in sh.values() if x["date"] >= cut), key=lambda x: (x["date"], x["ticker"]))


def make_entry(today, app, kind, combo, mult, ev):
    return {"date": today, "app": app, "type": kind, "stake": STAKE, "payout_x": round(mult, 2) if mult else None,
            "win_p": round(math.prod(l["p"] for l in combo), 3), "ev_pct": round(100 * ev, 1) if ev is not None else None,
            "status": "open", "pnl": 0,
            "legs": [{"league": l["prop"]["league"], "player": l["prop"]["player"], "team": l["prop"].get("team"),
                      "stat": l["prop"]["stat"], "side": l["side"], "line": l["line"], "price": l.get("price"),
                      "ticker": l.get("ticker"), "game": l["prop"]["game"], "start": l["prop"]["start"],
                      "p": l["p"], "edge": l.get("edge"), "news": l.get("news"), "ctx_dp": l.get("ctx_dp"), "why": describe(l["prop"], l["side"], l["line"], l),
                      "case": " ".join(x for x in (recent_case(l["prop"]["league"], l["prop"]["stat"], l["prop"]["player"], l["side"], l["line"]),
                                                   l.get("spots")) if x),
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


_kalshi_cache = {}


def kalshi_market(ticker):
    if ticker not in _kalshi_cache:
        import urllib.request
        req = urllib.request.Request(f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}",
                                     headers={"User-Agent": "BetterPicks (1.0)"})
        _kalshi_cache[ticker] = json.load(urllib.request.urlopen(req, timeout=15))["market"]
    return _kalshi_cache[ticker]


_combo_rules = None


def combo_ok(l):
    """Can this leg go in a Kalshi combo? Ryan 2026-10-20: Kalshi wouldn't combo LeBron PRA YES with Duren PRA NO.
    Kalshi's combo collection marks player-prop events yes-only, so a NO (under) prop can only be a single.
    Unknown or unreachable rules: allow only YES legs."""
    global _combo_rules
    if _combo_rules is None:
        try:
            import urllib.request
            req = urllib.request.Request("https://api.elections.kalshi.com/trade-api/v2/multivariate_event_collections/"
                                         "KXMVESPORTSMULTIGAMEEXTENDED-R", headers={"User-Agent": "BetterPicks (1.0)"})
            evs = json.load(urllib.request.urlopen(req, timeout=15))["multivariate_contract"]["associated_events"]
            _combo_rules = {e["ticker"]: e.get("is_yes_only", True) for e in evs}
        except Exception:
            _combo_rules = {}
    parts = (l.get("ticker") or "").split("-")
    event = next(("-".join(parts[:i]) for i in range(len(parts) - 1, 1, -1) if "-".join(parts[:i]) in _combo_rules), None)
    if _combo_rules and event is None:
        return False
    return l["side"] == "over" or _combo_rules.get(event) is False


def kalshi_value(leg):
    """The final number Kalshi settled on ('Shohei Ohtani: 5 hits + runs + rbis' -> 5), if it gives one."""
    import re
    try:
        m = re.search(r":\s*(\d+(?:\.\d+)?)", kalshi_market(leg["ticker"]).get("expiration_value") or "")
    except Exception:
        return None
    return float(m.group(1)) if m else None


def kalshi_result(leg):
    """Kalshi's own settlement decides Kalshi legs: 'won'/'lost'/'void', 'wait' while the market is still open
    (postponed games carry over), or None if Kalshi can't be reached (then the stats decide)."""
    try:
        m = kalshi_market(leg["ticker"])
    except Exception:
        return None
    res = (m.get("result") or "").lower()
    if res in ("yes", "no"):
        return "won" if (res == "yes") == (leg["side"] == "over") else "lost"
    if m.get("status") in ("finalized", "settled") and res in ("void", "all_no", "all_yes", ""):
        return "void"
    return "wait"


def grade():
    book = load(BOOK, None)
    for e in book.get("parlays", []):
        if e["status"] != "open":
            continue
        for l in e["legs"]:
            if l["result"] is None and l.get("ticker"):
                k = kalshi_result(l)
                if k == "wait":   # Ryan 2026-10-18: a delayed game isn't void; Kalshi keeps the market open for the makeup game
                    continue
                if k:
                    v = kalshi_value(l)
                    if v is None:
                        v = actual(l)
                    l["actual"] = v if isinstance(v, (int, float)) else None
                    l["result"], l["graded_by"] = k, "kalshi"
                    continue
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
        elif len(live) < (1 if e.get("lock") or len(e["legs"]) == 1 else 2):   # 1-leg picks of the day count like locks
            e["status"], e["pnl"] = "void", 0
        elif e.get("lock") or len(e["legs"]) == 1:
            e["status"], e["pnl"] = "won", round(e["stake"] * (e["payout_x"] - 1), 2)
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
