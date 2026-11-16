"""Real-money mirror for the options book (11/16 Reza: "Let's get archie on real options trading").

OFF unless rules.json options.live.enabled is true AND the live keys APCA_LIVE_KEY_ID / APCA_LIVE_SECRET_KEY are set
on the server. When on, every options-book entry is copied to the LIVE Alpaca account at a capped size, and the
paper book's exit (stop / profit lock / time) sells the live contracts too.

Hard limits (rules.json options.live): size_usd per trade, max_contracts, daily_loss_cap (no new live entry once the
live day P&L is at or below -cap), one live trade per day. Kill switch: create /etc/archie-live-off or set enabled false.
Never logs or prints keys.
"""
import json, os, time, urllib.request, urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

LIVE_API = "https://api.alpaca.markets"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "optlive-state.json")
KILL = "/etc/archie-live-off"
ET = ZoneInfo("America/New_York")


def cfg():
    try:
        return json.load(open(os.path.join(HERE, "rules.json")))["options"].get("live", {}) or {}
    except Exception:
        return {}


def _keys():
    k, s = os.environ.get("APCA_LIVE_KEY_ID"), os.environ.get("APCA_LIVE_SECRET_KEY")
    return (k, s) if k and s else None


def armed():
    c = cfg()
    return bool(c.get("enabled")) and _keys() is not None and not os.path.exists(KILL)


def _api(method, path, body=None):
    k, s = _keys()
    req = urllib.request.Request(LIVE_API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            t = r.read()
            return json.loads(t) if t else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"live {method} {path} -> {e.code} {e.read()[:200]!r}")


def load():
    today = datetime.now(ET).date().isoformat()
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {"history": [], "realized": 0}
    if st.get("date") != today:
        st.update({"date": today, "day_pnl": 0, "traded": False})
        if st.get("open") and st["open"].get("state") == "pending":
            st["open"] = None
    return st


def save(st):
    json.dump(st, open(STATE, "w"), indent=1)


def enter(osym, ask, why, log):
    """Copy a paper options entry to the live account, capped. Never raises."""
    try:
        if not armed():
            return
        c = cfg(); st = load()
        if st.get("traded") or st.get("open"):
            return
        if st.get("day_pnl", 0) <= -abs(c.get("daily_loss_cap", 0)):
            log(f"LIVE no entry: day loss cap hit ({st['day_pnl']:+.0f})"); return
        bal = c.get("budget_start", 1000) + st.get("realized", 0)  # 11/16 Reza: $1,000 starting balance
        cap = min(c.get("size_usd", 0), bal * c.get("max_frac", 0.8))
        qty = min(int(c.get("max_contracts", 1)), int(cap // (ask * 100))) if ask > 0 else 0
        if qty < 1:
            log(f"LIVE no entry: {osym} at {ask:.2f} is over the ${cap:.0f} per-trade cap (balance ${bal:.0f})"); return
        od = _api("POST", "/v2/orders", {"symbol": osym, "qty": str(qty), "side": "buy", "type": "limit",
                                          "limit_price": f"{ask:.2f}", "time_in_force": "day"})
        st["open"] = {"state": "pending", "order_id": od["id"], "symbol": osym, "qty": qty, "limit": ask,
                      "sent": time.time(), "why": why, "entry_time": datetime.now(ET).isoformat()[:16]}
        st["traded"] = True; save(st)
        log(f"LIVE buy {qty} {osym} limit {ask:.2f}")
    except Exception as e:
        log(f"LIVE enter error: {e}")


def exit(osym, bid, how, log):
    """Paper book decided to sell: sell the live contracts too. Never raises."""
    try:
        if _keys() is None:
            return
        st = load(); o = st.get("open")
        if not o or o["symbol"] != osym or o["state"] == "closing":
            return
        if o["state"] == "pending":
            _sync_entry(st, log); o = st.get("open")
            if not o or o["state"] != "open":
                return
        od = _api("POST", "/v2/orders", {"symbol": osym, "qty": str(o["qty"]), "side": "sell", "type": "limit",
                                          "limit_price": f"{bid:.2f}", "time_in_force": "day"})
        o.update({"state": "closing", "close_id": od["id"], "close_sent": time.time(), "how": how}); save(st)
        log(f"LIVE sell {o['qty']} {osym} limit {bid:.2f} ({how})")
    except Exception as e:
        log(f"LIVE exit error: {e}")


def _sync_entry(st, log):
    o = st["open"]; od = _api("GET", f"/v2/orders/{o['order_id']}")
    fq = int(float(od.get("filled_qty") or 0))
    if od["status"] == "filled" or (fq > 0 and time.time() - o["sent"] > 120):
        if od["status"] != "filled":
            _api("DELETE", f"/v2/orders/{o['order_id']}")
        o.update({"state": "open", "qty": fq, "entry": float(od["filled_avg_price"])})
        log(f"LIVE filled {fq} {o['symbol']} @ {o['entry']}")
    elif od["status"] in ("canceled", "expired", "rejected") or time.time() - o["sent"] > 120:
        if od["status"] not in ("canceled", "expired", "rejected"):
            _api("DELETE", f"/v2/orders/{o['order_id']}")
        log(f"LIVE entry not filled ({od['status']}), no live trade today"); st["open"] = None
    save(st)


def tick(quote, log):
    """Each loop: track the live entry fill, re-price an unfilled sell, and never hold past 3:55pm ET."""
    try:
        if _keys() is None:
            return
        st = load(); o = st.get("open")
        if not o:
            return
        if o["state"] == "pending":
            _sync_entry(st, log); return
        now = datetime.now(ET); late = now.hour * 60 + now.minute >= 955
        if o["state"] == "open" and late:
            bid, _ = quote(o["symbol"]); exit(o["symbol"], bid, "3:55 safety", log); return
        if o["state"] == "closing":
            od = _api("GET", f"/v2/orders/{o['close_id']}")
            if od["status"] == "filled":
                x = float(od["filled_avg_price"]); pnl = round((x - o["entry"]) * 100 * o["qty"], 2)
                st["history"].append({**{k: o.get(k) for k in ("symbol", "qty", "entry", "entry_time", "how")},
                                      "exit": x, "exit_time": now.isoformat()[:16], "pnl": pnl})
                st["realized"] = round(st.get("realized", 0) + pnl, 2); st["day_pnl"] = round(st.get("day_pnl", 0) + pnl, 2)
                st["open"] = None; save(st); log(f"LIVE closed {o['symbol']} @ {x} {pnl:+.2f}")
            elif time.time() - o["close_sent"] > 60 and od["status"] in ("new", "accepted", "partially_filled"):
                _api("DELETE", f"/v2/orders/{o['close_id']}")
                o["state"] = "open"; save(st)
                bid, _ = quote(o["symbol"]); exit(o["symbol"], bid, o.get("how", "retry"), log)
    except Exception as e:
        log(f"LIVE tick error: {e}")


def summary():
    c = cfg(); st = load()
    return {"armed": armed(), "budget_start": c.get("budget_start"), "balance": round(c.get("budget_start", 0) + st.get("realized", 0), 2), "keys": _keys() is not None, "size_usd": c.get("size_usd"), "max_contracts": c.get("max_contracts"),
            "daily_loss_cap": c.get("daily_loss_cap"), "day_pnl": st.get("day_pnl", 0), "realized": st.get("realized", 0),
            "open": st.get("open"), "trades": st.get("history", [])[-20:]}
