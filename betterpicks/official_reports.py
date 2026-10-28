"""Official availability straight from each league (Ryan 2026-10-28: "switch to it" for the NBA report, then "do this for
all sports possible"). Each function -> {player name lower: {name, team, status, reason}}; news.py lays these over
ESPN's injury lists, so a player the league itself lists as Out/Doubtful/Questionable is treated that way.
  NBA: league injury report PDF (nba_injury_report.py)
  NFL: nfl.com/injuries (official practice report + game status)
  MLB: statsapi.mlb.com, 40-man roster status (IL) + posted starting lineups (a hitter left out = Out for the day)
  EPL: Premier League's Fantasy API (status + chance of playing, written by the league)
"""
import html, json, os, re, sys, urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
UA = {"User-Agent": "BetterPicks"}


def _get(url, as_json=True):
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read()
    return json.loads(raw) if as_json else raw.decode("utf-8", "replace")


def nba():
    import nba_injury_report
    return (nba_injury_report.latest() or {}).get("players", {})


def nfl():
    page, out, team = _get("https://www.nfl.com/injuries/", False), {}, ""
    for chunk in re.split(r'(?=<div class="d3-o-section-sub-title"><span>)', page):
        m = re.match(r'<div class="d3-o-section-sub-title"><span>([^<]+)</span>', chunk)
        if m:
            team = m.group(1).strip()
        for row in re.findall(r"<tr>(.*?)</tr>", chunk, re.S):
            cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(cells) >= 5 and cells[0] and cells[4] in ("Out", "Doubtful", "Questionable"):
                out[cells[0].lower()] = {"name": cells[0], "team": team, "status": cells[4],
                                         "reason": f"{cells[2] or 'injury'}; {cells[3]}".strip("; ")}
    return out


def mlb(now=None):
    day = ((now or datetime.now(timezone.utc)) - timedelta(hours=7)).strftime("%Y-%m-%d")
    sched = _get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={day}&hydrate=lineups,team")
    out = {}
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            for side in ("away", "home"):
                t = g["teams"][side]["team"]
                abbr = t.get("abbreviation", "")
                roster = _get(f"https://statsapi.mlb.com/api/v1/teams/{t['id']}/roster?rosterType=40Man").get("roster", [])
                lineup = {p["fullName"].lower() for p in (g.get("lineups") or {}).get(f"{side}Players", [])}
                for p in roster:
                    name = p["person"]["fullName"]
                    st = (p.get("status") or {}).get("description", "Active")
                    if st != "Active":
                        out[name.lower()] = {"name": name, "team": abbr, "status": "Out",
                                             "reason": "not on the active roster" if st == "Forty Man" else st}
                    elif lineup and p.get("position", {}).get("abbreviation") not in ("P", "TWP") and name.lower() not in lineup:
                        out[name.lower()] = {"name": name, "team": abbr, "status": "Out",
                                             "reason": "not in the posted starting lineup"}
    return out


def epl():
    d = _get("https://fantasy.premierleague.com/api/bootstrap-static/")
    teams = {t["id"]: t["short_name"] for t in d.get("teams", [])}
    out = {}
    for e in d.get("elements", []):
        st, ch = e.get("status"), e.get("chance_of_playing_next_round")
        if st == "a":
            continue
        status = "Out" if st in ("i", "s", "u", "n") or ch == 0 else ("Doubtful" if (ch or 0) <= 25 else "Questionable")
        name = f"{e['first_name']} {e['second_name']}"
        out[name.lower()] = {"name": name, "team": teams.get(e.get("team"), ""), "status": status,
                             "reason": e.get("news") or ""}
    return out


SOURCES = {"nba": nba, "nfl": nfl, "mlb": mlb, "epl": epl}


def all_leagues():
    res = {}
    for lg, fn in SOURCES.items():
        try:
            res[lg] = fn()
        except Exception as e:
            print(f"official {lg} failed: {e}")
    return res


if __name__ == "__main__":
    for lg, v in all_leagues().items():
        print(lg, len(v), list(v.values())[:2])
