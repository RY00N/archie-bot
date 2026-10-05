#!/usr/bin/env python3
"""Archie live: the always-on loop that runs on the cloud server (paper only).

- Every POLL_SECONDS during market hours: pull the latest trade price for every holding.
- When a holding disappears (trailing stop hit) or every REBALANCE_MINUTES: run the same
  rotation as archie_bot.py, which only buys names Archie's research approved.
- Every RELOAD_MINUTES: `git pull` so the newest rules.json / research.json from Archie's
  study sessions take effect without a restart.
Writes live-status.json as a heartbeat. Stop with Ctrl+C or `systemctl stop archie`.
"""
import importlib, json, os, subprocess, sys, time, traceback
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import archie_bot as bot

POLL_SECONDS = 3
REBALANCE_MINUTES = 15
RELOAD_MINUTES = 5


def log(msg):
    print(f"{datetime.now(timezone.utc).isoformat()[:19]}Z {msg}", flush=True)


def latest_prices(symbols):
    if not symbols:
        return {}
    d = bot.api("GET", bot.DATA_API, "/v2/stocks/trades/latest", {"symbols": ",".join(symbols), "feed": "iex"})
    return {s: t["p"] for s, t in (d.get("trades") or {}).items()}


def reload_rules():
    if os.path.isdir(os.path.join(HERE, ".git")):
        subprocess.run(["git", "-C", HERE, "pull", "-q", "--ff-only"], timeout=60)
    importlib.reload(bot)


def main():
    acct = bot.trade("GET", "/v2/account")
    assert acct["account_number"].startswith("PA"), "Not a paper account. Refusing to run."
    log("Archie live started (paper)")
    last_rebalance = last_reload = 0
    held_before = None
    while True:
        try:
            now = time.time()
            if now - last_reload > RELOAD_MINUTES * 60:
                reload_rules()
                last_reload = now
            clock = bot.trade("GET", "/v2/clock")
            if not clock["is_open"]:
                json.dump({"time": datetime.now(timezone.utc).isoformat()[:19], "market_open": False,
                           "next_open": clock["next_open"]}, open(os.path.join(HERE, "live-status.json"), "w"))
                time.sleep(60)
                continue
            positions = {p["symbol"]: p for p in bot.trade("GET", "/v2/positions")}
            held = set(positions)
            lost = (held_before - held) if held_before is not None else set()
            if lost:
                log(f"Position closed (stop or sell): {', '.join(sorted(lost))}. Refilling now.")
            if lost or now - last_rebalance > REBALANCE_MINUTES * 60:
                bot.run()
                last_rebalance = now
                positions = {p["symbol"]: p for p in bot.trade("GET", "/v2/positions")}
                held = set(positions)
            prices = latest_prices(sorted(held))
            json.dump({"time": datetime.now(timezone.utc).isoformat()[:19], "market_open": True,
                       "prices": prices, "positions": {s: p["unrealized_plpc"] for s, p in positions.items()}},
                      open(os.path.join(HERE, "live-status.json"), "w"))
            held_before = held
            time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            log("stopped")
            return
        except Exception:
            log("error:\n" + traceback.format_exc())
            time.sleep(30)


if __name__ == "__main__":
    main()
