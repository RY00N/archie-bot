"""Publish Archie's live PAPER P&L snapshot for the Agent City page (runs on the server).

Every call writes pnl.json (equity, today's and total P&L, positions; never keys or the
account number) to the `live` branch of the bot's GitHub repo as a single force-pushed
commit, then asks jsDelivr to drop its cached copy. The city page reads:
  https://cdn.jsdelivr.net/gh/<repo>@live/pnl.json   (fresh right after each purge)
  https://raw.githubusercontent.com/<repo>/live/pnl.json   (fallback, up to 5 min old)
Needs GITHUB_TOKEN (fine-grained, this repo only, Contents read/write) in /etc/archie.env.
Without it, publishing is skipped and trading is unaffected.
"""
import json, os, re, subprocess, threading, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PUB_DIR = os.path.expanduser("~/pnl-pub")
_busy = threading.Lock()
_warned = False
_pushes = 0


def repo_slug():
    url = subprocess.run(["git", "-C", HERE, "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    return m.group(1) if m else None


def snapshot(bot, market_open):
    acct = bot.trade("GET", "/v2/account")
    positions = bot.trade("GET", "/v2/positions")
    offset = bot.RULES["alpaca_start_equity"] - bot.RULES["budget_start"]
    equity, last = float(acct["equity"]), float(acct["last_equity"])
    return {
        "book": "paper", "note": "Practice money. Never counts toward real totals.",
        "updated": datetime.now(timezone.utc).isoformat()[:19] + "Z", "market_open": market_open,
        "budget_start": bot.RULES["budget_start"],
        "equity": round(equity - offset, 2),
        "day_pnl": round(equity - last, 2),
        "day_pnl_pct": round((equity - last) / (last - offset) * 100, 2),
        "total_pnl": round(equity - offset - bot.RULES["budget_start"], 2),
        "total_pnl_pct": round((equity - offset) / bot.RULES["budget_start"] * 100 - 100, 2),
        "cash": round(float(acct["cash"]) - offset, 2),
        "positions": [{"symbol": p["symbol"], "qty": float(p["qty"]), "value": round(float(p["market_value"]), 2),
                       "pnl": round(float(p["unrealized_pl"]), 2), "pnl_pct": round(float(p["unrealized_plpc"]) * 100, 2),
                       "day_pnl": round(float(p["unrealized_intraday_pl"]), 2)} for p in positions],
    }


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
