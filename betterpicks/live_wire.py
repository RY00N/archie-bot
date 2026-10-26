"""BetterPicks live wire (Ryan 2026-10-26: "We need everything to be live, not just for injuries").
Runs every fast_loop cycle (~4 min) and collects anything that can move a pick, as it happens:
  - injury report changes: new names, status changes (Out / Doubtful / Questionable / back to Active)
  - in-game exits: NFL play-by-play ("was injured during the play", injury updates) and ESPN headlines
  - trades, signings, waivers, IR moves (ESPN transactions)
  - ESPN player headlines: suspensions, rest, ejections, starting/bench news, returns
  - NBA starting lineups that break a player's usual role (once ESPN posts them)
  - Kalshi price moves of 8+ cents on our open picks
New items go to live_wire.json (newest last); when any are new, it touches .live_alert so fast_loop.sh exits and the
thread posts them. The first run only records what's already out there (no flood). NFL/MLB report and transaction
noise is limited to players we track (props feed or open picks); NBA shows every change.
`python3 live_wire.py --dry` prints what's new without saving; `--unposted` prints items not yet relayed to the thread
and marks them relayed.
"""
import json, os, re, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BP = "/mnt/project-files/betterpicks"
STATE = os.path.join(BP, "live_wire_state.json")
WIRE = os.path.join(BP, "live_wire.json")
FLAG = os.path.join(BP, ".live_alert")
BOOK = os.path.join(BP, "book.json")
FEED = os.path.join(HERE, "books_feed.json")
LEAGUES = {"nba": "basketball/nba", "nfl": "football/nfl", "mlb": "baseball/mlb"}
PRICE_MOVE = 0.08
RECENT = time.strftime("%Y-%m-%dT%H:%M", time.gmtime(time.time() - 12 * 3600))   # older news/moves never alert
NEWS_KW = re.compile(r"\b(injur\w*|exits?|leaves?|left|out|ruled|questionable|doubtful|return\w*|back|trade[ds]?|"
                     r"acquir\w*|sign\w*|waive[ds]?|releas\w*|suspen\w*|eject\w*|rest\w*|start\w*|bench\w*|"
                     r"lineup|scratch\w*|illness|sick|surgery|sprain\w*|strain\w*|concussion|carted|IR|activated|"
                     r"miss\w*|status|limited|minutes restriction)\b", re.I)
NEWS_SKIP = re.compile(r"(Game Highlights|fantasy|odds|picks?\b|prediction|preview|travels to|hosts|faces|visits|"
                       r"square off|matchup|power rankings|takeaways|grades)", re.I)
RECAP = re.compile(r"\b(beat|beats|win|wins|victory|rout\w*|loss|lose|loses|fall|falls|edge|top|tops|rally|rallies|"
                   r"scores?|step back|stay|stays|unbeaten|undefeated)\b", re.I)
STRONG = re.compile(r"\b(injur\w*|exits?|leaves|ruled (out|in)|out (for|with|due|at least|indefinitely|tonight)|"
                    r"questionable|doubtful|trade[ds]?|acquir\w*|signs?|signed|waive[ds]?|releas(ed|es)|suspen\w*|"
                    r"eject\w*|surgery|sprain\w*|strain\w*|concussion|carted|benching|benched|will start|to start|"
                    r"starting|rest(s|ed|ing)?|scratch\w*|won't play|will miss|to miss|misses|return(s|ing)? from|"
                    r"back from|cleared|activated|illness|minutes restriction|lineup|sits?|sitting|plantar|fracture\w*|torn|tear)\b", re.I)
