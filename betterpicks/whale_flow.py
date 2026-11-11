#!/usr/bin/env python3
"""Big-money tracker (Ryan 2026-11-11: "what do the top traders have in common, and how will those picks do").
Kalshi hides who trades, so this watches every public player-prop trade and adds up the money on each side.
A side gets flagged when $500+ total (or one $250+ trade) lands on it today. Our model's win chance comes from
book.json shadow (same ticker/side). Read only, practice only: never places orders.

  python3 whale_flow.py          pull new trades since last run, update whale_flow.json, print flagged sides
  python3 whale_flow.py --show   print flagged sides only
"""
import json, os, sys, time
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kalshi_auth                      # noqa: E402
from books import KALSHI_PROP_SERIES    # noqa: E402

OUT = os.path.join(HERE, "whale_flow.json")
META = {}
FLAG_TOTAL = 500.0     # $ on one side of one prop market today
FLAG_SINGLE = 250.0    # one trade this big counts as a whale on its own
BIG = 100.0            # trades this size are kept in the per-side list
PREFIXES = tuple(s + "-" for lg in KALSHI_PROP_SERIES.values() for s in lg)
SERIES_STAT = {s: (lg, st) for lg, d in KALSHI_PROP_SERIES.items() for s, st in d.items()}


def load():
    try:
        return json.load(open(OUT))
    except Exception:
        return {"last_ts": None, "sides": {}}


def save(d):
    tmp = OUT + ".%d.tmp" % os.getpid()
    with open(tmp, "w") as f:
        json.dump(d, f)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, OUT)


def get(q):
    for tries in range(5):
        st, js = kalshi_auth.request("GET", q)
        if st != 429:
            return st, js
        time.sleep(2 * (tries + 1))
    return st, js


def markets():
    """{ticker: volume} for every open player-prop market (all leagues in KALSHI_PROP_SERIES)."""
    vol = {}
    for series in SERIES_STAT:
        cursor = None
        for _ in range(10):
            q = "/markets?series_ticker=%s&status=open&limit=1000" % series
            if cursor:
                q += "&cursor=" + cursor
            st, js = get(q)
            if st != 200:
                break
            for m in js.get("markets") or []:
                vol[m["ticker"]] = float(m.get("volume_fp") or 0)
                lg, st = SERIES_STAT[series]
                sub = m.get("yes_sub_title") or m.get("title") or ""
                META[m["ticker"]] = {"player": sub.split(":")[0].strip(), "stat": st, "league": lg,
                                     "line": (float(m["floor_strike"]) if m.get("floor_strike") is not None else 0.5),
                                     "yes_bid": m.get("yes_bid_dollars"), "no_bid": m.get("no_bid_dollars"),
                                     "oi": m.get("open_interest_fp"), "result": m.get("result")}
            cursor = js.get("cursor")
            if not cursor:
                break
    return vol


def pull(since_ts, old_vol):
    """Kalshi's all-markets trade feed runs ~12k trades a minute, so instead: list every prop market, and only
    fetch trades for markets whose traded volume grew since the last run."""
    vol = markets()
    moved = [t for t, v in vol.items() if v > old_vol.get(t, 0.0)]
    out = []
    for t in moved:
        cursor = None
        for _ in range(10):
            q = "/markets/trades?ticker=%s&limit=1000&min_ts=%d" % (t, since_ts)
            if cursor:
                q += "&cursor=" + cursor
            st, js = get(q)
            if st != 200:
                break
            out.extend(js.get("trades") or [])
            cursor = js.get("cursor")
            if not cursor:
                break
        time.sleep(0.05)
    return out, vol, len(moved)


def shadow_p():
    try:
        b = json.load(open(os.path.join(HERE, "book.json")))
    except Exception:
        return {}
    return {(x["ticker"], x["side"]): x for x in b.get("shadow", [])}


