#!/usr/bin/env python3
"""Archie: momentum rotation bot for the Alpaca PAPER account.

Usage: python3 archie_bot.py candidates  # ranked names + research status (research these first)
       python3 archie_bot.py run         # rebalance / protect / log; buys only research-approved names
       python3 archie_bot.py status      # dry run, no orders
       python3 archie_bot.py log         # only log new fills to trade-log.csv
Settings live in rules.json next to this file so Archie can tune them weekly.
Paper only: the base URL is hard coded and the account number must start with PA.
"""
import csv, json, os, sys, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TRADE_API = "https://paper-api.alpaca.markets"
DATA_API = "https://data.alpaca.markets"
LOG = os.path.join(HERE, "trade-log.csv")
STATE = os.path.join(HERE, "state.json")
RESEARCH = os.path.join(HERE, "research.json")
RULES = json.load(open(os.path.join(HERE, "rules.json")))
HDR = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
       "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"],
       "Content-Type": "application/json"}


def api(method, base, path, params=None, body=None):
    url = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=HDR)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt else None
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode()[:300]}")


def trade(method, path, params=None, body=None):
    return api(method, TRADE_API, path, params, body)


def daily_closes(symbols, days=400):
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    out, token = {s: [] for s in symbols}, None
    while True:
        p = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": start,
             "limit": 10000, "adjustment": "all", "feed": "iex"}
        if token:
            p["page_token"] = token
        d = api("GET", DATA_API, "/v2/stocks/bars", p)
        for s, bars in (d.get("bars") or {}).items():
            out[s].extend(b["c"] for b in bars)
        token = d.get("next_page_token")
        if not token:
            return out


def sma(xs, n):
    return sum(xs[-n:]) / n


def rank(closes):
    r = RULES
    scored = []
    for s, c in closes.items():
        if len(c) < max(r["lookbacks"]) + 1 or len(c) < r["trend_sma"]:
            continue
        price = c[-1]
        if price < r["min_price"] or price < sma(c, r["trend_sma"]):
            continue
        score = sum(w * (price / c[-1 - lb] - 1) for lb, w in zip(r["lookbacks"], r["weights"]))
        scored.append((score, s, price))
    scored.sort(reverse=True)
    return scored


def research():
    return json.load(open(RESEARCH)) if os.path.exists(RESEARCH) else {}


def approved(sym, notes):
    """A buy needs a fresh 'buy' verdict from Archie's research with enough confidence."""
    r = notes.get(sym)
    if not r or r.get("verdict") != "buy" or r.get("confidence", 0) < RULES["min_confidence"]:
        return False
    age = (datetime.now(timezone.utc).date() - datetime.strptime(r["date"], "%Y-%m-%d").date()).days
    return age <= RULES["research_max_age_days"]


def candidates():
    """List the names the bot would buy and whether they still need research."""
    notes = research()
    ranked = rank(daily_closes(RULES["universe"]))
    out = [{"rank": i + 1, "symbol": s, "score_pct": round(sc * 100, 1), "price": p,
            "research": "approved" if approved(s, notes) else (notes.get(s, {}).get("verdict", "none") + " (stale/missing/low confidence)")}
           for i, (sc, s, p) in enumerate(ranked[:RULES["keep_rank"]])]
    fb = RULES["fallback"]
    out.append({"rank": "fallback", "symbol": fb, "research": "approved" if approved(fb, notes) else "needs research"})
    print(json.dumps(out, indent=1))


def log_fills(state):
    """Append every newly filled order to trade-log.csv (account=paper)."""
    seen = set(state.get("logged_orders", []))
    after = state.get("log_after", "2026-10-01T00:00:00Z")
    orders = trade("GET", "/v2/orders", {"status": "closed", "after": after, "limit": 500, "direction": "asc"})
    cost = state.setdefault("cost_basis", {})
    rows = []
    for o in orders:
        if o["status"] not in ("filled", "partially_filled") or o["id"] in seen or not o.get("filled_qty"):
            continue
        qty, px, sym = float(o["filled_qty"]), float(o["filled_avg_price"]), o["symbol"]
        pnl, closed = "", ""
        if o["side"] == "buy":
            held, avg = cost.get(sym, [0, 0])
            cost[sym] = [held + qty, (held * avg + qty * px) / (held + qty)]
        else:
            held, avg = cost.get(sym, [qty, px])
            pnl = round((px - avg) * qty, 2)
            left = held - qty
            closed = "yes" if left <= 1e-9 else "no"
            if left <= 1e-9:
                cost.pop(sym, None)
            else:
                cost[sym] = [left, avg]
        cid = o.get("client_order_id") or ""
        reason = cid.split("_")[1] if cid.startswith("archie_") else o["type"]
        rows.append([o["filled_at"][:19].replace("T", " ") + " UTC", "paper", sym, o["side"], qty, px,
                     o["type"], reason, o.get("stop_price") or "", closed, pnl, "order " + o["id"]])
        seen.add(o["id"])
    if rows:
        with open(LOG, "a", newline="") as f:
            csv.writer(f).writerows(rows)
    state["logged_orders"] = sorted(seen)
    return rows


