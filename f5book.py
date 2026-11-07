"""$1,500 first-5-minute book on the Alpaca PAPER account (Reza 2026-11-06: "imagine I gave you an account with $1500 to
start this new strategy with", "using alpaca ofc"; method copied from IG @charlini_7: the first 5 minutes after the open
pick the direction, hold it all day).
At 9:35 ET each leg reads its 9:30 open vs 9:34 close. Up: buy the leg's "up" symbol; down: buy its "down" symbol, or sit
the leg out when it has none (cash-account rules: no shorting, no margin). Each leg gets its share ("frac") of the book.
Exit: stop "stop" % under the entry, trailing "trail" % under the best price since entry; anything left is sold at 15:55.
Backtest (QQQ/MSFT/GOOGL/NFLX 1-min, intrabar stops, 2024-02..2026-11): 2024-25 x1.52 dd18%, 2026 x1.38 dd12%, last 10 wks x1.16.
optbot.step starts this file's thread and reloads the module, so new code needs no restart."""
import json, math, os, threading, time, traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__)); ET = ZoneInfo("America/New_York")
STATE = os.path.join(HERE, "f5book-state.json")
DEFAULTS = {"enabled": True, "book_start": 1500, "entry_min": 575, "flat_min": 955,
            "legs": [{"sym": "QQQ", "up": "TQQQ", "down": "SQQQ", "frac": 0.7, "stop": 2.1, "trail": 3.6, "exit": 870},
                     {"sym": "MSFT", "up": "MSFT", "down": None, "frac": 0.1, "stop": 1.0, "trail": 99},
                     {"sym": "GOOGL", "up": "GOOGL", "down": None, "frac": 0.1, "stop": 1.0, "trail": 1.5},
                     {"sym": "NFLX", "up": "NFLX", "down": None, "frac": 0.1, "stop": 1.0, "trail": 1.5}]}
_last = globals().get("_last") or {"paper": None}


def cfg():
    try:
        c = json.load(open(os.path.join(HERE, "rules.json"))).get("f5book", {})
    except Exception:
        c = {}
    return {**DEFAULTS, **c}


def load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"date": None, "realized_total": 0.0, "history": [], "open": {}}


def save(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=1); os.replace(tmp, STATE)


def _px(bot, sym):
    return float(bot.api("GET", bot.DATA_API, f"/v2/stocks/{sym}/trades/latest", {"feed": "iex"})["trade"]["p"])


def _first5(bot, sym, today):
    """(9:30 open, 9:34 close) from the IEX 1-min bars, or None if either bar is missing."""
    d = bot.api("GET", bot.DATA_API, "/v2/stocks/bars", {"symbols": sym, "timeframe": "1Min", "feed": "iex", "limit": 20,
                                                         "start": f"{today}T13:25:00Z"})
    o = c5 = None
    for b in (d.get("bars") or {}).get(sym, []):
        x = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET); m = x.hour * 60 + x.minute
        if m == 570:
            o = b["o"]
        if m == 574:
            c5 = b["c"]
    return (o, c5) if o and c5 else None


def _book(st, leg, px, why, log):
    p = st["open"].pop(leg)
    pnl = round((px - p["entry"]) * p["qty"], 2)
    st["day_pnl"] = round(st.get("day_pnl", 0) + pnl, 2); st["realized_total"] = round(st.get("realized_total", 0) + pnl, 2)
    st["history"].append({"leg": leg, "symbol": p["symbol"], "side": p["side"], "qty": p["qty"], "entry": p["entry"],
                          "entry_time": p["at"], "exit": px, "exit_time": datetime.now(timezone.utc).astimezone(ET).isoformat()[:16],
                          "how": why, "pnl": pnl})
    st["history"] = st["history"][-300:]
    log(f"F5BOOK {leg}: sold {p['qty']} {p['symbol']} @ {px} ({why}) {pnl:+.2f}, day {st['day_pnl']:+.2f}")


def _gone(bot, st, leg, p, log):
    """True (and booked) when the leg's shares are no longer held, using the account's last filled sell."""
    try:
        held = float(bot.trade("GET", f"/v2/positions/{p['symbol']}").get("qty", 0))
    except Exception as e:
        if "404" not in str(e) and "not found" not in str(e).lower():
            return False
        held = 0.0
    if held >= p["qty"] * 0.999:
        return False
    sells = [o for o in bot.trade("GET", "/v2/orders", {"status": "closed", "symbols": p["symbol"], "limit": 50,
                                                        "after": st["date"] + "T13:00:00Z"}) or []
             if o["side"] == "sell" and o["status"] == "filled"]
    px = float(sells[0]["filled_avg_price"]) if sells else p.get("price", p["entry"])
    _book(st, leg, px, "sold by another bot", log)
    return True


