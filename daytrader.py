"""Archie's day-trading sleeve (PAPER only), run from archie_live.py on the server.

Strategy: 15-minute opening range breakout, long only, flat every day.
- 9:30-9:45 ET: record each approved symbol's opening range (high/low of the first 15 one-minute bars).
- 9:45-10:30 ET: when a finished one-minute bar closes above the range high, buy with a bracket order:
  stop at the range low. Skip if the risk is over 4% of price. Exit plan (Ryan, 2026-10-06): once up
  1.5x the risk the stop locks at +1.5x, once up 2x it locks at +2x, take profit at 2.5x, and above 2x a
  fade of 0.25x off the best cashes out (rules.json day: lock_steps, take_profit_r, fade_r).
- Limits: 3 trades open at once (~$3,300 each), 4 trades a day, and no new trades after -$200 on the day.
- 15:55 ET: close anything still open. Nothing is ever held overnight.
Research gate: only symbols in today's dayplan.json with verdict "buy" (written by Archie's morning
research), never a symbol the swing book holds, and never one with a red-flag headline today.
Settings live in rules.json under "day" so the nightly study can tune them.
The sleeve keeps its own books in daytrade-state.json, separate from the swing book.
"""
import json, os, re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "daytrade-state.json")
PLAN = os.path.join(HERE, "dayplan.json")
LOG = os.path.join(HERE, "day-log.jsonl")
ET = ZoneInfo("America/New_York")
RED_FLAGS = re.compile(r"downgrad|cuts? (guidance|outlook|forecast)|lowers? (guidance|outlook)|miss(es|ed)? (estimates|expectations)|"
                       r"investigation|probe|subpoena|lawsuit|fraud|recall|halt|bankrupt|delist|short seller|resign|ousted|"
                       r"plunge|tumble|sinks|crash|warns", re.I)
_last_tick = 0.0


def cfg(bot):
    return bot.RULES["day"]


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"date": "", "realized_cum": 0.0, "history": []}


def save_state(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE)


def open_symbols():
    """Symbols the day sleeve owns right now (the swing book must leave these alone)."""
    return set(load_state().get("open", {}))


def equity(positions=None):
    """Day sleeve value: its start budget + all realized day P&L + unrealized P&L of its open trades."""
    st = load_state()
    unreal = sum(float(p["unrealized_pl"]) for p in (positions or []) if p["symbol"] in st.get("open", {}))
    return st.get("budget_start", 10000) + st.get("realized_cum", 0.0) + unreal


def plan_symbols(today):
    if not os.path.exists(PLAN):
        return []
    p = json.load(open(PLAN))
    if p.get("date") != today:
        return []
    return [s for s, v in p.get("symbols", {}).items() if v.get("verdict") == "buy"]


def _log(rec):
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")


def _opening_range(bot, syms, day, minutes):
    start = datetime.combine(day, datetime.min.time(), ET).replace(hour=9, minute=30)
    end = start + timedelta(minutes=minutes)
    d = bot.api("GET", bot.DATA_API, "/v2/stocks/bars", {
        "symbols": ",".join(syms), "timeframe": "1Min", "feed": "iex", "limit": 10000,
        "start": start.astimezone(timezone.utc).isoformat(), "end": end.astimezone(timezone.utc).isoformat()})
    out = {}
    for s, bars in (d.get("bars") or {}).items():
        if len(bars) >= minutes * 0.6:
            out[s] = [max(b["h"] for b in bars), min(b["l"] for b in bars)]
    return out


def _red_flag_today(bot, sym, day):
    since = datetime.combine(day, datetime.min.time(), ET).replace(hour=4).astimezone(timezone.utc)
    d = bot.api("GET", bot.DATA_API, "/v1beta1/news", {"symbols": sym, "start": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 50})
    return any(RED_FLAGS.search(n["headline"]) for n in d.get("news", []))


