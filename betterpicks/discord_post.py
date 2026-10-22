#!/usr/bin/env python3
"""Post today's BetterPicks (practice) picks and parlays to Ryan's Discord channel.

Needs ONE of these in the environment (never paste them in chat):
  DISCORD_WEBHOOK_URL                      (channel > Edit Channel > Integrations > Webhooks > Copy URL)
  DISCORD_BOT_TOKEN + DISCORD_CHANNEL_ID   (Ryan's own bot; the bot needs Send Messages in that channel)

  python3 discord_post.py today [--dry]   # post picks/parlays dated today (PT); --dry prints instead
  python3 discord_post.py file <path>     # post a prepared message
  python3 discord_post.py verdicts [--dry] # HIT/LOST posts for graded parlays (also runs after 'today')
Already-posted items are remembered in discord_posted.json so nothing is posted twice.
"""
import json, math, os, re, sys, urllib.request
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
    return (f"**{p['bet']} {p['odds']:+.0f}** ({p.get('app') or 'sportsbook'}), ${p['stake']:.0f}\n"
            f"{p['game']}\n{p['why']}")


def fmt_parlay(e):
    pay = f"{e['payout_x']}x" if e.get("payout_x") else "multiplier: check the app"
    legs = "\n".join(f"- {leg_label(l)} ({l['game']})"
                     + (f" edge {l['edge'] * 100:+.1f}%" if l.get("edge") is not None else "")
                     for l in e["legs"])
    return f"**{e['app']} {e['type']}**, ${e['stake']:.0f}, {pay}, {e['win_p']:.0%} win chance\n{legs}"


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

STAT_WORDS = {"pitcher_ks": "strikeouts", "pitcher_outs": "outs recorded", "earned_runs": "earned runs allowed",
              "hits_allowed": "hits allowed", "hits": "hits", "total_bases": "total bases", "hrr": "hits + runs + RBIs",
              "singles": "singles", "hr": "home runs", "rbi": "RBIs", "runs": "runs", "batter_ks": "strikeouts",
              "pass_yds": "passing yards", "rush_yds": "rushing yards", "rec_yds": "receiving yards", "pra": "points + rebounds + assists",
              "threes": "3-pointers", "rush_att": "rushing attempts", "receptions": "receptions", "points": "points", "rebounds": "rebounds", "assists": "assists", "shots_on_target": "shots on target"}
_titles = {}


def leg_label(l):
    """How Kalshi itself words the leg (Ryan 2026-10-13: no '4.5', no 'ks'): 'YES on Parker Messick: 5+ strikeouts'."""
    t = l.get("ticker")
    if t and t not in _titles:
        try:
            req = urllib.request.Request(f"{KALSHI_API}/markets/{t}", headers={"User-Agent": "BetterPicks (1.0)"})
            _titles[t] = json.load(urllib.request.urlopen(req, timeout=10))["market"]["title"].rstrip("?")
        except Exception:
            _titles[t] = None
    title = _titles.get(t) or (f"{l['player']}: {math.ceil(l['line'])}+ "
                               f"{STAT_WORDS.get(l['stat'], l['stat'].replace('_', ' '))}")
    return f"{'YES' if l['side'] == 'over' else 'NO'} on {title}"


def rec_text(rec):
    if "won" not in rec:
        return "BetterPicks"
    sign = "-" if rec["pnl"] < 0 else "+"
    return f"BetterPicks record: {rec['won']}-{rec['lost']}, {sign}${abs(rec['pnl']):.2f}"


def record_line():
    """'Record: 5-0, +$25.65' from the project's feed (picks that pass today's rules, Ryan 2026-10-22)."""
    rec = feed_doc().get("record") or {}
    return rec_text(rec)


def embed_parlay(e):
    """One clean card per pick (Ryan 2026-10-22: posts were scuffed): each leg says what to tap on Kalshi,
    the price, our win chance and edge, why we like it, and a tap-to-open link."""
    pay = f"pays {e['payout_x']}x" if e.get("payout_x") else "check Kalshi for the payout"
    fields = []
    for l in e["legs"]:
        url = kalshi_url(l.get("ticker"))
        edge = l.get("edge")
        if edge is None and l.get("price"):
            edge = l["p"] - l["price"] - 0.07 * l["price"] * (1 - l["price"])
        nums = " · ".join(x for x in (f"{round(l['price'] * 100)}¢ on Kalshi" if l.get("price") else "",
                                      f"we have it at {l['p']:.0%}", f"edge {edge * 100:+.1f}%" if edge is not None else "") if x)
        why = (l.get("case") or l.get("why") or "").strip()
        val = "\n".join(x for x in (f"{l['game']} · {nums}", f"**Why:** {why}" if why else "",
                                      f"**News:** {l['news']}" if l.get("news") else "",
                                      f"[Open on Kalshi]({url})" if url else "") if x)
        fields.append({"name": leg_label(l)[:256], "value": val[:1024]})
    first = next((kalshi_url(l.get("ticker")) for l in e["legs"] if l.get("ticker")), None)
    title = leg_label(e["legs"][0]) if e.get("lock") or len(e["legs"]) == 1 else f"{len(e['legs'])}-leg combo"
    return {"title": title[:256], **({"url": first} if first else {}),
            "description": f"${e['stake']:.0f} · {pay} · {e['win_p']:.0%} to hit",
            "color": 0x00B37E, "fields": fields[:25], "footer": {"text": record_line()}}


