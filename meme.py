"""Meme desk: memecoin trenching on PRACTICE money (Ryan 2026-10-10: "memecoin trading, like you're going to be
trenching"). Finds fresh Solana launches on pump.fun, confirms buying pressure on DexScreener, and paper-buys small
practice positions with a hard stop, a take-profit and a time stop. No wallet, no keys, no real orders: it only
reads public price data and writes meme_state.json. A background thread runs step() every 30 seconds, 24/7."""
import json, os, sys, threading, time, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "meme_state.json")
LOG = os.path.join(HERE, "meme-log.jsonl")
PUMP = "https://frontend-api-v3.pump.fun/coins?offset=0&limit=50&sort=last_trade_timestamp&order=DESC&includeNsfw=false"
DEX = "https://api.dexscreener.com/tokens/v1/solana/"
R = {  # trench rules v0 (practice money, tune from results)
    "start": 1000.0, "size": 25.0, "max_open": 4, "max_trades_day": 30,
    "age_min": 3, "age_max": 60,            # minutes since launch
    "mc_min": 15000, "mc_max": 80000,       # USD market cap at entry
    "m5_vol_min": 3000, "buy_ratio": 1.3,   # last 5 min: volume and buys vs sells
    "tp": 1.00, "sl": -0.30, "max_hold_min": 20,
    "cost_side": 0.04,                      # 1% fee + 3% slippage each way
    "every_s": 30,
}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def _now():
    return datetime.now(timezone.utc).isoformat()[:19] + "Z"


def load():
    st = {"start": R["start"], "cash": R["start"], "positions": [], "trades": [], "seen": [], "last_error": "",
          "last_scan": "", "candidates": []}
    try:
        st.update(json.load(open(STATE)))
    except Exception:
        pass
    return st


def save(st):
    st["seen"] = st["seen"][-500:]
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE)


def _log(event, row):
    with open(LOG, "a") as f:
        f.write(json.dumps({"t": _now(), "event": event, **row}) + "\n")


def _pairs(mints):
    """Best DexScreener pair per mint (highest liquidity; a pump.fun curve pair has none)."""
    out = {}
    for i in range(0, len(mints), 30):
        for p in _get(DEX + ",".join(mints[i:i + 30])) or []:
            m = p["baseToken"]["address"]
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if m not in out or liq > ((out[m].get("liquidity") or {}).get("usd") or 0):
                out[m] = p
    return out


def _close(st, pos, price, why):
    net = pos["usd"] * price / pos["entry"] * (1 - R["cost_side"])
    st["cash"] += net
    t = {**pos, "exit": price, "exit_at": _now(), "why": why, "pnl": round(net - pos["usd"], 2)}
    st["trades"].append(t)
    st["positions"].remove(pos)
    _log("sell", t)


