"""Real-life context for BetterPicks legs (Ryan 2026-10-13 20:00Z): injuries, illness, a rough personal stretch
(divorce, family matter, bereavement), suspensions, rest days. Sources: ESPN injury reports (per league) and
Google News headlines for the player from the last 3 days. Results are cached for 30 minutes in news_cache.json.

adjust(legs) drops a leg whose player is listed Out/Doubtful/IL/suspended, and shaves the fair win chance of an
"over" when the player is questionable/day-to-day or the headlines carry bad news. Every checked leg gets a
"news" note that shows in Discord. `python3 news.py "<player>" <league>` prints the check for one player.

Ryan 2026-10-19: also look at who else is out. A teammate at the same position being out grows the player's role
(bad for unders), and an opposing starter out at the matching defensive spot makes the matchup easier. Where the
stats database has games without that teammate, the with/without hit rates move the win chance; otherwise a
fresh same-position absence still nudges it. Both feed the leg's win chance (and so its edge), every sport we have.
"""
import json, math, os, re, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "news_cache.json")
TTL = 1800
ESPN = {"mlb": "baseball/mlb", "nba": "basketball/nba", "nfl": "football/nfl", "ncaaf": "football/college-football",
        "epl": "soccer/eng.1"}
OUT = ("out", "doubtful", "injured reserve", "60-day", "15-day", "10-day", "7-day", "suspen", "il")
SHAKY = ("questionable", "day-to-day", "probable")
BAD = re.compile(r"\b(injur\w*|hurt|sore\w*|strain\w*|sprain\w*|illness|ill|sick|flu|virus|scratch\w*|leaves? game|exited|"
                 r"limited|rest(ed|ing)?|personal (reasons?|matter)|family (matter|emergency)|bereavement|paternity|"
                 r"divorce\w*|arrest\w*|suspen\w*|concussion|benched|slump\w*|struggl\w*)\b", re.I)
OVER_HIT_SHAKY, OVER_HIT_BAD = 0.08, 0.05   # win-chance points taken off an over
MINUTES = None   # parlays.py sets this to minutes(league, player) -> {(date, opp): minutes} (NBA only)
SQL = None   # parlays.py sets this to sql(league, query, args) -> rows on the stats DB (NBA position defense)
STAT_COL = None   # parlays.py sets this to the DB column/expression for (league, stat)
DB_TEAM = {}   # prop team code -> stats DB team code (parlays.DB_CODE)
ROWS = None   # parlays.py sets this to rows_for(league, stat, player) -> [(value, date, opp), ...] newest first
ALIAS = {"JAC": "JAX", "WAS": "WSH", "GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "CHW": "CWS",
         "AZ": "ARI", "KCR": "KC", "SDP": "SD", "SFG": "SF", "TBR": "TB", "WSN": "WSH", "LA": "LAR", "PHO": "PHX"}
GROUP = {"nba": {"PG": "G", "SG": "G", "G": "G", "SF": "F", "PF": "F", "F": "F", "C": "C"},
         "nfl": {"QB": "QB", "RB": "RB", "FB": "RB", "WR": "WR", "TE": "TE"},
         "mlb": {}, "epl": {}}
# opposing positions that guard each offensive group (an injured starter there = easier matchup)
GUARDS = {"nba": {"G": {"PG", "SG", "G"}, "F": {"SF", "PF", "F"}, "C": {"C", "PF"}},
          "nfl": {"WR": {"CB", "DB", "S", "SAF", "FS", "SS"}, "TE": {"LB", "ILB", "OLB", "MLB", "S", "SAF", "FS", "SS"},
                  "RB": {"DT", "DE", "NT", "DL", "LB", "ILB", "MLB"}, "QB": {"DE", "OLB", "DT", "CB", "S", "SAF"}}}
MATE_MAX, MATCHUP_ONE, CTX_CAP = 0.10, 0.03, 0.12


def _get(url, want_json=True):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (BetterPicks)"})
    raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")
    return json.loads(raw) if want_json else raw


def _cache():
    try:
        return json.load(open(CACHE))
    except Exception:
        return {}


def _cached(c, key, fn):
    hit = c.get(key)
    if hit and time.time() - hit["t"] < TTL:
        return hit["v"]
    try:
        v = fn()
    except Exception:
        v = hit["v"] if hit else None
    c[key] = {"t": time.time(), "v": v}
    return v


