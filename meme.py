"""Meme desk (Ryan 2026-10-10: "add a meme trading floor"). PAPER practice book, WATCH mode: it only reads prices
for meme coins and meme stocks from Alpaca market data and reports the movers. It never places orders."""
import json, os, time, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "meme_state.json")
COINS = ["DOGE", "SHIB", "PEPE", "BONK", "WIF", "TRUMP"]
STOCKS = ["GME", "AMC", "BB", "HOOD", "PLTR", "RDDT"]
DATA = "https://data.alpaca.markets"
_cache = {"t": 0, "movers": [], "err": ""}


def _get(path):
    hdr = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"], "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]}
    return json.loads(urllib.request.urlopen(urllib.request.Request(DATA + path, headers=hdr), timeout=20).read())


def _row(sym, kind, snap):
    price = (snap.get("latestTrade") or {}).get("p")
    prev, day = snap.get("prevDailyBar") or {}, snap.get("dailyBar") or {}
    if not price or not prev.get("c"):
        return None
    vol_x = round(day.get("v", 0) / prev["v"], 2) if prev.get("v") else None
    return {"sym": sym, "kind": kind, "price": price, "chg24h": round((price / prev["c"] - 1) * 100, 2), "vol_x": vol_x}


def movers():
    """Meme coins and stocks sorted by the biggest move, refreshed at most every 5 minutes."""
    if time.time() - _cache["t"] < 300:
        return _cache["movers"], _cache["err"]
    rows, err = [], ""
    try:
        c = _get("/v1beta3/crypto/us/snapshots?symbols=" + ",".join(s + "/USD" for s in COINS))["snapshots"]
        rows += [_row(s, "coin", c[s + "/USD"]) for s in COINS if s + "/USD" in c]
    except Exception as e:
        err = f"coins: {e}"
    try:
        s = _get("/v2/stocks/snapshots?feed=iex&symbols=" + ",".join(STOCKS))
        rows += [_row(k, "stock", s[k]) for k in STOCKS if k in s]
    except Exception as e:
        err = (err + "; " if err else "") + f"stocks: {e}"
    rows = sorted((r for r in rows if r), key=lambda r: -abs(r["chg24h"]))
    _cache.update(t=time.time(), movers=rows or _cache["movers"], err=err)
    return _cache["movers"], err


def summary():
    st = {"start": 1000.0, "equity": 1000.0, "positions": [], "trades": []}
    try:
        st.update(json.load(open(STATE)))
    except Exception:
        pass
    rows, err = movers()
    top = rows[0] if rows else None
    say = f"Watching {len(rows)} memes. Biggest mover: {top['sym']} {top['chg24h']:+.1f}%" if top else "Warming up"
    return {"updated": datetime.now(timezone.utc).isoformat()[:19] + "Z", "mode": "WATCH",
            "note": "Practice money only. Watch mode: no trades yet.", "start": st["start"], "budget_start": st["start"], "equity": st["equity"], "total_pnl": round(st["equity"] - st["start"], 2),
            "positions": st["positions"], "trades": st["trades"], "movers": rows, "say": say, "last_error": err}


if __name__ == "__main__":
    print(json.dumps(summary(), indent=1))
