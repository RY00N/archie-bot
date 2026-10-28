"""The NBA's official injury report (Ryan 2026-10-28: most accurate data for hit rate). The league posts a PDF at
ak-static.cms.nba.com several times a day, usually ahead of ESPN. latest() finds the newest one for today (ET) and
returns {"at": "...", "players": {"first last": {"team", "status", "reason"}}}. pdftotext if installed, else pypdf."""
import io, re, subprocess, urllib.request
from datetime import datetime, timedelta, timezone

URL = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{}.pdf"
TEAMS = ["Atlanta Hawks", "Boston Celtics", "Brooklyn Nets", "Charlotte Hornets", "Chicago Bulls", "Cleveland Cavaliers",
         "Dallas Mavericks", "Denver Nuggets", "Detroit Pistons", "Golden State Warriors", "Houston Rockets",
         "Indiana Pacers", "LA Clippers", "Los Angeles Lakers", "Memphis Grizzlies", "Miami Heat", "Milwaukee Bucks",
         "Minnesota Timberwolves", "New Orleans Pelicans", "New York Knicks", "Oklahoma City Thunder", "Orlando Magic",
         "Philadelphia 76ers", "Phoenix Suns", "Portland Trail Blazers", "Sacramento Kings", "San Antonio Spurs",
         "Toronto Raptors", "Utah Jazz", "Washington Wizards"]
ROW = re.compile(r"([A-Z][A-Za-z'.\-]+(?: [A-Za-z'.\-]+)*, [A-Z][A-Za-z'.\-]+(?: [A-Za-z'.\-]+)*?)\s+"
                 r"(Out|Doubtful|Questionable|Probable|Available)\b\s*(.*)")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "BetterPicks"})
    return urllib.request.urlopen(req, timeout=20).read()


def text(pdf):
    try:
        return subprocess.run(["pdftotext", "-layout", "-", "-"], input=pdf, capture_output=True, timeout=30).stdout.decode()
    except Exception:
        from pypdf import PdfReader
        return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)


def parse(txt):
    out, team = {}, None
    for line in txt.splitlines():
        for t in TEAMS:
            if t in line:
                team, line = t, line.replace(t, " ")
        m = ROW.search(line)
        if m and team:
            last, first = m.group(1).split(", ", 1)
            name = f"{first.strip()} {last.strip()}"
            out[name.lower()] = {"name": name, "team": team, "status": m.group(2), "reason": m.group(3).strip()}
    return out


def latest(now=None, back_hours=8):
    """Newest report for today (ET), stepping back 15 minutes at a time; None if nothing found."""
    et = (now or datetime.now(timezone.utc)) - timedelta(hours=4)
    t = et.replace(minute=et.minute - et.minute % 15, second=0, microsecond=0)
    for _ in range(back_hours * 4):
        stamp = t.strftime("%Y-%m-%d_%I_%M%p")
        try:
            pdf = _get(URL.format(stamp))
        except Exception:
            t -= timedelta(minutes=15)
            continue
        return {"at": stamp, "players": parse(text(pdf))}
    return None


if __name__ == "__main__":
    r = latest()
    print(r and r["at"], len(r["players"]) if r else 0)
    for v in list((r or {}).get("players", {}).values())[:8]:
        print(v)
