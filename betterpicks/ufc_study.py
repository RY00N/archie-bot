#!/usr/bin/env python3
"""Fight-by-fight study of every fighter on a UFC card (Ryan 2026-11-12: "study every single fight ... what happened,
what approach they took"). UFC fights come with full stats from data/mma.sqlite; the whole pro record (regional,
PFL, DWCS, Strikeforce, ...) comes from the fighter's Wikipedia "Mixed martial arts record" table.

  ufc_study.py <espn_event_id> [main]   -> ufc-study/<event>.md
"""
import json, os, re, sqlite3, sys, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "mma.sqlite")
UA = {"User-Agent": "BetterPicksResearch/1.0 (practice sports research)"}


def get(url):
    import time
    for i in range(5):
        try:
            if "wikipedia" in url:
                time.sleep(1.5)
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        except urllib.error.HTTPError as e:
            if e.code != 429 or i == 4:
                raise
            time.sleep(10 * (i + 1))


def mmss(s):
    s = int(s or 0)
    return f"{s // 60}:{s % 60:02d}"


def approach(r):
    """Plain-English read of how the fight was fought, from the stat line."""
    sig, att = r["sig_landed"] or 0, r["sig_att"] or 0
    dist, cl, gr = r["distance_landed"] or 0, r["clinch_landed"] or 0, r["ground_landed"] or 0
    tot = max(1, dist + cl + gr)
    tdl, tda, ctrl, octrl = r["td_landed"] or 0, r["td_att"] or 0, r["ctrl_sec"] or 0, r["opp_ctrl_sec"] or 0
    secs = max(1, r["fight_seconds"] or 1)
    bits = []
    if tda >= 3 or ctrl >= 0.35 * secs:
        bits.append(f"wrestled ({tdl}/{tda} TD, {mmss(ctrl)} control)")
    elif tdl:
        bits.append(f"mixed in takedowns ({tdl}/{tda})")
    if dist / tot >= 0.7 and att:
        bits.append("kept it at range")
    if cl / tot >= 0.3:
        bits.append("worked the clinch")
    if gr / tot >= 0.35:
        bits.append("did damage on the ground")
    if (r["leg_landed"] or 0) >= 0.3 * max(1, sig):
        bits.append("lots of leg kicks")
    if (r["body_landed"] or 0) >= 0.25 * max(1, sig):
        bits.append("body work")
    if r["kd"]:
        bits.append(f"{r['kd']} knockdown{'s' if r['kd'] > 1 else ''}")
    if r["sub_att"]:
        bits.append(f"{r['sub_att']} sub attempt{'s' if r['sub_att'] > 1 else ''}")
    if octrl >= 0.35 * secs or (r["opp_td_landed"] or 0) >= 2:
        bits.append(f"got taken down/controlled ({r['opp_td_landed'] or 0} TD against, {mmss(octrl)})")
    if r["opp_kd"]:
        bits.append(f"was knocked down {r['opp_kd']}x")
    pace = 60 * sig / secs
    return "; ".join(bits) or "striking exchange", pace


def ufc_fights(c, name):
    c.row_factory = sqlite3.Row
    return c.execute("select ff.*, f.method_detail, f.finish_details, f.time, e.name ev from fighter_fights ff "
                     "join fights f using(fight_id) left join events e on e.event_id=ff.event_id "
                     "where ff.fighter=? order by ff.date", (name,)).fetchall()


