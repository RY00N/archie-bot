#!/usr/bin/env python3
"""books.py - sportsbook / pick'em / prediction-market line scanner (PRACTICE ONLY).

No accounts, no betting, no VPN/proxy/geo tricks. Reads only data that is
publicly served to this container. Sources that refuse us are recorded as
blocked and skipped.

Commands
  python3 books.py status                  -> books-status.json
  python3 books.py lines [--league nfl ...] -> lines.json
  python3 books.py props [--league nfl ...] -> props.json

Leagues: nfl nba mlb ncaaf nhl epl ufc   (standard library only)
See books-notes.md for endpoints and quirks.
"""
import argparse
import gzip
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_STATUS = os.path.join(HERE, "books-status.json")
OUT_LINES = os.path.join(HERE, "lines.json")
OUT_PROPS = os.path.join(HERE, "props.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
LEAGUES = ["nfl", "nba", "mlb", "ncaaf", "nhl", "epl", "ufc"]
WINDOW_DAYS = 7

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    ET = timezone(timedelta(hours=-4))

# --------------------------------------------------------------------------
# HTTP (polite: per-host minimum spacing, retry on 429/5xx)
# --------------------------------------------------------------------------
_last_hit = {}
HOST_GAP = {
    "api.actionnetwork.com": 0.4,
    "api.elections.kalshi.com": 0.7,
    "guest.api.arcadia.pinnacle.com": 0.4,
}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def http_get(url, headers=None, timeout=40, retries=3, want_json=True):
    """Returns (status_code, data_or_None, error_str_or_None)."""
    host = urllib.parse.urlparse(url).netloc
    gap = HOST_GAP.get(host, 0.3)
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
         "Accept-Encoding": "gzip, deflate", "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        h.update(headers)
    code, err = None, None
    for attempt in range(retries):
        wait = _last_hit.get(host, 0) + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.time()
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                code = r.status
                raw = r.read()
                enc = (r.headers.get("Content-Encoding") or "").lower()
                if enc == "gzip":
                    raw = gzip.decompress(raw)
                elif enc == "deflate":
                    raw = zlib.decompress(raw)
                if not want_json:
                    return code, raw.decode("utf-8", "replace"), None
                return code, json.loads(raw.decode("utf-8")), None
        except urllib.error.HTTPError as e:
            code, err = e.code, "HTTP %s" % e.code
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2.0 * (attempt + 1))
                continue
            return code, None, err
        except Exception as e:  # network / json errors
            err = "%s: %s" % (type(e).__name__, str(e)[:160])
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            return code, None, err
    return code, None, err


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Odds math
# --------------------------------------------------------------------------
def am_to_dec(a):
    if a is None or a == 0:
        return None
    a = float(a)
    return 1 + (a / 100.0 if a > 0 else 100.0 / -a)


def am_to_prob(a):
    d = am_to_dec(a)
    return None if not d else 1.0 / d


def prob_to_am(p):
    if p is None or p <= 0 or p >= 1:
        return None
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def novig(prices):
    """prices: dict side->american. Returns side->fair prob (multiplicative)."""
    ps = {k: am_to_prob(v) for k, v in prices.items()}
    if any(v is None for v in ps.values()) or len(ps) < 2:
        return None
    s = sum(ps.values())
    return {k: round(v / s, 4) for k, v in ps.items()}