INGAME = re.compile(r"(was injured during the play|Injury Update)", re.I)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def norm(t):
    return {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX"}.get(t, t)


def tracked():
    """lowercase names of players we have props for or hold in open picks"""
    names = set()
    try:
        for k in json.load(open(FEED)).get("props", {}):
            names.add(k.split("|")[1])
    except Exception:
        pass
    for leg in open_legs():
        names.add(leg["player"].lower())
    return names


def open_legs():
    try:
        book = json.load(open(BOOK))
    except Exception:
        return []
    return [l for e in book.get("parlays", []) if e.get("status") == "open" and not e.get("retracted")
            and not e.get("test") for l in e.get("legs", []) if l.get("player")]


def pt_date():
    # PT = UTC-7 (PDT) / UTC-8 (PST); a 1h slip around the switch is fine for a scoreboard date
    off = 7 if 3 <= time.gmtime().tm_mon <= 10 else 8
    return time.strftime("%Y%m%d", time.gmtime(time.time() - off * 3600))


def injury_report(league):
    d = get(f"https://site.api.espn.com/apis/site/v2/sports/{LEAGUES[league]}/injuries")
    out = {}
    for team in d.get("injuries", []):
        tm = norm(team.get("abbreviation") or "") or team.get("displayName", "")
        for i in team.get("injuries", []):
            a = i.get("athlete") or {}
            if a.get("displayName"):
                tm2 = tm or norm((a.get("team") or {}).get("abbreviation", ""))
                out[a["displayName"]] = {"team": tm2, "status": i.get("status", ""),
                                         "detail": (i.get("shortComment") or "")[:160]}
    return out


def check_report(league, st, new, mine):
    try:
        rep = injury_report(league)
    except Exception as ex:
        print("live wire: injury report", league, ex)
        return
    old = st.setdefault("report", {}).get(league)
    st["report"][league] = {k: v["status"] for k, v in rep.items()}
    if old is None:
        return
    for name, v in rep.items():
        if league != "nba" and name.lower() not in mine:
            continue
        was = old.get(name)
        if was == v["status"] or v["status"].lower() == "active" and was is None:
            continue
        # ESPN resets last night's Out players to Day-To-Day each morning while the note still says "out for
        # <yesterday>": not news unless it's on one of our open picks
        if (was or "").lower() == "out" and v["status"].lower() == "day-to-day" and name.lower() not in mine:
            continue
        what = f"{v['status']}" + (f" (was {was})" if was else " (new on report)")
        new.append({"league": league, "kind": "injury report", "player": name, "team": v["team"],
                    "text": f"{name} ({v['team']}): {what}" + (f". {v['detail']}" if v["detail"] else "")})
    for name, was in old.items():
        if name not in rep and (league == "nba" or name.lower() in mine) and was.lower() != "active":
            new.append({"league": league, "kind": "injury report", "player": name,
                        "text": f"{name}: off the injury report (was {was}), likely cleared to play"})


def check_games(league, st, new):
    """NFL play-by-play injuries; NBA starting lineups vs usual role"""
    try:
        sb = get(f"https://site.api.espn.com/apis/site/v2/sports/{LEAGUES[league]}/scoreboard?dates={pt_date()}")
    except Exception as ex:
        print("live wire: scoreboard", league, ex)
        return
    seen = st.setdefault("plays", {})
    for ev in sb.get("events", []):
        state = ((ev.get("status") or {}).get("type") or {}).get("state")
        if league == "nfl" and state == "in":
            try:
                s = get(f"https://site.api.espn.com/apis/site/v2/sports/{LEAGUES[league]}/summary?event={ev['id']}")
            except Exception:
                continue
            for d in (s.get("drives") or {}).get("previous", []) + [(s.get("drives") or {}).get("current") or {}]:
                for p in d.get("plays", []) or []:
                    t = p.get("text", "")
                    if INGAME.search(t) and p.get("id") and p["id"] not in seen:
                        seen[p["id"]] = 1
                        bit = next((x.strip() for x in re.split(r"(?<=\.)\s+|\*\*", t) if INGAME.search(x)), t)
                        new.append({"league": league, "kind": "in-game", "game": ev.get("shortName"),
                                    "text": f"{ev.get('shortName')}: {bit[:200]}"})
        if league == "nba" and state == "pre":
            check_lineups(ev, st, new)


def check_lineups(ev, st, new):
    done = st.setdefault("lineups", {})
    if ev["id"] in done:
        return
    try:
        s = get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={ev['id']}")
    except Exception:
        return
    starters = {}
    for t in (s.get("boxscore") or {}).get("players", []):
        tm = norm((t.get("team") or {}).get("abbreviation", ""))
        for g in t.get("statistics", []):
            for a in g.get("athletes", []):
                n = (a.get("athlete") or {}).get("displayName")
                if n and "starter" in a:
                    starters[n] = (tm, bool(a["starter"]))
    if not any(v[1] for v in starters.values()):
        return
    done[ev["id"]] = 1
    try:
        sys.path.insert(0, HERE)
        import pregame
    except Exception:
        return
    odd = []
    for n, (tm, starting) in starters.items():
        try:
            usual = pregame.usual_role("nba", "points", n)
        except Exception:
            usual = None
        if usual is not None and usual != starting:
            odd.append(f"{n} ({tm}) {'STARTING' if starting else 'coming off the bench'}")
    line = f"{ev.get('shortName')} lineups are out"
    new.append({"league": "nba", "kind": "lineups", "game": ev.get("shortName"),
                "text": line + (": " + "; ".join(odd) if odd else ", no surprise starters")})


def check_news(league, st, new):
    try:
        d = get(f"https://site.api.espn.com/apis/site/v2/sports/{LEAGUES[league]}/news?limit=40")
    except Exception as ex:
        print("live wire: news", league, ex)
        return
    seen = st.setdefault("news", {})
    first = league not in st.setdefault("news_init", {})
    st["news_init"][league] = 1
    for a in d.get("articles", []):
        h, key = a.get("headline") or "", str(a.get("id") or a.get("headline"))
        if key in seen:
            continue
        seen[key] = a.get("published", "")[:10]
        if a.get("published", "") < RECENT or first or NEWS_SKIP.search(h) or not STRONG.search(h) or (a.get("type") or "") == "Media":
            continue
        new.append({"league": league, "kind": "news", "text": h})


def check_moves(league, st, new, mine):
    try:
        d = get(f"https://site.api.espn.com/apis/site/v2/sports/{LEAGUES[league]}/transactions")
    except Exception as ex:
        print("live wire: transactions", league, ex)
        return
    seen = st.setdefault("moves", {})
    first = league not in st.setdefault("moves_init", {})
    st["moves_init"][league] = 1
    for t in d.get("transactions", []):
        desc = t.get("description", "")
        key = f"{t.get('date', '')[:10]}|{desc[:80]}"
        if key in seen:
            continue
        seen[key] = t.get("date", "")[:10]
        if first or t.get("date", "") < RECENT[:10]:
            continue
        low = desc.lower()
        if league != "nba" and "trade" not in low and not any(n in low for n in mine):
            continue
        tm = norm((t.get("team") or {}).get("abbreviation", ""))
        new.append({"league": league, "kind": "roster move", "text": f"{tm}: {desc}"})


def check_prices(st, new):
    last = st.setdefault("prices", {})
    for leg in open_legs():
        tk = leg.get("ticker")
        if not tk or leg.get("price") is None:
            continue
        try:
            m = get(f"https://api.elections.kalshi.com/trade-api/v2/markets/{tk}")["market"]
        except Exception:
            continue
        def px(side):
            v = m.get(f"{side}_ask_dollars") or m.get(f"{side}_ask")
            v = float(v) if v not in (None, "") else None
            return v / 100 if v and v > 1 else v
        side = "no" if leg.get("side") == "under" else "yes"   # an under is bought as NO on Kalshi
        now = px(side)
        if not now:
            continue
        ref = last.get(tk, leg["price"])
        if abs(now - ref) >= PRICE_MOVE:
            last[tk] = now
            way = "up" if now > ref else "down"
            new.append({"league": leg.get("league"), "kind": "price move", "player": leg["player"],
                        "text": f"Open pick {leg['player']} {leg.get('side')} {leg.get('line')} {leg.get('stat')}: "
                                f"Kalshi price {way} {ref:.2f} -> {now:.2f}"
                                + (" (market likes it more)" if way == "up" else " (market likes it less)")})


def main(dry=False):
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {}
    new, mine = [], tracked()
    for lg in LEAGUES:
        check_report(lg, st, new, mine)
        check_games(lg, st, new)
        check_news(lg, st, new)
        check_moves(lg, st, new, mine)
    check_prices(st, new)
    hold = {l["player"].lower() for l in open_legs()}
    for n in new:
        n["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if n.get("player", "").lower() in hold:
            n["open_pick"] = True
    # keep state small: 3 days of news/moves/plays keys
    cut = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 3 * 86400))
    for k in ("news", "moves"):
        st[k] = {a: b for a, b in st.get(k, {}).items() if not b or b >= cut}
    st["plays"] = dict(list(st.get("plays", {}).items())[-3000:])
    st["lineups"] = dict(list(st.get("lineups", {}).items())[-200:])
    for n in new:
        print(f"LIVE [{n['league']}] {n['kind']}: {n['text']}" + ("  <- OPEN PICK" if n.get("open_pick") else ""))
    if dry:
        return
    json.dump(st, open(STATE, "w"))
    if new:
        try:
            wire = json.load(open(WIRE))
        except Exception:
            wire = []
        wire = (wire + new)[-500:]
        json.dump(wire, open(WIRE, "w"), indent=1)
        open(FLAG, "a").close()


def unposted():
    try:
        wire = json.load(open(WIRE))
    except Exception:
        wire = []
    for n in wire:
        if not n.get("posted"):
            print(json.dumps(n))
            n["posted"] = True
    json.dump(wire, open(WIRE, "w"), indent=1)
    try:
        os.remove(FLAG)
    except OSError:
        pass


if __name__ == "__main__":
    unposted() if "--unposted" in sys.argv else main("--dry" in sys.argv)