def kalshi_post(e):
    """Short header line; the legs and their Kalshi links live inside the one card (Ryan 2026-10-12)."""
    if e.get("lock"):
        return "\U0001F512 **BetterPicks lock**"
    if e["type"].startswith("pick of the day"):
        return "\u2B50 **BetterPicks pick of the day**"
    return f"**BetterPicks {e['type']}**"


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
    # Ryan 2026-10-12: Kalshi only for now. Ryan 2026-10-14 01:08Z: anything dated today posts whenever it's found, any hour.
    book = load(BOOK, {})
    sent = set(load(SENT, []))
    rules = load(os.path.join(HERE, "rules.json"), {})
    apps = set(rules.get("apps", ["PrizePicks", "Kalshi", "sportsbook"]))
    lo, hi = rules.get("post_window_pt", [0, 24])
    now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-7)))
    day, in_window = now.date().isoformat(), lo <= now.hour < hi
    items = [(key_pick(p), fmt_pick(p)) for p in book.get("picks", []) if p["date"] == day and "sportsbook" in apps]
    items += [(key_parlay(e), e) for e in book.get("parlays", [])
              if e["date"] == day and e["app"] in apps and not e.get("test")]   # Ryan 2026-10-14: post whenever, no window
    gone = {r["key"] for r in feed_doc().get("retract", [])}   # Ryan 2026-10-21: taken-back picks never post
    new = [(k, t) for k, t in items if k not in sent and k not in gone]
    # Ryan 2026-10-12: if nothing goes out today, say so once (only after noon and once parlays.py finds no Kalshi games left today)
    quiet_key = f"quiet:{day}"
    if (not new and now.hour >= hi and quiet_key not in sent and book.get("no_games_left") == day
            and not any(k.startswith(f"parlay:{day}:") for k in sent)):
        msg = "Bad day, no picks from me today. No Kalshi games left to play."
        print(msg) if dry else (send(msg), json.dump(sorted(sent | {quiet_key}), open(SENT, "w"), indent=1))
        return
    # Ryan 2026-10-12 17:46Z: between 6am and noon PT, if nothing has gone out yet, say so every 2 hours (8am, 10am)
    slot = now.hour - (now.hour - lo) % 2
    upd_key = f"update:{day}:{slot}"
    if (not new and lo < slot < hi and upd_key not in sent
            and not any(k.startswith(f"parlay:{day}:") for k in sent)):
        msg = "Couldn't find anything yet, market looks bad. Still scanning Kalshi."
        print(msg) if dry else (send(msg), json.dump(sorted(sent | {upd_key}), open(SENT, "w"), indent=1))
        return
    if not new:
        print("nothing new to post"); return
    head = [] if all(isinstance(t, dict) and t.get("lock") for _, t in new) else [f"**BetterPicks {datetime.fromisoformat(day).strftime('%A, %b %-d')}**"]
    msgs = head + [t for _, t in new]
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


FEED_URL = "https://raw.githubusercontent.com/RY00N/archie-bot/claude/project-thread-8sdhk6/betterpicks/books_feed.json"
MARK = {"won": "\u2705", "lost": "\u274c", "void": "\u2796", "push": "\u2796"}


def verdict_post(v, rec):
    """'HIT' or 'LOST' once the parlay's last game is graded, one embed per parlay."""
    day = datetime.fromisoformat(v["date"]).strftime("%b %-d")
    word = {"won": "HIT", "lost": "LOST", "void": "VOID"}[v["status"]]
    money = {"won": f"+${v['pnl']:.2f}", "lost": f"-${v['stake']:.2f}",
             "void": "no action, stake back"}[v["status"]]
    name = f"1-leg lock" if v.get("lock") else v["type"]
    head = f"{MARK[v['status']]} **{word}: BetterPicks {v['app']} {name} ({day})**, {money}"
    fields = []
    for l in v["legs"]:
        got = f"finished with {l['actual']:g}" if isinstance(l.get("actual"), (int, float)) else (
            "no result (voided)" if l.get("result") == "void" else "")
        fields.append({"name": f"{MARK.get(l.get('result'), '')} {leg_label(l)}"[:256], "value": f"{l['game']}" + (f", {got}" if got else "")})
    foot = rec_text(rec or {})
    color = {"won": 0x00B37E, "lost": 0xE5484D, "void": 0x8B8D98}[v["status"]]
    return head, {"title": f"Verdict: {word}", "color": color, "fields": fields[:25], "footer": {"text": foot}}


_feed = None


