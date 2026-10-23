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
import json, math, os, re, sys, threading, time, traceback
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
    t = c["triggers"]
    f5 = [b for b in day if b[1] < 575]
    if "f" in t and now_m in (575, 576) and f5 and day[0][1] == 570:  # first 5 minutes' direction (10/17 tick backtest winner)
        # 10/18: read the 9:30-9:34 bars directly so a late 9:34 bar on IEX retries at 9:36 instead of missing the day
        c5 = f5[-1][5]
        _last["skip_why"] = None  # 10/23 Ryan: with skip_trade_mult set, skip days trade small instead of sitting out
        small = c.get("skip_trade_mult")
        pd = [b for b in prev if b[0] == prev[-1][0]] if prev else []
        if pd and (max(b[3] for b in pd) - min(b[4] for b in pd)) / pd[-1][5] * 100 > c.get("max_prev_range_pct", 99):  # high - low (10/18 fix: was low - close)
            if not small:
                return "skip", "yesterday's range too wide", c5, 574  # 10/17: wild prior days lost in 2024-25 and 2026
            _last["skip_why"] = "yesterday's range too wide"
        hi, lo = max(b[3] for b in f5), min(b[4] for b in f5)
        pos = ((c5 - lo) if c5 > day[0][2] else (hi - c5)) / (hi - lo + 1e-9)
        if pos < c.get("min_close_pos", 0):  # 10/18: a 9:34 close far from the move's extreme often reverses
            if not small:
                return "skip", f"first 5 min closed weak ({pos:.2f})", c5, 574
            _last["skip_why"] = _last["skip_why"] or f"first 5 min closed weak ({pos:.2f})"
        side = 1 if c5 > day[0][2] else -1
        _last["spy_agree"] = None  # 10/20: size up when SPY's first 5 min point the same way, down when not
        if "spy_agree_mult" in c:
            try:
                s5 = [b for b in (( *_et(b["t"]), b["o"], b["c"]) for b in _bars(bot, "SPY", f"{today}T13:00:00Z"))
                      if b[0] == today and 570 <= b[1] < 575]
                if s5 and s5[0][1] == 570:
                    _last["spy_agree"] = (s5[-1][3] > s5[0][2]) == (side > 0)
            except Exception:
                pass
        return side, "first-5-min direction 9:35", c5, 574
    if m1 < c["first_min"] or m1 > c["last_min"]:
        return None
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


def _shares(c):
    return c.get("instrument") == "shares"


def _stock_px(bot, sym):
    """Last trade price; 10/23 shares mode prices its limits off this (IEX quotes are often one-sided)."""
    return float(bot.api("GET", bot.DATA_API, f"/v2/stocks/{sym}/trades/latest")["trade"]["p"])


def _quote(bot, osym):
    if not re.search(r"\d{6}[CP]\d{8}$", osym):  # a stock (shares mode): +-0.2% around the last trade
        p = _stock_px(bot, osym)
        return round(p * 0.998, 2), round(p * 1.002, 2)
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
                o["stop"] = round(o["entry"] * (1 - (c.get("share_stop_pct", 15) if o.get("mult") == 1 else c["stop_pct"]) / 100), 2); o["peak"] = o["entry"]
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
            x = float(od["filled_avg_price"]); pnl = round((x - o["entry"]) * o.get("mult", 100) * o["qty"], 2)
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
        elif m - o["entry_min"] >= o.get("hold", c["max_hold_min"]) or m >= 955:
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
    osym = (c["bull"] if side > 0 else c["bear"]) if _shares(c) else _contract(bot, c["symbol"], side, px, today)
    if not osym:
        log(f"OPT {why}: no contract found"); save(st); return
    bid, ask = _quote(bot, osym)
    if not _shares(c) and ask > 0 and (bid + ask) / 2 / px * 100 > c.get("max_opt_pct", 99):  # option too expensive = market too jumpy
        if not c.get("skip_trade_mult"):
            st["done"], st["why_done"] = True, f"skipped: option too expensive ({(bid + ask) / 2:.2f})"
            log(f"OPT {st['why_done']}"); save(st); return
        _last["skip_why"] = _last.get("skip_why") or f"option too expensive ({(bid + ask) / 2:.2f})"
    agree = _last.get("spy_agree")  # shares / book_frac sizing: tested without SPY sizing (disagree still holds 90 min)
    mult = 1.0 if agree is None or _shares(c) or c.get("book_frac") else c.get("spy_agree_mult", 1.0) if agree else c.get("spy_disagree_mult", 1.0)
    if agree is not None:
        why += f" (SPY {'agrees' if agree else 'disagrees'}, size x{mult})"
    if _last.get("skip_why") and c.get("skip_trade_mult"):
        mult *= c["skip_trade_mult"]; why += f" (skip day: {_last['skip_why']}, small size x{c['skip_trade_mult']})"
    unit = 1 if _shares(c) else 100
    qty = int(c["size"] * mult // (ask * unit)) if ask > 0 else 0
    if _shares(c) and ask > 0:  # 10/23: shares mode puts the whole $1k book in each trade (backtest: +78% in 2024-25, dd 9%)
        qty = int((c["budget_start"] + st.get("realized", 0)) * c.get("book_frac", 1.0) * mult // ask)
    elif c.get("book_frac") and ask > 0:  # 10/23 Ryan "$1000 to $4000 in 3 months": a set share of the book per trade, compounding, 1 contract minimum
        cash = c["budget_start"] + st.get("realized", 0)
        frac = c.get("disagree_frac", c["book_frac"]) if agree is False else c["book_frac"]  # 10/23: half size when SPY disagrees
        qty = int(cash * frac // (ask * 100))
        if qty < 1 and cash >= ask * 100:
            qty = 1
    elif c.get("min_qty") and ask > 0:  # 10/23 Ryan: "treat it like i gave u $1000": always 1+ contract if the account can pay for it
        cash = c["budget_start"] + st.get("realized", 0)
        qty = min(max(qty, c["min_qty"]), int(cash // (ask * 100)))
    if qty < 1:
        save(st); return
    od = bot.trade("POST", "/v2/orders", body={"symbol": osym, "qty": str(qty), "side": "buy", "type": "limit",
                                                "limit_price": f"{ask:.2f}", "time_in_force": "day"})
    st["open"] = {"state": "pending", "order_id": od["id"], "symbol": osym, "side": ("up" if side > 0 else "down") if _shares(c) else "call" if side > 0 else "put", "mult": unit,
                  "why": why, "qty": qty, "sent_min": m, "entry_min": m, "entry_time": now.isoformat()[:16],
                  "hold": c.get("disagree_hold_min", c["max_hold_min"]) if agree is False else c["max_hold_min"]}
    st["trades"] += 1
    log(f"OPT {why}: buy {qty} {osym} limit {ask:.2f} (QQQ {px})")
    save(st)


def summary():
    c = cfg(); st = load(); o = st.get("open") or {}
    eq = c["budget_start"] + st.get("realized", 0) + ((o.get("price", o.get("entry", 0)) - o.get("entry", 0)) * o.get("mult", 100) * o.get("qty", 0) if o.get("state") == "open" else 0)
    return {"name": "TQQQ/SQQQ first-5 day trade (Alpaca paper)" if _shares(c) else "QQQ options scalper (Alpaca paper)", "enabled": c["enabled"], "budget_start": c["budget_start"],
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
