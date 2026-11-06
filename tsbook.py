"""Paper Topstep 50K Combine on micro NQ (Reza 2026-11-06: "make a trader that's used to doing top step trading for funded
accounts" / "So it has to pass eval" / "Trading NQ"). Alpaca has no futures and Topstep bans bots on a VPS, so this is
a simulated Combine on this server: MNQ is priced from QQQ (NQ ~= QQQ x 41, $2 per NQ point per micro).
Rules simulated (2026 guides, not Topstep's site): +$3,000 target, best day <= 50% of total profit, max loss = best
end-of-day balance - $2,000 (locks at $50,000), touching it intraday fails; $2 per micro round trip for fees/slippage.
Method: first-5-minute direction at 9:35 ET (9:30 open vs 9:34 close), long or short, stop 0.5%, target 2% (4R),
otherwise out at 15:55. 2 micros.
Reza 11/06: "learn how to pass it and be profitable on 50K funding". So after a pass the account becomes a simulated
Express Funded Account (2026 guides, unverified): same $2,000 trailing max loss locking at $50,000; a payout after 5
winning days (>= $150) of up to 50% of profit, capped at $5,000, trader keeps 90%; after the first payout the floor sits
at $50,000. A blown funded account or failed Combine buys a new Combine. Paper fees: $109 per 20 Combine days, $149 activation.
Backtest of the whole cycle, 2 micros both stages: net after fees +$1.8k (2024), +$0.4k (2025), +$1.3k (2026), ~10 Combines a year."""
import json, os, threading, time, traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__)); ET = ZoneInfo("America/New_York")
STATE = os.path.join(HERE, "tsbook-state.json")
DEFAULTS = {"enabled": True, "start": 50000, "target": 3000, "mll": 2000, "cons": 0.5, "micros": 2, "fee": 109, "activation": 149, "stop_pct": 0.5,
            "tp_pct": 2.0, "k": 82.0, "cost": 2.0, "entry_min": 575, "flat_min": 955}
_last = globals().get("_last") or {}


def cfg():
    try:
        c = json.load(open(os.path.join(HERE, "rules.json"))).get("tsbook", {})
    except Exception:
        c = {}
    return {**DEFAULTS, **c}


def new_combine(c, n, kind="combine"):
    return {"n": n, "kind": kind, "wins": 0, "paid": False, "balance": c["start"], "peak_eod": c["start"], "floor": c["start"] - c["mll"], "best_day": 0.0,
            "days": 0, "started": datetime.now(timezone.utc).astimezone(ET).date().isoformat()}


def load(c):
    try:
        return json.load(open(STATE))
    except Exception:
        return {"date": None, "combine": new_combine(c, 1), "attempts": [], "history": [], "pos": None,
                "fees": c["fee"], "payouts": 0.0, "funded_n": 0}


def save(st):
    tmp = STATE + ".tmp"; json.dump(st, open(tmp, "w"), indent=1); os.replace(tmp, STATE)


def _px(bot):
    return float(bot.api("GET", bot.DATA_API, "/v2/stocks/QQQ/trades/latest", {"feed": "iex"})["trade"]["p"])


def _first5(bot, today):
    d = bot.api("GET", bot.DATA_API, "/v2/stocks/bars", {"symbols": "QQQ", "timeframe": "1Min", "feed": "iex", "limit": 20,
                                                         "start": f"{today}T13:25:00Z"})
    o = c5 = None
    for b in (d.get("bars") or {}).get("QQQ", []):
        x = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET); m = x.hour * 60 + x.minute
        if m == 570: o = b["o"]
        if m == 574: c5 = b["c"]
    return (o, c5) if o and c5 else None


def _close(st, c, px, why, log):
    p = st.pop("pos"); st["pos"] = None; cb = st["combine"]
    pnl = round(p["side"] * (px - p["entry"]) * c["k"] * p["micros"] - c["cost"] * p["micros"], 2)
    st["day_pnl"] = round(st.get("day_pnl", 0) + pnl, 2); cb["balance"] = round(cb["balance"] + pnl, 2)
    st["history"].append({"combine": cb["n"], "side": "long" if p["side"] > 0 else "short", "micros": p["micros"],
                          "entry_qqq": p["entry"], "exit_qqq": px, "nq_entry": round(p["entry"] * 41), "nq_exit": round(px * 41),
                          "entry_time": p["at"], "exit_time": datetime.now(timezone.utc).astimezone(ET).isoformat()[:16],
                          "how": why, "pnl": pnl})
    st["history"] = st["history"][-300:]
    log(f"TSBOOK {cb['kind']} {cb['n']}: closed {'long' if p['side'] > 0 else 'short'} {p['micros']} MNQ ({why}) {pnl:+.2f}, balance {cb['balance']}")


def _end(st, c, result, log):
    cb = st["combine"]; cb.update({"result": result, "ended": st["date"]})
    st["attempts"].append(cb); st["attempts"] = st["attempts"][-100:]
    if result == "pass":  # Combine passed: activate a funded account
        st["funded_n"] = st.get("funded_n", 0) + 1; st["fees"] = st.get("fees", 0) + c["activation"]
        st["combine"] = new_combine(c, cb["n"], "funded")
    else:  # failed Combine or blown funded account: buy a new Combine
        st["fees"] = st.get("fees", 0) + c["fee"]; st["combine"] = new_combine(c, cb["n"] + 1)
    log(f"TSBOOK {cb['kind']} {cb['n']} {result.upper()} after {cb['days']} days, balance {cb['balance']}")