def wiki_record(name):
    """Every pro fight from the Wikipedia "Mixed martial arts record" table (newest first). Reads the plain article
    page (the Wikipedia API is rate-limited from here); tries "<name> (fighter)" first, then "<name>"."""
    import html as H, time
    page = None
    for t in (f"{name} (fighter)", f"{name} (mixed martial artist)", name):
        url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(t.replace(" ", "_"))
        for i in range(3):
            try:
                time.sleep(2)
                page = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode("utf-8", "replace")
                break
            except urllib.error.HTTPError as e:
                page = None
                if e.code != 429:
                    break
                time.sleep(20 * (i + 1))
            except Exception:
                page = None
                break
        if page and 'id="Mixed_martial_arts_record"' in page:
            title = t
            break
        page = None
    if not page:
        return None, "(no Wikipedia MMA record found)"
    seg = page[page.index('id="Mixed_martial_arts_record"'):]
    seg = seg[seg.find("<table"):]
    # the record table is the first table after the summary box that has a "Res." header
    tables = re.findall(r"<table.*?</table>", seg, flags=re.S)
    tbl = next((x for x in tables if re.search(r">\s*Res\.?\s*<", x)), None)
    if not tbl:
        return title, "(MMA record table not found)"
    rows = []
    for tr in re.findall(r"<tr.*?</tr>", tbl, flags=re.S):
        cells = [re.sub(r"\s+", " ", H.unescape(re.sub(r"<sup.*?</sup>|<[^>]+>", " ", c))).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S)]
        if len(cells) >= 5 and cells[0].lower() in ("win", "loss", "draw", "nc", "no contest"):
            rows.append(cells)
    return title, rows

def style(rows):
    """One-line career style read from all UFC fights."""
    if not rows:
        return ""
    secs = sum(r["fight_seconds"] or 0 for r in rows) or 1
    m = secs / 60
    sig = sum(r["sig_landed"] or 0 for r in rows); osig = sum(r["opp_sig_landed"] or 0 for r in rows)
    td = sum(r["td_landed"] or 0 for r in rows); ctrl = sum(r["ctrl_sec"] or 0 for r in rows)
    otd = sum(r["opp_td_landed"] or 0 for r in rows); otda = sum(r["opp_td_att"] or 0 for r in rows)
    w = [r for r in rows if r["won"] == 1]; l = [r for r in rows if r["won"] == 0]
    how = lambda xs, k: sum(1 for r in xs if (r["method"] or "").startswith(k))
    tags = []
    tags.append("wrestler/grappler" if 15 * td / m >= 2 or ctrl / secs >= 0.3 else
                ("volume striker" if sig / m >= 5 else "counter/low-volume striker"))
    if otda >= 5 and otd / otda >= 0.45:
        tags.append(f"takedownable ({otd}/{otda} against)")
    last5 = "".join("W" if r["won"] == 1 else ("L" if r["won"] == 0 else "-") for r in rows[-5:])
    return (f"Style: {', '.join(tags)}. Wins: {how(w, 'KO')} KO, {how(w, 'SUB')} sub, {how(w, 'DEC')} dec. "
            f"Losses: {how(l, 'KO')} KO, {how(l, 'SUB')} sub, {how(l, 'DEC')} dec. Last 5 UFC: {last5}.")


def fighter_block(c, n, wiki=True):
    out = []
    alias = {"Uroš Medić": "Uros Medic", "Alatengheili": "Alateng Heili"}.get(n, n)
    f = c.execute("select record,reach_in,height_in,stance,dob,ufc_record from fighters where fighter=?", (alias,)).fetchone()
    out.append(f"### {n}" + (f"  ({f[0]}; UFC {f[5]}; reach {f[1]}, height {f[2]}, {f[3]}, born {f[4]})" if f else ""))
    rows = ufc_fights(c, alias)
    c.row_factory = None
    if rows:
        out.append("UFC fights (oldest first), with what the stats say they did:")
        tot = {"sig": 0, "osig": 0, "secs": 0, "td": 0, "tda": 0, "otd": 0, "otda": 0}
        for r in rows:
            how, pace = approach(r)
            res = "W" if r["won"] == 1 else ("L" if r["won"] == 0 else r["result"])
            out.append(f"- {r['date']} {res} vs {r['opponent']} · {r['method']} {r['method_detail'] or ''} R{r['round']} {r['time'] or ''}"
                       f" · sig {r['sig_landed']}/{r['sig_att']} vs {r['opp_sig_landed']} absorbed ({pace:.1f}/min)"
                       f" · {how}" + (f" · odds {r['odds']:+d}" if r['odds'] else ""))
            tot["sig"] += r["sig_landed"] or 0; tot["osig"] += r["opp_sig_landed"] or 0
            tot["secs"] += r["fight_seconds"] or 0; tot["td"] += r["td_landed"] or 0; tot["tda"] += r["td_att"] or 0
            tot["otd"] += r["opp_td_landed"] or 0; tot["otda"] += r["opp_td_att"] or 0
        m = max(1, tot["secs"]) / 60
        out.append(f"- UFC totals: lands {tot['sig'] / m:.2f} sig/min, absorbs {tot['osig'] / m:.2f}; TD {tot['td']}/{tot['tda']}"
                   f" ({15 * tot['td'] / m:.2f} per 15 min); opponents' TD {tot['otd']}/{tot['otda']}"
                   f" (defense {100 - 100 * tot['otd'] / max(1, tot['otda']):.0f}%)")
        out.append(style(rows))
    title, rec = wiki_record(n) if wiki else (None, "")
    if isinstance(rec, list):
        out.append(f"Whole pro record from Wikipedia ({title}), newest first:")
        out += ["- " + " | ".join(x for x in (r[:8] + r[9:10]) if x) for r in rec]
    else:
        out.append(rec)
    out.append("")
    return out


