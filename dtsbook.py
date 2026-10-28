"""QQQ day-trade book run by Jake Schincariol's day-trader-skill (Ryan 2026-10-28: "take any trade possible on QQQ and run
it through this to see if its good, and if it says take the trade then do it, if not dont (obv paper)"; "from market open
to market close, stop on $750 profit and $250").
Every minute 9:35-15:30 ET each QQQ setup the pack knows (long only, as the pack is) goes through its own tools, unmodified
(vendor/day-trader-skill):
  trader-entry/entry.py -> triggered?  trader-exit/exit.py -> stop + target  trader-timemachine -> 10,000 replays
  trader-budget/size.py -> bracket order  trader-risk/check.py -> PASS / BLOCK
It trades only when the Time Machine verdict leans or strongly leans toward the target AND the risk check passes, as one
Alpaca PAPER bracket order (stop + target at the broker). Day stop: +$750 or -$250 (realized + open) flattens and ends the
day; each trade risks at most $125 and never more than the room left to -$250. Flat by 15:55.
optbot.step starts this file's thread (ensure_thread) and reloads the module, so new code needs no restart."""
import json, os, subprocess, sys, threading, time, traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__)); ET = ZoneInfo("America/New_York")
PACK = os.path.join(HERE, "vendor", "day-trader-skill")
WORK = os.path.join(HERE, "dts", "book")
STATE = os.path.join(HERE, "dtsbook-state.json")
SYM = "QQQ"
DEFAULTS = {"enabled": True, "budget": 60000, "risk_per_trade": 125, "day_goal": 750, "day_brake": 250,
            "max_trades": 10, "first_min": 575, "last_min": 930, "flat_min": 955, "level_stop": "pct:0.4",
            "go": ["Leans toward the target", "Strongly toward the target"]}
SETUPS = [("orb15", ["--setup", "orb"]), ("orb5", ["--setup", "orb", "--or-minutes", "5"]),
          ("orb30", ["--setup", "orb", "--or-minutes", "30"]), ("pdh", ["--setup", "level"])]
_last = globals().get("_last") or {"min": None, "paper": None}


def cfg():
    try:
        c = json.load(open(os.path.join(HERE, "rules.json"))).get("dtsbook", {})
    except Exception:
        c = {}
    return {**DEFAULTS, **c}


def load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"date": None, "history": [], "realized_total": 0.0}


def save(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=1); os.replace(tmp, STATE)