def tick(bot, log):
    c = cfg()
    if not c["enabled"]:
        return
    now = datetime.now(timezone.utc).astimezone(ET)
    today, m = now.date().isoformat(), now.hour * 60 + now.minute
    if now.weekday() > 4 or not (570 <= m < 960):
        return
    st = load()
    if st.get("date") != today:
        st.update({"date": today, "day_pnl": 0.0, "entered": False, "skipped": {}})
        st.setdefault("open", {})
    if _last["paper"] != today:
        if not bot.trade("GET", "/v2/account")["account_number"].startswith("PA"):
            return
        _last["paper"] = today
    for leg, p in list(st["open"].items()):  # fills, then stop / trail / 3:55
        if p.get("state") == "pending":
            od = bot.trade("GET", f"/v2/orders/{p['order_id']}")
            if od["status"] == "filled":
                p.update({"state": "open", "entry": float(od["filled_avg_price"]), "qty": float(od["filled_qty"])})
                p["peak"] = p["entry"]; p["stop"] = round(p["entry"] * (1 - p["stop_pct"] / 100), 2)
                log(f"F5BOOK {leg}: bought {p['qty']} {p['symbol']} @ {p['entry']} ({p['side']})")
            elif od["status"] in ("canceled", "expired", "rejected"):
                log(f"F5BOOK {leg}: entry {od['status']}"); st["open"].pop(leg)
            continue
        if p.get("state") == "closing":
            od = bot.trade("GET", f"/v2/orders/{p['close_id']}")
            if od["status"] == "filled":
                _book(st, leg, float(od["filled_avg_price"]), p["why"], log)
            elif od["status"] in ("canceled", "expired", "rejected"):
                p["state"] = "open"
            continue
        if _gone(bot, st, leg, p, log):  # another bot sold it (11/06: swing-off sold MSFT/GOOGL): book its real fill
            continue
        px = _px(bot, p["symbol"]); p["price"] = px; p["peak"] = max(p["peak"], px)
        p["stop"] = round(max(p["stop"], p["peak"] * (1 - p["trail_pct"] / 100)), 2)
        xm = min(p.get("exit_min", c["flat_min"]), c["flat_min"])
        why = "stop" if px <= p["stop"] else (f"{xm // 60 - 12}:{xm % 60:02} time exit" if xm < c["flat_min"] else "3:55 close") if m >= xm else None
        if why:
            od = bot.trade("POST", "/v2/orders", body={"symbol": p["symbol"], "qty": str(p["qty"]), "side": "sell",
                                                        "type": "market", "time_in_force": "day"})
            p.update({"state": "closing", "close_id": od["id"], "why": "profit kept" if why == "stop" and px > p["entry"] else why})
    if not st["entered"] and c["entry_min"] <= m < c["entry_min"] + 10 and now.second >= 3:
        cash = c["book_start"] + st.get("realized_total", 0)
        for lg in c["legs"]:
            f5 = _first5(bot, lg["sym"], today)
            if not f5:
                if m < c["entry_min"] + 2:
                    save(st); return  # the 9:34 bar may still be on its way: retry
                st["skipped"][lg["sym"]] = "no 9:30/9:34 bar"; continue
            up = f5[1] > f5[0]; sym = lg["up"] if up else lg["down"]
            if not sym:
                st["skipped"][lg["sym"]] = "down open, no short in a cash account"; continue
            usd = round(cash * lg["frac"], 2)  # dollar orders: Alpaca buys fractional shares, so a $150 leg can hold MSFT
            if usd < 1:
                st["skipped"][lg["sym"]] = f"book too small (${usd})"; continue
            od = bot.trade("POST", "/v2/orders", body={"symbol": sym, "notional": f"{usd:.2f}", "side": "buy", "type": "market",
                                                        "time_in_force": "day"})
            qty = usd
            st["open"][lg["sym"]] = {"state": "pending", "order_id": od["id"], "symbol": sym, "side": "up" if up else "down",
                                    "usd": usd, "qty": 0, "stop_pct": lg["stop"], "trail_pct": lg["trail"], "exit_min": lg.get("exit", c["flat_min"]), "at": now.isoformat()[:16],
                                    "first5": f"{f5[0]} -> {f5[1]}"}
            log(f"F5BOOK {lg['sym']} first 5 min {'up' if up else 'down'} ({f5[0]} -> {f5[1]}): buy ${usd:.2f} of {sym}")
        st["entered"] = True
    save(st)


def summary():
    c = cfg(); st = load(); pos = list((st.get("open") or {}).values())
    unreal = sum((p.get("price", p.get("entry", 0)) - p.get("entry", 0)) * p.get("qty", 0) for p in pos if p.get("entry"))
    eq = round(c["book_start"] + st.get("realized_total", 0) + unreal, 2)
    return {"name": "$1,500 first-5-minute book: TQQQ/SQQQ 70%, MSFT/GOOGL/NFLX 10% each (Alpaca paper)",
            "enabled": c["enabled"], "budget_start": c["book_start"], "equity": eq, "day_pnl": st.get("day_pnl", 0),
            "realized_total": st.get("realized_total", 0), "positions": pos, "trades": st.get("history", [])[-60:],
            "skipped": st.get("skipped", {}), "date": st.get("date"),
            "status": "off" if not c["enabled"] else ("in trades" if pos else "watching")}


def _loop(bot, log):
    while True:
        try:
            import sys
            sys.modules["f5book"].tick(bot, log)
            time.sleep(2)
        except Exception:
            log("f5book error:\n" + traceback.format_exc()[-600:])
            time.sleep(5)


def ensure_thread(bot, log):
    if not any(t.name == "f5book" for t in threading.enumerate()):
        threading.Thread(target=_loop, args=(bot, log), name="f5book", daemon=True).start()
        log("f5book loop started")
