"""Archie's PAPER futures book: Micro Nasdaq (MNQ) and Micro S&P (MES), simulated on real CME prices.

No broker yet (Ryan 2026-10-08: "paper trading for now"), so fills are simulated from free 1-minute bars
(Yahoo, continuous front month): entry at the signal bar's close plus 1 tick, stop and target checked on
each new bar's high/low, $1.50 round-trip fees per contract. Runs on the server from daytrader.tick.

Setup v1 (the best of the first study, still unproven): 15-min opening range breakout, long or short,
stop at the other side of the range, target 1.5x risk, entries 9:45-11:30 ET, flat by 15:55 ET.
Settings live in rules.json "futures".
"""
import json, os, time, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "futures-state.json")
LOG = os.path.join(HERE, "futures-log.jsonl")
ET = ZoneInfo("America/New_York")
SPEC = {"MNQ": {"y": "MNQ=F", "pt": 2.0, "tick": 0.25}, "MES": {"y": "MES=F", "pt": 5.0, "tick": 0.25}}
DEFAULTS = {"enabled": True, "budget_start": 10000, "contracts": 1, "or_minutes": 15, "target_r": 1.5,
            "last_entry_min": 690, "flatten_min": 955, "fees": 1.5}
_last = 0


def cfg():
    try:
        return {**DEFAULTS, **json.load(open(os.path.join(HERE, "rules.json"))).get("futures", {})}
    except Exception:
        return dict(DEFAULTS)


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"date": "", "realized_cum": 0.0, "history": [], "open": {}, "done": []}