def feed_doc():
    """The project's graded feed (verdicts, delays, retractions), fetched once per run."""
    global _feed
    if _feed is None:
        try:
            req = urllib.request.Request(FEED_URL, headers={"User-Agent": "BetterPicks (1.0)", "Cache-Control": "no-cache"})
            _feed = json.load(urllib.request.urlopen(req, timeout=15)).get("verdicts") or {}
        except Exception:
            _feed = {}
    return _feed


def discord(method, path):
    tok = os.environ.get("DISCORD_BOT_TOKEN")
    req = urllib.request.Request(f"https://discord.com/api/v10{path}", method=method,
                                 headers={"Authorization": f"Bot {tok}", "User-Agent": "BetterPicks (1.0)"})
    raw = urllib.request.urlopen(req, timeout=20).read()
    return json.loads(raw) if raw else None


def retractions(dry=False):
    """Ryan 2026-10-21: when the project takes a pick back, delete its Discord post and tell people to fade it.
    A pick that never went out is just marked so it never posts."""
    sent = set(load(SENT, []))
    chan = os.environ.get("DISCORD_CHANNEL_ID")
    for r in feed_doc().get("retract", []):
        k = r["key"]
        if f"retract:{k}" in sent:
            continue
        if k in sent:
            head = kalshi_post(r)
            if dry:
                print("would delete + fade:", head); continue
            try:   # find our post by its header line among the channel's recent messages
                for m in discord("GET", f"/channels/{chan}/messages?limit=100") or []:
                    if m.get("content", "").startswith(head):
                        discord("DELETE", f"/channels/{chan}/messages/{m['id']}")
                        break
            except Exception as ex:
                print("delete failed:", ex)
            picks = ", ".join(leg_label(l) for l in r["legs"])
            send(f"\u21a9\ufe0f **Taking back a pick: {picks}.** It doesn't pass our updated rules, so fade it "
                 f"(skip it, or take the other side if you're on it). {r.get('why', '')}".replace("  ", " "))
        sent.add(f"retract:{k}"); sent.add(k)
        if not dry:
            json.dump(sorted(sent), open(SENT, "w"), indent=1)
            print("retracted", k)


def verdicts(dry=False):
    """Ryan 2026-10-18: when a posted parlay's last game is graded, say whether it hit or lost (past ones too)."""
    doc = feed_doc()
    if not doc:
        return
    sent = set(load(SENT, []))
    todo = [v for v in sorted(doc.get("parlays", []), key=lambda v: v["date"])
            if v["key"] in sent and f"verdict:{v['key']}" not in sent and f"retract:{v['key']}" not in sent]
    for dl in doc.get("delays", []):   # Ryan 2026-10-18: say when a posted pick's game is delayed; the pick carries over
        k = f"delay:{dl['key']}:{dl['game']}"
        if dl["key"] not in sent or k in sent:
            continue
        picks = ", ".join(leg_label(l) for l in dl["legs"])
        msg = (f"\u23f8\ufe0f **Game delayed: {dl['game']} ({dl['status']}).** The BetterPicks {dl['type']} ({picks}) still stands "
               "for the makeup game, so keep it on your slip. Kalshi keeps the market open.")
        if dry:
            print(msg); continue
        send(msg)
        sent.add(k)
        json.dump(sorted(sent), open(SENT, "w"), indent=1)
        print("posted delay", k)
    for v in todo:
        head, emb = verdict_post(v, doc.get("record"))
        if dry:
            print(head, json.dumps(emb, indent=1), "\n"); continue
        send(head, [emb])
        sent.add(f"verdict:{v['key']}")
        json.dump(sorted(sent), open(SENT, "w"), indent=1)
        print("posted verdict", v["key"])


def reload_presence():
    """BetterBot's presence (and /pnl) runs straight from the repo checkout, which the scanner pulls hourly.
    When the checkout holds newer code than the running copy, stop it; systemd starts it again on the new code."""
    import hashlib, subprocess
    src = os.path.join(os.environ.get("BP_REPO", "/home/archie/bp-repo"), "betterbot_presence.py")
    ver = os.path.join(HERE, "presence.version")
    if not os.path.exists(src):
        return
    want = hashlib.sha1(open(src, "rb").read()).hexdigest()
    have = open(ver).read().strip() if os.path.exists(ver) else None
    if want != have:
        subprocess.run(["pkill", "-f", "betterbot_presence.py"], capture_output=True)
        print("restarting BetterBot presence on new code")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "today":
        retractions("--dry" in sys.argv)
        today("--dry" in sys.argv)
        verdicts("--dry" in sys.argv)
        try:
            reload_presence()
        except Exception as e:
            print("presence reload failed:", e)
    elif len(sys.argv) > 1 and sys.argv[1] == "verdicts":
        verdicts("--dry" in sys.argv)
    elif len(sys.argv) > 2 and sys.argv[1] == "file":   # post a prepared message, e.g. discord_test.txt
        if sys.argv[2].endswith(".json"):   # a parlay entry -> embed
            e = json.load(open(sys.argv[2])); send(kalshi_post(e), [embed_parlay(e)])
        else:
            send(open(sys.argv[2]).read())
        print("posted", sys.argv[2])
    else:
        print(__doc__)
