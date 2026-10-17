"""Archie's Slave: the pre-market intel feed for Archie's QQQ options trade (Ryan 2026-10-17: "he will feed information
to Archie from sources online and that'll help us see what to trade on QQQ").

Gathers, before the 9:35 ET trade:
  futures   - Nasdaq/S&P futures, VIX, 10y yield, dollar, oil, bitcoin: % change since the prior close (Yahoo charts)
  megacaps  - pre-market % move of QQQ's biggest holdings, plus a QQQ-weighted average (Yahoo, pre/post data on)
  calendar  - today's US economic releases and Fed speakers with impact level (ForexFactory weekly feed)
  earnings  - QQQ heavyweights that reported last night or report today/tonight (Finnhub)
  news      - overnight Alpaca/Benzinga headlines for QQQ and the mega-caps, tagged bull/bear/shock;
              top Reuters market headlines (Google News RSS, reuters.com blocks bots)
  sentiment - CNN Fear & Greed, and Archie's own Liquid perps read (sentiment.json) when present
and sums it up as a lean (BULLISH / BEARISH / MIXED) plus caution flags.

INFO ONLY. The lean is not backtested and is not a trade signal; it never places or blocks a trade. Every run is also
appended to feeds/history/YYYY-MM-DD.jsonl so Archie can later test whether any of it predicts the 9:30-9:34 direction.

Usage: python3 intel.py            (one run now, prints the summary)
Bot hook: intel.maybe_run(log) from daytrader.tick; runs at the RUN_AT times below on weekdays, once each.
Needs only the stdlib. Keys from env: APCA_API_KEY_ID / APCA_API_SECRET_KEY, FINNHUB_API_KEY (each part is skipped
if its key or source is missing)."""
import json, os, re, threading, time, urllib.parse, urllib.request, xml.etree.ElementTree as XET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
FEEDS = os.path.join(HERE, "feeds")
OUT = os.path.join(FEEDS, "qqq-intel.json")
ET = ZoneInfo("America/New_York")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
RUN_AT = ["06:00", "08:00", "08:35", "09:05", "09:20", "09:33"]  # ET, weekdays
MACRO = {"NQ=F": "Nasdaq futures", "ES=F": "S&P futures", "^VIX": "VIX", "^TNX": "10y yield",
         "DX-Y.NYB": "Dollar", "CL=F": "Oil", "BTC-USD": "Bitcoin"}
# Approximate QQQ weights (%), close enough to weight the pre-market move; GOOGL stands in for both share classes.
WEIGHTS = {"NVDA": 9.0, "MSFT": 8.2, "AAPL": 7.8, "AMZN": 5.5, "AVGO": 5.0, "META": 3.6, "GOOGL": 5.0, "TSLA": 3.2,
           "NFLX": 2.2, "COST": 2.4, "PLTR": 2.0, "AMD": 1.8}
EARN_WATCH = list(WEIGHTS) + ["CSCO", "TMUS", "LIN", "PEP", "ISRG", "INTU", "ADBE", "QCOM", "TXN", "AMGN", "BKNG",
                              "AMAT", "LRCX", "MU", "INTC", "PANW", "ADP", "GILD", "KLAC", "CRWD", "SBUX", "MELI", "ASML"]
BULL = re.compile(r"\b(beats?|tops|raises? (guidance|outlook|forecast)|upgrades?d?|surges?|soars?|jumps?|rall(y|ies)|"
                  r"record high|rate cut|cooler|eases?|approv(al|ed)|buyback|strong demand|ends? (up|higher)|gains?|rebounds?)\b", re.I)
BEAR = re.compile(r"\b(miss(es)?|cuts? (guidance|outlook|forecast)|downgrades?d?|plunges?|tumbles?|slumps?|falls?|drops?|"
                  r"sell-?off|warns?|probe|antitrust|tariffs?|hotter|recession|layoffs?|bans?|curbs?|war|strike|ends? (down|lower)|weighs?|worr(y|ies)|rout)\b", re.I)
MARKET = re.compile(r"\b(stocks?|shares|Wall St|Nasdaq|S&P|Dow|futures|Fed|Powell|Treasur(y|ies)|yields?|inflation|"
                    r"jobs|payrolls|earnings|tariffs?|chips?|AI|dollar|oil)\b", re.I)
SHOCK = re.compile(r"\b(emergency|surprise (cut|hike)|crash|circuit breaker|trading halt|invasion|missile|nuclear|"
                   r"default|bankrupt|export (ban|curbs?)|tariffs? (on|hike))\b", re.I)