def to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Name helpers
# --------------------------------------------------------------------------
_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def norm_name(s):
    s = (s or "").lower().replace("&", "and")
    s = re.sub(r"[.'`’\-]", "", s)
    s = _SUFFIX.sub("", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def name_match(a, b):
    a, b = norm_name(a), norm_name(b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    ta, tb = a.split(), b.split()
    return len(ta) > 1 and len(tb) > 1 and ta[-1] == tb[-1] and ta[0][0] == tb[0][0]


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Book registry
# --------------------------------------------------------------------------
# Action Network book ids -> canonical name (one representative state each;
# AN shows identical prices across a brand's state skins).
AN = "https://api.actionnetwork.com"
AN_BOOKS = {
    15: ("AN Consensus", "reference"),
    30: ("AN Open", "reference"),
    68: ("DraftKings", "sportsbook"),
    69: ("FanDuel", "sportsbook"),
    75: ("BetMGM", "sportsbook"),
    71: ("BetRivers", "sportsbook"),
    79: ("bet365", "sportsbook"),
    123: ("Caesars", "sportsbook"),
    2988: ("Fanatics", "sportsbook"),
    2724: ("Hard Rock Bet", "sportsbook"),
    4620: ("theScore Bet (ex-ESPN BET)", "sportsbook"),
    4693: ("Bally Bet", "sportsbook"),
    1929: ("Parx", "sportsbook"),
    247: ("Unibet", "sportsbook"),
    4727: ("Kalshi", "exchange/prediction"),
    4795: ("Polymarket", "exchange/prediction"),
    2194: ("PrizePicks", "pickem"),
}
# Extra AN ids probed by `status` only (they exist in AN's book list but have
# not shown data in testing).
AN_EXTRA_PROBE = {3: "Pinnacle", 3348: "Underdog", 3612: "Sleeper", 2292: "Fliff",
                  1467: "Prophet", 78: "Circa", 21: "Bovada", 1: "BetOnline"}
NON_PRICE_BOOKS = {15, 30}       # excluded from best-price & fallback consensus
LINE_BOOK_IDS = [b for b in AN_BOOKS if b != 2194]   # PrizePicks has no game lines
PROP_BOOK_IDS = list(AN_BOOKS)

AN_SLUG = {"nfl": "nfl", "nba": "nba", "mlb": "mlb", "ncaaf": "ncaaf", "nhl": "nhl",
           "epl": "epl", "ufc": "ufc"}
WEEKLY = {"nfl", "ncaaf"}

PIN = "https://guest.api.arcadia.pinnacle.com/0.1"
PIN_LEAGUES = {"nfl": [889], "ncaaf": [880], "mlb": [246], "nba": [487, 5270],
               "nhl": [1456], "epl": [1980], "ufc": [1624]}

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_GAME_SERIES = {"nfl": "KXNFLGAME", "ncaaf": "KXNCAAFGAME", "mlb": "KXMLBGAME",
                      "nba": "KXNBAGAME", "nhl": "KXNHLGAME", "epl": "KXEPLGAME",
                      "ufc": "KXUFCFIGHT"}
KALSHI_PROP_SERIES = {
    "nfl": {"KXNFLPASSYDS": "pass_yds", "KXNFLRSHYDS": "rush_yds", "KXNFLRECYDS": "rec_yds",
            "KXNFLREC": "receptions", "KXNFLPASSTDS": "pass_tds", "KXNFLANYTD": "anytime_td",
            "KXNFLRRYDS": "rush_rec_yds", "KXNFLPASSATT": "pass_att",
            "KXNFLPASSCOMP": "pass_comp", "KXNFLRSHATT": "rush_att", "KXNFLPASSINT": "pass_ints"},
    "nba": {"KXNBAPTS": "points", "KXNBAREB": "rebounds", "KXNBAAST": "assists",
            "KXNBA3PT": "threes", "KXNBAPRA": "pra", "KXNBAPR": "pr", "KXNBAPA": "pa",
            "KXNBARA": "ra", "KXNBASTL": "steals", "KXNBABLK": "blocks"},
    "mlb": {"KXMLBHIT": "hits", "KXMLBHR": "home_runs", "KXMLBKS": "pitcher_ks",
            "KXMLBTB": "total_bases", "KXMLBRBI": "rbis", "KXMLBHRR": "hrr",
            "KXMLBOUTS": "pitch_outs", "KXMLBHA": "hits_allowed", "KXMLBERA": "earned_runs",
            "KXMLBSB": "stolen_bases", "KXMLBWALK": "walks"},
    "nhl": {"KXNHLPTS": "points", "KXNHLGOAL": "goals", "KXNHLAST": "assists",
            "KXNHLSAVES": "saves", "KXNHLSAVE": "saves", "KXNHLANYGOAL": "anytime_goal"},
    "epl": {"KXEPLGOAL": "goals", "KXEPLANYGOAL": "anytime_goal", "KXEPLAST": "assists",
            "KXEPLSOA": "goal_or_assist"},
    "ncaaf": {}, "ufc": {},
}

# Action Network player-prop custom pick types per league (core_bet_type_N_name)
AN_PROP_TYPES = {
    "nfl": [9, 10, 11, 12, 13, 15, 16, 17, 18, 30, 62, 65, 66, 71, 70, 578, 58, 59, 60],
    "nba": [27, 23, 26, 21, 85, 86, 87, 88, 24, 25, 89, 580],
    "mlb": [33, 37, 36, 77, 34, 431, 42, 72, 74, 76, 73, 32, 35, 78, 709, 710],
    "nhl": [31, 279, 280, 313, 38],
}
AN_PROP_TYPES["ncaaf"] = AN_PROP_TYPES["nfl"]
AN_TYPE_NAMES = {
    9: "passing_yards", 10: "pass_completions", 11: "passing_tds", 12: "rushing_yards",
    13: "rushing_tds", 15: "receptions", 16: "receiving_yards", 17: "receiving_tds",
    18: "rushing_attempts", 30: "passing_attempts", 62: "anytime_touchdown_scorer",
    65: "interceptions", 66: "rushing_receiving_yards", 71: "passing_rushing_yards",
    70: "tackles_assists", 578: "sacks", 58: "longest_rush", 59: "longest_reception",
    60: "longest_completion", 27: "points", 23: "rebounds", 26: "assists", 21: "3fgm",
    85: "points_rebounds_assists", 86: "points_rebounds", 87: "points_assists",
    88: "rebounds_assists", 24: "steals", 25: "blocks", 89: "steals_blocks", 580: "turnovers",
    33: "hr", 37: "strikeouts", 36: "hits", 77: "total_bases", 34: "rbi", 431: "hits_runs_rbis",
    42: "pitching_outs", 72: "hits_allowed", 74: "earned_runs", 76: "walks", 73: "stolen_bases",
    32: "singles", 35: "doubles", 78: "runs_scored", 709: "hitter_strikeouts",
    710: "hitter_walks", 31: "shots_on_goal", 279: "assists", 280: "points",
    313: "anytime_goal_scorer", 38: "goaltender_saves",
}

# Canonical stat names --------------------------------------------------------
STAT_COMMON = {
    # football
    "passing_yards": "pass_yds", "passing_yds": "pass_yds", "pass_yards": "pass_yds",
    "passing_tds": "pass_tds", "passing_touchdowns": "pass_tds", "touchdown_passes": "pass_tds",
    "passing_attempts": "pass_att", "passing_att": "pass_att", "pass_attempts": "pass_att",
    "pass_completions": "pass_comp", "passing_comps": "pass_comp",
    "passing_completions": "pass_comp",
    "rushing_yards": "rush_yds", "rushing_yds": "rush_yds",
    "rushing_attempts": "rush_att", "rushing_att": "rush_att", "rush_attempts": "rush_att",
    "receiving_yards": "rec_yds", "receiving_yds": "rec_yds",
    "receptions": "receptions", "receiving_rec": "receptions",
    "rushing_receiving_yards": "rush_rec_yds", "rush_rec_yds": "rush_rec_yds",
    "rushing_and_receiving_yards": "rush_rec_yds",
    "passing_rushing_yards": "pass_rush_yds", "passing_and_rushing_yds": "pass_rush_yds",
    "interceptions": "pass_ints", "passing_ints": "pass_ints",
    "passing_interceptions": "pass_ints",
    "anytime_touchdown_scorer": "anytime_td", "anytime_touchdowns": "anytime_td",
    "rushing_tds": "rush_tds", "receiving_tds": "rec_tds",
    "longest_reception": "longest_rec", "receiving_long": "longest_rec",
    "longest_rush": "longest_rush", "rushing_long": "longest_rush",
    "longest_completion": "longest_pass", "passing_long": "longest_pass",
    "tackles_assists": "tackles_ast", "tackles_and_assists": "tackles_ast",
    "sacks": "sacks", "kicking_points": "kicking_pts", "fantasy_points": "fantasy_pts",
    # basketball
    "points": "points", "rebounds": "rebounds", "assists": "assists",
    "3fgm": "threes", "three_points_made": "threes", "threes_made": "threes", "threes": "threes",
    "points_rebounds_assists": "pra", "pts_rebs_asts": "pra",
    "points_rebounds": "pr", "pts_rebs": "pr", "points_assists": "pa", "pts_asts": "pa",
    "rebounds_assists": "ra", "rebs_asts": "ra", "steals": "steals", "blocks": "blocks",
    "steals_blocks": "stocks", "blks_stls": "stocks", "turnovers": "turnovers",
    # baseball
    "hr": "home_runs", "home_runs": "home_runs", "hits": "hits", "total_bases": "total_bases",
    "rbi": "rbis", "rbis": "rbis", "hits_runs_rbis": "hrr", "runs_scored": "runs", "runs": "runs",
    "pitching_outs": "pitch_outs", "pitch_outs": "pitch_outs", "outs": "pitch_outs",
    "hits_allowed": "hits_allowed", "earned_runs": "earned_runs",
    "earned_runs_allowed": "earned_runs", "runs_allowed": "runs_allowed",
    "stolen_bases": "stolen_bases", "singles": "singles", "doubles": "doubles",
    "triples": "triples", "hitter_strikeouts": "batter_ks", "batter_strikeouts": "batter_ks",
    "hitter_walks": "walks", "bat_walks": "walks", "walks_allowed": "pitcher_walks",
    "pitcher_strikeouts": "pitcher_ks", "strikeouts": "pitcher_ks",
    "longest_passing_completion": "longest_pass", "passing_and_rushing_yards": "pass_rush_yds",
    "extra_point_made": "extra_points_made", "field_goal_made": "field_goals_made",
    "pts_&_rebs_&_asts": "pra", "pts_reb_ast": "pra", "pts_&_rebs": "pr", "pts_&_asts": "pa",
    "rebs_&_asts": "ra", "3_point_fg": "threes", "bases": "total_bases",
    "powerplay_points": "pp_points", "strike_outs": "pitcher_ks",
    # hockey
    "shots_on_goal": "sog", "goaltender_saves": "saves", "saves": "saves",
    "anytime_goal_scorer": "anytime_goal", "goals": "goals", "power_play_points": "pp_points",
    # soccer
    "shots_on_target": "shots_on_target", "shots": "shots",
}
STAT_LEAGUE = {
    "mlb": {"walks": "pitcher_walks", "strikeouts": "pitcher_ks"},
    "nhl": {"shots": "sog"},
}


def canon_stat(league, raw, line=None):
    raw = (raw or "").lower().strip().replace(" ", "_").replace("+", "_")
    if raw in ("touchdowns", "rush_rec_tds", "anytime_td_scorer") and line == 0.5 \
            and league in ("nfl", "ncaaf"):
        return "anytime_td"
    if league in STAT_LEAGUE and raw in STAT_LEAGUE[league]:
        return STAT_LEAGUE[league][raw]
    return STAT_COMMON.get(raw, raw)


# --------------------------------------------------------------------------
# Window helpers
# --------------------------------------------------------------------------
def window():
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    return start, start + timedelta(days=WINDOW_DAYS, hours=1)


def in_window(ts):
    t = parse_ts(ts) if isinstance(ts, str) else ts
    if t is None:
        return True
    a, b = window()
    return a <= t <= b


def et_dates(days=WINDOW_DAYS):
    d0 = datetime.now(ET).date()
    return [d0 + timedelta(days=i) for i in range(days + 1)]


# --------------------------------------------------------------------------
# Action Network
# --------------------------------------------------------------------------
def an_scoreboard_urls(league, book_ids, extra=""):
    """Yields URLs covering the next WINDOW_DAYS for a league."""
    slug = AN_SLUG[league]
    ids = ",".join(map(str, book_ids))
    base = "%s/web/v2/scoreboard/%s%s?bookIds=%s&periods=event" % (AN, slug, extra, ids)
    if league in WEEKLY:
        return [base, None]  # None => caller adds next week
    return [base + "&date=" + d.strftime("%Y%m%d") for d in et_dates()]


def an_fetch_games(league, book_ids, extra="", markets_mode=False):
    """Returns list of raw AN game/competition dicts (deduped) plus http code."""
    urls = an_scoreboard_urls(league, book_ids, extra)
    seen, out, last_code = set(), [], None
    payloads = []
    i = 0
    while i < len(urls):
        u = urls[i]
        i += 1
        if u is None:
            # weekly league: also fetch next week
            weeks = [g.get("week") for p in payloads for g in (p.get("games") or [])
                     if g.get("week")]
            if weeks:
                urls.append(urls[0] + "&week=%d" % (max(weeks) + 1))
            continue
        code, data, err = http_get(u)
        last_code = code
        if not data:
            log("  AN %s -> %s %s" % (u[:110], code, err))
            continue
        payloads.append(data)
        for g in (data.get("games") or data.get("competitions") or []):
            if g["id"] in seen:
                continue
            seen.add(g["id"])
            out.append(g)
    return out, payloads, last_code


def an_participants(g):
    """Returns (away, home) dicts: {id, name, abbr, kind}."""
    if "competitors" in g:  # UFC
        away = home = None
        for c in g["competitors"]:
            p = c.get("player") or {}
            d = {"id": c["id"], "name": p.get("full_name"), "abbr": p.get("abbr"),
                 "player_id": c.get("player_id")}
            if c.get("side") == "home":
                home = d
            else:
                away = d
        return away, home
    teams = {t["id"]: t for t in g.get("teams", [])}

    def td(tid):
        t = teams.get(tid) or {}
        return {"id": tid, "name": t.get("full_name"), "abbr": t.get("abbr"),
                "short": t.get("display_name"), "location": t.get("location")}
    return td(g.get("away_team_id")), td(g.get("home_team_id"))


def an_parse_lines(g, away, home):
    """Returns book_name -> {moneyline, spread, total} using AN v2 markets."""
    books = {}
    ufc = "competitors" in g
    for bid, m in (g.get("markets") or {}).items():
        bid = int(bid)
        if bid not in AN_BOOKS:
            continue
        ev = (m or {}).get("event") or {}
        rec = {}
        ml = {}
        for o in ev.get("moneyline", []):
            if o.get("is_alt_market") or o.get("is_live"):
                continue
            side = o.get("side")
            if ufc:
                cid = o.get("competitor_id")
                side = "home" if home and cid == home["id"] else (
                    "away" if away and cid == away["id"] else None)
            if side in ("home", "away", "draw") and o.get("odds"):
                ml[side] = o["odds"]
        if ml:
            rec["moneyline"] = ml
        sp = {}
        for o in ev.get("spread", []):
            if o.get("is_alt_market") or o.get("is_live"):
                continue
            side = o.get("side")
            if ufc:
                cid = o.get("competitor_id")
                side = "home" if home and cid == home["id"] else "away"
            if side in ("home", "away") and o.get("odds"):
                sp[side] = {"line": o.get("value"), "price": o["odds"]}
        if len(sp) == 2:
            rec["spread"] = sp
        tot = {}
        for o in ev.get("total", []):
            if o.get("is_alt_market") or o.get("is_live"):
                continue
            if o.get("side") in ("over", "under") and o.get("odds"):
                tot[o["side"]] = o["odds"]
                tot["line"] = o.get("value")
        if "over" in tot and "under" in tot:
            rec["total"] = tot
        if rec:
            books[AN_BOOKS[bid][0]] = rec
    return books


# --------------------------------------------------------------------------
# Pinnacle (guest API used by pinnacle.com's public site; no key required)
# --------------------------------------------------------------------------
def pin_league(lid):
    c1, mus, e1 = http_get("%s/leagues/%d/matchups" % (PIN, lid))
    c2, mks, e2 = http_get("%s/leagues/%d/markets/straight" % (PIN, lid))
    return (mus or []), (mks or []), c1 or c2


def pin_games(league):
    """Returns list of {start, home, away, moneyline, spread, total} from Pinnacle."""
    out = []
    for lid in PIN_LEAGUES.get(league, []):
        mus, mks, _ = pin_league(lid)
        bym = {}
        for mk in mks:
            if mk.get("period") != 0 or mk.get("isAlternate") or mk.get("status") != "open":
                continue
            bym.setdefault(mk["matchupId"], []).append(mk)
        for mu in mus:
            if mu.get("type") != "matchup" or mu.get("parentId"):
                continue
            if mu.get("isLive"):
                continue
            parts = {p.get("alignment"): p.get("name") for p in mu.get("participants", [])}
            if "home" not in parts or "away" not in parts:
                continue
            rec = {"start": mu.get("startTime"), "home": parts["home"], "away": parts["away"]}
            for mk in bym.get(mu["id"], []):
                pr = {p.get("designation"): p for p in mk.get("prices", [])}
                if mk["type"] == "moneyline":
                    ml = {k: pr[k]["price"] for k in ("home", "away", "draw") if k in pr}
                    if len(ml) >= 2:
                        rec["moneyline"] = ml
                elif mk["type"] == "spread" and "home" in pr and "away" in pr:
                    rec["spread"] = {s: {"line": pr[s].get("points"), "price": pr[s]["price"]}
                                     for s in ("home", "away")}
                elif mk["type"] == "total" and "over" in pr and "under" in pr:
                    rec["total"] = {"line": pr["over"].get("points"),
                                    "over": pr["over"]["price"], "under": pr["under"]["price"]}
            out.append(rec)
    return out


def pin_props(league):
    """Player props from Pinnacle 'special' matchups (category Player Props)."""
    out = []
    for lid in PIN_LEAGUES.get(league, []):
        mus, mks, _ = pin_league(lid)
        bym = {}
        for mk in mks:
            if mk.get("period") == 0 and mk.get("status", "open") == "open":
                bym.setdefault(mk["matchupId"], []).append(mk)
        for mu in mus:
            sp = mu.get("special") or {}
            if mu.get("type") != "special" or sp.get("category") != "Player Props":
                continue
            desc = sp.get("description") or ""
            m = re.match(r"(.+?)\s+(?:Total\s+)?(.+?)(?:\s*\(.*\))?$", desc)
            player = desc.split(" Total ")[0] if " Total " in desc else (m.group(1) if m else desc)
            pmap = {p["id"]: p["name"].lower() for p in mu.get("participants", [])}
            parent = mu.get("parent") or {}
            pparts = {p.get("alignment"): p.get("name") for p in parent.get("participants", [])}
            for mk in bym.get(mu["id"], []):
                o = u = None
                line = None
                for p in mk.get("prices", []):
                    nm = pmap.get(p.get("participantId"), "")
                    if nm in ("over", "yes"):
                        o, line = p.get("price"), p.get("points", line)
                    elif nm in ("under", "no"):
                        u, line = p.get("price"), p.get("points", line)
                if o is None and u is None:
                    continue
                out.append({
                    "source": "Pinnacle", "league": league, "player": player.strip(),
                    "team": None, "stat_raw": mu.get("units") or desc,
                    "stat": canon_stat(league, mu.get("units") or "", line),
                    "line": line, "over": o, "under": u,
                    "game": ("%s @ %s" % (pparts.get("away"), pparts.get("home"))
                             if pparts else None),
                    "start": mu.get("startTime"),
                })
    return out


# --------------------------------------------------------------------------
# Kalshi (public, unauthenticated market data)
# --------------------------------------------------------------------------
def kalshi_markets(series, max_pages=5):
    out, cursor, code = [], None, None
    for _ in range(max_pages):
        u = "%s/markets?series_ticker=%s&status=open&limit=1000" % (KALSHI, series)
        if cursor:
            u += "&cursor=" + urllib.parse.quote(cursor)
        code, d, err = http_get(u)
        if not d:
            log("  Kalshi %s -> %s %s" % (series, code, err))
            break
        out.extend(d.get("markets") or [])
        cursor = d.get("cursor")
        if not cursor:
            break
    return out, code


def kprice(x):
    v = to_float(x)
    return None if v is None else round(v, 4)


_MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def kalshi_event_date(event_ticker):
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", event_ticker or "")
    if not m:
        return None
    try:
        return datetime(2000 + int(m.group(1)), _MON[m.group(2)], int(m.group(3))).date()
    except (KeyError, ValueError):
        return None


def kalshi_game_lines(league):
    """Returns event_ticker -> {date, outcomes:[{code,name,yes_bid,yes_ask,last}]}."""
    series = KALSHI_GAME_SERIES.get(league)
    if not series:
        return {}
    mk, _ = kalshi_markets(series)
    ev = {}
    for m in mk:
        et = m.get("event_ticker")
        e = ev.setdefault(et, {"date": kalshi_event_date(et), "outcomes": []})
        e["outcomes"].append({
            "code": (m.get("ticker") or "").rsplit("-", 1)[-1],
            "name": m.get("yes_sub_title"), "yes_bid": kprice(m.get("yes_bid_dollars")),
            "yes_ask": kprice(m.get("yes_ask_dollars")),
            "last": kprice(m.get("last_price_dollars")), "ticker": m.get("ticker")})
    return ev


def kalshi_match(game_rec, away, home, kev):
    """Attach Kalshi direct moneyline (from yes ask) to a game if matched."""
    st = parse_ts(game_rec["start_time"])
    gdate = st.astimezone(ET).date() if st else None
    for et, e in kev.items():
        if gdate and e["date"] and abs((e["date"] - gdate).days) > 1:
            continue
        side = {}
        for o in e["outcomes"]:
            c, n = (o["code"] or "").upper(), o["name"] or ""
            if c in ("TIE", "DRAW") or n.lower() in ("tie", "draw"):
                side["draw"] = o
                continue
            for lab, t in (("away", away), ("home", home)):
                if not t:
                    continue
                if (t.get("abbr") and c == (t["abbr"] or "").upper()) or \
                        name_match(n, t.get("name")) or \
                        (t.get("location") and norm_name(n) == norm_name(t["location"])) or \
                        (t.get("short") and norm_name(n) == norm_name(t["short"])):
                    side.setdefault(lab, o)
        if "away" in side and "home" in side and side["away"] is not side["home"]:
            ml = {}
            for k, o in side.items():
                a = prob_to_am(o["yes_ask"]) if o["yes_ask"] else None
                if a:
                    ml[k] = a
            return {"event_ticker": et, "moneyline": ml,
                    "yes_prices": {k: {"bid": o["yes_bid"], "ask": o["yes_ask"],
                                       "last": o["last"]} for k, o in side.items()}}
    return None


# --------------------------------------------------------------------------
# Lines
# --------------------------------------------------------------------------
def better(a, b):
    """True if american odds a pays more than b."""
    return am_to_dec(a) > am_to_dec(b)


EXCHANGE_BOOKS = {"Kalshi", "Polymarket", "Kalshi (direct)"}


def best_prices(books, include_exchanges=False):
    best = {}

    def upd(key, book, price, line=None, line_better=None):
        cur = best.get(key)
        cand = {"book": book, "price": price}
        if line is not None:
            cand["line"] = line
        if cur is None:
            best[key] = cand
            return
        if line is not None and cur.get("line") != line:
            if line_better(line, cur["line"]):
                best[key] = cand
            return
        if better(price, cur["price"]):
            best[key] = cand

    for name, rec in books.items():
        if name in ("AN Consensus", "AN Open"):
            continue
        if name in EXCHANGE_BOOKS and not include_exchanges:
            continue
        for side, p in (rec.get("moneyline") or {}).items():
            upd("ml_" + side, name, p)
        sp = rec.get("spread")
        if sp:
            for side in ("away", "home"):
                upd("spread_" + side, name, sp[side]["price"], sp[side]["line"],
                    lambda a, b: a > b)
        t = rec.get("total")
        if t:
            upd("total_over", name, t["over"], t["line"], lambda a, b: a < b)
            upd("total_under", name, t["under"], t["line"], lambda a, b: a > b)
    return best


def consensus(books, pin):
    out = {}
    if pin:
        out["source"] = "Pinnacle"
        if pin.get("moneyline"):
            out["moneyline"] = novig(pin["moneyline"])
        if pin.get("spread"):
            s = pin["spread"]
            nv = novig({"away": s["away"]["price"], "home": s["home"]["price"]})
            out["spread"] = {"home_line": s["home"]["line"], **(nv or {})}
        if pin.get("total"):
            t = pin["total"]
            nv = novig({"over": t["over"], "under": t["under"]})
            out["total"] = {"line": t["line"], **(nv or {})}
        if out.get("moneyline"):
            return out
    # fallback: mean of per-book no-vig probabilities (sportsbooks + exchanges)
    out = {"source": "mean no-vig of visible books"}
    mls = [novig(r["moneyline"]) for n, r in books.items()
           if n not in ("AN Consensus", "AN Open") and r.get("moneyline")]
    mls = [m for m in mls if m]
    if mls:
        keys = set.intersection(*[set(m) for m in mls]) if mls else set()
        # only average books offering the same sides (2-way vs 3-way)
        modal = max(({tuple(sorted(m)) for m in mls}),
                    key=lambda k: sum(1 for m in mls if tuple(sorted(m)) == k))
        same = [m for m in mls if tuple(sorted(m)) == modal]
        out["moneyline"] = {k: round(statistics.mean(m[k] for m in same), 4) for k in modal}
        out["n_books"] = len(same)
    for mkt, a, b, lk in (("spread", "away", "home", None), ("total", "over", "under", None)):
        rows = []
        for n, r in books.items():
            if n in ("AN Consensus", "AN Open") or not r.get(mkt):
                continue
            x = r[mkt]
            if mkt == "spread":
                rows.append((x["home"]["line"], x["away"]["price"], x["home"]["price"]))
            else:
                rows.append((x["line"], x["over"], x["under"]))
        if not rows:
            continue
        lines = [r[0] for r in rows]
        modal = max(set(lines), key=lines.count)
        nv = [novig({a: r[1], b: r[2]}) for r in rows if r[0] == modal]
        nv = [v for v in nv if v]
        if nv:
            rec = {("home_line" if mkt == "spread" else "line"): modal}
            rec[a] = round(statistics.mean(v[a] for v in nv), 4)
            rec[b] = round(statistics.mean(v[b] for v in nv), 4)
            out[mkt] = rec
    return out


def match_pin(rec, away, home, pins, ufc=False):
    st = parse_ts(rec["start_time"])
    for p in pins:
        pt = parse_ts(p["start"])
        if st and pt and abs((st - pt).total_seconds()) > 6 * 3600:
            continue
        an, hn = (away or {}).get("name"), (home or {}).get("name")
        if name_match(p["home"], hn) and name_match(p["away"], an):
            return p, False
        if name_match(p["home"], an) and name_match(p["away"], hn):
            return p, True   # flipped (neutral site / fight card ordering)
    return None, False


def flip(p):
    q = dict(p)
    if p.get("moneyline"):
        q["moneyline"] = dict(p["moneyline"])
        q["moneyline"]["home"], q["moneyline"]["away"] = p["moneyline"].get("away"), \
            p["moneyline"].get("home")
    if p.get("spread"):
        q["spread"] = {"home": p["spread"]["away"], "away": p["spread"]["home"]}
    return q


def cmd_lines(leagues):
    games_out, summary = [], {}
    for lg in leagues:
        log("[lines] %s" % lg)
        games, _, code = an_fetch_games(lg, LINE_BOOK_IDS)
        pins = pin_games(lg)
        kev = kalshi_game_lines(lg)
        n = 0
        for g in games:
            if g.get("status") not in (None, "scheduled", "created"):
                continue
            if not in_window(g.get("start_time")):
                continue
            away, home = an_participants(g)
            rec = {"league": lg, "an_game_id": g["id"], "start_time": g.get("start_time"),
                   "away": (away or {}).get("name"), "home": (home or {}).get("name"),
                   "away_abbr": (away or {}).get("abbr"), "home_abbr": (home or {}).get("abbr")}
            books = an_parse_lines(g, away, home)
            pin, flipped = match_pin(rec, away, home, pins)
            if pin:
                pb = flip(pin) if flipped else pin
                books["Pinnacle"] = {k: pb[k] for k in ("moneyline", "spread", "total")
                                     if pb.get(k)}
            kd = kalshi_match(rec, away, home, kev) if kev else None
            if kd and kd.get("moneyline"):
                books["Kalshi (direct)"] = {"moneyline": kd["moneyline"]}
                rec["kalshi_yes_prices"] = kd["yes_prices"]
                rec["kalshi_event"] = kd["event_ticker"]
            rec["books"] = books
            rec["best"] = best_prices(books)
            rec["best_incl_exchanges"] = best_prices(books, include_exchanges=True)
            rec["consensus_novig"] = consensus(books, books.get("Pinnacle"))
            games_out.append(rec)
            n += 1
        summary[lg] = {"games": n, "an_http": code, "pinnacle_events": len(pins),
                       "kalshi_events": len(kev),
                       "with_pinnacle": sum(1 for x in games_out
                                            if x["league"] == lg and "Pinnacle" in x["books"])}
        log("   games=%d pinnacle=%d kalshi=%d" % (n, len(pins), len(kev)))
    games_out.sort(key=lambda r: (r["start_time"] or "", r["league"]))
    out = {"generated_at": now_iso(), "window_days": WINDOW_DAYS,
           "note": "Practice data only. American odds. 'best' = sportsbooks only (incl. "
                   "Pinnacle); 'best_incl_exchanges' adds Kalshi/Polymarket (AN shows "
                   "Polymarket at ~mid/no-vig prices, often not actually fillable). Kalshi (direct) moneyline is the "
                   "yes-ask converted to American odds (fees ignored). consensus_novig prefers "
                   "Pinnacle; otherwise mean of per-book no-vig probs at the modal line.",
           "summary": summary, "games": games_out}
    write_json(OUT_LINES, out, leagues)
    log("wrote %s (%d games)" % (OUT_LINES, len(games_out)))


def write_json(path, out, leagues=None):
    """Writes; if a league subset was requested, merges with existing file."""
    if leagues and set(leagues) != set(LEAGUES) and os.path.exists(path):
        try:
            old = json.load(open(path))
            key = "games" if "games" in out else "props"
            keep = [x for x in old.get(key, []) if x.get("league") not in leagues]
            out[key] = keep + out[key]
            s = old.get("summary", {})
            s.update(out.get("summary", {}))
            out["summary"] = s
        except Exception:
            pass
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, indent=1, default=str)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Props
# --------------------------------------------------------------------------
def an_props(league):
    types = AN_PROP_TYPES.get(league)
    if not types:
        return [], None
    names = ["core_bet_type_%d_%s" % (t, AN_TYPE_NAMES[t]) for t in types]
    rows, code = [], None
    ids = ",".join(map(str, PROP_BOOK_IDS))
    for i in range(0, len(names), 5):
        chunk = ",".join(names[i:i + 5])
        base = "%s/web/v2/scoreboard/%s/markets?bookIds=%s&customPickTypes=%s" % (
            AN, AN_SLUG[league], ids, chunk)
        urls = [base]
        if league in WEEKLY:
            urls = [base]  # current week only; props rarely posted further out
        else:
            urls = [base] + [base + "&date=" + d.strftime("%Y%m%d") for d in et_dates(2)]
        for u in urls:
            code, d, err = http_get(u, timeout=60)
            if not d:
                log("  AN props %s -> %s %s" % (chunk[:60], code, err))
                continue
            players = {p["id"]: p for p in d.get("players") or []}
            games = {g["id"]: g for g in d.get("games") or []}
            teams = {}
            for g in games.values():
                for k in ("home_team", "away_team"):
                    if g.get(k):
                        teams[g[k]["id"]] = g[k].get("abbr")
            for bid, m in (d.get("markets") or {}).items():
                bid = int(bid)
                if bid not in AN_BOOKS:
                    continue
                for typ, outs in ((m or {}).get("event") or {}).items():
                    agg = {}
                    for o in outs:
                        if o.get("is_live") or o.get("is_alt_market"):
                            continue
                        pid = o.get("player_id")
                        if not pid:
                            continue
                        key = (pid, o.get("event_id"), o.get("value") if o.get("side") != "noside"
                               else None)
                        a = agg.setdefault(key, {"o": o})
                        side = o.get("side")
                        if side in ("over", "under"):
                            a[side] = o.get("odds")
                        elif side in ("noside", "yes"):
                            a["over"] = o.get("odds")
                        elif side == "no":
                            a["under"] = o.get("odds")
                    raw = re.sub(r"^core_bet_type_\d+_", "", typ)
                    for (pid, eid, val), a in agg.items():
                        p = players.get(pid, {})
                        g = games.get(eid, {})
                        if g and not in_window(g.get("start_time")):
                            continue
                        line = val if val is not None else (0.5 if raw.startswith("anytime")
                                                            else None)
                        rows.append({
                            "source": AN_BOOKS[bid][0], "via": "Action Network",
                            "league": league, "player": p.get("full_name") or str(pid),
                            "team": teams.get(p.get("team_id")),
                            "stat_raw": raw, "stat": canon_stat(league, raw, line), "line": line,
                            "over": a.get("over") or None, "under": a.get("under") or None,
                            "game": ("%s @ %s" % (g["away_team"]["abbr"], g["home_team"]["abbr"])
                                     if g.get("away_team") and g.get("home_team") else None),
                            "start": g.get("start_time"),
                        })
    # de-dup (same source/player/stat/line from overlapping date queries)
    seen, out = set(), []
    for r in rows:
        k = (r["source"], r["player"], r["stat"], r["line"], r["game"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out, code


UD_SPORT = {"NFL": "nfl", "CFB": "ncaaf", "NBA": "nba", "MLB": "mlb", "NHL": "nhl",
            "MMA": "ufc", "FIFA": "soccer"}
_ud_cache = {}


def underdog_all():
    if "d" not in _ud_cache:
        _ud_cache["d"] = http_get("https://api.underdogfantasy.com/v1/over_under_lines",
                                  timeout=90)
    return _ud_cache["d"]


def underdog_props(leagues):
    code, d, err = underdog_all()
    if not d:
        log("  Underdog -> %s %s" % (code, err))
        return [], code
    players = {p["id"]: p for p in d.get("players", [])}
    apps = {a["id"]: a for a in d.get("appearances", [])}
    games = {g["id"]: g for g in d.get("games", [])}
    team_abbr = {}
    for g in games.values():
        t = (g.get("abbreviated_title") or "")
        parts = re.split(r"\s+(?:@|vs\.?)\s+", t)
        if len(parts) == 2:
            team_abbr[g.get("away_team_id")] = parts[0]
            team_abbr[g.get("home_team_id")] = parts[1]
    out = []
    for ln in d.get("over_under_lines", []):
        ou = ln.get("over_under") or {}
        st = ou.get("appearance_stat") or {}
        app = apps.get(st.get("appearance_id"))
        if not app or ln.get("status") != "active":
            continue
        pl = players.get(app.get("player_id"), {})
        lg = UD_SPORT.get(pl.get("sport_id"))
        g = games.get(app.get("match_id"), {})
        if lg == "soccer":
            lg = "epl" if g.get("sport_group_name") == "EPL" else None
        if lg not in leagues:
            continue
        if g and not in_window(g.get("scheduled_at")):
            continue
        raw = st.get("stat") or ""
        if raw.startswith("period_1_2_") and lg == "epl":
            raw = raw[len("period_1_2_"):]
        elif raw.startswith("period_"):
            continue
        if raw.startswith("regular_season") or raw.startswith("season_"):
            continue  # season-long futures, not game props
        line = to_float(ln.get("stat_value"))
        stat = canon_stat(lg, raw, line)
        o = u = None
        for opt in ln.get("options", []):
            pr = to_float(opt.get("american_price"))
            pr = int(pr) if pr is not None else None
            if opt.get("choice") in ("higher", "over"):
                o = pr
            elif opt.get("choice") in ("lower", "under"):
                u = pr
        out.append({
            "source": "Underdog", "league": lg,
            "player": ("%s %s" % (pl.get("first_name", ""), pl.get("last_name", ""))).strip(),
            "team": team_abbr.get(app.get("team_id")), "stat_raw": raw, "stat": stat,
            "line": line, "over": o, "under": u, "line_type": ln.get("line_type"),
            "game": g.get("abbreviated_title"), "start": g.get("scheduled_at"),
        })
    return out, code


SLEEPER_SPORT = {"nfl": "nfl", "cfb": "ncaaf", "nba": "nba", "mlb": "mlb", "nhl": "nhl",
                 "clubsoccer:epl": "epl"}
SLEEPER_PLAYERS = {"nfl": "nfl", "ncaaf": "cfb", "nba": "nba", "mlb": "mlb", "nhl": "nhl",
                   "epl": "clubsoccer:epl"}


def sleeper_props(leagues):
    code, d, err = http_get("https://api.sleeper.app/lines/available?dynamic=true"
                            "&include_preseason=true&eg=15.control", timeout=60)
    if not d:
        log("  Sleeper -> %s %s" % (code, err))
        return [], code
    need = {SLEEPER_SPORT.get(x.get("sport")) for x in d} & set(leagues)
    names = {}
    for lg in need:
        sp = SLEEPER_PLAYERS.get(lg)
        if not sp:
            continue
        c, pdata, e = http_get("https://api.sleeper.app/v1/players/" + sp, timeout=90)
        if pdata:
            names[lg] = {k: (v.get("full_name") or ("%s %s" % (v.get("first_name", ""),
                                                               v.get("last_name", ""))).strip())
                         for k, v in pdata.items()}
    out = []
    for x in d:
        lg = SLEEPER_SPORT.get(x.get("sport"))
        if lg not in leagues or x.get("subject_type") != "player":
            continue
        if x.get("status") != "active" or x.get("game_status") not in (None, "pre_game"):
            continue
        o = u = None
        line = None
        team = None
        for opt in x.get("options", []):
            pm = to_float(opt.get("payout_multiplier"))
            am = prob_to_am(1 / pm) if pm and pm > 1 else None
            if opt.get("outcome") in ("over", "yes"):
                o, line = am, opt.get("outcome_value", line)
            elif opt.get("outcome") in ("under", "no"):
                u, line = am, opt.get("outcome_value", line)
            team = team or opt.get("subject_team")
        raw = x.get("wager_type") or ""
        nm = names.get(lg, {}).get(str(x.get("subject_id")))
        if not nm:
            continue  # no public name mapping for this subject id
        out.append({"source": "Sleeper", "league": lg, "player": nm, "team": team,
                    "stat_raw": raw, "stat": canon_stat(lg, raw, line), "line": line,
                    "over": o, "under": u, "line_type": x.get("line_type"),
                    "game": None, "start": None, "sleeper_game_id": x.get("game_id"),
                    "price_note": "converted from payout_multiplier"})
    return out, code


def kalshi_props(league):
    out = []
    code = None
    for series, stat in KALSHI_PROP_SERIES.get(league, {}).items():
        mk, code = kalshi_markets(series)
        for m in mk:
            sub = m.get("yes_sub_title") or m.get("title") or ""
            player = sub.split(":")[0].strip()
            line = to_float(m.get("floor_strike"))
            if stat.startswith("anytime"):
                line = 0.5
            out.append({
                "source": "Kalshi", "league": league, "player": player, "team": None,
                "stat_raw": series, "stat": stat, "line": line,
                "yes_bid": kprice(m.get("yes_bid_dollars")),
                "yes_ask": kprice(m.get("yes_ask_dollars")),
                "no_bid": kprice(m.get("no_bid_dollars")),
                "no_ask": kprice(m.get("no_ask_dollars")),
                "last": kprice(m.get("last_price_dollars")),
                "ticker": m.get("ticker"), "title": m.get("title"),
                "game": m.get("event_ticker"), "start": None,
            })
    return out, code


def _balance(e):
    p = am_to_prob(e.get("over")) if e.get("over") else None
    return abs(p - 0.5) if p else 0.25


def _pick_main_lines(g):
    """For sources with several lines, make the main entry the standard line
    (closest to the cross-source median, then closest to even odds)."""
    firsts = [s["line"] for n, s in g["sources"].items()
              if n != "Kalshi" and isinstance(s.get("line"), (int, float))]
    if not firsts:
        return
    med = statistics.median(firsts)
    for n, s in list(g["sources"].items()):
        if n == "Kalshi" or not s.get("alts"):
            continue
        cands = [dict(s, alts=None)] + s["alts"]
        for c in cands:
            c.pop("alts", None)
        cands.sort(key=lambda e: (bool(e.get("line_type")),
                                  abs((e["line"] if isinstance(e.get("line"), (int, float))
                                       else med) - med), _balance(e)))
        main = cands[0]
        main["alts"] = cands[1:]
        g["sources"][n] = main


def group_props(rows):
    """Groups source rows into one record per (league, player, stat)."""
    groups = {}
    for r in rows:
        k = (r["league"], norm_name(r["player"]), r["stat"])
        g = groups.setdefault(k, {"league": r["league"], "player": r["player"],
                                  "team": r.get("team"), "stat": r["stat"],
                                  "game": None, "start": None, "sources": {}})
        if not g["team"] and r.get("team"):
            g["team"] = r["team"]
        if r.get("game") and not (r["source"] == "Kalshi") and not g["game"]:
            g["game"] = r["game"]
        if r.get("start") and not g["start"]:
            g["start"] = r["start"]
        src = r["source"]
        if src == "Kalshi":
            g["sources"].setdefault("Kalshi", {"ladder": []})["ladder"].append(
                {k2: r[k2] for k2 in ("line", "yes_bid", "yes_ask", "no_bid", "no_ask", "last",
                                      "ticker")})
            continue
        entry = {"line": r["line"], "over": r.get("over"), "under": r.get("under")}
        if r.get("line_type") and r["line_type"] not in ("balanced", "normal"):
            entry["line_type"] = r["line_type"]
        if r.get("via"):
            entry["via"] = r["via"]
        cur = g["sources"].get(src)
        if cur is None:
            g["sources"][src] = entry
        else:
            # keep main line; extra lines go to alts
            if entry.get("line_type") or (cur.get("line_type") is None and
                                          entry["line"] == cur["line"]):
                cur.setdefault("alts", []).append(entry)
            elif cur.get("line_type"):
                entry["alts"] = cur.pop("alts", []) + [cur]
                g["sources"][src] = entry
            else:
                cur.setdefault("alts", []).append(entry)
    out = []
    for g in groups.values():
        for s in g["sources"].values():
            if "ladder" in s:
                s["ladder"].sort(key=lambda x: (x["line"] is None, x["line"] or 0))
        _pick_main_lines(g)
        lines = [s["line"] for n, s in g["sources"].items()
                 if n != "Kalshi" and isinstance(s.get("line"), (int, float))]
        g["consensus_line"] = statistics.median(lines) if lines else None
        g["n_sources"] = len(g["sources"])
        out.append(g)
    out.sort(key=lambda g: (g["league"], -g["n_sources"], g["player"], g["stat"]))
    return out


def cmd_props(leagues):
    rows, summary = [], {}
    ud, ud_code = underdog_props(leagues)
    sl, sl_code = sleeper_props(leagues)
    rows += ud + sl
    for lg in leagues:
        log("[props] %s" % lg)
        a, a_code = an_props(lg)
        p = pin_props(lg)
        k, k_code = kalshi_props(lg)
        rows += a + p + k
        cnt = {}
        for r in a + p + k + [x for x in ud + sl if x["league"] == lg]:
            cnt[r["source"]] = cnt.get(r["source"], 0) + 1
        summary[lg] = {"rows_by_source": cnt}
        log("   %s" % cnt)
    grouped = group_props(rows)
    for lg in leagues:
        summary[lg]["grouped_props"] = sum(1 for g in grouped if g["league"] == lg)
    out = {"generated_at": now_iso(),
           "note": "Practice data only. over/under are American odds per source. PrizePicks "
                   "(via Action Network) posts lines only (odds 0 -> null). Underdog prices are "
                   "its american_price; Sleeper prices are converted from payout multipliers. "
                   "Kalshi rows give yes/no dollar prices per threshold market (line = X-0.5 "
                   "for 'X+'). 'alts' holds additional lines from the same source.",
           "summary": summary, "props": grouped}
    write_json(OUT_PROPS, out, leagues)
    log("wrote %s (%d grouped props from %d rows)" % (OUT_PROPS, len(grouped), len(rows)))


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------
DIRECT_PROBES = [
    # name, kind, url, headers
    ("FanDuel", "sportsbook",
     "https://sbapi.oh.sportsbook.fanduel.com/api/content-managed-page?page=CUSTOM"
     "&customPageId=nfl&_ak=FhMFpcPWXMeyZxOx"),
    ("DraftKings", "sportsbook",
     "https://sportsbook-nash.draftkings.com/api/sportscontent/dkusnj/v1/leagues/88808"),
    ("BetMGM", "sportsbook",
     "https://sports.nj.betmgm.com/cds-api/bettingoffer/fixtures?x-bwin-accessid="
     "NmFjNmUwZjAtMGI3Yi00YzA3LTg3OTktNDgxMGIwM2YxZGVh&lang=en-us&country=US&userCountry=US"
     "&fixtureTypes=Standard&state=Latest&offerMapping=Filtered&sportIds=11"),
    ("Caesars", "sportsbook",
     "https://api.americanwagering.com/regions/us/locations/nj/brands/czr/sb/v3/sports/"
     "americanfootball/events/schedule"),
    ("Pinnacle", "sportsbook", PIN + "/leagues/889/matchups"),
    ("Bovada (offshore, not US-licensed)", "sportsbook",
     "https://www.bovada.lv/services/sports/event/v2/events/A/description/football/nfl"),
    ("PrizePicks", "pickem", "https://api.prizepicks.com/projections?league_id=9&per_page=10"),
    ("Underdog", "pickem", "https://api.underdogfantasy.com/v1/over_under_lines"),
    ("Sleeper", "pickem",
     "https://api.sleeper.app/lines/available?dynamic=true&include_preseason=true&eg=15.control"),
    ("Kalshi", "exchange/prediction",
     KALSHI + "/markets?series_ticker=KXNFLGAME&status=open&limit=5"),
    ("Polymarket", "exchange/prediction",
     "https://gamma-api.polymarket.com/events?tag_slug=nfl&closed=false&limit=5"),
    ("Novig", "exchange/prediction", "https://api.novig.us/"),
    ("ProphetX", "exchange/prediction", "https://api.prophetx.co/partner/mm/get_sport_events"),
]
AGG_PROBES = [
    ("Action Network", AN + "/web/v1/books"),
    ("ESPN (DraftKings odds)", "https://site.api.espn.com/apis/site/v2/sports/football/nfl/"
                              "scoreboard"),
    ("OddsShark", "https://www.oddsshark.com/nfl/odds"),
]
STATUS_BOOKS = [  # name, kind, AN canonical id (or None)
    ("DraftKings", "sportsbook", 68), ("FanDuel", "sportsbook", 69),
    ("BetMGM", "sportsbook", 75), ("Caesars", "sportsbook", 123),
    ("bet365", "sportsbook", 79), ("BetRivers", "sportsbook", 71),
    ("Fanatics", "sportsbook", 2988), ("Hard Rock Bet", "sportsbook", 2724),
    ("theScore Bet (ex-ESPN BET)", "sportsbook", 4620), ("Bally Bet", "sportsbook", 4693),
    ("Parx", "sportsbook", 1929), ("Unibet", "sportsbook", 247),
    ("Pinnacle", "sportsbook", 3), ("Circa", "sportsbook", 78), ("Fliff", "sportsbook", 2292),
    ("Bovada (offshore, not US-licensed)", "sportsbook", 21),
    ("PrizePicks", "pickem", 2194), ("Underdog", "pickem", 3348), ("Sleeper", "pickem", 3612),
    ("Kalshi", "exchange/prediction", 4727), ("Polymarket", "exchange/prediction", 4795),
    ("Prophet", "exchange/prediction", 1467),
    ("Novig", "exchange/prediction", None), ("ProphetX", "exchange/prediction", None),
]


def cmd_status():
    checked = now_iso()
    direct = {}
    for name, kind, url in DIRECT_PROBES:
        code, data, err = http_get(url, timeout=30, retries=2,
                                   want_json=not url.endswith("/"))
        ok = code == 200 and data not in (None, "", [], {})
        direct[name] = {"url": url, "http": code, "ok": ok, "error": err}
        log("direct %-36s %s %s" % (name, code, "" if ok else (err or "")))
    aggs = {}
    for name, url in AGG_PROBES:
        code, data, err = http_get(url, timeout=30, retries=2,
                                   want_json="oddsshark" not in url)
        aggs[name] = {"url": url, "http": code, "ok": code == 200 and bool(data),
                      "error": err, "checked_at": checked}
        log("agg    %-36s %s" % (name, code))
    # ESPN odds provider list
    espn_providers = set()
    code, d, _ = http_get(AGG_PROBES[1][1])
    for ev in (d or {}).get("events", []):
        for c in ev.get("competitions", []):
            for o in c.get("odds") or []:
                espn_providers.add((o.get("provider") or {}).get("name"))
    aggs["ESPN (DraftKings odds)"]["providers"] = sorted(x for x in espn_providers if x)
    # Action Network coverage per book (game lines + one props query) across leagues
    probe_ids = sorted(set(AN_BOOKS) | set(AN_EXTRA_PROBE))
    an_cov = {}
    an_code = None
    for lg in ("nfl", "ncaaf", "mlb", "nhl", "nba", "epl", "ufc"):
        slug = AN_SLUG[lg]
        u = "%s/web/v2/scoreboard/%s?bookIds=%s&periods=event" % (
            AN, slug, ",".join(map(str, probe_ids)))
        code, d, err = http_get(u)
        an_code = an_code or code
        for g in (d or {}).get("games", []) + (d or {}).get("competitions", []):
            for b in (g.get("markets") or {}):
                an_cov.setdefault(int(b), set()).add("lines:" + lg)
    for lg, typ in (("nfl", "core_bet_type_16_receiving_yards"),
                    ("mlb", "core_bet_type_36_hits"), ("nhl", "core_bet_type_31_shots_on_goal"),
                    ("nba", "core_bet_type_27_points")):
        u = "%s/web/v2/scoreboard/%s/markets?bookIds=%s&customPickTypes=%s" % (
            AN, lg, ",".join(map(str, probe_ids)), typ)
        code, d, err = http_get(u, timeout=60)
        for b, m in ((d or {}).get("markets") or {}).items():
            if any((m.get("event") or {}).values()):
                an_cov.setdefault(int(b), set()).add("props:" + lg)
    books = []
    for name, kind, an_id in STATUS_BOOKS:
        dr = direct.get(name)
        cov = sorted(an_cov.get(an_id, [])) if an_id else []
        via = []
        if cov:
            via.append("Action Network (book_id %d: %s)" % (an_id, ", ".join(cov)))
        if name == "DraftKings" and "DraftKings" in espn_providers:
            via.append("ESPN scoreboard odds")
        direct_ok = bool(dr and dr["ok"])
        how = []
        if direct_ok:
            how.append("direct API")
        how += via
        books.append({
            "name": name, "kind": kind, "visible": bool(direct_ok or via),
            "how": "; ".join(how) if how else ("blocked/not available" if dr else
                                              "no public source found"),
            "direct": ({"http": dr["http"], "ok": dr["ok"], "url": dr["url"]} if dr else
                       "not probed"),
            "aggregators": via, "http": dr["http"] if dr else an_code,
            "checked_at": checked,
        })
    only_agg = [b["name"] for b in books
                if b["visible"] and (not isinstance(b["direct"], dict) or not b["direct"]["ok"])
                and "offshore" not in b["name"]]
    blocked = [b["name"] for b in books if isinstance(b["direct"], dict) and not b["direct"]["ok"]]
    out = {"checked_at": checked, "books": books, "aggregators": aggs,
           "visible_only_via_aggregator": only_agg,
           "direct_blocked_or_failed": blocked,
           "prizepicks_props_via_aggregator": any(c.startswith("props")
                                                  for c in an_cov.get(2194, [])),
           "notes": "Direct probes use a browser User-Agent only; no proxies/VPN/geo spoofing. "
                    "AN = Action Network public web API (api.actionnetwork.com)."}
    write_json(OUT_STATUS, out)
    log("wrote %s" % OUT_STATUS)
    for b in books:
        log("  %-36s %-20s visible=%-5s %s" % (b["name"], b["kind"], b["visible"], b["how"][:90]))


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["status", "lines", "props"])
    ap.add_argument("--league", nargs="*", action="extend", default=[],
                    help="one or more of: " + " ".join(LEAGUES))
    a = ap.parse_args()
    leagues = []
    for x in a.league:
        leagues += [y.strip().lower() for y in x.split(",") if y.strip()]
    bad = [x for x in leagues if x not in LEAGUES]
    if bad:
        ap.error("unknown league(s): %s" % bad)
    leagues = leagues or LEAGUES
    if a.cmd == "status":
        cmd_status()
    elif a.cmd == "lines":
        cmd_lines(leagues)
    else:
        cmd_props(leagues)


if __name__ == "__main__":
    main()
