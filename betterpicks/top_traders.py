#!/usr/bin/env python3
"""Top-trader picks list (Ryan 2026-11-11, chose "Both"): joins
  1) big-money sides from whale_flow.json (public prop trades, names hidden by Kalshi), and
  2) props people post on Kalshi's Social feed (/mnt/project-files/kalshi_feed_posts.jsonl, logged by the habibi
     Chrome thread as a normal visitor),
with our model's win chance (book.json shadow) or, if we have none, the market's price.
Writes top_traders.json and prints the list. Read only.
"""
import glob, json, os, re, sys
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import whale_flow  # noqa: E402

FEED = "/mnt/project-files/kalshi_feed_posts.jsonl"
AUTHORS = "/mnt/project-files/kalshi_feed_authors.json"   # profile P&L by category, cached by the habibi logger
OUT = os.path.join(HERE, "top_traders.json")
STAT_WORDS = {"points": "points", "rebounds": "rebounds", "assists": "assists", "threes": "threes",
              "3-pointers": "threes", "three": "threes", "steals": "steals", "blocks": "blocks",
              "receptions": "receptions", "receiving yards": "rec_yds", "rushing yards": "rush_yds",
              "passing yards": "pass_yds", "goals": "goals", "touchdown": "anytime_td"}


def norm(s):
    return re.sub(r"[^a-z]", "", (s or "").lower())


def newest(pattern):
    f = sorted(glob.glob(pattern), key=os.path.getmtime)
    return f[-1] if f else None


def sports_author(p, authors):
    """Ryan 2026-11-11: only watch people whose money is in the Sports category: Sports is their top category
    and they are up in it. Unknown authors are left out until their profile is read."""
    a = authors.get(p.get("author")) or {}
    top = a.get("author_top_category") or p.get("author_top_category")
    pnl = a.get("author_sports_pnl", p.get("author_sports_pnl"))
    return (top or "").lower() == "sports" and isinstance(pnl, (int, float)) and pnl > 0


def feed_posts(hours=30):
    try:
        authors = json.load(open(newest("/mnt/project-files/kalshi_feed_authors*.json") or AUTHORS))
    except (OSError, ValueError, TypeError):
        authors = {}
    cut = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    # the PC can't overwrite an upload, so each upload is a new kalshi_feed_posts-*.jsonl: read them all, dedupe
    out, seen = [], set()
    for fn in glob.glob("/mnt/project-files/kalshi_feed_posts*.jsonl"):
        try:
            lines = open(fn).read().splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                p = json.loads(ln)
            except ValueError:
                continue
            k = (p.get("author"), p.get("url"), p.get("market_title"), p.get("line_text"))
            if k in seen:
                continue
            seen.add(k)
            if (p.get("seen_at") or "") >= cut[:19] and p.get("player") and sports_author(p, authors):
                out.append(p)
    return out


def parse_line(p):
    """'Julius Randle: 2+ assists' -> (1.5, 'assists')."""
    m = re.search(r":\s*(\d+(?:\.\d+)?)\+\s*(.*)$", p.get("line_text") or "")
    if not m:
        return None, None
    words = (m.group(2) or p.get("market_title", "").split(":")[-1]).strip().lower()
    stat = next((v for k, v in STAT_WORDS.items() if k in words), None)
    if not stat:
        words = (p.get("market_title") or "").split(":")[-1].strip().lower()
        stat = next((v for k, v in STAT_WORDS.items() if k in words), None)
    return float(m.group(1)) - 0.5, stat


def build():
    d = whale_flow.load()
    meta = d.get("meta", {})
    sp = whale_flow.shadow_p()
    by_key = {}   # (player, stat, line, side) -> row

    def row(ticker, side):
        mt = meta.get(ticker, {})
        k = (norm(mt.get("player")), mt.get("stat"), mt.get("line"), side)
        if k not in by_key:
            sh = sp.get((ticker, "over" if side == "yes" else "under"), {})
            bid = mt.get("yes_bid") if side == "yes" else mt.get("no_bid")
            by_key[k] = {"ticker": ticker, "player": mt.get("player"), "stat": mt.get("stat"), "line": mt.get("line"),
                         "pick": "over" if side == "yes" else "under", "money": 0.0, "against": 0.0,
                         "biggest": 0.0, "posts": 0, "posters": [], "our_p": sh.get("p"),
                         "market_p": float(bid) if bid else None}
        return by_key[k]

    for s in d.get("sides", {}).values():
        if s.get("won") is not None or not whale_flow.is_flag(s):
            continue
        r = row(s["ticker"], s["side"])
        other = d["sides"].get(s["ticker"] + "|" + ("no" if s["side"] == "yes" else "yes"), {})
        r.update(money=s["usd"], against=other.get("usd", 0.0), biggest=s["max_trade"])

    idx = {(norm(m.get("player")), m.get("stat"), m.get("line")): t for t, m in meta.items()}
    unmatched = 0
    for p in feed_posts():
        line, stat = parse_line(p)
        t = idx.get((norm(p["player"]), stat, line))
        if not t:
            unmatched += 1
            continue
        r = row(t, p.get("side") or "yes")
        r["posts"] += 1
        if p.get("author") not in r["posters"]:
            r["posters"].append(p.get("author"))
        if r["market_p"] is None and p.get("price_pct"):
            r["market_p"] = p["price_pct"] / 100.0

    rows = [r for r in by_key.values() if r["player"]]
    for r in rows:
        r["win_p"] = r["our_p"] if r["our_p"] is not None else r["market_p"]
        r["win_src"] = "ours" if r["our_p"] is not None else "market"
        r["both"] = bool(r["money"] and r["posts"])
    rows.sort(key=lambda r: (-(r["both"]), -(len(r["posters"]) * 1000 + r["money"])))
    res = {"updated": datetime.now(timezone.utc).isoformat()[:19], "record": d.get("record", {}),
           "feed_unmatched": unmatched, "rows": rows}
    tmp = OUT + ".%d.tmp" % os.getpid()
    json.dump(res, open(tmp, "w"))
    os.replace(tmp, OUT)
    return res


def show(res, n=25):
    r0 = res["record"]
    print("big-money side record: %s-%s   feed posts not matched to a prop market: %d" % (
        r0.get("won", 0), r0.get("lost", 0), res["feed_unmatched"]))
    for r in res["rows"][:n]:
        wp = "%.0f%% (%s)" % (100 * r["win_p"], r["win_src"]) if r["win_p"] else "n/a"
        print("%-22s %-5s %5s %-9s money $%6.0f vs $%5.0f  posts %d %-24s win %s" % (
            r["player"][:22], r["pick"], r["line"], r["stat"], r["money"], r["against"], r["posts"],
            ",".join(r["posters"])[:24], wp))


if __name__ == "__main__":
    show(build())
