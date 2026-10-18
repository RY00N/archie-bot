"""QQQ options scalper on the Alpaca PAPER account (Ryan 2026-10-17: "Put futures trading aside, let's do this on alpaca").
Copies nickafischer's "Scalp City" bots as described in his reels: 1-min chart, three triggers (a candle closes across
VWAP, across the 50 EMA, or out of the first 15-min range; above = buy a call, below = buy a put), next-day (1DTE)
ATM options, a 5% profit lock that then trails, a stop loss, and a daily profit goal / loss brake that switch it off.
Only trades the first 30 minutes (his reels: "I only trade the first 30 minutes"). Backtest: options/scalp_bt.py.
10/17 second-by-second backtest (options/scalp_tick.py) showed his three triggers lose even at zero cost, so the live
setting is trigger "f": the first 5 minutes' direction (9:34 close vs 9:30 open) buys one next-day ATM call/put at
9:35, 50% stop, no profit lock, sold at 10:35 (options/first5.py, f5sim.py).
Settings: rules.json "options". daytrader.tick calls ensure_thread(), which starts one background thread that runs
step() EVERY SECOND (Ryan 10/17: "these trades are quick"): open trades are checked each second, and the chart is read
right after each 1-min candle closes. The thread always runs the newest loaded code. PAPER ONLY:
refuses to run unless the account number starts with PA."""
import json, math, os, sys, threading, time, traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__)); ET = ZoneInfo("America/New_York")
STATE = os.path.join(HERE, "optbot-state.json")
DEFAULTS = {"enabled": True, "symbol": "QQQ", "budget_start": 100000, "size": 12000, "stop_pct": 12, "lock_pct": 5,
            "keep_pct": 2, "trail_pct": 10, "max_hold_min": 30, "daily_goal": 1500, "daily_brake": 2500,
            "first_min": 571, "last_min": 600, "max_trades": 6, "triggers": "veo"}
_last = globals().get("_last") or {"min": None, "paper": None}  # survives importlib.reload


def cfg():
    try:
        c = json.load(open(os.path.join(HERE, "rules.json"))).get("options", {})
    except Exception:
        c = {}
    return {**DEFAULTS, **c}


def load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"date": None, "open": None, "history": [], "realized": 0.0}


def save(st):
    json.dump(st, open(STATE, "w"), indent=1)


def _bars(bot, sym, start):
    out, tok = [], None
    while True:
        p = {"symbols": sym, "timeframe": "1Min", "start": start, "limit": 10000, "feed": "iex"}
        if tok:
            p["page_token"] = tok
        d = bot.api("GET", bot.DATA_API, "/v2/stocks/bars", p)
        out += (d.get("bars") or {}).get(sym, [])
        tok = d.get("next_page_token")
        if not tok:
            return out


def _et(t):
    x = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(ET)
    return x.date().isoformat(), x.hour * 60 + x.minute


def signal(bot, c, today, now_m):
    """Check the last CLOSED 1-min bar. Returns (side, why, price) or None."""
    sym = c["symbol"]
    start = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    B = [(*_et(b["t"]), b["o"], b["h"], b["l"], b["c"], b["v"]) for b in _bars(bot, sym, start)]
    B = [b for b in B if 570 <= b[1] < 960]
    prev = [b for b in B if b[0] < today]; day = [b for b in B if b[0] == today and b[1] < now_m]  # closed bars only
    if len(day) < 2:
        return None
    if day[-1][1] < now_m - 1 and now_m - 1 >= 570:
        return "wait"
    k = 2 / 51; e = None
    for b in prev[-120:]:
        e = b[5] if e is None else e + k * (b[5] - e)
    pv = vv = 0; I = []
    for d, m, o, h, l, cl, v in day:
        pv += (h + l + cl) / 3 * v; vv += v; e = cl if e is None else e + k * (cl - e)
        I.append((m, cl, pv / vv if vv else cl, e, h, l))
    (m0, c0, w0, e0, _, _), (m1, c1, w1, e1, _, _) = I[-2], I[-1]
    if m1 < c["first_min"] or m1 > c["last_min"]:
        return None
    t = c["triggers"]
    if "f" in t and m1 == 574 and day[0][1] == 570:  # first 5 minutes' direction (10/17 tick backtest winner)
        pd = [b for b in prev if b[0] == prev[-1][0]] if prev else []
        if pd and (max(b[3] for b in pd) - min(b[4] for b in pd)) / pd[-1][5] * 100 > c.get("max_prev_range_pct", 99):  # high - low (10/18 fix: was low - close)
            return "skip", "yesterday's range too wide", c1, m1  # 10/17: wild prior days lost in 2024-25 and 2026
        f5 = [b for b in day if b[1] < 575]; hi, lo = max(b[3] for b in f5), min(b[4] for b in f5)
        pos = ((c1 - lo) if c1 > day[0][2] else (hi - c1)) / (hi - lo + 1e-9)
        if pos < c.get("min_close_pos", 0):  # 10/18: a 9:34 close far from the move's extreme often reverses
            return "skip", f"first 5 min closed weak ({pos:.2f})", c1, m1
        return (1 if c1 > day[0][2] else -1), "first-5-min direction 9:35", c1, m1
    if "v" in t and (c0 - w0) * (c1 - w1) < 0:
        return (1 if c1 > w1 else -1), f"VWAP cross {m1 // 60}:{m1 % 60:02d}", c1, m1
    if "e" in t and (c0 - e0) * (c1 - e1) < 0:
        return (1 if c1 > e1 else -1), f"EMA50 cross {m1 // 60}:{m1 % 60:02d}", c1, m1
    if "o" in t and m1 >= 585:
        rng = [x for x in I if x[0] < 585]
        hi, lo = max(x[4] for x in rng), min(x[5] for x in rng)
        before = [x for x in I[:-1] if x[0] >= 585 and (x[1] > hi or x[1] < lo)]
        if not before and (c1 > hi or c1 < lo):
            return (1 if c1 > hi else -1), f"15-min range break {m1 // 60}:{m1 % 60:02d}", c1, m1
    return None


