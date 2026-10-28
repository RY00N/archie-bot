"""Tennis background test (Ryan 2026-10-28: "lets do it, try to get the highest % win"). No posts.
Each run: Pinnacle's tennis moneylines (de-vigged = fair win chance) vs Kalshi's ATP/WTA/Challenger match markets.
Every Kalshi player whose fair chance is 60%+ gets logged once per match with the YES ask at first sighting;
later runs grade settled markets from Kalshi. `report` prints hit rate and ROI by win-chance band.
  python3 tennis_shadow.py [run|report]"""
import json, os, sys, unicodedata, urllib.parse, urllib.request
from datetime import datetime, timezone

PIN = "https://guest.api.arcadia.pinnacle.com/0.1"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ("KXATPMATCH", "KXWTAMATCH", "KXCHALLENGERMATCH")
LOG = "/mnt/project-files/betterpicks/tennis_shadow.json"
MIN_P = 0.60


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (BetterPicks)"})
    return json.loads(urllib.request.urlopen(req, timeout=25).read())


def key(name):
    """last name, ascii lower (Kalshi and Pinnacle spell full names differently, last names agree)."""
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower().replace("-", " ")
    return n.split()[-1] if n.split() else ""


def am_prob(a):
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def pinnacle():
    """{(last, last): {last: fair p}} for every pre-match tennis singles moneyline."""
    out = {}
    for lg in get(f"{PIN}/sports/33/leagues?all=false"):
        if "doubles" in lg["name"].lower():
            continue
        try:
            mus, mks = get(f"{PIN}/leagues/{lg['id']}/matchups"), get(f"{PIN}/leagues/{lg['id']}/markets/straight")
        except Exception:
            continue
        names = {}
        for mu in mus:
            if mu.get("type") == "matchup" and not mu.get("parentId") and not mu.get("isLive"):
                names[mu["id"]] = {p["alignment"]: p["name"] for p in mu.get("participants", [])}
        for mk in mks:
            n = names.get(mk.get("matchupId"))
            if not n or mk.get("type") != "moneyline" or mk.get("period") != 0 or mk.get("status") != "open":
                continue
            pr = {p["designation"]: p["price"] for p in mk.get("prices", [])}
            if "home" in pr and "away" in pr and "home" in n and "away" in n:
                h, a = am_prob(pr["home"]), am_prob(pr["away"])
                kh, ka = key(n["home"]), key(n["away"])
                out[frozenset((kh, ka))] = {kh: h / (h + a), ka: a / (h + a)}
    return out


def kalshi(status="open"):
    out = []
    for s in SERIES:
        cursor = None
        for _ in range(5):
            u = f"{KALSHI}/markets?series_ticker={s}&status={status}&limit=1000" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
            d = get(u)
            out += d.get("markets") or []
            cursor = d.get("cursor")
            if not cursor:
                break
    return out


def load():
    try:
        return json.load(open(LOG))
    except Exception:
        return {"legs": {}}


def run():
    log, now = load(), datetime.now(timezone.utc).isoformat(timespec="seconds")
    pin = pinnacle()
    mk = kalshi()
    by_event = {}
    for m in mk:
        by_event.setdefault(m["event_ticker"], []).append(m)
    new = 0
    for ev, ms in by_event.items():
        if len(ms) != 2:
            continue
        ks = [key(m.get("yes_sub_title") or m["title"].replace(" wins", "")) for m in ms]
        fair = pin.get(frozenset(ks))
        if not fair:
            continue
        for m, k in zip(ms, ks):
            p, ask = fair.get(k), float(m.get("yes_ask_dollars") or 0)
            if p is None or p < MIN_P or not 0 < ask < 1 or m["ticker"] in log["legs"]:
                continue
            log["legs"][m["ticker"]] = {"seen": now, "player": m.get("yes_sub_title"), "series": m["ticker"].split("-")[0],
                                        "p": round(p, 3), "ask": ask, "edge": round(p - ask, 3), "result": None}
            new += 1
    # grade: settled markets from Kalshi
    open_t = [t for t, l in log["legs"].items() if l["result"] is None]
    for t in open_t:
        try:
            m = get(f"{KALSHI}/markets/{t}")["market"]
        except Exception:
            continue
        if m.get("result") in ("yes", "no"):
            log["legs"][t]["result"] = "won" if m["result"] == "yes" else "lost"
    json.dump(log, open(LOG, "w"), indent=1)
    print(f"tennis shadow: {len(pin)} Pinnacle matches, {len(by_event)} Kalshi matches, {new} new legs, {len(open_t)} were open")


def report():
    legs = [l for l in load()["legs"].values() if l["result"]]
    for lo, hi in ((0.6, 0.7), (0.7, 0.8), (0.8, 1.01)):
        for need_edge in (False, True):
            b = [l for l in legs if lo <= l["p"] < hi and (not need_edge or l["edge"] >= 0.03)]
            if b:
                w = sum(l["result"] == "won" for l in b)
                roi = sum((1 / l["ask"] - 1) if l["result"] == "won" else -1 for l in b) / len(b)
                print(f"p {lo:.0%}-{min(hi, 1):.0%}{' edge 3%+' if need_edge else ''}: {w}-{len(b) - w} ({w / len(b):.0%}), ROI {roi:+.0%}")


if __name__ == "__main__":
    report() if sys.argv[1:] == ["report"] else run()
