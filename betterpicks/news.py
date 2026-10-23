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
import json, os, re, sys, time, urllib.parse, urllib.request

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
    return _cached(c, f"inj:{league}", fetch) or {}


def norm(t):
    t = (t or "").upper()
    return ALIAS.get(t, t)


def injury_list(league, c):
    """[{name, team, pos, status, date}] for everyone on ESPN's league injury report."""
    path = ESPN.get(league)
    if not path:
        return []
    def fetch():
        d = _get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/injuries")
        out = []
        for team in d.get("injuries", []):
            for i in team.get("injuries", []):
                a = i.get("athlete") or {}
                if a.get("displayName"):
                    out.append({"name": a["displayName"], "team": norm((a.get("team") or {}).get("abbreviation")),
                                "pos": (a.get("position") or {}).get("abbreviation", ""),
                                "status": i.get("status", ""), "date": (i.get("date") or "")[:10]})
        return out
    return _cached(c, f"injl:{league}", fetch) or []


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