def _contract(bot, sym, side, price, today):
    """Nearest-to-the-money option expiring on the next trading day after today."""
    for add in range(1, 6):
        exp = (datetime.fromisoformat(today) + timedelta(days=add)).date().isoformat()
        d = bot.trade("GET", "/v2/options/contracts", {"underlying_symbols": sym, "expiration_date": exp,
                      "type": "call" if side > 0 else "put", "strike_price_gte": str(math.floor(price) - 2),
                      "strike_price_lte": str(math.ceil(price) + 2), "limit": 50})
        cs = [x for x in d.get("option_contracts", []) if x.get("tradable")]
        if cs:
            return min(cs, key=lambda x: abs(float(x["strike_price"]) - price))["symbol"]
    return None


def _quote(bot, osym):
    q = bot.api("GET", bot.DATA_API, "/v1beta1/options/quotes/latest", {"symbols": osym, "feed": "indicative"})["quotes"][osym]
    return float(q["bp"]), float(q["ap"])


def step(bot, log):
    c = cfg()
    now = datetime.now(timezone.utc).astimezone(ET)
    today, m = now.date().isoformat(), now.hour * 60 + now.minute
    st = load()
    if st.get("date") != today:
        st.update({"date": today, "day_pnl": 0.0, "trades": 0, "done": False, "last_sig_min": None, "why_done": ""})
        save(st)
    if _last["paper"] != today:  # paper only: check the account once a day
        if not bot.trade("GET", "/v2/account")["account_number"].startswith("PA"):
            return
        _last["paper"] = today
    o = st.get("open")
    if o:  # manage the open option: stop, profit lock + trail, time stop
        if o.get("state") == "pending":
            od = bot.trade("GET", f"/v2/orders/{o['order_id']}")
            if od["status"] == "filled":
                o.update({"state": "open", "entry": float(od["filled_avg_price"]), "qty": int(float(od["filled_qty"]))})
                o["stop"] = round(o["entry"] * (1 - c["stop_pct"] / 100), 2); o["peak"] = o["entry"]
                log(f"OPT filled {o['qty']} {o['symbol']} @ {o['entry']}")
            elif od["status"] in ("canceled", "expired", "rejected") or m - o["sent_min"] >= 2:
                if od["status"] not in ("canceled", "expired", "rejected"):
                    bot.trade("DELETE", f"/v2/orders/{o['order_id']}")
                log(f"OPT entry not filled ({od['status']}), skipped")
                st["open"] = None
            save(st)
            return
        if o.get("state") == "closing":
            od = bot.trade("GET", f"/v2/orders/{o['close_id']}")
            if od["status"] != "filled":
                if m - o["close_min"] >= 1 and od["status"] in ("new", "accepted", "partially_filled"):
                    bot.trade("DELETE", f"/v2/orders/{o['close_id']}"); o["state"] = "open"  # re-try next pass at the bid
                save(st)
                return
            x = float(od["filled_avg_price"]); pnl = round((x - o["entry"]) * 100 * o["qty"], 2)
            tr = {**{k: o[k] for k in ("symbol", "side", "why", "qty", "entry", "entry_time")}, "exit": x,
                  "exit_time": now.isoformat()[:16], "how": o["how"], "pnl": pnl}
            st["history"].append(tr); st["realized"] = round(st.get("realized", 0) + pnl, 2)
            st["day_pnl"] = round(st["day_pnl"] + pnl, 2); st["open"] = None
            log(f"OPT closed {o['symbol']} @ {x} ({o['how']}) {pnl:+.0f}, day {st['day_pnl']:+.0f}")
            if st["day_pnl"] >= c["daily_goal"]:
                st["done"], st["why_done"] = True, f"daily goal hit {st['day_pnl']:+.0f}"
            elif st["day_pnl"] <= -c["daily_brake"]:
                st["done"], st["why_done"] = True, f"loss brake hit {st['day_pnl']:+.0f}"
            elif st["trades"] >= c["max_trades"]:
                st["done"], st["why_done"] = True, "max trades"
            save(st)
            return
        bid, ask = _quote(bot, o["symbol"])
        o["peak"] = max(o["peak"], bid); o["price"] = bid
        if o["peak"] >= o["entry"] * (1 + c["lock_pct"] / 100):
            o["stop"] = round(max(o["stop"], o["entry"] * (1 + c["keep_pct"] / 100), o["peak"] * (1 - c["trail_pct"] / 100)), 2)
        how = None
        if bid <= o["stop"]:
            how = "stopped out" if o["stop"] < o["entry"] else "profit locked"
        elif m - o["entry_min"] >= c["max_hold_min"] or m >= 955:
            how = "time"
        if how:
            od = bot.trade("POST", "/v2/orders", body={"symbol": o["symbol"], "qty": str(o["qty"]), "side": "sell",
                                                        "type": "limit", "limit_price": f"{bid:.2f}", "time_in_force": "day"})
            o.update({"state": "closing", "close_id": od["id"], "close_min": m, "how": how})
        save(st)
        return
    if not c["enabled"] or st["done"] or now.weekday() >= 5 or not c["first_min"] <= m <= c["last_min"] + 1:
        return
    if _last["min"] == m:  # this minute's closed candle already read
        return
    sig = signal(bot, c, today, m)
    if sig == "wait":
        if now.second < 10:
            return  # the candle that just closed isn't in the data yet; retry next second
        sig = None  # no print on IEX that minute: nothing to read
    _last["min"] = m
    if not sig or sig[3] == st.get("last_sig_min"):
        return
    if sig[0] == "skip":
        st["done"], st["why_done"] = True, f"skipped: {sig[1]}"; log(f"OPT {st['why_done']}"); save(st); return
    side, why, px, sm = sig; st["last_sig_min"] = sm
    osym = _contract(bot, c["symbol"], side, px, today)
    if not osym:
        log(f"OPT {why}: no contract found"); save(st); return
    bid, ask = _quote(bot, osym)
    if ask > 0 and (bid + ask) / 2 / px * 100 > c.get("max_opt_pct", 99):  # option too expensive = market too jumpy
        st["done"], st["why_done"] = True, f"skipped: option too expensive ({(bid + ask) / 2:.2f})"
        log(f"OPT {st['why_done']}"); save(st); return
    qty = int(c["size"] // (ask * 100)) if ask > 0 else 0
    if qty < 1:
        save(st); return
    od = bot.trade("POST", "/v2/orders", body={"symbol": osym, "qty": str(qty), "side": "buy", "type": "limit",
                                                "limit_price": f"{ask:.2f}", "time_in_force": "day"})
    st["open"] = {"state": "pending", "order_id": od["id"], "symbol": osym, "side": "call" if side > 0 else "put",
                  "why": why, "qty": qty, "sent_min": m, "entry_min": m, "entry_time": now.isoformat()[:16]}
    st["trades"] += 1
    log(f"OPT {why}: buy {qty} {osym} limit {ask:.2f} (QQQ {px})")
    save(st)


def summary():
    c = cfg(); st = load(); o = st.get("open") or {}
    eq = c["budget_start"] + st.get("realized", 0) + ((o.get("price", o.get("entry", 0)) - o.get("entry", 0)) * 100 * o.get("qty", 0) if o.get("state") == "open" else 0)
    return {"name": "QQQ options scalper (Alpaca paper)", "enabled": c["enabled"], "budget_start": c["budget_start"],
            "equity": round(eq, 2), "day_pnl": st.get("day_pnl", 0), "realized": st.get("realized", 0),
            "positions": [o] if o else [], "trades": st.get("history", [])[-50:],
            "status": ("off" if not c["enabled"] else st.get("why_done") or ("in a trade" if o else "watching")), "date": st.get("date")}


def _loop(bot, log):
    while True:
        try:
            try:  # helper's pre-market intel (Ryan OK'd 10/17; info only, never gates a trade); this thread runs all day, ticks don't
                import importlib, intel
                if time.time() - _last.get("intel_reload", 0) > 300:
                    importlib.reload(intel); _last["intel_reload"] = time.time()
                intel.maybe_run(log)
            except Exception as e:
                log(f"intel hook error: {e}")
            now = datetime.now(timezone.utc).astimezone(ET)
            if now.weekday() < 5 and 570 <= now.hour * 60 + now.minute < 960:
                sys.modules["optbot"].step(bot, log)  # newest code after each git pull + reload
                time.sleep(1)
            else:
                time.sleep(20)
        except Exception:
            log("optbot error:\n" + traceback.format_exc()[-600:])
            time.sleep(5)


def ensure_thread(bot, log):
    if not any(t.name == "optbot" for t in threading.enumerate()):
        threading.Thread(target=_loop, args=(bot, log), name="optbot", daemon=True).start()
        log("optbot 1-second loop started")