def _settle(bot, st, sym, info, positions):
    """If a day trade has finished (stop, target or flatten filled), book its realized P&L."""
    o = bot.trade("GET", f"/v2/orders/{info['order_id']}", {"nested": "true"})
    if o["status"] in ("canceled", "expired", "rejected") and not float(o.get("filled_qty") or 0):
        st["open"].pop(sym)
        return
    if o["status"] != "filled":
        if o["status"] in ("new", "accepted") and datetime.now(timezone.utc).timestamp() - info["t"] > 120:
            bot.trade("DELETE", f"/v2/orders/{o['id']}")  # entry never filled: drop it
        return
    entry, qty = float(o["filled_avg_price"]), float(o["filled_qty"])
    info["entry"] = entry
    info.setdefault("entry_time", (o.get("filled_at") or "")[:19] + "Z")
    if sym in positions:
        return
    exits = [l for l in (o.get("legs") or []) if l["status"] == "filled"]
    if info.get("stop_leg") and info["stop_leg"] not in {l["id"] for l in exits}:
        sl = bot.trade("GET", f"/v2/orders/{info['stop_leg']}")
        if sl["status"] == "filled":
            exits.append(sl)
    if info.get("flatten_id"):
        f = bot.trade("GET", f"/v2/orders/{info['flatten_id']}")
        if f["status"] == "filled":
            exits.append(f)
    if not exits:
        return
    out_qty = sum(float(l["filled_qty"]) for l in exits)
    out_px = sum(float(l["filled_qty"]) * float(l["filled_avg_price"]) for l in exits) / out_qty
    pnl = round((out_px - entry) * qty, 2)
    if info.get("flatten_id") and exits[-1]["id"] == info["flatten_id"]:
        how = info.get("flatten_reason", "flatten")
    elif exits[0]["type"] == "limit":
        how = "target"
    else:
        how = "locked-profit stop" if out_px > entry else "stop"
    st["realized_today"] = round(st["realized_today"] + pnl, 2)
    st["realized_cum"] = round(st["realized_cum"] + pnl, 2)
    rec = {"date": st["date"], "symbol": sym, "qty": qty, "entry": round(entry, 2), "exit": round(out_px, 2),
           "pnl": pnl, "exit_reason": how, "stop": info["stop"], "target": info["target"],
           "best": info.get("best"), "entry_time": info.get("entry_time"),
           "exit_time": max((l.get("filled_at") or "")[:19] for l in exits) + "Z"}
    st["history"] = (st.get("history", []) + [rec])[-200:]
    _log(rec)
    st["open"].pop(sym)


def _manage_runners(bot, log, st, c, pos, now):
    """Ryan's exit plan: once a trade is up 1.5x its risk, lock the stop at +1.5x; once up 2x, lock +2x;
    the take-profit sits at 2.5x. Above 2x, if it fades fade_r x risk off its best, the raised stop cashes out."""
    live = {s: i for s, i in st["open"].items() if s in pos and i.get("entry") and not i.get("flatten_id")}
    if not live:
        return
    px = {s: t["p"] for s, t in (bot.api("GET", bot.DATA_API, "/v2/stocks/trades/latest",
                                         {"symbols": ",".join(live), "feed": "iex"}).get("trades") or {}).items()}
    for sym, info in live.items():
        if sym not in px:
            continue
        entry, r = info["entry"], info["entry"] - info["risk_stop"]
        info["best"] = max(info.get("best", entry), px[sym])
        want = info["risk_stop"]
        for reach, stop_r in c["lock_steps"]:
            if info["best"] >= entry + reach * r:
                want = max(want, entry + stop_r * r)
        if c.get("fade_r") and info["best"] >= entry + c["lock_steps"][-1][0] * r:
            want = max(want, info["best"] - c["fade_r"] * r)
        want = round(want, 2)
        if want <= info["stop"] + 0.01:
            continue
        if px[sym] <= want + 0.01:  # at or under the lock level already: sell now to keep the profit
            for o in bot.trade("GET", "/v2/orders", {"status": "open", "symbols": sym}):
                try:
                    bot.trade("DELETE", f"/v2/orders/{o['id']}")
                except Exception:
                    pass  # the other bracket leg cancels with it
            f = bot.trade("POST", "/v2/orders", body={"symbol": sym, "qty": pos[sym]["qty"], "side": "sell", "type": "market",
                          "time_in_force": "day", "client_order_id": f"archie_day-lock_{now.timestamp():.0f}_{sym}"})
            info.update(flatten_id=f["id"], flatten_reason="locked profit")
            log(f"DAY {sym} dropped to the lock level, selling at ~{px[sym]}")
            continue
        leg_id = info.get("stop_leg") or next(
            (l["id"] for l in (bot.trade("GET", f"/v2/orders/{info['order_id']}", {"nested": "true"}).get("legs") or [])
             if l["type"] == "stop" and l["status"] in ("new", "accepted", "held")), None)
        if not leg_id:
            continue
        new = bot.trade("PATCH", f"/v2/orders/{leg_id}", body={"stop_price": str(want)})
        info["stop_leg"] = new["id"]  # a replaced order gets a new id
        log(f"DAY {sym} stop raised {info['stop']} -> {want} (best {info['best']})")
        info["stop"] = want


