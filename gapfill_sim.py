"""Archie's MNQ gap-fill PRACTICE book (lab book #1; Ryan approved 2026-10-16, starts Monday 10/19).

Separate from the main ORB book: own state (gapfill-state.json), own $10k paper budget, own pnl.json key "gapfill".
Rules (from futures/lab/lab_gapfill.py, "full+conf"):
  gap = 9:30 open vs prior regular-session close (close of the 15:55 5-min bar); trade only if 0.3% <= |gap| <= 1.0%.
  The 9:30-9:35 bar must already move toward the fill. Enter at the 9:35 close (+/- 1 tick), fading the gap.
  Target = prior close. Stop = 1x gap beyond the 9:30 open. Out at 11:30 if neither is hit. 1 MNQ, $1.50 fees.
Simulated fills on free Yahoo 1-min bars, same as futures_sim. PAPER ONLY, never sends orders.
Settings live in rules.json "lab_books" -> "gapfill". The daily lab turns it off automatically if it fails live
(10+ trades and net negative, or drawdown over 1.5x the 2-yr backtest drawdown).
"""
import json, os, time
from datetime import datetime
import futures_sim as fs

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "gapfill-state.json")
LOG = os.path.join(HERE, "gapfill-log.jsonl")
DEFAULTS = {"enabled": True, "symbol": "MNQ", "budget_start": 10000, "contracts": 1, "min_gap_pct": 0.3,
            "max_gap_pct": 1.0, "entry_min": 575, "exit_min": 690, "fees": 1.5, "backtest_dd": 1463}
_last = 0


def cfg():
    try:
        return {**DEFAULTS, **json.load(open(os.path.join(HERE, "rules.json"))).get("lab_books", {}).get("gapfill", {})}
    except Exception:
        return dict(DEFAULTS)


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"date": "", "realized_cum": 0.0, "realized_today": 0.0, "history": [], "open": None, "plan": {}}


