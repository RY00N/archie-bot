"""Archie's hookup to Jake Schincariol's day-trader-skill (Ryan 2026-10-28: "not copy it but actually have it use it").
The pack is installed unmodified in vendor/day-trader-skill (MIT, pinned commit in PINNED.txt) and run as-is:
  - risk_check(): trader-risk/check.py checks the exact order optbot is about to send. BLOCK = no trade.
    Its sizing math is per share, so an option order goes in with prices x100 (one contract = 100 shares).
    If the tool itself breaks (crash, timeout) the trade goes ahead and the error is logged.
  - news(): trader-news/news.py pulls Yahoo headlines and the move for QQQ and its biggest holdings once a day
    at the 9:30 open, in its own thread. Info only: it never gates a trade (no backtest shows a news rule helps yet).
Stdlib only; the tools read public data and never see the Alpaca keys."""
import json, os, subprocess, sys, tempfile, time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__)); ET = ZoneInfo("America/New_York")
PACK = os.path.join(HERE, "vendor", "day-trader-skill")
WORK = os.path.join(HERE, "dts")  # the pack's trader/ folder: its state files live here
NEWS_TICKERS = ["QQQ", "NVDA", "MSFT", "AAPL", "AMZN", "META", "GOOGL", "AVGO", "TSLA"]
_last = globals().get("_last") or {"news_day": None, "news": None, "risk": None}


def _run(tool, args, timeout):
    os.makedirs(os.path.join(WORK, "state"), exist_ok=True)
    p = subprocess.run([sys.executable, "-I", os.path.join(PACK, tool)] + args, cwd=WORK,
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def risk_check(osym, qty, limit, stop_pct, cash, max_trades, log, unit=100, now=None):
    """True = send the order. Runs the pack's pre-trade risk check on this exact order."""
    order = {"symbol": osym, "side": "buy", "qty": str(qty), "type": "limit", "time_in_force": "day",
             "limit_price": f"{limit * unit:.2f}", "plan": {"entry": round(limit * unit, 2),
                                                             "stop": round(limit * unit * (1 - stop_pct / 100), 2)}}
    config = {"budget": cash, "cash_buffer_pct": 0, "max_position_pct": 100, "max_order_usd": cash,
              "min_order_usd": 5, "max_loss_per_trade_usd": round(cash * stop_pct / 100, 2) + 1,
              "max_daily_loss_usd": round(cash * stop_pct / 100, 2) + 1, "max_trades_per_day": max_trades,
              "max_orders_per_day": max_trades * 4, "min_reward_risk": 0, "require_stop": True,
              "no_entries_before": "09:35", "no_entries_after": "10:00", "flat_by": "15:55",
              "paper": True, "blocked": [], "allow_options": True, "allow_margin": False, "allow_short": False}
    try:
        with tempfile.TemporaryDirectory() as d:
            files = {"orders": [order], "config": config, "portfolio": {"cash": cash, "positions": []}}
            for k, v in files.items():
                with open(os.path.join(d, k + ".json"), "w") as f:
                    json.dump(v, f)
            rc, out, err = _run("trader-risk/check.py", ["--orders", f"{d}/orders.json", "--config", f"{d}/config.json",
                                                         "--portfolio", f"{d}/portfolio.json", "--prices", f"{d}/none.json",
                                                         "--state", f"{d}/none.json", "--no-write", "--json"] + (["--now", now] if now else []), 20)
        res = json.loads(out)
        v = res["results"][0]
    except Exception as e:
        log(f"DTS risk check broke ({e}); trading anyway"); _last["risk"] = {"at": _now(), "error": str(e)[:200]}
        return True
    ok = v.get("verdict") == "PASS"
    _last["risk"] = {"at": _now(), "order": f"buy {qty} {osym} @ {limit:.2f}", "verdict": "PASS" if ok else "BLOCK",
                     "why": v.get("reasons") or [], "max_loss": v.get("max_loss")}
    log(f"DTS risk {_last['risk']['verdict']}: {_last['risk']['order']} {_last['risk']['why'] or ''}")
    return ok


def news(log, force=False):
    """Headlines + moves for QQQ and its top holdings (info only). optbot calls it once a day at the open."""
    now = datetime.now(timezone.utc).astimezone(ET); today = now.date().isoformat()
    if not force and (_last["news_day"] == today or now.weekday() > 4 or not (560 <= now.hour * 60 + now.minute < 570)):
        return _last["news"]
    _last["news_day"] = today
    try:
        rc, out, err = _run("trader-news/news.py", ["--tickers"] + NEWS_TICKERS + ["--hours", "16", "--no-sec", "--json"], 120)
        d = json.loads(out)
    except Exception as e:
        log(f"DTS news broke: {e}"); _last["news"] = {"at": _now(), "error": str(e)[:200]}; return _last["news"]
    items = d.get("items", [])
    _last["news"] = {"at": _now(), "moves": {m["ticker"]: m.get("pct") for m in d.get("moves", [])},
                     "moving": [m["ticker"] for m in d.get("moves", []) if m.get("moving")],
                     "headlines": [{"t": i.get("ticker"), "title": i.get("title"), "tags": i.get("tags"), "time": i.get("time")}
                                   for i in items[:8]], "count": len(items)}
    log(f"DTS news: {len(items)} items, moving {_last['news']['moving'] or 'none'}")
    return _last["news"]


def summary():
    return {"pack": "day-trader-skill " + _pin(), "news": _last["news"], "risk": _last["risk"]}


def _pin():
    try:
        return open(os.path.join(PACK, "PINNED.txt")).read().split("@ ")[1][:7]
    except Exception:
        return "?"


def _now():
    return datetime.now(timezone.utc).isoformat()[:19] + "Z"