def tick(bot, log):
    """Called from the live loop while the market is open. Self-throttles to every 15 seconds."""
    global _last_tick
    now = datetime.now(timezone.utc)
    if now.timestamp() - _last_tick < 15:
        return
    _last_tick = now.timestamp()
    positions = bot.trade("GET", "/v2/positions")
    open_orders = bot.trade("GET", "/v2/orders", {"status": "open", "limit": 500})
    c = cfg(bot)
    et = now.astimezone(ET)
    today, m = et.date().isoformat(), et.hour * 60 + et.minute
    st = load_state()
    st.setdefault("budget_start", c["budget_start"])
    if st["date"] != today:
        st.update({"date": today, "realized_today": 0.0, "trades_today": 0, "or": {}, "done": [],
                   "open": st.get("open", {}), "status": "waiting"})
    pos = {p["symbol"]: p for p in positions}
    for sym, info in list(st["open"].items()):
        try:
            _settle(bot, st, sym, info, pos)
        except Exception as e:
            log(f"DAY settle error {sym}: {e}")

    if c.get("lock_steps") and m < c["flatten_min"]:
        try:
            _manage_runners(bot, log, st, c, pos, now)
        except Exception as e:
            log(f"DAY runner error: {e}")

    or_end, last_entry, flat = 570 + c["or_minutes"], c["last_entry_min"], c["flatten_min"]
    if m >= flat:  # close everything before the bell
        for sym, info in st["open"].items():
            if info.get("flatten_id") or sym not in pos:
                continue
            for o in open_orders:
                if o["symbol"] == sym:
                    bot.trade("DELETE", f"/v2/orders/{o['id']}")
            f = bot.trade("POST", "/v2/orders", body={"symbol": sym, "qty": pos[sym]["qty"], "side": "sell", "type": "market",
                          "time_in_force": "day", "client_order_id": f"archie_day-flatten_{now.timestamp():.0f}_{sym}"})
            info["flatten_id"] = f["id"]
            log(f"DAY flatten {sym}")
    elif or_end <= m <= last_entry:
        plan = plan_symbols(today)
        if plan and not st["or"]:
            st["or"] = _opening_range(bot, plan, et.date(), c["or_minutes"])
            log(f"DAY opening ranges: {st['or']}")
        swing_busy = {s for s in pos if s not in st["open"]} | {o["symbol"] for o in open_orders if o["symbol"] not in st["open"]}
        cands = [s for s in st["or"] if s not in st["done"] and s not in swing_busy]
        can_trade = (st["realized_today"] > -c["daily_loss"] and st["trades_today"] < c["max_trades"]
                     and len(st["open"]) < c["max_concurrent"])
        if st["realized_today"] <= -c["daily_loss"]:
            st["status"] = "halted"
        if cands and can_trade:
            latest = bot.api("GET", bot.DATA_API, "/v2/stocks/bars/latest", {"symbols": ",".join(cands), "feed": "iex"}).get("bars", {})
            for sym in cands:
                b = latest.get(sym)
                if not b or len(st["open"]) >= c["max_concurrent"] or st["trades_today"] >= c["max_trades"]:
                    continue
                bar_min = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
                hi, lo = st["or"][sym]
                if bar_min.hour * 60 + bar_min.minute < or_end or b["c"] <= hi:
                    continue
                st["done"].append(sym)  # one shot per symbol per day
                px, risk = b["c"], b["c"] - lo
                qty = int(c["notional"] // px)
                if risk <= 0 or risk / px * 100 > c["max_risk_pct"] or qty < 1:
                    log(f"DAY skip {sym}: range too wide or too pricey")
                    continue
                if _red_flag_today(bot, sym, et.date()):
                    log(f"DAY skip {sym}: red-flag headline today")
                    continue
                target = round(px + c.get("take_profit_r", c["target_r"]) * risk, 2)
                body = {"symbol": sym, "qty": str(qty), "side": "buy", "type": "market", "time_in_force": "day",
                        "order_class": "bracket", "take_profit": {"limit_price": str(target)},
                        "stop_loss": {"stop_price": str(round(lo, 2))},
                        "client_order_id": f"archie_day-entry_{now.timestamp():.0f}_{sym}"}
                o = bot.trade("POST", "/v2/orders", body=body)
                st["open"][sym] = {"order_id": o["id"], "qty": qty, "stop": round(lo, 2), "risk_stop": round(lo, 2), "target": target,
                                   "t": now.timestamp()}
                st["trades_today"] += 1
                log(f"DAY BUY {qty} {sym} ~{px} (range {lo:.2f}-{hi:.2f}), stop {lo:.2f}, target {target}")
    if st.get("status") != "halted":
        st["status"] = ("trading" if st["open"] else
                        "waiting" if m < or_end else
                        "watching" if m <= last_entry and st["or"] else "done")
    save_state(st)


def summary(positions):
    """Day sleeve numbers for the P&L snapshot."""
    st = load_state()
    open_ = st.get("open", {})
    unreal = sum(float(p["unrealized_pl"]) for p in positions if p["symbol"] in open_)
    start = st.get("budget_start", 10000)
    eq = start + st.get("realized_cum", 0.0) + unreal
    today = st.get("realized_today", 0.0) + unreal if st.get("date") == datetime.now(ET).date().isoformat() else unreal
    return {"budget_start": start, "equity": round(eq, 2), "day_pnl": round(today, 2),
            "day_pnl_pct": round(today / (eq - today) * 100, 2) if eq - today else 0.0,
            "total_pnl": round(eq - start, 2), "total_pnl_pct": round((eq / start - 1) * 100, 2),
            "trades_today": st.get("trades_today", 0), "realized_today": st.get("realized_today", 0.0),
            "status": st.get("status", "waiting"),
            "positions": [{"symbol": p["symbol"], "qty": float(p["qty"]), "value": round(float(p["market_value"]), 2),
                           "entry": round(float(p["avg_entry_price"]), 2), "price": round(float(p["current_price"]), 2),
                           "stop": open_[p["symbol"]].get("stop"), "target": open_[p["symbol"]].get("target"),
                           "entry_time": open_[p["symbol"]].get("entry_time"),
                           "pnl": round(float(p["unrealized_pl"]), 2), "pnl_pct": round(float(p["unrealized_plpc"]) * 100, 2),
                           "day_pnl": round(float(p["unrealized_intraday_pl"]), 2)} for p in positions if p["symbol"] in open_],
            "recent_trades": st.get("history", [])[-10:],
            "trades": st.get("history", [])}