def active(days=365, wiki=True):
    """Every fighter with a UFC fight in the last <days> days -> ufc-study/fighters/<name>.md (skips ones already done today)."""
    import datetime, time
    c = sqlite3.connect(DB)
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    names = [r[0] for r in c.execute("select distinct fighter from fighter_fights where date>=? and fighter is not null order by fighter", (since,))]
    d = os.path.join(HERE, "ufc-study", "fighters")
    os.makedirs(d, exist_ok=True)
    index = []
    for i, n in enumerate(names):
        path = os.path.join(d, re.sub(r"[^A-Za-z0-9]+", "-", n).strip("-") + ".md")
        if not (os.path.exists(path) and time.time() - os.path.getmtime(path) < 86400):
            open(path, "w").write("\n".join(fighter_block(c, n, wiki)))
        c.row_factory = None
        index.append(f"- [{n}](fighters/{os.path.basename(path)})")
        if i % 25 == 0:
            print(i, len(names), n, flush=True)
    open(os.path.join(HERE, "ufc-study", "ACTIVE-FIGHTERS.md"), "w").write(
        f"# Active UFC fighters ({len(names)}, fought since {since})\n\nEach file: every UFC fight with what happened and how they fought it (from the stats), career style, and the full pro record from Wikipedia.\n\n" + "\n".join(index))
    print("done", len(names))


def main():
    if sys.argv[1] == "active":
        return active(int(sys.argv[2]) if len(sys.argv) > 2 else 730)
    eid = sys.argv[1]
    main_only = "main" in sys.argv[2:]
    d = get(f"https://site.api.espn.com/apis/site/v2/sports/mma/ufc/summary?event={eid}") if False else None
    sb = get("https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard?dates=" + sys.argv[3]) if len(sys.argv) > 3 else None
    ev = next(e for e in sb["events"] if str(e["id"]) == eid)
    comps = ev["competitions"]
    if main_only:
        last = max(c["date"] for c in comps)
        comps = [c for c in comps if c["date"] == last]
    c = sqlite3.connect(DB)
    out = [f"# {ev['name']}: fight-by-fight study{' (main card)' if main_only else ''}", ""]
    for comp in reversed(comps):
        names = [x["athlete"]["displayName"] for x in comp["competitors"]]
        out += [f"## {names[0]} vs {names[1]}", ""]
        for n in names:
            out += fighter_block(c, n)
    os.makedirs(os.path.join(HERE, "ufc-study"), exist_ok=True)
    path = os.path.join(HERE, "ufc-study", re.sub(r"[^A-Za-z0-9]+", "-", ev["name"]).strip("-") + ".md")
    open(path, "w").write("\n".join(out))
    print(path)


if __name__ == "__main__":
    main()