def injuries(league, c):
    """{player name lower: 'Status: short comment'} from ESPN's league injury report."""
    path = ESPN.get(league)
    if not path:
        return {}
    def fetch():
        d = _get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/injuries")
        out = {}
        for team in d.get("injuries", []):
            for i in team.get("injuries", []):
                name = (i.get("athlete") or {}).get("displayName")
                if name:
                    out[name.lower()] = f"{i.get('status', '')}: {i.get('shortComment', '')}"[:300]
        return out
    out = dict(_cached(c, f"inj:{league}", fetch) or {})
    for k, o in official(league, c).items():
        out[k] = f"{o['status']}: {o['reason']} (official {league.upper()} report)"[:300]
    return out


def norm(t):
    t = (t or "").upper()
    return ALIAS.get(t, t)


def team_abbrs(league, c):
    """{ESPN team id: abbreviation} for the league."""
    path = ESPN.get(league)
    if not path:
        return {}
    def fetch():
        d = _get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/teams")
        return {str(t["team"]["id"]): t["team"]["abbreviation"]
                for lg in d.get("sports", [{}])[0].get("leagues", []) for t in lg.get("teams", [])}
    return _cached(c, f"teams:{league}", fetch) or {}


# Ryan 2026-10-28: "switch to it" / "all sports possible" - each league's own report (official_reports.py) overrides
# ESPN's status for every player it lists and adds anyone ESPN hasn't caught yet. NFL team names stay as nfl.com gives them.
OFFICIAL = None
NBA_TEAM = {"Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
            "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
            "Detroit Pistons": "DET", "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
            "LA Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM", "Miami Heat": "MIA",
            "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
            "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
            "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS",
            "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WSH"}


def official(league, c):
    """{name lower: {name, team, status, reason}} from the league's own report (official_reports.py), else {}.
    The droplet gets these parsed in books_feed.json (OFFICIAL); the project side fetches them itself."""
    if OFFICIAL and league in OFFICIAL:
        return OFFICIAL[league] or {}
    try:
        import official_reports
    except Exception:
        return {}
    fn = official_reports.SOURCES.get(league)
    return (_cached(c, f"official:{league}", fn) or {}) if fn else {}


def injury_list(league, c):
    """[{name, team, pos, status, date}] for everyone on ESPN's league injury report."""
    path = ESPN.get(league)
    if not path:
        return []
    def fetch():
        d = _get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/injuries")
        abbr = team_abbrs(league, c)
        out = []
        for team in d.get("injuries", []):
            for i in team.get("injuries", []):
                a = i.get("athlete") or {}
                if a.get("displayName"):
                    # Ryan 2026-10-25 (Barnes/Ingram): the athlete's own team tag can be stale after a trade
                    # (Ingram listed TOR while on the Clippers' report), so the report's team group wins
                    tm = abbr.get(str(team.get("id"))) or (a.get("team") or {}).get("abbreviation")
                    out.append({"name": a["displayName"], "team": norm(tm),
                                "pos": (a.get("position") or {}).get("abbreviation", ""),
                                "status": i.get("status", ""), "date": (i.get("date") or "")[:10]})
        return out
    out = [dict(i) for i in (_cached(c, f"injl:{league}", fetch) or [])]
    off = official(league, c)
    if off:
        seen = set()
        for i in out:
            o = off.get(i["name"].lower())
            if o:
                i["status"], i["source"] = o["status"], "official"
                seen.add(i["name"].lower())
        out += [{"name": o["name"], "team": norm(NBA_TEAM.get(o["team"], o["team"])), "pos": "", "status": o["status"],
                 "date": "", "source": "official"} for k, o in off.items() if k not in seen]
    return out


def roster_pos(league, team, c):
    """{player name lower: position} from ESPN's team roster (nba/nfl/mlb)."""
    path = ESPN.get(league)
    if not path or not team or league == "epl":
        return {}
    def fetch():
        d = _get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/teams/{team.lower()}/roster")
        a = d.get("athletes", [])
        items = [x for g in a for x in g.get("items", [])] if a and "items" in a[0] else a
        return {x["displayName"].lower(): (x.get("position") or {}).get("abbreviation", "") for x in items if x.get("displayName")}
    return _cached(c, f"roster:{league}:{team}", fetch) or {}


def is_out(status):
    s = status.lower()
    return any(s.startswith(k) for k in OUT) or "injured reserve" in s