def update():
    d = load()
    now = time.time()
    since = d.get("last_ts") or now - 6 * 3600
    trades, vol, moved = pull(int(since) - 5, d.get("vol", {}))
    d["vol"] = vol
    d.setdefault("meta", {}).update(META)
    seen = set(d.get("seen_ids", []))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    for t in trades:
        if t["trade_id"] in seen:
            continue
        seen.add(t["trade_id"])
        side = t.get("taker_side")  # yes = over, no = under
        price = float(t["yes_price_dollars"] if side == "yes" else t["no_price_dollars"])
        n = float(t.get("count_fp") or t.get("count") or 0)
        usd = round(n * price, 2)
        key = t["ticker"] + "|" + side
        s = d["sides"].setdefault(key, {"ticker": t["ticker"], "side": side, "usd": 0.0, "trades": 0,
                                        "max_trade": 0.0, "big": [], "first": t["created_time"]})
        s["usd"] = round(s["usd"] + usd, 2)
        s["trades"] += 1
        s["max_trade"] = max(s["max_trade"], usd)
        s["last"] = t["created_time"]
        if usd >= BIG:
            s["big"].append([t["created_time"][:19], usd, price])
            s["big"] = s["big"][-20:]
    grade(d, vol)
    d["sides"] = {k: v for k, v in d["sides"].items() if v.get("last", v["first"]) >= cutoff or
                  (is_flag(v) and v.get("won") is None)}
    d["record"] = record(d)
    live = {v["ticker"] for v in d["sides"].values()}
    d["meta"] = {t: m for t, m in d["meta"].items() if t in live}
    d["seen_ids"] = list(seen)[-50000:]
    d["last_ts"] = now
    d["updated"] = datetime.now(timezone.utc).isoformat()
    save(d)
    print("%d prop markets, %d traded since last run, %d trades" % (len(vol), moved, len(trades)))
    return d


def is_flag(s):
    return s["usd"] >= FLAG_TOTAL or s["max_trade"] >= FLAG_SINGLE


def grade(d, vol):
    """Markets that left the open list: look up the result once and mark each flagged side won/lost."""
    done = {}
    for s in d["sides"].values():
        if s.get("won") is not None or s["ticker"] in vol or not is_flag(s):
            continue
        t = s["ticker"]
        if t not in done:
            st, js = get("/markets/" + t)
            done[t] = (js.get("market") or {}).get("result") if st == 200 else None
        if done[t] in ("yes", "no"):
            s["won"] = (done[t] == s["side"])
            hist = d.setdefault("graded", [])
            hist.append({"ticker": t, "side": s["side"], "usd": s["usd"], "max_trade": s["max_trade"],
                         "share": None, "won": s["won"], "at": datetime.now(timezone.utc).isoformat()[:19]})
            d["graded"] = hist[-5000:]


def record(d):
    g = d.get("graded", [])
    w = sum(1 for x in g if x["won"])
    return {"won": w, "lost": len(g) - w}


def flagged(d):
    sp = shadow_p()
    meta = d.get("meta", {})
    rows = []
    for k, s in d["sides"].items():
        if not is_flag(s) or s.get("won") is not None:
            continue
        other = d["sides"].get(s["ticker"] + "|" + ("no" if s["side"] == "yes" else "yes"), {})
        share = s["usd"] / (s["usd"] + other.get("usd", 0.0))
        mside = "over" if s["side"] == "yes" else "under"
        sh = sp.get((s["ticker"], mside), {})
        mt = meta.get(s["ticker"], {})
        line = mt.get("line")
        rows.append(dict(s, against=other.get("usd", 0.0), share=round(share, 2), our_p=sh.get("p"),
                         player=sh.get("player") or mt.get("player"), stat=sh.get("stat") or mt.get("stat"),
                         line=sh.get("line") or line, date=sh.get("date")))
    rows.sort(key=lambda r: -r["usd"])
    return rows


def show(d):
    r = d.get("record", {})
    print("big-money side record so far: %s-%s" % (r.get("won", 0), r.get("lost", 0)))
    for r in flagged(d)[:40]:
        name = r["player"] or r["ticker"]
        pick = "%s %s %s" % ("over" if r["side"] == "yes" else "under", r["line"] or "", r["stat"] or "")
        print("$%7.0f vs $%6.0f (%2.0f%% of money)  biggest $%5.0f  %-24s %-22s our win %s" % (
            r["usd"], r["against"], 100 * r["share"], r["max_trade"], name[:24], pick[:22],
            "%.0f%%" % (100 * r["our_p"]) if r["our_p"] else "n/a"))


if __name__ == "__main__":
    show(load() if "--show" in sys.argv else update())
