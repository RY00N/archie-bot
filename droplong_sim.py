"""Archie's MNQ drop-day long PRACTICE book (lab book #2, auto-added 2026-10-16 under Ryan's rule
"If something fails automatically replace it with another until it succeeds").

Trades only on the days the main ORB book SKIPS MNQ for a drop (gap <= -1% or prior day <= -2%), and only long:
15-min opening range (9:30-9:45); long on a 5-min close above the range high, stop 100 pts (rules.json), target 2R,
out 80 min after entry if neither is hit, entries to 11:30. 1 MNQ, $1.50 fees, 1 tick slip each way.
Backtest (futures/lab/lab_droplong.py, 100/2R): 10wk 4 trades +$524; 2yr 53 trades, 58% wins, +$2,878, dd $551.
Own state (droplong-state.json), own $10k, own pnl.json key "droplong". PAPER ONLY, never sends orders.
Settings in rules.json "lab_books" -> "droplong". The daily lab turns it off if it fails live.
"""
import json, os, time
from datetime import datetime
import futures_sim as fs

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "droplong-state.json")
LOG = os.path.join(HERE, "droplong-log.jsonl")
DEFAULTS = {"enabled": True, "symbol": "MNQ", "budget_start": 10000, "contracts": 1, "or_end_min": 585, "stop_pts": 100,
            "target_r": 2.0, "time_stop_min": 80, "last_entry_min": 690, "fees": 1.5, "gap": -1.0, "prev": -2.0,
            "backtest_dd": 551}
_last = 0


def cfg():
    try:
        return {**DEFAULTS, **json.load(open(os.path.join(HERE, "rules.json"))).get("lab_books", {}).get("droplong", {})}
    except Exception:
        return dict(DEFAULTS)


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"date": "", "realized_cum": 0.0, "realized_today": 0.0, "history": [], "open": None, "plan": {}}


def _close(st, c, sym, px, t, why, log):
    tr = st["open"]
    pnl = round((px - tr["entry"]) * fs.SPEC[sym]["pt"] * tr["qty"] - c["fees"] * tr["qty"], 2)
    rec = {**{k: v for k, v in tr.items() if k not in ("price", "seen")}, "exit": round(px, 2), "exit_time": t, "why": why, "pnl": pnl}
    st["history"] = (st["history"] + [rec])[-200:]
    st["realized_cum"] = round(st["realized_cum"] + pnl, 2)
    st["realized_today"] = round(st.get("realized_today", 0.0) + pnl, 2)
    st["open"] = None
    st["plan"]["status"] = f"done: {why} {pnl:+.2f}"
    open(LOG, "a").write(json.dumps(rec) + "\n")
    log(f"DROPLONG EXIT {sym} {why} @ {px} pnl {pnl:+.2f}")


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
    sym, tk = c["symbol"], fs.SPEC[c["symbol"]]["tick"]
    p = st["plan"]
    try:
        b = [x for x in fs.bars(sym) if 570 <= x[1] < 960]
        if "drop_day" not in p and b:
            g, pv = fs.drop_check(sym, today)
            p.update({"gap_pct": g, "prev_pct": pv, "drop_day": g <= c["gap"] or pv <= c["prev"]})
            if not p["drop_day"]:
                p["status"] = f"skip: not a drop day (gap {g:+.2f}%, prior day {pv:+.2f}%)"
        st["last_error"] = None
    except Exception as e:
        st["last_error"] = f"{e}"[:200]
        json.dump(st, open(STATE, "w"), indent=1)
        return
    if not b:
        json.dump(st, open(STATE, "w"), indent=1)
        return
    st["last_data"] = b[-1][0]
    orb = [x for x in b if x[1] < c["or_end_min"]]
    if p.get("drop_day") and "status" not in p and any(x[1] >= c["or_end_min"] - 1 for x in b):
        p["range_high"] = round(max(x[3] for x in orb), 2)
        p["status"] = "waiting for a 5-min close above the range high"
    if p.get("drop_day") and p.get("status", "").startswith("waiting") and not st["open"] and c["enabled"]:
        for t, mm, o, h, l, cl in [x for x in b if c["or_end_min"] + 4 <= x[1] < c["last_entry_min"] and (x[1] + 1) % 5 == 0
                                   and x[0] > p.get("checked", "")]:
            p["checked"] = t
            if cl > p["range_high"]:
                e = round(cl + tk, 2)
                st["open"] = {"symbol": sym, "side": 1, "qty": c["contracts"], "entry": e, "entry_time": t, "entry_min": mm,
                              "stop": e - c["stop_pts"], "target": e + c["stop_pts"] * c["target_r"], "price": round(cl, 2)}
                p["status"] = "in trade"
                log(f"DROPLONG ENTRY long {c['contracts']} {sym} @ {e} stop {e - c['stop_pts']} target {st['open']['target']}")
                break
        if p["status"].startswith("waiting") and m >= c["last_entry_min"]:
            p["status"] = "no breakout by 11:30"
    tr = st["open"]
    if tr:
        for t, mm, o, h, l, cl in [x for x in b if x[0] > tr["entry_time"] and x[0] > tr.get("seen", "")]:
            tr["seen"] = t; tr["price"] = round(cl, 2)
            if l <= tr["stop"]:
                _close(st, c, sym, tr["stop"] - tk, t, "stop", log); break
            if h >= tr["target"]:
                _close(st, c, sym, tr["target"], t, "target", log); break
            if mm - tr["entry_min"] >= c["time_stop_min"] or mm >= 954:
                _close(st, c, sym, cl - tk, t, "time stop", log); break
    json.dump(st, open(STATE, "w"), indent=1)


def summary():
    """Drop-day long book for the P&L snapshot (and the city). Practice money, never in real totals."""
    c, st = cfg(), load_state()
    tr = st.get("open")
    unreal = (tr["price"] - tr["entry"]) * fs.SPEC[tr["symbol"]]["pt"] * tr["qty"] if tr else 0.0
    eq = c["budget_start"] + st.get("realized_cum", 0.0) + unreal
    today = datetime.now(fs.ET).date().isoformat()
    day = (st.get("realized_today", 0.0) if st.get("date") == today else 0.0) + unreal
    eqc = pk = dd = 0.0
    for h in st.get("history", []):
        eqc += h["pnl"]; pk = max(pk, eqc); dd = max(dd, pk - eqc)
    return {"name": "MNQ drop-day long", "enabled": c["enabled"], "budget_start": c["budget_start"], "equity": round(eq, 2),
            "day_pnl": round(day, 2), "total_pnl": round(eq - c["budget_start"], 2),
            "total_pnl_pct": round((eq / c["budget_start"] - 1) * 100, 2), "max_dd": round(dd, 2),
            "note": "Practice lab book: on days after a big Nasdaq drop (when the main book sits out), buys a break above the first 15 minutes' high.",
            "positions": [{"symbol": tr["symbol"], "side": "long", "qty": tr["qty"], "entry": tr["entry"], "price": tr["price"],
                           "stop": tr["stop"], "target": tr["target"], "entry_time": tr["entry_time"], "pnl": round(unreal, 2)}] if tr else [],
            "trades": st.get("history", []), "status": "trading" if tr else ("off" if not c["enabled"] else "watching"),
            "today": {k: v for k, v in st.get("plan", {}).items() if k != "checked"} if st.get("date") == today else {},
            "last_data": st.get("last_data"), "last_error": st.get("last_error")}