def _hit(vals, line, side):
    return sum((v > line) if side == "over" else (v < line) for v in vals) / len(vals)


def without_split(league, stat, player, mate, line, side):
    """Player's hit rate for this side in games without vs with the teammate, since their first game together.
    None = not enough shared games to measure; (0, "") = the teammate barely touches this stat."""
    if ROWS is None:
        return None
    mine, theirs = ROWS(league, stat, player) or [], ROWS(league, stat, mate) or []
    shared = {(r[1], r[2]): r[0] for r in theirs}   # same date + same opponent = same game, same team
    together = [d for v, d, o in mine if (d, o) in shared]
    if len(together) < 3:
        return None
    # only a teammate who really eats into this stat (skips backup QBs, deep reserves)
    mv = [shared[(d, o)] or 0 for v, d, o in mine if (d, o) in shared]
    pv = [v or 0 for v, d, o in mine if (d, o) in shared]
    if sum(mv) < 0.25 * sum(pv):
        return 0.0, ""
    games = [(r[0], (r[1], r[2]) in shared) for r in mine if r[0] is not None and r[1] >= min(together)][:40]
    w, wo = [v for v, t in games if t], [v for v, t in games if not t]
    if len(wo) < 2 or len(w) < 3:
        return None
    hw, hwo = _hit(w, line, side), _hit(wo, line, side)
    dp = max(-MATE_MAX, min(MATE_MAX, 0.5 * (hwo - hw) * len(wo) / (len(wo) + 4)))
    return dp, (f"{mate} is out: {player} hit the {side} in {round(hwo * len(wo))}/{len(wo)} games without him "
                f"vs {round(hw * len(w))}/{len(w)} with him")


def role_grew(league, stat, player, mate):
    """Ryan 2026-10-23 (Hartenstein: Sorber out, 23 min, 6 boards): a teammate being out only means a bigger role
    if this player's minutes actually went up in past games without him. True / False, or None when it can't be
    measured (no minutes data, e.g. NFL). With no shared games at all the absence is old news: his recent games
    were already without that teammate, so it's False."""
    if ROWS is None:
        return None
    mine, theirs = ROWS(league, stat, player) or [], ROWS(league, stat, mate) or []
    shared = {(r[1], r[2]) for r in theirs}
    together = [(d, o) for v, d, o in mine if (d, o) in shared]
    if not together:
        return False
    mins = MINUTES(league, player) if MINUTES else None
    if not mins:
        return None
    start = min(d for d, o in together)
    w = [m for k, m in mins.items() if k[0] >= start and k in shared]
    wo = [m for k, m in mins.items() if k[0] >= start and k not in shared]
    if len(w) < 3 or len(wo) < 2:
        return None
    return sum(wo) / len(wo) >= sum(w) / len(w) + 1.5   # at least ~1.5 more minutes a game without him


def regular(league, stat, name, since):
    """Plays most games lately (a starter-level piece, not a deep reserve)."""
    if ROWS is None:
        return True
    n = sum(1 for r in (ROWS(league, stat, name) or []) if r[1] >= since)
    return n >= (3 if league == "nfl" else 8)


