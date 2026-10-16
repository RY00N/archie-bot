"""Archie's news watcher (Ryan 2026-10-16: "you need someone who is looking at news at all times while you trade").

Called from futures_sim.tick about once a minute. On weekdays 6:00-16:00 ET it reads new Alpaca (Benzinga)
headlines for the index ETFs and the mega-caps that move MNQ/MES, and flags shock headlines
(Fed/tariff/war/crash/mega-cap warnings). Results go to news-watch.json, which the desk's News bot shows.
ALERT ONLY: it never changes what the paper book does (any trading effect needs Ryan's OK first).
"""
import json, os, re, time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "news-watch.json")
ET = ZoneInfo("America/New_York")
SYMS = ["SPY", "QQQ", "NVDA", "MSFT", "AAPL", "AMZN", "META", "GOOGL", "AVGO", "TSLA"]
ALERT_MIN = 30  # an alert stays up this long
SHOCK = [
    ("Fed", r"\b(fed|fomc|powell|waller|jefferson|williams)\b.*\b(emergency|surprise|unexpected|cut|hike|hawkish|dovish)\b"),
    ("Tariffs/trade", r"\btariff|trade war|export (ban|curb|control)|sanction"),
    ("War/geo", r"\b(war|invasion|invade|missile|airstrike|attack(s|ed)?|troops|nuclear|ceasefire)\b"),
    ("Market drop", r"\b(crash|plunge|plummet|tumble|sell-?off|rout|circuit breaker|trading halt)"),
    ("Company warning", r"\b(cuts? (guidance|outlook|forecast)|warns|profit warning|downgrade|recall|probe|antitrust|sec charges|bankrupt)"),
    ("Data shock", r"\b(cpi|inflation|jobs report|payrolls|gdp|unemployment)\b.*\b(hotter|cooler|surge|jump|unexpected|beats|misses)\b"),
]
SHOCK = [(k, re.compile(p, re.I)) for k, p in SHOCK]
_last = 0


def load():
    try:
        return json.load(open(OUT))
    except Exception:
        return {"since": None, "checked": None, "recent": [], "alert": None}


def check(log):
    """Poll once a minute in the watch window; safe to call more often."""
    global _last
    if time.time() - _last < 60:
        return
    _last = time.time()
    now = datetime.now(ET)
    if now.weekday() >= 5 or not (6 * 60 <= now.hour * 60 + now.minute < 16 * 60):
        return
    import archie_bot as bot
    st = load()
    utc = datetime.now(timezone.utc)
    since = st.get("since") or (utc - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = bot.api("GET", bot.DATA_API, "/v1beta1/news", {"symbols": ",".join(SYMS), "start": since, "limit": 50, "sort": "asc"})
    for n in d.get("news", []):
        st["since"] = max(st.get("since") or "", n["created_at"])
        if any(r["id"] == n.get("id") for r in st["recent"]):
            continue
        why = next((k for k, rx in SHOCK if rx.search(n["headline"])), None)
        row = {"id": n.get("id"), "t": n["created_at"][:16] + "Z", "symbols": [s for s in n.get("symbols", []) if s in SYMS],
               "headline": n["headline"][:200], "shock": why}
        st["recent"] = (st["recent"] + [row])[-25:]
        if why:
            st["alert"] = {"t": row["t"], "why": why, "headline": row["headline"]}
            log(f"NEWS ALERT ({why}): {row['headline'][:140]}")
    st["checked"] = utc.isoformat()[:16] + "Z"
    json.dump(st, open(OUT, "w"), indent=1)


def active_alert():
    """The latest shock headline if it is under ALERT_MIN minutes old, plus when headlines were last checked."""
    st = load()
    a = st.get("alert")
    if a:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(a["t"].replace("Z", "+00:00"))
        if age > timedelta(minutes=ALERT_MIN):
            a = None
    return a, st.get("checked")