def step():
    st = load()
    now_ms = time.time() * 1000
    try:
        try:
            coins = _get(PUMP)
        except Exception as e:  # pump.fun throttles sometimes: still manage open positions
            coins = []
            st["last_error"] = f"{_now()} pump.fun: {e}"
        fresh = [c for c in coins if not c.get("is_banned")
                 and R["age_min"] <= (now_ms - c["created_timestamp"]) / 60000 <= R["age_max"]
                 and R["mc_min"] <= (c.get("usd_market_cap") or 0) <= R["mc_max"]
                 and (c.get("twitter") or c.get("website") or c.get("telegram"))
                 and c["mint"] not in st["seen"]]
        held = [p["mint"] for p in st["positions"]]
        pairs = _pairs(list(dict.fromkeys(held + [c["mint"] for c in fresh])))
        for pos in list(st["positions"]):  # exits first
            p = pairs.get(pos["mint"])
            price = float(p["priceUsd"]) if p and p.get("priceUsd") else None
            held_min = (now_ms - pos["at_ms"]) / 60000
            if price is None:
                if held_min > R["max_hold_min"] + 10:
                    _close(st, pos, pos["last"] * 0.1, "no price (likely rug)")
                continue
            pos["last"] = price
            ch = price / pos["entry"] - 1
            if ch >= R["tp"]:
                _close(st, pos, price, "take profit")
            elif ch <= R["sl"]:
                _close(st, pos, price, "stop")
            elif held_min >= R["max_hold_min"]:
                _close(st, pos, price, "time")
        today = _now()[:10]
        n_today = sum(1 for t in st["trades"] + st["positions"] if t["at"][:10] == today)
        cands = []
        for c in fresh:
            p = pairs.get(c["mint"])
            if not p or not p.get("priceUsd"):
                continue
            tx = (p.get("txns") or {}).get("m5") or {}
            b, s = tx.get("buys", 0), tx.get("sells", 0)
            vol = (p.get("volume") or {}).get("m5") or 0
            chg = (p.get("priceChange") or {}).get("m5") or 0
            ok = vol >= R["m5_vol_min"] and b >= R["buy_ratio"] * max(s, 1) and chg > 0
            cands.append({"sym": c["symbol"][:12], "mint": c["mint"], "mc": round(c.get("usd_market_cap") or 0),
                          "m5_vol": round(vol), "buys": b, "sells": s, "chg5m": chg, "ok": ok})
            if ok and len(st["positions"]) < R["max_open"] and n_today < R["max_trades_day"] and st["cash"] >= R["size"]:
                price = float(p["priceUsd"])
                entry = price * (1 + R["cost_side"])  # pay fee and slippage on the way in
                pos = {"symbol": c["symbol"][:12], "mint": c["mint"], "entry": entry, "last": price, "usd": R["size"],
                       "qty": R["size"] / entry, "mc_entry": round(c.get("usd_market_cap") or 0), "at": _now(),
                       "at_ms": now_ms, "url": p.get("url", "")}
                st["cash"] -= R["size"]
                st["positions"].append(pos)
                st["seen"].append(c["mint"])
                n_today += 1
                _log("buy", pos)
        st["candidates"] = sorted(cands, key=lambda x: -x["m5_vol"])[:12]
        if coins:
            st["last_scan"] = _now()
            st["last_error"] = ""
    except Exception as e:
        st["last_error"] = f"{_now()} dexscreener: {e}"
    save(st)
    return st


def _loop():
    while True:
        try:
            mod = sys.modules.get("meme")
            (mod.step if mod and hasattr(mod, "step") else step)()  # newest code after a reload
        except Exception:
            pass
        time.sleep(R["every_s"])


def start():
    if not getattr(sys, "_meme_thread", None):
        sys._meme_thread = threading.Thread(target=_loop, daemon=True, name="meme-trench")
        sys._meme_thread.start()


def summary():
    start()
    st = load()
    equity = st["cash"] + sum(p["usd"] * p["last"] / p["entry"] for p in st["positions"])
    closed = st["trades"]
    wins = sum(1 for t in closed if t["pnl"] > 0)
    pos = [{"symbol": p["symbol"], "qty": p["qty"], "entry": p["entry"], "price": p["last"],
            "pnl": round(p["usd"] * (p["last"] / p["entry"] - 1), 2), "mc_entry": p["mc_entry"], "at": p["at"],
            "url": p.get("url", "")} for p in st["positions"]]
    best = max(closed, key=lambda t: t["pnl"], default=None)
    say = (f"Trenching: {len(pos)} open, {len(closed)} closed ({wins} wins)" +
           (f", best {best['symbol']} {best['pnl']:+.0f}" if best else "")) if closed or pos else "Scanning the trenches"
    return {"updated": _now(), "mode": "PAPER TRENCH", "note": "Practice money only. No wallet, no real orders.",
            "start": st["start"], "budget_start": st["start"], "equity": round(equity, 2),
            "total_pnl": round(equity - st["start"], 2), "positions": pos,
            "trades": [{k: t[k] for k in ("symbol", "entry", "exit", "pnl", "why", "at", "exit_at")} for t in closed[-30:]],
            "movers": [{"sym": c["sym"], "kind": "coin", "price": None, "mc": c["mc"], "chg24h": c["chg5m"],
                        "vol_x": None, "m5_vol": c["m5_vol"], "ok": c["ok"]} for c in st["candidates"]],
            "say": say, "last_scan": st["last_scan"], "last_error": st["last_error"], "rules": R}


if __name__ == "__main__":
    s = step()
    print(json.dumps({k: s[k] for k in ("cash", "positions", "candidates", "last_error")}, indent=1)[:3000])