def context(leg, c):
    """-> (win-chance change, [notes]) from teammates and opponents who are out."""
    pr, side, line = leg["prop"], leg["side"], leg["line"]
    league, player, stat = pr["league"], pr["player"], pr["stat"]
    if league not in GUARDS and league != "mlb":
        return 0.0, []
    teams = [norm(t) for t in (pr.get("game") or "").replace(" vs ", " @ ").split(" @ ")]
    team = norm(pr.get("team"))
    if len(teams) != 2 or team not in teams:
        return 0.0, []
    opp = teams[1] if teams[0] == team else teams[0]
    outs = [i for i in injury_list(league, c) if is_out(i["status"]) and i["name"].lower() != player.lower()]
    # Ryan 2026-10-25: trades happen any day, so an absence only counts when today's ESPN roster (30 min cache)
    # still has that player on the team; a traded-away teammate or opponent never moves a pick
    if league != "mlb":
        cur = {team: roster_pos(league, team, c), opp: roster_pos(league, opp, c)}
        outs = [i for i in outs if i["team"] not in cur or not cur[i["team"]] or i["name"].lower() in cur[i["team"]]]
    grp_of = GROUP.get(league, {})
    my_grp = grp_of.get(roster_pos(league, team, c).get(player.lower(), ""))
    fresh = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 10 * 86400))
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 60 * 86400))
    dp, notes = 0.0, []
    for m in [i for i in outs if i["team"] == team]:
        same = my_grp is not None and grp_of.get(m["pos"]) == my_grp
        if league != "mlb" and not same:
            continue
        sp = without_split(league, stat, player, m["name"], line, side)
        if sp and abs(sp[0]) >= 0.01:   # measured with/without split: real results, no minutes check needed
            dp += sp[0]; notes.append(sp[1])
        elif (sp is None and same and my_grp != "QB" and m["date"] >= fresh
              and role_grew(league, stat, player, m["name"]) is not False):   # unmeasured bump needs the minutes check
            dp += -0.05 if side == "under" else 0.02
            notes.append(f"{m['name']} ({m['pos']}) is out, so {player}'s role likely grows")
    hits = 0
    for d in [i for i in outs if i["team"] == opp and my_grp and i["pos"] in GUARDS.get(league, {}).get(my_grp, ())]:
        if hits >= 2 or not regular(league, stat, d["name"], since):
            continue
        hits += 1
        dp += MATCHUP_ONE if side == "over" else -(MATCHUP_ONE + 0.01)
        notes.append(f"{opp}'s {d['pos']} {d['name']} is out, an easier matchup")
    return max(-CTX_CAP, min(CTX_CAP, dp)), notes


# Ryan 2026-10-23/24: blowout rule, on only when a blowout looks likely. Backtest (blowout.py, 2024-26 regular
# seasons, starters): with a 32%+ chance of a 20-point game, the favored team's starters lose ~1.6-2 minutes and go
# over their scoring average ~2 points less often (45.4% vs 48.0%, 46.1% vs 47.5%); rebounds and assists don't drop,
# and the underdog side is mixed. So: favored-side starters, scoring props only, 2-point nudge. Below 32%, ignored.
BLOWOUT_ON, BLOWOUT_DP, BLOWOUT_STATS, BLOWOUT_SIGMA = 0.32, 0.02, ("points", "pra", "pr", "pa"), 13.48


