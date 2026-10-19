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

Usage: python3 intel.py            (one run now, prints the summary); python3 intel.py catalysts | squawk | darkpool [YYYY-MM-DD] (one part only)
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
    try:
        last_dp = sorted(f for f in os.listdir(DP_DIR) if f.endswith(".json"))[-1]
        r["darkpool_yesterday"] = json.load(open(os.path.join(DP_DIR, last_dp)))["say"]
    except Exception:
        pass
    pre, row = squawk_status()
    if row:
        caution.append(f"squawk says {pre}: {row['catalyst']} ({row['directional_bias']})")
    try:
        for c_ in json.load(open(CAT_OUT))[:3]:
            caution.append(f"verified {c_['catalyst_type']} ({c_['bias_score']}, {c_['execution_action']}): {c_['hard_data_metric'][:90]}")
    except Exception:
        pass
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


# ---------------------------------------------------------------------------------------------------------------------
# Verified catalyst filter (Ryan 2026-10-17 spec "QQQ/MNQ real-time news verification & bias filter").
# Reads Yahoo Finance, CNBC, Nasdaq.com, Seeking Alpha and Reuters (via Google News; reuters.com blocks bots), keeps only
# MACRO (FOMC, CPI, PPI, NFP), EARNINGS (NVDA, AAPL, MSFT, AMZN, GOOGL results/guidance/filings) and STRUCTURAL
# (Nasdaq-100 rebalance/adds/deletes, big block trades or QQQ outflows) headlines, drops hype words, analyst opinion and
# forum chatter, and only reports an event once at least two different sites carry it. Writes feeds/qqq-catalysts.json
# as a JSON array in Ryan's format. Info only like the rest of this file: Archie decides whether any action is obeyed.
CAT_OUT = os.path.join(FEEDS, "qqq-catalysts.json")
CAT_STATE = os.path.join(FEEDS, "catalyst-state.json")
TOP5 = {"NVDA": r"nvidia|nvda", "AAPL": r"apple|aapl", "MSFT": r"microsoft|msft", "AMZN": r"amazon|amzn",
        "GOOGL": r"alphabet|google|googl"}