def run(tool, args, cwd, timeout=60):
    p = subprocess.run([sys.executable, "-I", os.path.join(PACK, tool)] + args, cwd=cwd,
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def _wj(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(obj, open(path, "w"), indent=1)


def evaluate(name, args, c, room, cash, placed, today, now_s=None, log=print):
    """Runs one setup through the whole pack. Returns (decision dict, bracket order or None)."""
    d = os.path.join(WORK, name); t = os.path.join(d, "trader"); os.makedirs(os.path.join(t, "state"), exist_ok=True)
    now = ["--now", now_s] if now_s else []
    risk = round(min(c["risk_per_trade"], room), 2)
    _wj(f"{t}/config.json", {"budget": cash, "cash_buffer_pct": 0, "max_position_pct": 100, "max_order_usd": cash,
                             "min_order_usd": 5, "max_loss_per_trade_usd": risk, "max_daily_loss_usd": 100000,
                             "max_trades_per_day": c["max_trades"], "max_orders_per_day": c["max_trades"] * 3,
                             "min_reward_risk": 1.0, "require_stop": True, "no_entries_before": "09:35",
                             "no_entries_after": "15:30", "flat_by": "15:55", "fractional": False, "broker": "alpaca",
                             "paper": True, "allow_options": False, "allow_margin": False, "allow_short": False})
    _wj(f"{t}/portfolio.json", {"cash": cash, "positions": []})
    _wj(f"{t}/state/broker.json", {"orders": [{"status": "placed", "date": today, "symbol": SYM, "side": "buy",
                                               "kind": "entry"} for _ in range(placed)]})
    rc, out, err = run("trader-entry/entry.py", ["check", SYM] + args + now + ["--write", "trader/entry.json", "--json"], d)
    e = json.loads(out)
    if e.get("status") != "triggered":
        return {"setup": name, "step": "entry", "status": e.get("status"), "why": e.get("reason")}, None
    tr = e["trigger"]
    hh, mm = map(int, tr["close_time"].split(":"))
    clock = datetime.strptime(now_s, "%Y-%m-%d %H:%M") if now_s else datetime.now(timezone.utc).astimezone(ET)
    if clock.hour * 60 + clock.minute - (hh * 60 + mm) > 10:  # an old trigger (bot started late): the price is gone
        return {"setup": name, "step": "entry", "status": "stale", "why": f"triggered at {tr['time']}, too long ago"}, None
    run("trader-exit/exit.py", ["plan"] + (["--stop", c["level_stop"]] if "level" in args else []) + now + ["--write"], d)
    rc, out, err = run("trader-timemachine/timemachine.py", ["run", "--until", "15:55"] + now + ["--write", "--json"], d, 120)
    tm = json.loads(out); v = tm.get("verdict", "")
    dec = {"setup": name, "trigger": f"{tr['time']} close {tr['price']}", "verdict": v,
           "odds": {k: tm["mc"].get(k) for k in ("p_target", "p_stop", "p_neither")} if tm.get("mc") else None,
           "history": {k: (tm.get("history") or {}).get(k) for k in ("sessions", "triggered", "target", "stop", "close", "avg_R")}}
    if not any(v.startswith(g) for g in c["go"]):
        dec.update(step="timemachine", go=False); return dec, None
    run("trader-budget/size.py", now + ["--write"], d)
    rc, out, err = run("trader-risk/check.py", now + ["--no-write", "--json"], d)
    r = json.loads(out)["results"][0]
    dec.update(step="risk", risk=r.get("verdict"), why=r.get("reasons"), max_loss=r.get("max_loss"))
    if r.get("verdict") != "PASS":
        dec["go"] = False; return dec, None
    o = json.load(open(f"{t}/orders.json"))["orders"][0]
    dec["go"] = True
    return dec, o


def _alpaca_bracket(o):
    return {"symbol": o["symbol"], "qty": str(o["qty"]), "side": "buy", "type": "market", "time_in_force": "day",
            "order_class": "bracket", "take_profit": {"limit_price": str(o["take_profit_limit_price"])},
            "stop_loss": {"stop_price": str(o["stop_loss_stop_price"])}}


def _unreal(bot):
    try:
        return float(bot.trade("GET", f"/v2/positions/{SYM}").get("unrealized_pl", 0))
    except Exception:
        return 0.0


def _flatten(bot, st, why, log):
    for od in bot.trade("GET", "/v2/orders", {"status": "open", "symbols": SYM}) or []:
        try:
            bot.trade("DELETE", f"/v2/orders/{od['id']}")
        except Exception:
            pass
    o = st.get("open")
    if o and o.get("entry"):
        time.sleep(1)
        try:
            cl = bot.trade("DELETE", f"/v2/positions/{SYM}")
            o["close_id"] = (cl or {}).get("id")
        except Exception as e:
            log(f"DTSBOOK flatten error: {e}")
        o["close_why"] = why
    elif o:
        st["open"] = None
    log(f"DTSBOOK flatten: {why}")


def _manage(bot, st, log):
    """Reads the open bracket's fills; books P&L when it is out."""
    o = st.get("open")
    if not o:
        return
    if o.get("close_id"):
        od = bot.trade("GET", f"/v2/orders/{o['close_id']}")
        if od.get("status") == "filled":
            _book(st, o, float(od["filled_avg_price"]), o.get("close_why", "closed"), log)
        return
    od = bot.trade("GET", f"/v2/orders/{o['order_id']}", {"nested": "true"})
    if od.get("status") in ("canceled", "expired", "rejected") and not o.get("entry"):
        log(f"DTSBOOK entry {od.get('status')}"); st["open"] = None; return
    if od.get("filled_avg_price") and not o.get("entry"):
        o["entry"] = float(od["filled_avg_price"]); log(f"DTSBOOK filled {o['qty']} {SYM} @ {o['entry']}")
    for leg in od.get("legs") or []:
        if leg.get("status") == "filled":
            _book(st, o, float(leg["filled_avg_price"]), "target" if leg.get("type") == "limit" else "stop", log)
            return


def _book(st, o, px, why, log):
    pnl = round((px - o["entry"]) * int(o["qty"]), 2)
    st["day_pnl"] = round(st.get("day_pnl", 0) + pnl, 2); st["realized_total"] = round(st.get("realized_total", 0) + pnl, 2)
    st["history"].append({**{k: o.get(k) for k in ("setup", "qty", "entry", "stop", "target", "at", "verdict")},
                          "exit": px, "why_out": why, "pnl": pnl, "out_at": datetime.now(timezone.utc).astimezone(ET).isoformat()[:16]})
    st["history"] = st["history"][-200:]; st["open"] = None
    log(f"DTSBOOK out ({why}) @ {px}: {pnl:+.2f}, day {st['day_pnl']:+.2f}")


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
        st.update({"date": today, "day_pnl": 0.0, "placed": 0, "done": False, "why_done": "", "setups": {}, "open": None,
                   "pdh": None, "decisions": []})
    if _last["paper"] != today:
        if not bot.trade("GET", "/v2/account")["account_number"].startswith("PA"):
            return
        _last["paper"] = today
    _manage(bot, st, log)
    if st.get("open") and st["open"].get("close_id"):
        save(st); return
    total = st["day_pnl"] + (_unreal(bot) if st.get("open") else 0)
    if not st["done"]:
        why = (f"day goal +${c['day_goal']} hit" if total >= c["day_goal"] else
               f"day brake -${c['day_brake']} hit" if total <= -c["day_brake"] else
               "flat by 3:55 PM" if m >= c["flat_min"] else None)
        if why:
            st["done"], st["why_done"] = True, why
            if st.get("open"):
                _flatten(bot, st, why, log)
            save(st); return
    if st["done"] or st.get("open") or _last["min"] == m or not (c["first_min"] <= m <= c["last_min"]) or now.second < 20:
        save(st); return
    _last["min"] = m
    if st["placed"] >= c["max_trades"]:
        save(st); return
    if st.get("pdh") is None:
        try:
            b = bot.api("GET", bot.DATA_API, f"/v2/stocks/{SYM}/bars", {"timeframe": "1Day", "limit": 5, "feed": "iex",
                                                                       "end": now.date().isoformat()})["bars"]
            st["pdh"] = [x for x in b if x["t"][:10] < today][-1]["h"]
        except Exception as e:
            log(f"DTSBOOK prior-day high: {e}")
    room = c["day_brake"] + st["day_pnl"]
    try:
        cash = min(c["budget"], float(bot.trade("GET", "/v2/account")["cash"]))
    except Exception:
        cash = c["budget"]
    for name, args in SETUPS:
        if st["setups"].get(name):
            continue
        if name == "pdh":
            if not st.get("pdh"):
                continue
            args = args + ["--level", str(st["pdh"])]
        try:
            dec, order = evaluate(name, args, c, room, cash, st["placed"], today, log=log)
        except Exception as e:
            log(f"DTSBOOK {name} error: {e}"); continue
        if dec.get("step") == "entry":
            if dec.get("status") in ("expired", "stale"):
                st["setups"][name] = dec["status"]
            continue
        st["setups"][name] = "go" if dec.get("go") else "no"
        dec["at"] = now.isoformat()[11:16]; st["decisions"].append(dec)
        log(f"DTSBOOK {name}: {dec.get('verdict', '')[:60]} -> {'TAKE' if dec.get('go') else 'pass'}")
        if dec.get("go") and order:
            od = bot.trade("POST", "/v2/orders", body=_alpaca_bracket(order))
            st["open"] = {"order_id": od["id"], "setup": name, "qty": order["qty"], "stop": order["stop_loss_stop_price"],
                          "target": order["take_profit_limit_price"], "at": now.isoformat()[:16], "verdict": dec["verdict"][:80]}
            st["placed"] += 1
            log(f"DTSBOOK TAKE {name}: buy {order['qty']} {SYM}, stop {order['stop_loss_stop_price']}, target {order['take_profit_limit_price']}")
            break
    save(st)


def summary():
    c = cfg(); st = load(); o = st.get("open") or {}
    return {"name": "QQQ day trades picked by day-trader-skill (Alpaca paper)", "enabled": c["enabled"],
            "day_goal": c["day_goal"], "day_brake": c["day_brake"], "day_pnl": st.get("day_pnl", 0),
            "realized_total": st.get("realized_total", 0), "positions": [o] if o else [],
            "trades": st.get("history", [])[-50:], "decisions": st.get("decisions", [])[-20:],
            "status": "off" if not c["enabled"] else st.get("why_done") or ("in a trade" if o else "watching"),
            "date": st.get("date")}


def _loop(bot, log):
    while True:
        try:
            sys.modules["dtsbook"].tick(bot, log)
        except Exception:
            log("dtsbook error:\n" + traceback.format_exc()[-600:])
        time.sleep(5)


def ensure_thread(bot, log):
    if not any(t.name == "dtsbook" for t in threading.enumerate()):
        threading.Thread(target=_loop, args=(bot, log), name="dtsbook", daemon=True).start()
        log("dtsbook loop started")