def game_spread(league, game, start, c):
    """Pregame spread from ESPN's scoreboard: (favorite team, points) or None."""
    path = ESPN.get(league)
    day = (start or "")[:10].replace("-", "")
    if not path or not day:
        return None
    def fetch():
        out = {}
        for e in (_get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={day}") or {}).get("events", []):
            o = (e.get("competitions") or [{}])[0].get("odds") or []
            det = (o[0].get("details") if o else "") or ""
            m = re.match(r"([A-Z]+) -([0-9.]+)", det)
            if m:
                out[" @ ".join(norm(x) for x in e["shortName"].split(" @ "))] = [norm(m.group(1)), float(m.group(2))]
        return out
    lines = _cached(c, f"spreads:{league}:{day}", fetch) or {}
    return lines.get(" @ ".join(norm(x) for x in (game or "").split(" @ ")))


# Ryan 2026-10-26 (Ayo Dosunmu threes): check the position and role that guards him, not just the team. NBA.com's
# defender-tracking data isn't reachable from here, so this uses what the stats DB has: how much of this stat the
# opponent allowed to players at his position group in his role (starter or bench) over its last 60 such player-games,
# vs the league average for that group and role. Backtest 2024-26 (33k player-games): players beat their own recent
# average 40-45% of the time vs the stingiest fifth of matchups and 43-48% vs the softest fifth (threes 40->43%,
# points 45->49%, rebounds 43->46%, assists 42->48%). Small nudge, capped at 3 pts.
POSDEF_K, POSDEF_CAP, POSDEF_MIN = 0.15, 0.03, 25


def positions(league, c):
    """{player name: position} from every team's ESPN roster (cached with the rosters)."""
    out = {}
    for team in sorted(set(team_abbrs(league, c).values())):
        out.update({n: p for n, p in roster_pos(league, team, c).items()})
    return out


# Ryan 2026-10-26: "incorporate teammate switches so a better defender could defend the player". A defender at
# another position can switch onto him, so for every active opposing player at a switchable spot (guards <-> wings,
# wings <-> bigs) we measure what the opponent allowed to his position group in its last 40 games with that defender
# playing vs without him, shrunk toward no effect by how few games he missed. The two biggest effects move the
# opponent's number to tonight's lineup (with him if he plays, without him if he's out).
SWITCH = {"G": ("G", "F"), "F": ("G", "F", "C"), "C": ("F", "C")}
SWITCH_GAMES, SWITCH_SHRINK, SWITCH_MIN_OFF = 40, 8, 4


def switchers(opp_db, opp, grp, col, pos, c, better=False):
    """-> [(factor for tonight, name, effect, playing)] for the opponent's defenders who can switch onto this group."""
    rows = _sql_memo("nba", f"select date, player, {col} from player_games where opp=? and season_type='regular' "
                      f"and minutes>=12 order by date desc limit 1500", (opp_db,))
    dates = sorted({r[0] for r in rows}, reverse=True)[:SWITCH_GAMES]
    if len(dates) < 20:
        return []
    keep = set(dates)
    per = {d: [] for d in dates}
    for d, p, v in rows:
        if d in keep and GROUP["nba"].get(pos.get((p or "").lower(), "")) == grp:
            per[d].append(v or 0)
    game_val = {d: sum(v) / len(v) for d, v in per.items() if v}
    if len(game_val) < 20:
        return []
    m = sum(game_val.values()) / len(game_val)
    if m <= 0:
        return []
    played = {}
    for p, d in SQL("nba", "select player, date from player_games where team=? and minutes>=10 and date>=?",
                    (opp_db, min(dates))):
        if d in keep:
            played.setdefault(p, set()).add(d)
    rost = roster_pos("nba", opp, c)
    outs = {i["name"].lower() for i in injury_list("nba", c) if is_out(i["status"])}
    out = []
    for p, ds in played.items():
        if p.lower() not in rost or GROUP["nba"].get(pos.get(p.lower(), "")) not in SWITCH[grp] or len(ds) < 8:
            continue
        w = [v for d, v in game_val.items() if d in ds]
        wo = [v for d, v in game_val.items() if d not in ds]
        if len(wo) < SWITCH_MIN_OFF:
            continue
        mw, mwo = sum(w) / len(w), sum(wo) / len(wo)
        k = len(wo) / (len(wo) + SWITCH_SHRINK)
        mw, mwo = m + k * (mw - m), m + k * (mwo - m)   # shrunk toward the team average
        playing = p.lower() not in outs
        out.append(((mw if playing else mwo) / m, p, mw / mwo - 1 if mwo else 0, playing))
    if better:
        out = [x for x in out if x[2] < 0]
    return sorted(out, key=lambda x: x[2])[:2]


# Ryan 2026-10-26: double-team rule. A star coming off 3+ huge scoring games may draw double teams, so think about
# what drops and who he passes to. Backtest 2021-26 (stars averaging 18+, last 3 games all 20%+ over their 20-game
# scoring average, 518 games): vs the last-12 averages our picks use, his points held up (beat it 44% vs 46% normally,
# 48% at 30%+ runs), his assists went over a bit more often (49-52% vs 46%), and teammates' scoring didn't rise.
# So: no points penalty (the data says it would cost us), a small assists nudge, and a note on the pick.
HOT_MIN_AVG, HOT_RUN, HOT_DP = 18, 1.2, 0.02


def hot_star(leg, c):
    """-> (win-chance change, note) for a star on a 3-game scoring run, else (0, None)."""
    pr, side = leg["prop"], leg["side"]
    if pr["league"] != "nba" or SQL is None:
        return 0.0, None
    g = SQL("nba", "select pts from player_games where player=? and season_type in ('regular', 'playoffs') "
                   "and minutes>=10 order by date desc limit 23", (pr["player"],))
    pts = [r[0] or 0 for r in g]
    if len(pts) < 23:
        return 0.0, None
    base = sum(pts[3:]) / 20
    if base < HOT_MIN_AVG or not all(p >= HOT_RUN * base for p in pts[:3]):
        return 0.0, None
    run = ", ".join(str(p) for p in pts[:3])
    note = f"{pr['player']} is on a scoring run ({run} vs his {base:.0f} average), so expect more double teams"
    if pr["stat"] in ("assists", "pa", "ra"):
        return (HOT_DP if side == "over" else -HOT_DP), note + ": stars on runs like this pass a bit more"
    if pr["stat"] in ("points", "pra", "pr"):
        return 0.0, note + ", though in our data stars on runs like this kept scoring at their usual rate"
    return 0.0, None


# Ryan 2026-10-28: weigh the likely defenders by how well they actually defend. NBA.com's season defense dashboards
# (pulled through Ryan's Chrome, build_defenders.py) give each defender's DIFF%: FG% allowed minus what those shooters
# normally hit. Backtest 2025-26 (likely defenders = opponent's top-2 minutes at his position, rated on 2024-25 data):
# vs the stingiest fifth (DIFF% -1.9 or lower) players beat their own scoring average 47% (points) / 47% (PRA) vs
# 49-54% for every other fifth; no clear effect for threes, rebounds or rim defense, so points-type props only.
DEF_URL = "https://raw.githubusercontent.com/RY00N/archie-bot/claude/project-thread-8sdhk6/betterpicks/nba_defenders.json"
DEF_STATS, DEF_STINGY, DEF_DP = ("points", "pra", "pr", "pa"), -2.0, 0.03


def defenders(c):
    """{defender name lower: [overall DIFF%, shots defended]} (local file, else the repo copy, cached)."""
    path = os.path.join(HERE, "nba_defenders.json")
    if os.path.exists(path):
        try:
            return json.load(open(path)).get("overall", {})
        except Exception:
            pass
    return (_cached(c, "nba_defenders", lambda: _get(DEF_URL)) or {}).get("overall", {})


_MEMO = {}


def _sql_memo(league, q, args):
    """SQL with a per-run memo: pos_defense/switchers ask the same league-wide queries for every leg (2026-10-28:
    ~60s of a build, enough to push the droplet past its step timeout)."""
    k = (league, q, tuple(args))
    if k not in _MEMO:
        _MEMO[k] = SQL(league, q, args)
    return _MEMO[k]


def pos_defense(leg, c):
    """-> (win-chance change, note) from the opponent's defense vs his position + role, else (0, None)."""
    pr, side = leg["prop"], leg["side"]
    if pr["league"] != "nba" or SQL is None or STAT_COL is None:
        return 0.0, None
    col = STAT_COL("nba", pr["stat"])
    teams = [norm(t) for t in (pr.get("game") or "").replace(" vs ", " @ ").split(" @ ")]
    team = norm(pr.get("team"))
    if not col or len(teams) != 2 or team not in teams:
        return 0.0, None
    opp = teams[1] if teams[0] == team else teams[0]
    pos = positions("nba", c)
    grp = GROUP["nba"].get(pos.get(pr["player"].lower(), ""))
    role = SQL("nba", "select starter, date from player_games where player=? and season_type in ('regular', 'preseason') "
                      "order by date desc limit 30", (pr["player"],))
    cut = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 150 * 86400))
    flags = [r[0] for r in role if r[0] is not None and r[1] >= cut][:10] or [r[0] for r in role if r[0] is not None][:10]
    if not grp or not flags:
        return 0.0, None
    starter = 1 if 2 * sum(flags) >= len(flags) else 0
    in_grp = lambda name: GROUP["nba"].get(pos.get((name or "").lower(), "")) == grp
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 400 * 86400))
    q = (f"select player, {col}, date from player_games where season_type='regular' and starter=? and minutes>=12 "
         f"and date>=? and {{}} order by date desc")
    vs = [r for r in _sql_memo("nba", q.format("opp=?"), (starter, since, DB_TEAM.get(opp, opp))) if in_grp(r[0])][:60]
    lg = [r for r in _sql_memo("nba", q.format("1=1"), (starter, since)) if in_grp(r[0])][:3000]
    if len(vs) < POSDEF_MIN or len(lg) < 500:
        return 0.0, None
    a, b = sum(r[1] or 0 for r in vs) / len(vs), sum(r[1] or 0 for r in lg) / len(lg)
    if b <= 0:
        return 0.0, None
    ratio = base = a / b
    # only defenders who make it harder (allowed 8%+ less with them playing): the ones a switch would put on him
    sw = [x for x in switchers(DB_TEAM.get(opp, opp), opp, grp, col, pos, c, better=True) if x[2] <= -0.08]
    for f, *_ in sw:
        ratio *= f
    dp = max(-POSDEF_CAP, min(POSDEF_CAP, POSDEF_K * (ratio - 1)))
    if side == "under":
        dp = -dp
    # likely defenders: the opponent's players at his position in his role, most minutes lately, active tonight
    outs = {i["name"].lower() for i in injury_list("nba", c) if is_out(i["status"])}
    rost = roster_pos("nba", opp, c)
    mins = _sql_memo("nba", "select player, avg(minutes) from (select player, minutes from player_games where team=? and "
                      "starter=? and date>=? and season_type='regular' order by date desc limit 120) group by player order by avg(minutes) desc",
               (DB_TEAM.get(opp, opp), starter, time.strftime("%Y-%m-%d", time.gmtime(time.time() - 200 * 86400))))
    who = [p for p, m in mins if in_grp(p) and p.lower() in rost and p.lower() not in outs][:2]
    stingy = None
    if pr["stat"] in DEF_STATS and who:
        dd = defenders(c)
        rated = [dd[w.lower()][0] for w in who if w.lower() in dd and dd[w.lower()][1] >= 150]
        if rated and sum(rated) / len(rated) <= DEF_STINGY:
            stingy = sum(rated) / len(rated)
            dp += -DEF_DP if side == "over" else DEF_DP
    label = {"G": "guards", "F": "forwards", "C": "centers"}[grp]
    word = "more" if base >= 1 else "fewer"
    note = (f"{opp} has allowed {'starting' if starter else 'bench'} {label} {abs(base - 1):.0%} {word} "
            f"{pr['stat'].replace('_', ' ')} than average" + (f" (likely on him: {', '.join(who)})" if who else ""))
    for f, name, eff, playing in sw:
        note += (f"; {name} can switch onto him ({opp} allowed {label} {abs(eff):.0%} fewer "
                 f"{pr['stat'].replace('_', ' ')} with him playing; {'he plays' if playing else 'he is out'} tonight)")
    if stingy is not None:
        note += f"; those defenders hold shooters {abs(stingy):.1f}% below their normal FG%, among the league's stingiest"
    return (round(dp, 3), note) if abs(ratio - 1) >= 0.05 or who else (0.0, None)


