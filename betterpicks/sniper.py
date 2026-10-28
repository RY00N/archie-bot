"""Line sniper, background test (Ryan 2026-10-28: "make a sniper for when the line goes low knowing it'll go back high").
No posts. Every fast_loop cycle (~4 min) it snapshots each Kalshi player-prop YES ask next to the sportsbooks' fair price
(de-vigged median at the same line). A snipe = the ask fell 8c+ below its high of the last 30 min while the books' fair
price barely moved (<2c) and the ask now sits 3c+ under fair: a dip with no news behind it. Each snipe is logged; later
runs record whether the ask bounced back within 2h and how the market settled. `report` prints both rates.
  python3 sniper.py [run|report]"""
import json, os, statistics, sys, time, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PROPS = os.path.join(HERE, "props.json")
STATE = "/mnt/project-files/betterpicks/sniper_state.json"
LOG = "/mnt/project-files/betterpicks/sniper_log.json"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
DIP, CALM, UNDER, LOOK, BOUNCE = 0.08, 0.02, 0.03, 1800, 7200
SKIP = {"Kalshi", "Polymarket", "AN Open"}


def am_prob(a):
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def fair_over(sources, line):
    ps = []
    for n, s in sources.items():
        if n in SKIP or not isinstance(s, dict) or s.get("line") != line or s.get("over") is None or s.get("under") is None:
            continue
        o, u = am_prob(s["over"]), am_prob(s["under"])
        ps.append(o / (o + u))
    return statistics.median(ps) if len(ps) >= 3 else None


def load(p, d):
    try:
        return json.load(open(p))
    except Exception:
        return d


def run():
    now = time.time()
    state, log = load(STATE, {}), load(LOG, {"snipes": []})
    props = load(PROPS, {}).get("props", [])
    seen = 0
    for pr in props:
        k = (pr.get("sources") or {}).get("Kalshi") or {}
        for rung in k.get("ladder", []):
            fair = fair_over(pr["sources"], rung.get("line"))
            ask = rung.get("yes_ask")
            if fair is None or not ask or not 0 < ask < 1:
                continue
            t = rung["ticker"]
            hist = [h for h in state.get(t, []) if now - h[0] <= BOUNCE]
            recent = [h for h in hist if now - h[0] <= LOOK]
            if recent:
                hi = max(recent, key=lambda h: h[1])
                if (hi[1] - ask >= DIP and abs(fair - hi[2]) < CALM and fair - ask >= UNDER
                        and not any(s["ticker"] == t and now - s["t"] < LOOK for s in log["snipes"])):
                    log["snipes"].append({"t": now, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                          "ticker": t, "player": pr["player"], "stat": pr["stat"], "league": pr["league"],
                                          "line": rung["line"], "start": pr.get("start"), "ask_before": hi[1], "ask": ask,
                                          "fair": round(fair, 3), "bounced": None, "result": None})
            hist.append([now, ask, round(fair, 3)])
            state[t] = hist
            seen += 1
    # follow up: bounce within 2h (from our own snapshots), then settlement from Kalshi
    for s in log["snipes"]:
        if s["bounced"] is None:
            later = [h for h in state.get(s["ticker"], []) if s["t"] < h[0] <= s["t"] + BOUNCE]
            if any(h[1] >= s["ask_before"] - 0.03 for h in later):
                s["bounced"] = True
            elif now - s["t"] > BOUNCE:
                s["bounced"] = False
        if s["result"] is None and s.get("start") and datetime.fromisoformat(s["start"].replace("Z", "+00:00")).timestamp() < now - 3 * 3600:
            try:
                m = json.loads(urllib.request.urlopen(f"{KALSHI}/markets/{s['ticker']}", timeout=15).read())["market"]
                if m.get("result") in ("yes", "no"):
                    s["result"] = "won" if m["result"] == "yes" else "lost"
            except Exception:
                pass
    state = {t: h for t, h in state.items() if h and now - h[-1][0] <= BOUNCE}
    json.dump(state, open(STATE, "w"), separators=(",", ":"))
    json.dump(log, open(LOG, "w"), indent=1)
    print(f"sniper: {seen} Kalshi rungs watched, {len(log['snipes'])} snipes logged")


def report():
    s = load(LOG, {"snipes": []})["snipes"]
    b = [x for x in s if x["bounced"] is not None]
    r = [x for x in s if x["result"]]
    print(f"snipes {len(s)}; bounced back {sum(x['bounced'] for x in b)}/{len(b)}; "
          f"hit {sum(x['result'] == 'won' for x in r)}/{len(r)}, "
          f"ROI at dip price {sum((1 / x['ask'] - 1) if x['result'] == 'won' else -1 for x in r) / max(len(r), 1):+.0%}")


if __name__ == "__main__":
    report() if sys.argv[1:] == ["report"] else run()
