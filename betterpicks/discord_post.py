#!/usr/bin/env python3
"""Post today's BetterPicks (practice) picks and parlays to Ryan's Discord channel.

Needs ONE of these in the environment (never paste them in chat):
  DISCORD_WEBHOOK_URL                      (channel > Edit Channel > Integrations > Webhooks > Copy URL)
  DISCORD_BOT_TOKEN + DISCORD_CHANNEL_ID   (Ryan's own bot; the bot needs Send Messages in that channel)

  python3 discord_post.py today [--dry]   # post picks/parlays dated today (PT); --dry prints instead
  python3 discord_post.py file <path>     # post a prepared message
Already-posted items are remembered in discord_posted.json so nothing is posted twice.
"""
import json, os, re, sys, urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(HERE, "book.json")
SENT = os.path.join(HERE, "discord_posted.json")


def load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def key_pick(p):
    return f"pick:{p['game_id']}:{p['bet']}"


def key_parlay(e):
    return f"parlay:{e['date']}:{e['app']}:{e['type']}"


def fmt_pick(p):
    return (f"**{p['bet']} {p['odds']:+.0f}** ({p.get('app') or 'sportsbook'}), ${p['stake']:.0f} practice\n"
            f"{p['game']}\n{p['why']}")


def fmt_parlay(e):
    pay = f"{e['payout_x']}x" if e.get("payout_x") else "multiplier: check the app"
    legs = "\n".join(f"- {l['player']} {l['side']} {l['line']} {l['stat'].replace('_', ' ')} ({l['game']})"
                     for l in e["legs"])
    return f"**{e['app']} {e['type']}**, ${e['stake']:.0f} practice, {pay}, {e['win_p']:.0%} win chance\n{legs}"


KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
_series_slug = {}


def kalshi_url(ticker):
    """Public Kalshi page for a leg's market (kalshi.com/markets/<series>/<series-title-slug>/<event>)."""
    if not ticker:
        return None
    parts = ticker.split("-")
    series, event = parts[0], "-".join(parts[:2])
    if series not in _series_slug:
        try:
            req = urllib.request.Request(f"{KALSHI_API}/series/{series}", headers={"User-Agent": "BetterPicks (1.0)"})
            title = json.load(urllib.request.urlopen(req, timeout=10))["series"]["title"]
            _series_slug[series] = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        except Exception:
            _series_slug[series] = None
    slug = _series_slug[series]
    return f"https://kalshi.com/markets/{series.lower()}/{slug}/{event.lower()}" if slug else f"https://kalshi.com/markets/{series.lower()}"


def embed_parlay(e):
    """BetterPicks-branded embed with a tap-to-open Kalshi market link per leg (practice only)."""
    pay = f"{e['payout_x']}x" if e.get("payout_x") else "check Kalshi for the multiplier"
    fields = []
    for l in e["legs"]:
        url = kalshi_url(l.get("ticker"))
        price = f", {round(l['price'] * 100)}c" if l.get("price") else ""
        name = f"{l['player']} {l['side']} {l['line']} {l['stat'].replace('_', ' ')}"
        val = f"{l['game']}{price}, our fair {l['p']:.0%}" + (f"\n[Open on Kalshi]({url})" if url else "")
        fields.append({"name": name[:256], "value": val[:1024]})
    return {"title": f"BetterPicks: {e['app']} {e['type']}"[:256],
            "description": f"PRACTICE PICK, NOT A REAL BET. ${e['stake']:.0f} practice, {pay}, {e['win_p']:.0%} win chance."
                           " Tap a leg to open its market, then pick that player and line.",
            "color": 0x00B37E, "fields": fields[:25], "footer": {"text": "BetterPicks practice bankroll. 21+ only for real wagers."}}


def kalshi_post(e):
    """Message text with each leg's real Kalshi link on its own line, so Discord unfurls Kalshi's own preview."""
    links = [kalshi_url(l.get("ticker")) for l in e["legs"]]
    lines = [f"**BetterPicks {e['app']} {e['type']}** (practice pick, not a real bet)"]
    lines += [f"{l['player']} {l['side']} {l['line']} {l['stat'].replace('_', ' ')}: {u}" for l, u in zip(e["legs"], links) if u]
    return "\n".join(lines)


def send(text, embeds=None):
    hook = os.environ.get("DISCORD_WEBHOOK_URL")
    tok, chan = os.environ.get("DISCORD_BOT_TOKEN"), os.environ.get("DISCORD_CHANNEL_ID")
    if hook:
        url, headers = hook, {}
    elif tok and chan:
        url, headers = f"https://discord.com/api/v10/channels/{chan}/messages", {"Authorization": f"Bot {tok}"}
    else:
        sys.exit("no DISCORD_WEBHOOK_URL or DISCORD_BOT_TOKEN+DISCORD_CHANNEL_ID in the environment")
    payload = {"content": text[:1990], "allowed_mentions": {"parse": []}}
    if embeds:
        payload["embeds"] = embeds[:10]
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "BetterPicks (1.0)", **headers})
    urllib.request.urlopen(req, timeout=20).read()


def today(dry=False):
    book = load(BOOK, {})
    sent = set(load(SENT, []))
    day = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-7))).date().isoformat()
    items = [(key_pick(p), fmt_pick(p)) for p in book.get("picks", []) if p["date"] == day]
    items += [(key_parlay(e), e) for e in book.get("parlays", []) if e["date"] == day]
    new = [(k, t) for k, t in items if k not in sent]
    if not new:
        print("nothing new to post"); return
    msgs = [f"**BetterPicks {day}** (practice picks, not real bets)"] + [t for _, t in new]
    for m in msgs:
        if isinstance(m, dict):   # a parlay: Kalshi ones get the link embed, others plain text
            if m["app"] == "Kalshi":
                emb = embed_parlay(m)
                print(kalshi_post(m), json.dumps(emb, indent=1), "\n") if dry else send(kalshi_post(m), [emb])
            else:
                print(fmt_parlay(m), "\n") if dry else send(fmt_parlay(m))
        else:
            print(m, "\n") if dry else send(m)
    if not dry:
        json.dump(sorted(sent | {k for k, _ in new}), open(SENT, "w"), indent=1)
        print("posted", len(new))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "today":
        today("--dry" in sys.argv)
    elif len(sys.argv) > 2 and sys.argv[1] == "file":   # post a prepared message, e.g. discord_test.txt
        if sys.argv[2].endswith(".json"):   # a parlay entry -> embed
            e = json.load(open(sys.argv[2])); send(kalshi_post(e), [embed_parlay(e)])
        else:
            send(open(sys.argv[2]).read())
        print("posted", sys.argv[2])
    else:
        print(__doc__)
