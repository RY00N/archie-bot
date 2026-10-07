"""Publish Archie's live PAPER P&L snapshot for the Agent City page (runs on the server).

Every call writes pnl.json (equity, today's and total P&L, positions; never keys or the
account number) to the `live` branch of the bot's GitHub repo as a single force-pushed
commit, then asks jsDelivr to drop its cached copy. The city page reads:
  https://cdn.jsdelivr.net/gh/<repo>@live/pnl.json   (fresh right after each purge)
  https://raw.githubusercontent.com/<repo>/live/pnl.json   (fallback, up to 5 min old)
Needs GITHUB_TOKEN (fine-grained, this repo only, Contents read/write) in /etc/archie.env.
Without it, publishing is skipped and trading is unaffected.
"""
import csv, json, os, re, subprocess, threading, urllib.request
import daytrader
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PUB_DIR = os.path.expanduser("~/pnl-pub")
_busy = threading.Lock()
_warned = False
_pushes = 0


def repo_slug():
    url = subprocess.run(["git", "-C", HERE, "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    return m.group(1) if m else None


def _pos(p):
    return {"symbol": p["symbol"], "qty": float(p["qty"]), "value": round(float(p["market_value"]), 2),
            "entry": round(float(p["avg_entry_price"]), 2), "price": round(float(p["current_price"]), 2),
            "cost": round(float(p["cost_basis"]), 2),
            "pnl": round(float(p["unrealized_pl"]), 2), "pnl_pct": round(float(p["unrealized_plpc"]) * 100, 2),
            "day_pnl": round(float(p["unrealized_intraday_pl"]), 2)}


def _swing_log():
    """From the server's trade-log.csv: first buy date per held symbol, and every closed swing sale."""
    opened, trades = {}, []
    try:
        with open(os.path.join(HERE, "trade-log.csv")) as f:
            for r in csv.DictReader(f):
                if r.get("account") != "paper":
                    continue
                sym, qty, px = r["symbol"], float(r["qty"]), float(r["price"])
                if r["side"] == "buy":
                    opened.setdefault(sym, r["date_time_et"][:16])
                elif r.get("realized_pnl"):
                    pnl = float(r["realized_pnl"])
                    trades.append({"date": r["date_time_et"][:10], "opened": opened.get(sym, "")[:10], "symbol": sym,
                                   "qty": qty, "entry": round(px - pnl / qty, 2), "exit": round(px, 2), "pnl": round(pnl, 2),
                                   "exit_reason": r.get("reason") or r.get("order_type", "")})
                    if r.get("position_closed") == "yes":
                        opened.pop(sym, None)
    except FileNotFoundError:
        pass
    return opened, trades[-200:]


_opened_cache = {}


def _swing_positions(bot, positions, skip):
    """Swing holdings with entry, current price, open date and the trailing stop protecting each."""
    stops = {}
    try:
        for o in bot.trade("GET", "/v2/orders", {"status": "open", "limit": 500}):
            if o["type"] == "trailing_stop" and o["side"] == "sell":
                stops[o["symbol"]] = {"stop": round(float(o["stop_price"]), 2) if o.get("stop_price") else None,
                                      "trail_pct": float(o["trail_percent"]) if o.get("trail_percent") else None}
    except Exception:
        pass
    opened, _ = _swing_log()
    for p in positions:  # buys made before the server's log existed: ask Alpaca once, then cache
        sym = p["symbol"]
        if sym in skip or opened.get(sym):
            continue
        if sym not in _opened_cache:
            try:
                buys = [o for o in bot.trade("GET", "/v2/orders", {"status": "closed", "symbols": sym, "limit": 50, "direction": "desc"})
                        if o["side"] == "buy" and o.get("filled_at")]
                _opened_cache[sym] = buys[-1]["filled_at"][:16].replace("T", " ") if buys else ""
            except Exception:
                _opened_cache[sym] = ""
        opened[sym] = _opened_cache[sym]
    return [{**_pos(p), "opened": opened.get(p["symbol"], ""), **stops.get(p["symbol"], {})}
            for p in positions if p["symbol"] not in skip]


_chart_cache = {}


def _bars(bot, sym, start, end):
    """[[minute UTC, close], ...] from start-5m to end+5m (end None = now), at most ~120 points."""
    t0 = datetime.fromisoformat(start.replace("Z", "+00:00")) - timedelta(minutes=5)
    t1 = (datetime.fromisoformat(end.replace("Z", "+00:00")) + timedelta(minutes=6)) if end else datetime.now(timezone.utc)
    d = bot.api("GET", bot.DATA_API, "/v2/stocks/bars", {"symbols": sym, "timeframe": "1Min", "feed": "iex", "limit": 10000,
                "start": t0.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": t1.strftime("%Y-%m-%dT%H:%M:%SZ")})
    pts = [[b["t"][:16] + "Z", round(b["c"], 2)] for b in (d.get("bars") or {}).get(sym, [])]
    step = max(1, -(-len(pts) // 120))
    return pts[::step] + ([pts[-1]] if pts and (len(pts) - 1) % step else [])


def _fill_times(bot, t):
    """Older history records have no times: find the day's buy and sell fills for that symbol once."""
    try:
        os_ = [o for o in bot.trade("GET", "/v2/orders", {"status": "closed", "symbols": t["symbol"], "limit": 100,
                                                            "after": t["date"] + "T00:00:00Z", "nested": "true"})]
        fills = []
        for o in os_:
            fills += [o] + (o.get("legs") or [])
        fills = [f for f in fills if f.get("filled_at") and f["filled_at"][:10] == t["date"]]
        buys = sorted(f["filled_at"][:19] for f in fills if f["side"] == "buy")
        sells = sorted(f["filled_at"][:19] for f in fills if f["side"] == "sell")
        if buys and sells:
            return buys[0] + "Z", sells[-1] + "Z"
    except Exception:
        pass
    return None, None


def _day_charts(bot, day):
    """Price path for every day trade: open ones refresh each publish, closed ones are fetched once."""
    for p in day["positions"]:
        if p.get("entry_time"):
            try:
                p["chart"] = _bars(bot, p["symbol"], p["entry_time"], None)
            except Exception:
                pass
    for t in day.get("trades", [])[-30:]:
        k = f'{t["symbol"]}|{t["date"]}|{t["exit"]}'
        if k not in _chart_cache:
            if not t.get("entry_time"):
                t["entry_time"], t["exit_time"] = _fill_times(bot, t)
            try:
                _chart_cache[k] = (t["entry_time"], t["exit_time"], _bars(bot, t["symbol"], t["entry_time"], t["exit_time"])) \
                    if t.get("entry_time") and t.get("exit_time") else (None, None, [])
            except Exception:
                continue
        t["entry_time"], t["exit_time"], t["chart"] = _chart_cache[k]
    day["recent_trades"] = day.get("trades", [])[-10:]


def _book(start, equity, day_pnl, positions):
    return {"budget_start": start, "equity": round(equity, 2), "day_pnl": round(day_pnl, 2),
            "day_pnl_pct": round(day_pnl / (equity - day_pnl) * 100, 2) if equity - day_pnl else 0.0,
            "total_pnl": round(equity - start, 2), "total_pnl_pct": round((equity / start - 1) * 100, 2),
            "positions": positions}


def snapshot(bot, market_open):
    """Top level = swing + day combined; "swing" and "day" hold each book on its own."""
    acct = bot.trade("GET", "/v2/account")
    positions = bot.trade("GET", "/v2/positions")
    day = daytrader.summary(positions)
    _day_charts(bot, day)
    day_syms = {p["symbol"] for p in day["positions"]}
    swing_start, day_start = bot.RULES["budget_start"], bot.RULES["day"]["budget_start"]
    swing_eq = bot.swing_equity(acct, positions)
    acct_day_change = float(acct["equity"]) - float(acct["last_equity"])
    swing = _book(swing_start, swing_eq, acct_day_change - day["day_pnl"], _swing_positions(bot, positions, day_syms))
    swing["trades"] = _swing_log()[1]
    combined = _book(swing_start + day_start, swing_eq + day["equity"], acct_day_change,
                     [_pos(p) for p in positions])
    return {"book": "paper", "note": "Practice money. Never counts toward real totals.",
            "updated": datetime.now(timezone.utc).isoformat()[:19] + "Z", "market_open": market_open,
            **combined, "swing": swing, "day": day}


def _git(*args):
    return subprocess.run(["git", "-C", PUB_DIR, *args], capture_output=True, text=True, timeout=60)


def _push(snap, token, slug):
    os.makedirs(PUB_DIR, exist_ok=True)
    if not os.path.isdir(os.path.join(PUB_DIR, ".git")):
        _git("init", "-q")
        _git("symbolic-ref", "HEAD", "refs/heads/live")
    json.dump(snap, open(os.path.join(PUB_DIR, "pnl.json"), "w"), indent=1)
    _git("update-ref", "-d", "HEAD")  # new root commit each time: the branch stays one commit long
    _git("add", "pnl.json")
    _git("-c", "user.name=Archie", "-c", "user.email=archie@localhost", "commit", "-q", "-m", "P&L " + snap["updated"])
    r = _git("push", "-q", "-f", f"https://x-access-token:{token}@github.com/{slug}.git", "HEAD:refs/heads/live")
    if r.returncode:
        raise RuntimeError("push failed: " + r.stderr.replace(token, "***")[:300])
    global _pushes
    _pushes += 1
    if _pushes % 200 == 0:
        _git("gc", "-q", "--prune=now")  # drop the old snapshot objects
    try:
        urllib.request.urlopen(f"https://purge.jsdelivr.net/gh/{slug}@live/pnl.json", timeout=20).read()
    except Exception:
        pass  # raw.githubusercontent.com still serves it


def publish(bot, market_open, log):
    """Build and push a snapshot in the background so trading never waits on GitHub."""
    global _warned
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        if not _warned:
            log("P&L publish off: add GITHUB_TOKEN to /etc/archie.env to send live P&L to the Agent City")
            _warned = True
        return
    if not _busy.acquire(blocking=False):
        return

    def work():
        try:
            _push(snapshot(bot, market_open), token, repo_slug())
        except Exception as e:
            log(f"P&L publish error: {e}")
        finally:
            _busy.release()
    threading.Thread(target=work, daemon=True).start()