def get(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def getj(url, headers=None):
    return json.loads(get(url, headers))


def yahoo(sym):
    """Last price (pre/post market included) vs the prior regular close."""
    d = getj(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
             "?interval=1m&range=1d&includePrePost=true")["chart"]["result"][0]
    prev = d["meta"].get("chartPreviousClose") or d["meta"].get("previousClose")
    closes = [c for c in (d.get("indicators", {}).get("quote") or [{}])[0].get("close", []) if c is not None]
    last = closes[-1] if closes else d["meta"].get("regularMarketPrice")
    return {"last": round(last, 4), "prev_close": round(prev, 4), "pct": round((last / prev - 1) * 100, 2)}


def part_futures():
    out = {}
    for s, name in MACRO.items():
        try:
            out[s] = {"name": name, **yahoo(s)}
        except Exception as e:
            out[s] = {"name": name, "error": str(e)[:80]}
    return out


def part_megacaps():
    rows, wsum, wtot = {}, 0.0, 0.0
    for s in ["QQQ"] + list(WEIGHTS):
        try:
            rows[s] = yahoo(s)
            if s in WEIGHTS:
                wsum += rows[s]["pct"] * WEIGHTS[s]; wtot += WEIGHTS[s]
        except Exception as e:
            rows[s] = {"error": str(e)[:80]}
    movers = sorted((s for s in WEIGHTS if "pct" in rows.get(s, {})), key=lambda s: -abs(rows[s]["pct"]))[:4]
    return {"stocks": rows, "weighted_pct": round(wsum / wtot, 2) if wtot else None,
            "covers_weight_pct": round(wtot, 1), "biggest_movers": [f"{s} {rows[s]['pct']:+.2f}%" for s in movers]}


def part_calendar(now):
    ev = getj("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
    today = []
    for e in ev:
        if e.get("country") != "USD" or e.get("impact") not in ("High", "Medium", "Holiday"):
            continue
        t = datetime.fromisoformat(e["date"]).astimezone(ET)
        if t.date() != now.date():
            continue
        today.append({"time_et": t.strftime("%H:%M"), "event": e["title"], "impact": e["impact"],
                      "forecast": e.get("forecast") or None, "previous": e.get("previous") or None})
    in_window = [e for e in today if e["impact"] == "High" and "08:00" <= e["time_et"] <= "10:35"]
    return {"today": today, "high_impact_before_10_35": in_window}


def part_earnings(now):
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        return {"error": "no FINNHUB_API_KEY"}
    frm = (now - timedelta(days=4)).date().isoformat(); to = (now + timedelta(days=1)).date().isoformat()
    cal = getj(f"https://finnhub.io/api/v1/calendar/earnings?from={frm}&to={to}&token={key}").get("earningsCalendar", [])
    prev_day = now.date() - timedelta(days=1 if now.weekday() else 3)
    out = {"reported_last_night": [], "today_before_open": [], "tonight_after_close": []}
    for r in cal:
        if r["symbol"] not in EARN_WATCH:
            continue
        d, h = r["date"], r.get("hour")
        row = {"symbol": r["symbol"], "eps_est": r.get("epsEstimate"), "eps_actual": r.get("epsActual"),
               "rev_est": r.get("revenueEstimate"), "rev_actual": r.get("revenueActual")}
        if d == prev_day.isoformat() and h == "amc":
            out["reported_last_night"].append(row)
        elif d == now.date().isoformat() and h == "bmo":
            out["today_before_open"].append(row)
        elif d == now.date().isoformat() and h == "amc":
            out["tonight_after_close"].append(row)
    return out


def tone(h):
    if SHOCK.search(h):
        return "shock"
    b, s = len(BULL.findall(h)), len(BEAR.findall(h))
    return "bull" if b > s else "bear" if s > b else "neutral"


def part_news(now):
    out = {"alpaca": [], "reuters": []}
    if os.environ.get("APCA_API_KEY_ID"):
        since = (now.replace(hour=16, minute=0, second=0, microsecond=0) - timedelta(days=1 if now.weekday() else 3))
        q = urllib.parse.urlencode({"symbols": ",".join(["QQQ", "SPY"] + list(WEIGHTS)), "limit": 50, "sort": "desc",
                                    "start": since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        d = getj(f"https://data.alpaca.markets/v1beta1/news?{q}",
                 {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"], "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]})
        for n in d.get("news", []):
            out["alpaca"].append({"t": n["created_at"][:16] + "Z", "headline": n["headline"][:200],
                                  "symbols": [s for s in n.get("symbols", []) if s in WEIGHTS or s in ("QQQ", "SPY")],
                                  "tone": tone(n["headline"])})
    try:
        seen = set()
        for term in ("Wall Street stocks", "Nasdaq", "stock futures", "Fed rates Treasury yields"):
            q = urllib.parse.quote(f"{term} site:reuters.com when:1d")
            root = XET.fromstring(get(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"))
            for it in root.iter("item"):
                title = it.findtext("title", "").rsplit(" - Reuters", 1)[0]
                if title in seen or "Latest News" in title or not MARKET.search(title):
                    continue
                seen.add(title)
                t = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc)
                out["reuters"].append({"t": t.strftime("%Y-%m-%dT%H:%MZ"), "headline": title[:200], "tone": tone(title)})
        out["reuters"] = sorted(out["reuters"], key=lambda r: r["t"], reverse=True)[:12]
    except Exception as e:
        out["reuters_error"] = str(e)[:80]
    allh = out["alpaca"] + out["reuters"]
    out["counts"] = {k: sum(1 for r in allh if r["tone"] == k) for k in ("bull", "bear", "shock", "neutral")}
    return out


def part_sentiment():
    out = {}
    try:
        f = getj("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                 {"Referer": "https://www.cnn.com/", "Origin": "https://www.cnn.com"})["fear_and_greed"]
        out["fear_greed"] = {"score": round(f["score"], 1), "rating": f["rating"], "prev_close": round(f["previous_close"], 1),
                             "week_ago": round(f["previous_1_week"], 1)}
    except Exception as e:
        out["fear_greed"] = {"error": str(e)[:80]}
    try:
        out["liquid_perps"] = json.load(open(os.path.join(HERE, "sentiment.json")))
    except Exception:
        pass
    return out


def summarize(r):
    """Plain lean + caution flags. Not backtested: context for Archie, never a trade gate."""
    pts, why, caution = 0, [], []
    nq = r["futures"].get("NQ=F", {}).get("pct")
    if nq is not None and abs(nq) >= 0.25:
        pts += 1 if nq > 0 else -1; why.append(f"Nasdaq futures {nq:+.2f}%")
    mc = r["megacaps"].get("weighted_pct")
    if mc is not None and abs(mc) >= 0.25:
        pts += 1 if mc > 0 else -1; why.append(f"mega-caps pre-market {mc:+.2f}%")
    c = r["news"].get("counts", {})
    if c.get("bull", 0) - c.get("bear", 0) >= 4:
        pts += 1; why.append(f"news leans bullish ({c['bull']} vs {c['bear']})")
    elif c.get("bear", 0) - c.get("bull", 0) >= 4:
        pts -= 1; why.append(f"news leans bearish ({c['bear']} vs {c['bull']})")
    vix = r["futures"].get("^VIX", {})
    if vix.get("pct") is not None and vix["pct"] >= 8:
        pts -= 1; why.append(f"VIX up {vix['pct']:+.1f}%")
    if vix.get("last") and vix["last"] >= 25:
        caution.append(f"VIX high ({vix['last']:.1f}): bigger swings, pricier options")
    for e in r["calendar"].get("high_impact_before_10_35", []):
        caution.append(f"{e['event']} at {e['time_et']} ET (high impact, inside the trade window)")
    ern = r["earnings"]
    for k, label in (("reported_last_night", "reported last night"), ("today_before_open", "reports before the open")):
        for e in ern.get(k, []) if isinstance(ern, dict) else []:
            caution.append(f"{e['symbol']} {label}")
    if c.get("shock"):
        caution.append(f"{c['shock']} shock headline(s) overnight")
    fg = r["sentiment"].get("fear_greed", {})
    if fg.get("score") is not None and (fg["score"] <= 20 or fg["score"] >= 80):
        caution.append(f"Fear & Greed at {fg['score']} ({fg['rating']}): crowded mood, reversals more likely")
    lean = "BULLISH" if pts >= 2 else "BEARISH" if pts <= -2 else "MIXED"
    return {"lean": lean, "score": pts, "why": why, "caution": caution,
            "say": f"{lean} ({pts:+d})" + (": " + "; ".join(why) if why else "") +
                   (". Watch: " + "; ".join(caution) if caution else "")}


def run(log=print):
    now = datetime.now(ET)
    r = {"at_et": now.strftime("%Y-%m-%d %H:%M"), "for_session": now.date().isoformat(),
         "note": "Info only, not backtested. Never places or blocks a trade."}
    for name, fn in (("futures", part_futures), ("megacaps", part_megacaps), ("calendar", lambda: part_calendar(now)),
                     ("earnings", lambda: part_earnings(now)), ("news", lambda: part_news(now)), ("sentiment", part_sentiment)):
        try:
            r[name] = fn()
        except Exception as e:
            r[name] = {"error": str(e)[:120]}
    r["summary"] = summarize(r)
    os.makedirs(os.path.join(FEEDS, "history"), exist_ok=True)
    tmp = OUT + ".tmp"; json.dump(r, open(tmp, "w"), indent=1); os.replace(tmp, OUT)
    with open(os.path.join(FEEDS, "history", now.date().isoformat() + ".jsonl"), "a") as f:
        f.write(json.dumps(r) + "\n")
    log(f"INTEL {r['at_et']} ET: {r['summary']['say'][:300]}")
    return r


_done = globals().get("_done") or set()  # survives importlib.reload


def maybe_run(log):
    """Call from the bot loop; runs once at each RUN_AT time on weekdays (late start runs the latest missed slot)."""
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return
    hm = now.strftime("%H:%M")
    due = [t for t in RUN_AT if t <= hm and hm < "09:35"]
    if due and (now.date(), due[-1]) not in _done:
        _done.add((now.date(), due[-1]))

        def job():
            try:
                run(log)
            except Exception as e:
                log(f"INTEL failed: {e}")
        threading.Thread(target=job, daemon=True, name="intel").start()  # ~25 web calls; keep the bot loop free


if __name__ == "__main__":
    print(json.dumps(run()["summary"], indent=1))