def prior_close(sym, today):
    """Close of the last regular-session 5-min bar (15:55) before today."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{fs.SPEC[sym]['y']}?interval=5m&range=5d"
    d = json.loads(fs.urllib.request.urlopen(fs.urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read())
    r = d["chart"]["result"][0]; q = r["indicators"]["quote"][0]; days = {}
    for i, t in enumerate(r["timestamp"]):
        e = datetime.fromtimestamp(t, fs.ET)
        if q["close"][i] is None or not 570 <= e.hour * 60 + e.minute < 960 or e.date().isoformat() >= today:
            continue
        days.setdefault(e.date().isoformat(), []).append((e.hour * 60 + e.minute, q["close"][i]))
    last = days[max(days)]
    return round(max(last)[1], 2)


def _close(st, c, sym, px, t, why, log):
    tr = st["open"]
    pnl = round(tr["side"] * (px - tr["entry"]) * fs.SPEC[sym]["pt"] * tr["qty"] - c["fees"] * tr["qty"], 2)
    rec = {**{k: v for k, v in tr.items() if k != "price"}, "exit": round(px, 2), "exit_time": t, "why": why, "pnl": pnl}
    st["history"] = (st["history"] + [rec])[-200:]
    st["realized_cum"] = round(st["realized_cum"] + pnl, 2)
    st["realized_today"] = round(st.get("realized_today", 0.0) + pnl, 2)
    st["open"] = None
    st["plan"]["status"] = f"done: {why} {pnl:+.2f}"
    open(LOG, "a").write(json.dumps(rec) + "\n")
    log(f"GAPFILL EXIT {sym} {why} @ {px} pnl {pnl:+.2f}")


def tick(log):
    """Call often while the stock market is open; works once a minute."""
    global _last
    if time.time() - _last < 60:
        return
    _last = time.time()
    c = cfg()
    now = datetime.now(fs.ET)
    today, m = now.date().isoformat(), now.hour * 60 + now.minute
    st = load_state()
    if st["date"] != today:
        st.update({"date": today, "realized_today": 0.0, "plan": {}})
    if (not c["enabled"] and not st["open"]) or now.weekday() >= 5 or not 570 <= m < 965:
        json.dump(st, open(STATE, "w"), indent=1)
        return
    sym, tick_sz = c["symbol"], fs.SPEC[c["symbol"]]["tick"]
    try:
        b = [x for x in fs.bars(sym) if 570 <= x[1] < 960]
        if "prior_close" not in st["plan"]:
            st["plan"]["prior_close"] = prior_close(sym, today)
        st["last_error"] = None
    except Exception as e:
        st["last_error"] = f"{e}"[:200]
        json.dump(st, open(STATE, "w"), indent=1)
        return
    if not b:
        json.dump(st, open(STATE, "w"), indent=1)
        return
    st["last_data"] = b[-1][0]
    p = st["plan"]; pc = p["prior_close"]
    # Entry decision, once, at the 9:35 close
    if "status" not in p and b[0][1] == 570 and any(x[1] == c["entry_min"] - 1 for x in b):
        o = b[0][2]; c0 = [x for x in b if x[1] == c["entry_min"] - 1][0][5]
        gap = o - pc; gp = gap / pc * 100; s = -1 if gap > 0 else 1
        p.update({"open_930": o, "close_935": c0, "gap_pct": round(gp, 2)})
        e = c0 + s * tick_sz; stop = o + gap; target = pc
        if not c["min_gap_pct"] <= abs(gp) <= c["max_gap_pct"]:
            p["status"] = f"skip: gap {gp:+.2f}% outside {c['min_gap_pct']}-{c['max_gap_pct']}%"
        elif not s * (c0 - o) > 0:
            p["status"] = "skip: first 5-min bar moved away from the fill"
        elif s * (target - e) <= 0 or s * (e - stop) <= 0:
            p["status"] = "skip: already filled or stopped by 9:35"
        elif not c["enabled"]:
            p["status"] = "off"
        else:
            t = [x for x in b if x[1] == c["entry_min"] - 1][0][0]
            st["open"] = {"symbol": sym, "side": s, "qty": c["contracts"], "entry": round(e, 2), "entry_time": t,
                          "stop": round(stop, 2), "target": round(target, 2), "price": round(c0, 2), "gap_pct": round(gp, 2)}
            p["status"] = "in trade"
            log(f"GAPFILL ENTRY {'long' if s == 1 else 'short'} {c['contracts']} {sym} @ {e} stop {stop:.2f} target {target} (gap {gp:+.2f}%)")
    elif "status" not in p and m >= c["entry_min"] + 5:
        p["status"] = "skip: missed the 9:35 bar (data late)"
    # Manage the open trade on each new 1-min bar after entry
    tr = st["open"]
    if tr:
        s = tr["side"]
        for t, mm, o, h, l, cl in [x for x in b if x[0] > tr["entry_time"] and x[0] > tr.get("seen", "")]:
            tr["seen"] = t; tr["price"] = round(cl, 2)
            if (s == 1 and l <= tr["stop"]) or (s == -1 and h >= tr["stop"]):
                _close(st, c, sym, tr["stop"] - s * tick_sz, t, "stop", log); break
            if (s == 1 and h >= tr["target"]) or (s == -1 and l <= tr["target"]):
                _close(st, c, sym, tr["target"], t, "target", log); break
            if mm >= c["exit_min"] - 1:
                _close(st, c, sym, cl - s * tick_sz, t, "11:30 time exit", log); break
    json.dump(st, open(STATE, "w"), indent=1)


def summary():
    """Gap-fill book for the P&L snapshot (and the city). Practice money, never in real totals."""
    c, st = cfg(), load_state()
    tr = st.get("open")
    unreal = tr["side"] * (tr["price"] - tr["entry"]) * fs.SPEC[tr["symbol"]]["pt"] * tr["qty"] if tr else 0.0
    eq = c["budget_start"] + st.get("realized_cum", 0.0) + unreal
    today = datetime.now(fs.ET).date().isoformat()
    day = (st.get("realized_today", 0.0) if st.get("date") == today else 0.0) + unreal
    eqc = pk = dd = 0.0
    for h in st.get("history", []):
        eqc += h["pnl"]; pk = max(pk, eqc); dd = max(dd, pk - eqc)
    return {"name": "MNQ gap fill", "enabled": c["enabled"], "budget_start": c["budget_start"], "equity": round(eq, 2),
            "day_pnl": round(day, 2), "total_pnl": round(eq - c["budget_start"], 2),
            "total_pnl_pct": round((eq / c["budget_start"] - 1) * 100, 2), "max_dd": round(dd, 2),
            "note": "Practice lab book: fades MNQ opening gaps of 0.3-1% back to yesterday's close. Enters 9:35, out by 11:30.",
            "positions": [{"symbol": tr["symbol"], "side": "long" if tr["side"] == 1 else "short", "qty": tr["qty"],
                           "entry": tr["entry"], "price": tr["price"], "stop": tr["stop"], "target": tr["target"],
                           "entry_time": tr["entry_time"], "pnl": round(unreal, 2)}] if tr else [],
            "trades": st.get("history", []), "status": "trading" if tr else ("off" if not c["enabled"] else "watching"),
            "today": st.get("plan", {}) if st.get("date") == today else {}, "last_data": st.get("last_data"),
            "last_error": st.get("last_error")}