def tick(bot, log):
    c = cfg()
    if not c["enabled"]: return
    now = datetime.now(timezone.utc).astimezone(ET); today, m = now.date().isoformat(), now.hour * 60 + now.minute
    if now.weekday() > 4 or not (570 <= m < 965): return
    st = load(c)
    if st.get("date") != today:
        st.update({"date": today, "day_pnl": 0.0, "entered": False, "closed_day": False})
    cb = st["combine"]; p = st.get("pos")
    if p:
        px = _px(bot); p["price"] = px; adv = p["side"] * (px - p["entry"])
        p["worst"] = min(p.get("worst", 0), adv)
        if cb["balance"] + adv * c["k"] * p["micros"] <= cb["floor"]:
            _close(st, c, px, "max loss hit", log)
        elif adv <= -p["entry"] * c["stop_pct"] / 100:
            _close(st, c, px, "stop", log)
        elif adv >= p["entry"] * c["tp_pct"] / 100:
            _close(st, c, px, "target", log)
        elif m >= c["flat_min"]:
            _close(st, c, px, "3:55 close", log)
    if not st["entered"] and c["entry_min"] <= m < c["entry_min"] + 10 and now.second >= 3:
        f5 = _first5(bot, today)
        if f5:
            side = 1 if f5[1] > f5[0] else -1; e = _px(bot)
            st["pos"] = {"side": side, "entry": e, "micros": c["micros"], "at": now.isoformat()[:16], "first5": f"{f5[0]} -> {f5[1]}"}
            st["entered"] = True; cb["days"] += 1
            if cb["kind"] == "combine" and cb["days"] % 20 == 0:
                st["fees"] = st.get("fees", 0) + c["fee"]
            log(f"TSBOOK {cb['kind']} {cb['n']}: first 5 min {'up' if side > 0 else 'down'} ({f5[0]} -> {f5[1]}): "
                f"{'buy' if side > 0 else 'sell'} {c['micros']} MNQ ~{round(e * 41)}")
        elif m >= c["entry_min"] + 2:
            st["entered"] = True; log("TSBOOK: no 9:30/9:34 bar today")
    if not st.get("pos") and st["entered"] and not st["closed_day"] and (m >= c["flat_min"] or st.get("history") and
                                                                          st["history"][-1].get("exit_time", "")[:10] == today):
        st["closed_day"] = True; cb["best_day"] = max(cb["best_day"], st["day_pnl"])
        if cb["balance"] <= cb["floor"]:
            _end(st, c, "fail" if cb["kind"] == "combine" else "blown", log)
        else:
            cb["peak_eod"] = max(cb["peak_eod"], cb["balance"])
            if not cb.get("paid"):
                cb["floor"] = min(c["start"], max(cb["floor"], cb["peak_eod"] - c["mll"]))
            prof = cb["balance"] - c["start"]
            if cb["kind"] == "combine":
                if prof >= c["target"] and cb["best_day"] <= c["cons"] * prof:
                    _end(st, c, "pass", log)
            else:
                cb["wins"] += st["day_pnl"] >= 150
                if cb["wins"] >= 5 and prof > 0:
                    amt = round(min(5000, 0.5 * prof), 2); cb["balance"] = round(cb["balance"] - amt, 2)
                    st["payouts"] = round(st.get("payouts", 0) + 0.9 * amt, 2); cb["wins"] = 0; cb["paid"] = True
                    cb["floor"] = c["start"]; cb["peak_eod"] = cb["balance"]
                    log(f"TSBOOK funded {cb['n']}: payout ${amt} (keep ${0.9 * amt:.2f}), balance {cb['balance']}")
    save(st)


def summary():
    c = cfg(); st = load(c); cb = st["combine"]; p = st.get("pos")
    unreal = round(p["side"] * (p.get("price", p["entry"]) - p["entry"]) * c["k"] * p["micros"], 2) if p else 0
    at = st.get("attempts", [])
    return {"name": "Paper Topstep 50K Combine + funded account, micro NQ, first-5-minute direction", "enabled": c["enabled"],
            "stage": cb.get("kind", "combine"), "fees": st.get("fees", 0), "payouts_kept": st.get("payouts", 0),
            "net": round(st.get("payouts", 0) - st.get("fees", 0), 2), "funded_accounts": st.get("funded_n", 0),
            "combine": cb, "unrealized": unreal, "to_target": round(c["start"] + c["target"] - cb["balance"], 2),
            "cushion": round(cb["balance"] - cb["floor"], 2), "position": p, "day_pnl": st.get("day_pnl", 0),
            "passed": sum(a.get("result") == "pass" for a in at), "failed": sum(a.get("result") == "fail" for a in at),
            "blown": sum(a.get("result") == "blown" for a in at),
            "attempts": at[-20:], "trades": st.get("history", [])[-60:], "date": st.get("date"),
            "status": "off" if not c["enabled"] else ("in a trade" if p else "watching")}


def _loop(bot, log):
    while True:
        try:
            import sys
            sys.modules["tsbook"].tick(bot, log); time.sleep(2)
        except Exception:
            log("tsbook error:\n" + traceback.format_exc()[-600:]); time.sleep(5)


def ensure_thread(bot, log):
    if not any(t.name == "tsbook" for t in threading.enumerate()):
        threading.Thread(target=_loop, args=(bot, log), name="tsbook", daemon=True).start(); log("tsbook loop started")
