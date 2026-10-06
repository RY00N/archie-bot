#!/usr/bin/env python3
"""Archie live: the always-on loop that runs on the cloud server (paper only).

- Every POLL_SECONDS during market hours: pull the latest trade price for every holding.
- When a holding disappears (trailing stop hit) or every REBALANCE_MINUTES: run the same
  rotation as archie_bot.py, which only buys names Archie's research approved.
- Every NEWS_SECONDS: read new headlines on every holding (news-log.jsonl); a red-flag headline
  (downgrade, guidance cut, probe, lawsuit, plunge...) tightens that stock's trailing stop to 4%.
- Every RELOAD_MINUTES: `git pull` so the newest rules.json / research.json from Archie's
  study sessions take effect without a restart.
Writes live-status.json as a heartbeat. Stop with Ctrl+C or `systemctl stop archie`.
"""
import importlib, json, os, re, subprocess, sys, time, traceback
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import archie_bot as bot

POLL_SECONDS = 3
REBALANCE_MINUTES = 15
RELOAD_MINUTES = 5
NEWS_SECONDS = 60
TIGHT_TRAIL_PCT = "4"
# Headlines that mean "protect this position now": tighten its trailing stop until research reviews it.
RED_FLAGS = re.compile(r"downgrad|cuts? (guidance|outlook|forecast)|lowers? (guidance|outlook)|miss(es|ed)? (estimates|expectations)|"
                       r"investigation|probe|subpoena|lawsuit|fraud|recall|halt|bankrupt|delist|short seller|resign|ousted|"
                       r"plunge|tumble|sinks|crash|warns", re.I)
NEWS_LOG = os.path.join(HERE, "news-log.jsonl")


def log(msg):
    print(f"{datetime.now(timezone.utc).isoformat()[:19]}Z {msg}", flush=True)


def latest_prices(symbols):
    if not symbols:
        return {}
    d = bot.api("GET", bot.DATA_API, "/v2/stocks/trades/latest", {"symbols": ",".join(symbols), "feed": "iex"})
    return {s: t["p"] for s, t in (d.get("trades") or {}).items()}


def check_news(held, since, flagged):
    """Read new headlines on our holdings; on a red flag, tighten that position's trailing stop."""
    if not held:
        return since
    d = bot.api("GET", bot.DATA_API, "/v1beta1/news", {"symbols": ",".join(sorted(held)), "start": since,
                                                       "limit": 50, "sort": "asc"})
    for n in d.get("news", []):
        since = max(since, n["created_at"])
        syms = [s for s in n.get("symbols", []) if s in held]
        hit = bool(RED_FLAGS.search(n["headline"]))
        with open(NEWS_LOG, "a") as f:
            f.write(json.dumps({"t": n["created_at"], "symbols": syms, "headline": n["headline"], "red_flag": hit}) + "\n")
        log(f"NEWS {','.join(syms)}: {n['headline'][:120]}" + ("  [RED FLAG]" if hit else ""))
        if not hit:
            continue
        for sym in syms:
            if sym in flagged:
                continue
            for o in bot.trade("GET", "/v2/orders", {"status": "open", "symbols": sym}):
                if o["type"] == "trailing_stop" and float(o.get("trail_percent") or 0) > float(TIGHT_TRAIL_PCT):
                    bot.trade("PATCH", f"/v2/orders/{o['id']}", body={"trail": TIGHT_TRAIL_PCT})
                    log(f"Tightened {sym} trailing stop to {TIGHT_TRAIL_PCT}% on red-flag headline")
            flagged.add(sym)
    return since


def reload_rules():
    if os.path.isdir(os.path.join(HERE, ".git")):
        subprocess.run(["git", "-C", HERE, "pull", "-q", "--ff-only"], timeout=60)
    importlib.reload(bot)


def main():
    acct = bot.trade("GET", "/v2/account")
    assert acct["account_number"].startswith("PA"), "Not a paper account. Refusing to run."
    log("Archie live started (paper)")
    last_rebalance = last_reload = last_news = 0
    held_before = None
    news_since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    flagged = set()
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
            if now - last_news > NEWS_SECONDS:
                try:
                    news_since = check_news(held, news_since, flagged)
                except Exception:
                    log("news error:\n" + traceback.format_exc())
                last_news = now
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