def bars(sym):
    """Today's 1-minute bars as [(utc_iso, et_minute, o, h, l, c)]."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{SPEC[sym]['y']}?interval=1m&range=1d"
    d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read())
    r = d["chart"]["result"][0]; q = r["indicators"]["quote"][0]; out = []
    for i, t in enumerate(r["timestamp"]):
        if q["close"][i] is None:
            continue
        e = datetime.fromtimestamp(t, ET)
        out.append((datetime.fromtimestamp(t, timezone.utc).isoformat()[:16] + "Z", e.hour * 60 + e.minute,
                    q["open"][i], q["high"][i], q["low"][i], q["close"][i]))
    return out


def _minus5(t):
    return (datetime.fromisoformat(t[:-1]) - timedelta(minutes=5)).isoformat()[:16] + "Z"


def _plus5(t):
    return (datetime.fromisoformat(t[:-1]) + timedelta(minutes=5)).isoformat()[:16] + "Z"


def tick(log):
    """Call often while the stock market is open; works once a minute."""
    global _last
    if time.time() - _last < 60:
        return
    _last = time.time()
    c = cfg()
    st = load_state()
    now = datetime.now(ET); today, m = now.date().isoformat(), now.hour * 60 + now.minute
    if st["date"] != today:
        st.update({"date": today, "done": [], "realized_today": 0.0, "or": {}})
    for sym, sp in SPEC.items():
        try:
            b = bars(sym)
        except Exception as e:
            log(f"FUT {sym} data error: {e}")
            st["last_error"] = f"{datetime.now(timezone.utc).isoformat()[:16]} {sym}: {e}"[:200]
            continue
        st["last_data"] = datetime.now(timezone.utc).isoformat()[:16] + "Z"
        rth = [x for x in b if 570 <= x[1] < 960 and datetime.fromisoformat(x[0][:-1]).replace(tzinfo=timezone.utc).astimezone(ET).date().isoformat() == today]
        if not rth:
            continue
        tr = st["open"].get(sym)
        if tr:
            start = tr["entry_time"]
            pts = [[x[0], round(x[5], 2)] for x in b if x[0] >= _minus5(start)]
            step = max(1, -(-len(pts) // 120))
            tr["chart"] = pts[::step] + ([pts[-1]] if pts and (len(pts) - 1) % step else [])  # manage the open trade on bars after the last one seen
            for t, mm, o, h, l, cl in [x for x in rth if x[0] > tr["seen"]]:
                tr["seen"], tr["price"] = t, cl
                tr["best"] = max(tr.get("best", tr["entry"]), h) if tr["side"] == 1 else min(tr.get("best", tr["entry"]), l)
                out = how = None
                if (tr["side"] == 1 and l <= tr["stop"]) or (tr["side"] == -1 and h >= tr["stop"]):
                    out, how = tr["stop"] - tr["side"] * sp["tick"], "stop"
                elif (tr["side"] == 1 and h >= tr["target"]) or (tr["side"] == -1 and l <= tr["target"]):
                    out, how = tr["target"], "target"
                elif mm >= c["flatten_min"]:
                    out, how = cl, "end of day"
                if out is not None:
                    pnl = round(tr["side"] * (out - tr["entry"]) * sp["pt"] * tr["qty"] - c["fees"] * tr["qty"], 2)
                    rec = {"date": today, "symbol": sym, "side": "long" if tr["side"] == 1 else "short", "qty": tr["qty"],
                           "entry": tr["entry"], "exit": round(out, 2), "pnl": pnl, "exit_reason": how, "stop": tr["stop0"],
                           "target": tr["target"], "entry_time": tr["entry_time"], "exit_time": t,
                           "points": round(tr["side"] * (out - tr["entry"]), 2),
                           "chart": [p for p in tr.get("chart", []) if p[0] <= _plus5(t)]}
                    st["history"] = (st.get("history", []) + [rec])[-200:]
                    st["realized_cum"] = round(st.get("realized_cum", 0.0) + pnl, 2)
                    st["realized_today"] = round(st.get("realized_today", 0.0) + pnl, 2)
                    st["open"].pop(sym)
                    open(LOG, "a").write(json.dumps(rec) + "\n")
                    log(f"FUT EXIT {rec['side']} {sym} @ {out} ({how}) {pnl:+.2f}")
                    break
            continue
        if not c["enabled"] or sym in st["done"]:
            continue
        orb = [x for x in rth if x[1] < 570 + c["or_minutes"]]
        if len(orb) < c["or_minutes"] * 0.6 or m < 570 + c["or_minutes"]:
            continue
        hi, lo = max(x[3] for x in orb), min(x[4] for x in orb)
        st["or"][sym] = [hi, lo]
        for t, mm, o, h, l, cl in [x for x in rth if x[1] >= 570 + c["or_minutes"]]:
            if mm > c["last_entry_min"]:
                st["done"].append(sym)
                break
            side = 1 if cl > hi else -1 if cl < lo else 0
            if not side:
                continue
            st["done"].append(sym)
            if mm < m - 20:
                break  # breakout happened long before the bot was watching (free data runs ~10 min late): no chase
            e = cl + side * sp["tick"]; stop = lo if side == 1 else hi; r = abs(e - stop)
            st["open"][sym] = {"side": side, "qty": c["contracts"], "entry": round(e, 2), "stop": stop, "stop0": stop,
                               "target": round(e + side * c["target_r"] * r, 2), "entry_time": t, "seen": t, "price": cl}
            log(f"FUT ENTRY {'long' if side == 1 else 'short'} {c['contracts']} {sym} @ {e} stop {stop} target {st['open'][sym]['target']}")
            break
    json.dump(st, open(STATE, "w"), indent=1)


def summary():
    """Futures book for the P&L snapshot (and the city)."""
    c, st = cfg(), load_state()
    unreal = sum(tr["side"] * (tr["price"] - tr["entry"]) * SPEC[s]["pt"] * tr["qty"] for s, tr in st.get("open", {}).items())
    eq = c["budget_start"] + st.get("realized_cum", 0.0) + unreal
    today = datetime.now(ET).date().isoformat()
    day = (st.get("realized_today", 0.0) if st.get("date") == today else 0.0) + unreal
    return {"budget_start": c["budget_start"], "equity": round(eq, 2), "day_pnl": round(day, 2),
            "total_pnl": round(eq - c["budget_start"], 2), "total_pnl_pct": round((eq / c["budget_start"] - 1) * 100, 2),
            "note": "Simulated paper futures on real CME prices (no broker yet). 1 contract each of MNQ ($2/pt) and MES ($5/pt).",
            "positions": [{"symbol": s, "side": "long" if tr["side"] == 1 else "short", "qty": tr["qty"], "entry": tr["entry"],
                           "price": tr["price"], "stop": tr["stop"], "target": tr["target"], "entry_time": tr["entry_time"],
                           "pnl": round(tr["side"] * (tr["price"] - tr["entry"]) * SPEC[s]["pt"] * tr["qty"], 2),
                           "chart": tr.get("chart", [])}
                          for s, tr in st.get("open", {}).items()],
            "trades": st.get("history", []), "status": "trading" if st.get("open") else "watching",
            "last_data": st.get("last_data"), "last_error": st.get("last_error"), "opening_range": st.get("or", {})}