# Ryan 2026-10-28: "be your own version of their service" (minutes projections, free). Our projection = average of his
# last-5 and last-15 minutes (beat last-15 alone, last-5 alone, and versions adding freed-up teammate minutes: MAE 4.99
# vs 5.21 over 46k games, 2024-26). Backtest, players 20+ min / 8+ pts: projection minus his last-15 minutes vs how often
# he beat his last-15 scoring average: +3 or more 59%, +1..3 50%, -1..1 46%, -3..-1 42%, -3 or less 32%.
# Applied at about half those gaps, points-type props only (the only stats tested).
MIN_STATS = ("points", "pra", "pr", "pa")
MIN_STEPS = ((3, 0.06), (1, 0.02), (-1, 0.0), (-3, -0.02), (-99, -0.06))


def minutes_trend(leg, c):
    """-> (win-chance change, note) from his minutes trend (our own minutes projection), else (0, None)."""
    pr, side = leg["prop"], leg["side"]
    if pr["league"] != "nba" or pr["stat"] not in MIN_STATS or SQL is None:
        return 0.0, None
    g = [r[0] or 0 for r in SQL("nba", "select minutes from player_games where player=? and season_type in "
                                       "('regular', 'playoffs') and minutes>0 order by date desc limit 15", (pr["player"],))]
    if len(g) < 10:
        return 0.0, None
    l5, l15 = sum(g[:5]) / 5, sum(g) / len(g)
    if l15 < 20:
        return 0.0, None
    proj = (l5 + l15) / 2
    d = proj - l15
    dp = next(v for cut, v in MIN_STEPS if d >= cut)
    if not dp:
        return 0.0, None
    note = (f"minutes trending {'up' if d > 0 else 'down'} (we project {proj:.0f}, his last 5 avg {l5:.0f} vs "
            f"{l15:.0f} over 15)")
    return (dp if side == "over" else -dp), note