def run(dry=False):
    acct = trade("GET", "/v2/account")
    assert acct["account_number"].startswith("PA"), "Not a paper account. Refusing to trade."
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    fills = log_fills(state)
    clock = trade("GET", "/v2/clock")
    positions = {p["symbol"]: p for p in trade("GET", "/v2/positions")}
    open_orders = trade("GET", "/v2/orders", {"status": "open", "limit": 500})
    budget = float(acct["equity"]) - (RULES["alpaca_start_equity"] - RULES["budget_start"])
    report = {"time": datetime.now(timezone.utc).isoformat()[:19], "market_open": clock["is_open"],
              "budget_equity": round(budget, 2), "new_fills": len(fills), "actions": []}

    closes = daily_closes(RULES["universe"])
    ranked = rank(closes)
    top = [s for _, s, _ in ranked[:RULES["max_positions"]]]
    keep_zone = {s for _, s, _ in ranked[:RULES["keep_rank"]]}
    report["top"] = [(s, round(sc * 100, 1)) for sc, s, _ in ranked[:10]]
    price = {s: c[-1] for s, c in closes.items() if c}

    def cancel_for(sym):
        for o in open_orders:
            if o["symbol"] == sym:
                if not dry:
                    trade("DELETE", f"/v2/orders/{o['id']}")

    pending = {o["symbol"] for o in open_orders if o["side"] == "buy"}
    # 1. Sell holdings that fell out of the keep zone or broke trend.
    notes = research()
    for sym, p in positions.items():
        if sym in keep_zone and notes.get(sym, {}).get("verdict") == "avoid" and \
                notes[sym].get("date", "") >= (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d"):
            keep_zone.discard(sym)
            report["actions"].append(f"SELL {sym} (research says avoid: {notes[sym].get('notes', '')[:80]})")
            if not dry:
                cancel_for(sym)
                trade("POST", "/v2/orders", body={"symbol": sym, "qty": p["qty"], "side": "sell", "type": "market",
                      "time_in_force": "day", "client_order_id": f"archie_research-exit_{datetime.now().timestamp():.0f}_{sym}"})
            continue
        if sym not in keep_zone:
            report["actions"].append(f"SELL {sym} (dropped out of top {RULES['keep_rank']} or broke trend)")
            if not dry:
                cancel_for(sym)
                trade("POST", "/v2/orders", body={"symbol": sym, "qty": p["qty"], "side": "sell", "type": "market",
                      "time_in_force": "day", "client_order_id": f"archie_rotate-out_{datetime.now().timestamp():.0f}_{sym}"})
    held = [s for s in positions if s in keep_zone] + [s for s in pending if s not in positions]
    # 2. Fill empty slots with the best ranked names (always invested).
    slot = budget / RULES["max_positions"]
    # Only researched, approved names get bought: walk down the ranking past anything unapproved.
    fill_list = [s for _, s, _ in ranked[:RULES["keep_rank"]] if s not in held and approved(s, notes)]
    skipped = [s for s in top if s not in held and not approved(s, notes)]
    if skipped:
        report["actions"].append(f"SKIP {', '.join(skipped)} (no fresh research approval)")
    for sym in fill_list[:RULES["max_positions"] - len(held)]:
        qty = int(slot // price[sym])
        if qty < 1:
            continue
        report["actions"].append(f"BUY {qty} {sym} (~${qty * price[sym]:.0f}, momentum rank)")
        if not dry:
            trade("POST", "/v2/orders", body={"symbol": sym, "qty": str(qty), "side": "buy", "type": "market",
                  "time_in_force": "day", "client_order_id": f"archie_momentum-entry_{datetime.now().timestamp():.0f}_{sym}"})
        held.append(sym)
    # Not enough qualifiers: park the rest in the fallback ETF so we stay in the market.
    empty = RULES["max_positions"] - len(held)
    fb = RULES["fallback"]
    if empty > 0 and fb not in held and not approved(fb, notes):
        report["actions"].append(f"HOLD CASH for {empty} slot(s): fallback {fb} not researched yet")
    elif empty > 0 and fb not in held:
        qty = int(slot * empty // price[fb])
        report["actions"].append(f"BUY {qty} {fb} (fallback, only {len(held)} names qualified)")
        if not dry and qty:
            trade("POST", "/v2/orders", body={"symbol": fb, "qty": str(qty), "side": "buy", "type": "market",
                  "time_in_force": "day", "client_order_id": f"archie_fallback_{datetime.now().timestamp():.0f}_{fb}"})
    # 3. Protect every filled position with a trailing stop that lives on Alpaca's servers.
    stops = {o["symbol"] for o in open_orders if o["side"] == "sell"}
    for sym, p in positions.items():
        if sym in keep_zone and sym not in stops and float(p["qty"]) >= 1:
            report["actions"].append(f"STOP {sym} trailing {RULES['trail_pct']}%")
            if not dry:
                trade("POST", "/v2/orders", body={"symbol": sym, "qty": str(int(float(p["qty"]))), "side": "sell",
                      "type": "trailing_stop", "trail_percent": str(RULES["trail_pct"]), "time_in_force": "gtc",
                      "client_order_id": f"archie_trailing-stop_{datetime.now().timestamp():.0f}_{sym}"})
    report["positions"] = {s: {"qty": p["qty"], "pl_pct": round(float(p["unrealized_plpc"]) * 100, 2)}
                           for s, p in positions.items()}
    state["log_after"] = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state.setdefault("runs", []).append({k: report[k] for k in ("time", "budget_equity", "actions")})
    state["runs"] = state["runs"][-200:]
    if not dry:
        json.dump(state, open(STATE, "w"), indent=1)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    cmd = (sys.argv[1:] or ["run"])[0]
    if cmd == "candidates":
        candidates()
    elif cmd == "log":  # log fills only; the server bot does the trading
        st = json.load(open(STATE)) if os.path.exists(STATE) else {}
        print(f"new fills logged: {len(log_fills(st))}")
        json.dump(st, open(STATE, "w"), indent=1)
    else:
        run(dry=cmd != "run")
