"""Real-life context for BetterPicks legs (Ryan 2026-10-13 20:00Z): injuries, illness, a rough personal stretch
(divorce, family matter, bereavement), suspensions, rest days. Sources: ESPN injury reports (per league) and
Google News headlines for the player from the last 3 days. Results are cached for 30 minutes in news_cache.json.

adjust(legs) drops a leg whose player is listed Out/Doubtful/IL/suspended, and shaves the fair win chance of an
"over" when the player is questionable/day-to-day or the headlines carry bad news. Every checked leg gets a
"news" note that shows in Discord. `python3 news.py "<player>" <league>` prints the check for one player.
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
        if l["side"] == "over" and verdict in ("shaky", "bad_news"):
            hit = OVER_HIT_SHAKY if verdict == "shaky" else OVER_HIT_BAD
            l["p"] = round(l["p"] - hit, 3)
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