def blowout_adj(leg, c):
    """-> (win-chance change, note) for a likely blowout, else (0, None)."""
    pr = leg["prop"]
    if pr["league"] != "nba" or pr["stat"] not in BLOWOUT_STATS:
        return 0.0, None
    try:
        sp = game_spread("nba", pr.get("game"), pr.get("start") or leg.get("start"), c)
        if not sp or norm(pr.get("team")) != sp[0]:
            return 0.0, None
        cdf = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))   # same model as blowout.py, sigma fit there
        chance = 1 - cdf((19.5 - sp[1]) / BLOWOUT_SIGMA) + cdf((-19.5 - sp[1]) / BLOWOUT_SIGMA)
        mins = sorted((MINUTES("nba", pr["player"]) or {}).values()) if MINUTES else []
        if chance < BLOWOUT_ON or not mins or mins[len(mins) // 2] < 24:   # starters only; bench players gain minutes
            return 0.0, None
    except Exception:
        return 0.0, None
    note = f"{pr.get('team')} are {sp[1]:g}-point favorites ({chance:.0%} blowout chance), so starters may sit late"
    return (-BLOWOUT_DP if leg["side"] == "over" else BLOWOUT_DP), note


def headlines(player, c):
    q = urllib.parse.quote(f'"{player}" when:3d')
    def fetch():
        x = _get(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en", want_json=False)
        return [re.sub(r"&amp;", "&", t) for t in re.findall(r"<title>(.*?)</title>", x)[1:]][:15]
    return _cached(c, f"news:{player.lower()}", fetch) or []


def check(player, league, c=None):
    """-> (verdict, note): verdict 'out' | 'shaky' | 'bad_news' | 'clear'."""
    c = _cache() if c is None else c
    inj = injuries(league, c).get(player.lower())
    if inj:
        s = inj.lower()
        if any(s.startswith(k) or f" {k}" in s.split(":")[0] for k in OUT):
            return "out", f"Injury report: {inj}"
        if any(k in s.split(":")[0] for k in SHAKY):
            return "shaky", f"Injury report: {inj}"
    last = player.split()[-1].lower()
    # the bad word has to sit in the same clause as the player's name ("Contreras shines as Dodgers' stars slump" is fine)
    clauses = lambda h: re.split(r"\s+(?:as|while|but|and|after|amid|-|\u2013|\u2014)\s+|[,;:|]|\.\.\.", h.lower())
    bad = [h for h in headlines(player, c) if any(last in cl and BAD.search(cl) for cl in clauses(h))]
    if bad:
        return "bad_news", f"News: {bad[0][:180]}"
    return "clear", "No injury or off-field red flags in the last 3 days."


def adjust(legs):
    c = _cache()
    seen, kept = {}, []
    for l in legs:
        pr = l["prop"]
        key = (pr["player"], pr["league"])
        if key not in seen:
            seen[key] = check(pr["player"], pr["league"], c)
        verdict, note = seen[key]
        l["news"] = note
        if verdict == "out":
            continue
        dp = 0.0
        if l["side"] == "over" and verdict in ("shaky", "bad_news"):
            dp -= OVER_HIT_SHAKY if verdict == "shaky" else OVER_HIT_BAD
        try:
            cdp, cnotes = context(l, c)
        except Exception as ex:
            cdp, cnotes = 0.0, []
            print("matchup check skipped:", pr["player"], ex)
        bdp, bnote = blowout_adj(l, c)
        if bnote:
            cdp += bdp; cnotes = list(cnotes) + [bnote]
        try:
            pdp, pnote = pos_defense(l, c)
        except Exception as ex:
            pdp, pnote = 0.0, None
            print("position defense skipped:", pr["player"], ex)
        if pnote:
            cdp += pdp; cnotes = list(cnotes) + [pnote]
        try:
            hdp, hnote = hot_star(l, c)
        except Exception as ex:
            hdp, hnote = 0.0, None
            print("double-team check skipped:", pr["player"], ex)
        if hnote:
            cdp += hdp; cnotes = list(cnotes) + [hnote]
        try:
            mdp, mnote = minutes_trend(l, c)
        except Exception as ex:
            mdp, mnote = 0.0, None
            print("minutes check skipped:", pr["player"], ex)
        if mnote:
            cdp += mdp; cnotes = list(cnotes) + [mnote]
        l["ctx_dp"] = round(cdp, 3) if cnotes else 0.0   # pregame.py re-checks this part right before tip
        if cnotes:
            dp += cdp
            l["news"] = (note if verdict != "clear" else "") + (" " if verdict != "clear" else "") + "; ".join(cnotes) + "."
        if dp:
            l["p"] = round(min(0.99, max(0.01, l["p"] + dp)), 3)
            if l.get("price"):
                ask = l["price"]
                l["edge"] = round(l["p"] - ask - 0.07 * ask * (1 - ask), 3)
        kept.append(l)
    try:
        json.dump(c, open(CACHE, "w"))
    except Exception:
        pass
    return kept


if __name__ == "__main__":
    print(check(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "mlb"))