GN = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="
CAT_FEEDS = [  # (site, url)
    ("yahoo.com", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=QQQ,%5EIXIC,NVDA,AAPL,MSFT,AMZN,GOOGL&region=US&lang=en-US"),
    ("cnbc.com", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("cnbc.com", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("cnbc.com", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839135"),
    ("nasdaq.com", "https://www.nasdaq.com/feed/rssoutbound?category=Markets"),
    ("nasdaq.com", "https://www.nasdaq.com/feed/rssoutbound?category=Earnings"),
    ("nasdaq.com", "https://www.nasdaq.com/feed/rssoutbound?category=Stocks"),
    ("seekingalpha.com", "https://seekingalpha.com/market_currents.xml"),
] + [("reuters.com", GN + urllib.parse.quote(f"{q} site:reuters.com when:2d")) for q in (
    "Fed interest rates", "US consumer prices CPI", "US producer prices", "US payrolls jobs report",
    "Nvidia results", "Apple results", "Microsoft results", "Amazon results", "Alphabet results", "Nasdaq-100 index")]
MACRO_KEYS = [("FOMC", r"\b(fomc|rate decision|fed (holds|cuts|raises|hikes|keeps|leaves|lowers)( interest)? rates?|"
                      r"fed (cuts|raises|hikes|lowers) .*basis points?)\b"),
              ("CPI", r"\b(cpi|consumer prices?|consumer price index)\b"),
              ("PPI", r"\b(ppi|producer prices?|producer price index|wholesale (prices|inflation))\b"),
              ("NFP", r"\b(nonfarm|non-farm|payrolls?|jobs report|unemployment rate)\b")]
EARN_WORDS = re.compile(r"\b(results?|earnings|quarter(ly)?|q[1-4]|revenue|sales|eps|profit|guidance|outlook|forecast|"
                        r"10-[qk]|8-k|sec filing|files?)\b", re.I)
STRUCT = re.compile(r"(nasdaq-?100|ndx).*(rebalanc|add(s|ed)?|delet|remov|join|replace|reconstitut)|"
                    r"(rebalanc|reconstitut).*(nasdaq|ndx)|block trades?|\bqqq\b.*outflows?|outflows?.*\bqqq\b", re.I)
HYPE = re.compile(r"\b(skyrocket\w*|crash\w*|doomed|soar\w*|plung\w*|plummet\w*|explod\w*|meltdown|collaps\w*|"
                  r"moonshot|to the moon|must-?buy|millionaire|bubble)\b", re.I)
OPINION = re.compile(r"\?|\b(analysts?|upgrades?d?|downgrades?d?|price target|should you|here'?s (why|what|how)|"
                     r"opinion|commentary|column|buy (now|before)|reddit|wallstreetbets|stocktwits|my take|i think|"
                     r"could|might|may)\b", re.I)


def _feed_items(site, url):
    root = XET.fromstring(get(url, timeout=12))
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        if site == "reuters.com":
            if not title.endswith("- Reuters"):
                continue
            title = title.rsplit(" - Reuters", 1)[0]
        try:
            t = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc)
        except Exception:
            continue
        yield {"site": site, "t": t, "title": title, "link": (it.findtext("link") or "").strip()}


def _classify(title):
    """(catalyst_type, event key) or None. Hype/opinion headlines are dropped before this is called."""
    for key, rx in MACRO_KEYS:
        if re.search(rx, title, re.I):
            return "MACRO", key
    for sym, rx in TOP5.items():
        if re.search(rf"\b({rx})\b", title, re.I) and EARN_WORDS.search(title):
            return "EARNINGS", sym
    if STRUCT.search(title):
        return "STRUCTURAL", "NDX-STRUCTURE"
    return None


def _metric(titles):
    """The most number-heavy headline, trimmed: e.g. 'CPI rises 0.4% in September vs 0.3% expected'."""
    best = max(titles, key=lambda h: (len(re.findall(r"\d[\d.,]*\s?(%|bp|basis points?|billion|million|cents?)|\$\d", h, re.I)),
                                       bool(re.search(r"expect|estimate|forecast|consensus", h, re.I))))
    return best[:180]


def _bias(ctype, key, titles):
    text = " ".join(titles).lower()
    up = down = 0
    if ctype == "MACRO" and key in ("CPI", "PPI"):
        down += len(re.findall(r"hotter|higher than expected|accelerat|jumps|rises more|above (expectations|estimates)|heats up", text))
        up += len(re.findall(r"cooler|softer|lower than expected|slows|eases|below (expectations|estimates)|cools", text))
    elif ctype == "MACRO" and key == "FOMC":
        up += len(re.findall(r"\bcuts?\b|dovish|lower rates", text))
        down += len(re.findall(r"\bhikes?\b|raises rates|hawkish", text))
    elif ctype == "MACRO" and key == "NFP":
        up += len(re.findall(r"cooler|slows|below expectations|moderat", text))  # soft but not collapsing = rate-cut hopes
        down += len(re.findall(r"hotter|surges|blowout|above expectations|unemployment (jumps|rises to)", text))
    else:
        up += len(re.findall(r"\bbeats?\b|tops|above estimates|raises (guidance|outlook|forecast)|record (revenue|profit)", text))
        down += len(re.findall(r"\bmiss(es)?\b|below estimates|cuts? (guidance|outlook|forecast)|weak (guidance|outlook)|warns", text))
    return "BULLISH" if up > down else "BEARISH" if down > up else "NEUTRAL"


def scan_catalysts(log=print, hours=36):
    now = datetime.now(timezone.utc)
    reuters_due = time.time() - _cat_last.get("reuters", 0) >= 600  # Google News rate-limits; Reuters every 10 min
    if reuters_due:
        _cat_last["reuters"] = time.time()
    try:
        st = json.load(open(CAT_STATE))
    except Exception:
        st = {"seen": []}
    seen = {(r["site"], r["title"]): r for r in st["seen"] if now - datetime.fromisoformat(r["t"]) < timedelta(hours=hours)}
    errors = []
    for site, url in CAT_FEEDS:
        if site == "reuters.com":
            if not reuters_due:
                continue
            time.sleep(2)
        try:
            for it in _feed_items(site, url):
                if now - it["t"] > timedelta(hours=hours) or HYPE.search(it["title"]) or OPINION.search(it["title"]):
                    continue
                c = _classify(it["title"])
                if c:
                    seen[(site, it["title"])] = {"site": site, "t": it["t"].isoformat(), "title": it["title"],
                                                 "link": it["link"], "type": c[0], "key": c[1]}
        except Exception as e:
            errors.append(f"{site}: {str(e)[:60]}")
    rows = list(seen.values())
    # Group by event; an event counts once two or more different sites carry it.
    events = {}
    for r in rows:
        events.setdefault((r["type"], r["key"]), []).append(r)
    cal = []
    try:
        cal = [e for e in getj("https://nfs.faireconomy.media/ff_calendar_thisweek.json") if e.get("country") == "USD"]
    except Exception as e:
        errors.append(f"calendar: {str(e)[:60]}")
    out = []
    for (ctype, key), rs in events.items():
        sites = sorted({r["site"] for r in rs})
        if len(sites) < 2:
            continue
        rs.sort(key=lambda r: r["t"])
        first = datetime.fromisoformat(rs[0]["t"])
        urls = []
        for s_ in sites:
            urls.append(next(r["link"] for r in rs if r["site"] == s_))
        titles = [r["title"] for r in rs]
        metric = _metric(titles)
        if ctype == "MACRO":
            m = next((e for e in cal if re.search(dict(MACRO_KEYS)[key], e["title"], re.I) and
                      abs(datetime.fromisoformat(e["date"]).astimezone(timezone.utc) - first) < timedelta(hours=18)), None)
            if m and (m.get("forecast") or m.get("previous")):
                metric += f" | {m['title']}: forecast {m.get('forecast') or 'n/a'}, previous {m.get('previous') or 'n/a'}"
        age = now - first
        if ctype == "MACRO" and age < timedelta(minutes=30):
            action = "HALT_TRADING"
        elif ctype in ("MACRO", "EARNINGS") and age < timedelta(hours=18):
            action = "VOLATILITY_SCALING"
        else:
            action = "NORMAL"
        out.append({"timestamp": first.strftime("%Y-%m-%d %H:%M:%S UTC"), "catalyst_type": ctype,
                    "primary_source_urls": urls[:4], "asset_affected": "QQQ / MNQ", "hard_data_metric": metric,
                    "bias_score": _bias(ctype, key, titles), "execution_action": action})
    # Scheduled high-impact US release within 15 minutes either side: halt even before headlines confirm it.
    for e in cal:
        if e.get("impact") != "High" or not any(re.search(rx, e["title"], re.I) for _, rx in MACRO_KEYS):
            continue
        t = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
        if abs(now - t) <= timedelta(minutes=15):
            out.append({"timestamp": t.strftime("%Y-%m-%d %H:%M:%S UTC"), "catalyst_type": "MACRO",
                        "primary_source_urls": ["https://www.forexfactory.com/calendar"], "asset_affected": "QQQ / MNQ",
                        "hard_data_metric": f"Scheduled: {e['title']} (forecast {e.get('forecast') or 'n/a'}, "
                                            f"previous {e.get('previous') or 'n/a'})",
                        "bias_score": "NEUTRAL", "execution_action": "HALT_TRADING"})
    out.sort(key=lambda r: r["timestamp"], reverse=True)
    os.makedirs(FEEDS, exist_ok=True)
    tmp = CAT_OUT + ".tmp"; json.dump(out, open(tmp, "w"), indent=1); os.replace(tmp, CAT_OUT)
    st = {"checked": now.isoformat()[:16] + "Z", "errors": errors, "seen": list(seen.values())}
    json.dump(st, open(CAT_STATE, "w"), indent=1)
    new = [r for r in out if r["timestamp"] not in set(_cat_logged)]
    for r in new:
        _cat_logged.add(r["timestamp"])
        log(f"CATALYST {r['catalyst_type']} {r['bias_score']} {r['execution_action']}: {r['hard_data_metric'][:140]}")
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Squawk filter (Ryan 2026-10-17 "intraday scalping news filter"; he OK'd Financial Juice + a suspend-entries flag).
# Every minute: Financial Juice's free squawk headlines (SQUAWK), Alpaca/Benzinga headlines for the top-5 (RAW_TERMINAL)
# and the ForexFactory countdown (CALENDAR). Keeps only Fed/FOMC speaker lines, US CPI/PPI/NFP prints, unscheduled
# NVDA/AAPL/MSFT/AMZN/GOOGL shocks and QQQ/Nasdaq block-trade lines; reads only the headline's first sentence.
# Writes feeds/qqq-squawk.json (last 2 hours, newest first) in Ryan's packet format. squawk_status() gives the
# precaution in force right now. Info only: Archie decides whether the bot obeys SUSPEND_ENTRIES / MACRO_RELEASE_HALT.
SQ_OUT = os.path.join(FEEDS, "qqq-squawk.json")
FJ_URL = "https://www.financialjuice.com/feed.ashx?xy=rss"
SQ_FED = re.compile(r"\b(fed'?s?|fomc|powell|warsh|waller|jefferson|williams|bowman|barr|cook|kugler|logan|goolsbee|"
                    r"hammack|musalem|schmid|bostic|daly|kashkari|harker|barkin|collins|miran)\b", re.I)
SQ_DATA = re.compile(r"\bUS\b.*\b(CPI|PPI|non-?farm|payrolls|unemployment rate|core inflation|consumer price|producer price)\b", re.I)
SQ_TOP5 = re.compile(r"\b(nvidia|nvda|apple|aapl|microsoft|msft|amazon|amzn|alphabet|google|googl)\b", re.I)
SQ_SHOCK = re.compile(r"\b(antitrust|doj|ftc|lawsuit|sues|sued|probe|investigat\w*|fine[ds]?|ban(s|ned)?|export (curbs?|"
                      r"controls?|ban)|delay(s|ed)?|recall\w*|fda|resign\w*|steps? down|departs?|departure|ousted|fired|"
                      r"outage|hack\w*|breach|halt\w*|guidance|warns?|cuts?|downgrade\w*|approv\w*|wins?|record)\b", re.I)
SQ_BLOCK = re.compile(r"(block trade|dark pool|order imbalance|moc imbalance|imbalance).*(qqq|nasdaq|ndx|nq)|"
                      r"(qqq|nasdaq|ndx|nq).*(block trade|dark pool|imbalance)", re.I)
SQ_HAWK = re.compile(r"no (need|rush|hurry)|patien|higher for longer|hike|not (yet )?(time|ready) to cut|inflation (risk|"
                     r"concern|sticky|too high)|hold rates|restrictive", re.I)
SQ_DOVE = re.compile(r"\bcut(s|ting)?\b|eas(e|ing)|lower rates|labor market (weak|soft|cool)|dovish|appropriate to reduce", re.I)
SQ_GOOD = re.compile(r"\b(approv\w*|wins?|record|beats?|raises guidance|cleared|dismiss\w*)\b", re.I)


def _sq_short(h, n=10):
    h = re.sub(r"^FinancialJuice:\s*", "", h).strip()
    h = re.split(r"(?<=[.!?])\s", h)[0]
    w = h.split()
    return " ".join(w[:n]).rstrip(",;:")


def _sq_classify(h):
    """(kind, bias, precaution) or None."""
    if SQ_DATA.search(h):
        m = re.search(r"actual\s*(-?[\d.]+).*?(forecast|expected|est\.?)\s*(-?[\d.]+)", h, re.I)
        bias = "NO_BIAS"
        if m and not re.search(r"payrolls|non-?farm|unemployment", h, re.I):
            a, f = float(m.group(1)), float(m.group(3))
            bias = "SHARP_BEARISH" if a > f else "SHARP_BULLISH" if a < f else "NO_BIAS"
        return "DATA", bias, "MACRO_RELEASE_HALT"
    if SQ_FED.search(h) and re.search(r"\b(rate|rates|inflation|cut|hike|policy|labor|economy|balance sheet|qt)\b", h, re.I):
        bias = "SHARP_BEARISH" if SQ_HAWK.search(h) else "SHARP_BULLISH" if SQ_DOVE.search(h) else "NO_BIAS"
        return "FED", bias, "SUSPEND_ENTRIES" if bias != "NO_BIAS" else "CLEAR_MOMENTUM"
    if SQ_TOP5.search(h) and SQ_SHOCK.search(h):
        good = SQ_GOOD.search(h) and not re.search(r"antitrust|probe|lawsuit|delay|resign|ban|recall|warns|cut", h, re.I)
        return "SHOCK", "SHARP_BULLISH" if good else "SHARP_BEARISH", "SUSPEND_ENTRIES"
    if SQ_BLOCK.search(h):
        bias = "SHARP_BULLISH" if re.search(r"buy", h, re.I) else "SHARP_BEARISH" if re.search(r"sell", h, re.I) else "NO_BIAS"
        return "BLOCK", bias, "SUSPEND_ENTRIES"
    return None


def scan_squawk(log=print):
    now = datetime.now(timezone.utc)
    try:
        old = json.load(open(SQ_OUT))
    except Exception:
        old = []
    keep = {(r["catalyst"], r["source_channel"]): r for r in old
            if now - datetime.fromisoformat(r["_t"]) < timedelta(hours=2)}
    heads = []
    try:
        root = XET.fromstring(get(FJ_URL, timeout=10))
        for it in root.iter("item"):
            heads.append(("SQUAWK", parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc), it.findtext("title") or ""))
    except Exception as e:
        log(f"SQUAWK financialjuice failed: {str(e)[:80]}")
    if os.environ.get("APCA_API_KEY_ID"):
        try:
            q = urllib.parse.urlencode({"symbols": "NVDA,AAPL,MSFT,AMZN,GOOGL,GOOG,QQQ", "limit": 30, "sort": "desc",
                                        "start": (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")})
            d = getj(f"https://data.alpaca.markets/v1beta1/news?{q}", {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
                                                                     "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]})
            for n in d.get("news", []):
                heads.append(("RAW_TERMINAL", datetime.fromisoformat(n["created_at"].replace("Z", "+00:00")), n["headline"]))
        except Exception as e:
            log(f"SQUAWK alpaca failed: {str(e)[:80]}")
    new = []
    for ch, t, h in heads:
        if now - t > timedelta(hours=2) or OPINION.search(_sq_short(h, 40)) and ch == "RAW_TERMINAL":
            continue
        c = _sq_classify(_sq_short(h, 40))
        if not c:
            continue
        key = (_sq_short(h), ch)
        if key in keep:
            continue
        keep[key] = {"timestamp_utc": t.strftime("%H:%M:%S.") + f"{t.microsecond // 1000:03d}", "source_channel": ch,
                     "catalyst": _sq_short(h), "directional_bias": c[1], "scalp_precaution": c[2], "_t": t.isoformat()}
        new.append(keep[key])
    try:  # countdown: a high-impact US release within 15 minutes either side
        for e in getj("https://nfs.faireconomy.media/ff_calendar_thisweek.json"):
            if e.get("country") != "USD" or e.get("impact") != "High":
                continue
            t = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
            if abs(now - t) <= timedelta(minutes=15):
                key = (_sq_short(e["title"] + " release"), "CALENDAR")
                if key not in keep:
                    keep[key] = {"timestamp_utc": t.strftime("%H:%M:%S.000"), "source_channel": "CALENDAR",
                                 "catalyst": key[0], "directional_bias": "NO_BIAS",
                                 "scalp_precaution": "MACRO_RELEASE_HALT", "_t": t.isoformat()}
                    new.append(keep[key])
    except Exception:
        pass
    rows = sorted(keep.values(), key=lambda r: r["_t"], reverse=True)
    os.makedirs(FEEDS, exist_ok=True)
    tmp = SQ_OUT + ".tmp"; json.dump(rows, open(tmp, "w"), indent=1); os.replace(tmp, SQ_OUT)
    for r in new:
        if r["scalp_precaution"] != "CLEAR_MOMENTUM":
            log(f"SQUAWK {r['scalp_precaution']} {r['directional_bias']}: {r['catalyst']}")
    return rows


def squawk_status():
    """The precaution in force now: MACRO_RELEASE_HALT for 15 min after a data print / around a scheduled release,
    SUSPEND_ENTRIES for 5 min after a Fed/top-5/block shock, else CLEAR_MOMENTUM. Returns (precaution, row or None)."""
    now = datetime.now(timezone.utc)
    try:
        rows = json.load(open(SQ_OUT))
    except Exception:
        return "CLEAR_MOMENTUM", None
    for r in rows:
        age = now - datetime.fromisoformat(r["_t"])
        if r["scalp_precaution"] == "MACRO_RELEASE_HALT" and timedelta(minutes=-15) <= age <= timedelta(minutes=15):
            return "MACRO_RELEASE_HALT", r
    for r in rows:
        age = now - datetime.fromisoformat(r["_t"])
        if r["scalp_precaution"] == "SUSPEND_ENTRIES" and timedelta(0) <= age <= timedelta(minutes=5):
            return "SUSPEND_ENTRIES", r
    return "CLEAR_MOMENTUM", None


# ---------------------------------------------------------------------------------------------------------------------
# Dark pool / block recap (Ryan 2026-10-17: "not for live trading but for a day late analysis as to why the charts moved").
# After the close (19:30 ET) it reads that day's full QQQ tape from Alpaca (SIP, free once 15 min old), pulls every print
# of 10,000+ shares (about $7M), marks dark-pool prints (FINRA TRF/ADF, exchange "D") vs lit, guesses buyer/seller with a
# tick test, finds the day's biggest 15-minute moves from 1-min bars and lists the blocks in the 10 minutes before each,
# and adds the SqueezeMetrics DIX (dark-pool buying index), FINRA off-exchange short volume and that day's squawks.
# Writes feeds/darkpool/YYYY-MM-DD.json. Review only: nothing here feeds the live trade.
DP_DIR = os.path.join(FEEDS, "darkpool")
BLOCK_MIN = 10000
SKIP_COND = {"O", "Q", "6", "M", "9", "4", "W", "Z"}  # auction open/close, official open/close, corrected/odd-timed prints


def _alpaca(path, params):
    h = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"], "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]}
    return getj(f"https://data.alpaca.markets{path}?{urllib.parse.urlencode(params)}", h)


def darkpool_recap(day=None, log=print):
    day = day or datetime.now(ET).date().isoformat()
    o = datetime.fromisoformat(day + "T09:30").replace(tzinfo=ET).astimezone(timezone.utc)
    c = datetime.fromisoformat(day + "T16:00").replace(tzinfo=ET).astimezone(timezone.utc)
    z = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
    tot = dark = 0; blocks = []; last = None; tok = None
    while True:  # ~100 pages of 10k trades for a full QQQ day
        p = {"start": z(o), "end": z(c), "limit": 10000, "feed": "sip"}
        if tok:
            p["page_token"] = tok
        d = _alpaca("/v2/stocks/QQQ/trades", p)
        for t in d.get("trades") or []:
            tot += t["s"]
            if t["x"] == "D":
                dark += t["s"]
            cond = set(t.get("c") or [])
            if t["s"] >= BLOCK_MIN and not cond & SKIP_COND:
                side = "buy?" if last and t["p"] > last else "sell?" if last and t["p"] < last else "even"
                blocks.append({"t_et": datetime.fromisoformat(t["t"][:26].rstrip("Z") + "+00:00").astimezone(ET).strftime("%H:%M:%S"),
                               "shares": t["s"], "price": t["p"], "usd_m": round(t["s"] * t["p"] / 1e6, 1),
                               "venue": "dark" if t["x"] == "D" else "lit", "side_guess": side,
                               "contingent": bool(cond & {"7", "V"})})
            if t["s"] < BLOCK_MIN:
                last = t["p"]
        tok = d.get("next_page_token")
        if not tok:
            break
    if not tot:
        log(f"DARKPOOL {day}: no trades (holiday or data not ready)")
        return None
    bars = (_alpaca("/v2/stocks/bars", {"symbols": "QQQ", "timeframe": "1Min", "start": z(o), "end": z(c), "limit": 10000,
                                         "feed": "sip"}).get("bars") or {}).get("QQQ", [])
    for b in bars:
        b["et"] = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET).strftime("%H:%M")
    moves = []
    for i in range(0, max(0, len(bars) - 15)):
        a, b = bars[i], bars[i + 15]
        moves.append((abs(b["c"] / a["o"] - 1), i, round((b["c"] / a["o"] - 1) * 100, 2)))
    picked = []
    for m in sorted(moves, reverse=True):
        if all(abs(m[1] - q[1]) >= 30 for q in picked):
            picked.append(m)
        if len(picked) == 4:
            break
    big_moves = []
    for _, i, pct in sorted(picked, key=lambda m: m[1]):
        start, end = bars[i]["et"], bars[i + 15]["et"]
        lo = (datetime.strptime(start, "%H:%M") - timedelta(minutes=10)).strftime("%H:%M")
        before = [b for b in blocks if lo <= b["t_et"][:5] <= end]
        bias = sum(b["usd_m"] * (1 if b["side_guess"] == "buy?" else -1 if b["side_guess"] == "sell?" else 0)
                   for b in before if not b["contingent"])
        big_moves.append({"from_et": start, "to_et": end, "pct": pct, "blocks_near": len(before),
                          "block_usd_m": round(sum(b["usd_m"] for b in before), 1), "net_guess_usd_m": round(bias, 1),
                          "biggest": sorted(before, key=lambda b: -b["shares"])[:3]})
    out = {"date": day, "qqq_volume": tot, "dark_pct": round(dark / tot * 100, 1), "blocks": len(blocks),
           "block_usd_m": round(sum(b["usd_m"] for b in blocks), 1),
           "dark_blocks": sum(1 for b in blocks if b["venue"] == "dark"),
           "net_block_guess_usd_m": round(sum(b["usd_m"] * (1 if b["side_guess"] == "buy?" else -1 if b["side_guess"] == "sell?" else 0)
                                              for b in blocks if not b["contingent"]), 1),
           "big_moves": big_moves, "top_blocks": sorted(blocks, key=lambda b: -b["shares"])[:10],
           "note": "side_guess is a tick test (print above/below the last small trade), only a hint. Contingent prints "
                   "(often hedges) are left out of the net. Review only, never used by the live trade."}
    try:
        rows = get("https://squeezemetrics.com/monitor/static/DIX.csv").decode().strip().splitlines()
        hist = {r.split(",")[0]: r.split(",") for r in rows[1:]}
        if day in hist:
            dix = [float(hist[k][2]) for k in sorted(hist)[-20:]]
            out["dix"] = {"value": round(float(hist[day][2]) * 100, 1), "avg_20d": round(sum(dix) / len(dix) * 100, 1),
                          "meaning": "share of S&P 500 dark-pool volume that was buying; above ~45 = dip buying"}
    except Exception as e:
        out["dix_error"] = str(e)[:80]
    try:
        for line in get(f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{day.replace('-', '')}.txt").decode().splitlines():
            f = line.split("|")
            if len(f) > 4 and f[1] == "QQQ":
                out["finra_offexchange"] = {"short_volume": round(float(f[2])), "total_volume": round(float(f[4])),
                                            "short_pct": round(float(f[2]) / float(f[4]) * 100, 1),
                                            "meaning": "ETF market makers short to fill buyers, so 50-70% is normal; watch big jumps vs usual"}
    except Exception as e:
        out["finra_error"] = str(e)[:80]
    try:
        sq = [r for r in json.load(open(SQ_OUT)) if r["_t"][:10] == day and r["scalp_precaution"] != "CLEAR_MOMENTUM"]
        out["squawks"] = [{k: r[k] for k in ("timestamp_utc", "catalyst", "directional_bias")} for r in sq][:15]
    except Exception:
        pass
    mv = "; ".join(f"{m['from_et']}-{m['to_et']} {m['pct']:+.2f}% ({m['blocks_near']} blocks, net {m['net_guess_usd_m']:+.0f}M)"
                   for m in big_moves)
    out["say"] = (f"{day}: dark pools {out['dark_pct']}% of QQQ volume, {out['blocks']} blocks ${out['block_usd_m']:.0f}M "
                  f"(net guess {out['net_block_guess_usd_m']:+.0f}M)" +
                  (f", DIX {out['dix']['value']} vs {out['dix']['avg_20d']} avg" if "dix" in out else "") +
                  (f". Biggest moves: {mv}" if mv else ""))
    os.makedirs(DP_DIR, exist_ok=True)
    json.dump(out, open(os.path.join(DP_DIR, day + ".json"), "w"), indent=1)
    log(f"DARKPOOL {out['say'][:300]}")
    return out


_cat_logged = globals().get("_cat_logged") or set()
_cat_last = globals().get("_cat_last") or {"t": 0}


_done = globals().get("_done") or set()  # survives importlib.reload


def maybe_run(log):
    """Call from the bot loop: the catalyst scan on its own timer, and the full intel run once at each RUN_AT time on
    weekdays (late start runs the latest missed slot)."""
    now = datetime.now(ET)
    # Squawk filter: every minute on weekdays 6:00-16:00 ET, every 10 minutes otherwise.
    sq_every = 60 if now.weekday() < 5 and 6 <= now.hour < 16 else 600
    if time.time() - _cat_last.get("squawk", 0) >= sq_every:
        _cat_last["squawk"] = time.time()

        def sjob():
            try:
                scan_squawk(log)
            except Exception as e:
                log(f"SQUAWK scan failed: {e}")
        threading.Thread(target=sjob, daemon=True, name="squawk").start()
    # Catalyst filter: every 2 minutes on weekdays 6:00-16:00 ET, every 15 minutes otherwise.
    every = 120 if now.weekday() < 5 and 6 <= now.hour < 16 else 900
    if time.time() - _cat_last["t"] >= every:
        _cat_last["t"] = time.time()

        def cjob():
            try:
                scan_catalysts(log)
            except Exception as e:
                log(f"CATALYST scan failed: {e}")
        threading.Thread(target=cjob, daemon=True, name="catalysts").start()
    if now.weekday() >= 5:
        return
    hm = now.strftime("%H:%M")
    dp_day = now.date().isoformat()
    if hm >= "19:30" and ("dp", dp_day) not in _done and os.environ.get("APCA_API_KEY_ID"):
        _done.add(("dp", dp_day))
        if not os.path.exists(os.path.join(DP_DIR, dp_day + ".json")):
            def djob():
                try:
                    darkpool_recap(dp_day, log)
                except Exception as e:
                    log(f"DARKPOOL failed: {e}")
            threading.Thread(target=djob, daemon=True, name="darkpool").start()
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
    import sys
    if "darkpool" in sys.argv:
        print(json.dumps(darkpool_recap(sys.argv[-1] if sys.argv[-1][:2] == "20" else None), indent=1))
    elif "squawk" in sys.argv:
        print(json.dumps(scan_squawk(), indent=1)); print(squawk_status()[0])
    elif "catalysts" in sys.argv:
        print(json.dumps(scan_catalysts(), indent=1))
    else:
        print(json.dumps(run()["summary"], indent=1))
