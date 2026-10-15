"""Archie's PAPER prop-firm practice (Ryan 2026-10-14: "Paper prop first"): two simulated $50k Topstep-style accounts.

No money, no broker: fills simulated on free CME prices like futures_sim.py. Since 2026-10-15 (Ryan picked "one each")
each account runs its own strategy from the all-markets study (futures/allmkts/REPORT.md), both only on same-day news days
(news tags as in newscombo.py: a non-FOMC news day) with a quiet pre-open (|8:00->9:25 move| < 0.25 x 14-day avg range):
  Prop 1, 6 MES, "orb30": 9:30-10:00 range; first 5-min close outside it, needs that bar's volume above the day's running
          average and yesterday's range >= its 20-day median; stop 0.2 x avg range from entry, target 1R.
  Prop 2, 6 MYM, "sweep": 9:30-10:30, price trades through yesterday's high (low) and a 5-min bar closes back inside ->
          fade it; stop just past the sweep extreme (+0.05 x avg range), skip if that is > 0.3 x avg range; target 1R.
Both: 4-hour time stop, flat 15:55, one trade a day. Sizes picked so no backtested eval start was blown (2 yrs, 97 starts).
Prop rules (third-party summaries of Topstep, not verified on Topstep's site): eval passes at +$3,000 with best day
<= 50% of profit; fails when balance hits the trailing max loss (end-of-day peak - $2,000, stops at $50k);
funded account: same trailing loss, locks at $0 profit, payout after 5 days of >= $150 (50% of profit, first <= $2,000,
later <= $5,000, Ryan keeps 90%). Fees tracked as if real: $109 per 4 weeks of eval, $149 to activate.
Settings live in rules.json "prop". Runs on the server from daytrader.tick, next to futures_sim."""
import json, os, re, statistics, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "prop-state.json")
LOG = os.path.join(HERE, "prop-log.jsonl")
ET = ZoneInfo("America/New_York")
SPEC = {"MES": {"y": "MES=F", "pt": 5.0, "tick": 0.25}, "MYM": {"y": "MYM=F", "pt": 0.5, "tick": 1.0}}
DEFAULTS = {"enabled": True, "accounts": 2, "size": 50000, "target": 3000, "max_loss": 2000,
            "consistency": 0.5, "win_day": 150, "payout_days": 5, "payout_frac": 0.5, "first_cap": 2000, "cap": 5000,
            "split": 0.9, "eval_fee": 109, "activation": 149, "quiet": 0.25, "flatten": 955, "fees": 1.5,
            "plans": [{"sym": "MES", "strat": "orb30", "contracts": 6, "or_min": 30, "stop_adr": 0.2, "tr": 1.0, "active": 1.0,
                       "last_entry": 690, "time_stop": 240},
                      {"sym": "MYM", "strat": "sweep", "contracts": 6, "last_entry": 630, "maxrisk": 0.3, "buf": 0.05, "tr": 1.0,
                       "time_stop": 240}],
            "cpi": [], "jobs": [], "fomc": []}
MEGA = {"NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "AVGO"}
RX = dict(  # same patterns and counts as the backtest (newscombo.py)
    mega_earn=(re.compile(r"\b(earnings|q[1-4] (results|eps|revenue)|quarterly results|beats|misses|tops estimates|guidance|eps \$|sales \$)"), 2),
    trade=(re.compile(r"\b(tariff|tariffs|trade war|trade deal|trade talks|export controls?|sanction)"), 3),
    geo=(re.compile(r"\b(iran|israel|missile|airstrike|invasion|war\b|ukraine|russia|middle east|strike on|ceasefire|shutdown)"), 3),
    data=(re.compile(r"\b(ppi|producer price|retail sales|gdp|pce|jobless claims|ism |pmi|consumer sentiment|housing starts)"), 2),
    fed=(re.compile(r"\b(powell|fed chair|fed's|fed governor|fomc|rate cut|rate hike|waller|williams|goolsbee|bowman|jefferson)"), 3))
ORDER = ["cpi", "jobs", "fomc", "mega_earn", "trade", "geo", "data", "fed"]  # newscombo.primary(): first tag decides the day
_last = 0


def cfg():
    try:
        return {**DEFAULTS, **json.load(open(os.path.join(HERE, "rules.json"))).get("prop", {})}
    except Exception:
        return dict(DEFAULTS)


def new_account(c, n, today):
    return {"name": f"Prop {n}", "phase": "eval", "attempt": 1, "bal": 0.0, "peak": 0.0, "best_day": 0.0, "day": 0.0,
            "win_days": 0, "payouts": [], "fees": c["eval_fee"], "fee_due": today, "history": []}


def load_state(c, today):
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {"date": "", "trades": [], "accounts": []}
    while len(st["accounts"]) < c["accounts"]:
        a = new_account(c, len(st["accounts"]) + 1, today); a["fee_due"] = (date.fromisoformat(today) + timedelta(days=28)).isoformat()
        st["accounts"].append(a)
    st.setdefault("slots", {})
    return st


def plan_of(c, i):
    return c["plans"][i % len(c["plans"])]


def yahoo(sym, interval, rng):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{SPEC[sym]['y']}?interval={interval}&range={rng}"
    d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read())
    r = d["chart"]["result"][0]; q = r["indicators"]["quote"][0]; out = []
    for i, t in enumerate(r["timestamp"]):
        if q["close"][i] is None:
            continue
        e = datetime.fromtimestamp(t, ET)
        out.append(dict(t=datetime.fromtimestamp(t, timezone.utc).isoformat()[:16] + "Z", d=e.date().isoformat(),
                        m=e.hour * 60 + e.minute, o=q["open"][i], h=q["high"][i], l=q["low"][i], c=q["close"][i], v=q["volume"][i] or 0))
    return out


def news_tags(c, today):
    """Same-day news: calendar days plus headline counts from Alpaca/Benzinga, prior 16:00 -> 9:30 ET."""
    t = set()
    if today in c["cpi"]: t.add("cpi")
    if today in c["jobs"]: t.add("jobs")
    if today in c["fomc"]: t.add("fomc")
    d = date.fromisoformat(today); prev = d - timedelta(days=3 if d.weekday() == 0 else 1)
    iso = lambda x, hh, mm: datetime(x.year, x.month, x.day, hh, mm, tzinfo=ET).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    H = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"], "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]}
    rows, tok = [], None
    for _ in range(10):
        q = dict(start=iso(prev, 16, 0), end=iso(d, 9, 30), limit=50, sort="desc",
                 symbols="SPY,QQQ,NVDA,AAPL,MSFT,AMZN,GOOGL,META,TSLA,AVGO")
        if tok: q["page_token"] = tok
        r = json.loads(urllib.request.urlopen(urllib.request.Request("https://data.alpaca.markets/v1beta1/news?" + urllib.parse.urlencode(q), headers=H), timeout=30).read())
        rows += [(set(n["symbols"]), n["headline"].lower()) for n in r.get("news", [])]
        tok = r.get("next_page_token")
        if not tok: break
    for k, (rx, need) in RX.items():
        if sum(1 for s, h in rows if rx.search(h) and (k != "mega_earn" or MEGA & s)) >= need:
            t.add(k)
    return sorted(t), len(rows)


def day_stats(b5, today):
    """Like allmkts/engine.build(): yesterday's high/low, 14-day avg range % (adr), yesterday's range vs its 20-day median,
    and the 8:00 -> 9:25 pre-open move %."""
    days = {}
    for x in b5:
        if date.fromisoformat(x["d"]).weekday() < 5:
            days.setdefault(x["d"], []).append(x)
    rngs, prev = [], None
    for d in sorted(days):
        if d >= today:
            break
        rth = [x for x in days[d] if 570 <= x["m"] < 960]
        if len(rth) < 60:
            continue
        hi, lo = max(x["h"] for x in rth), min(x["l"] for x in rth)
        rngs.append((hi - lo) / rth[-1]["c"] * 100); prev = (hi, lo, rth[-1]["c"])
    if not prev or len(rngs) < 10:
        return None
    pre = [x for x in days.get(today, []) if 480 <= x["m"] < 570]
    return dict(pH=prev[0], pL=prev[1], pC=prev[2], adr=statistics.mean(rngs[-14:]), prng_rel=rngs[-1] / statistics.median(rngs[-20:]),
                pm=(pre[-1]["c"] / pre[0]["o"] - 1) * 100 if len(pre) >= 6 else None)


def signal(p, X, rth, now_m):
    """First signal on closed 5-min bars today -> (side, bar, stop_dist) / "none" (no trade today) / None (keep waiting)."""
    if p["strat"] == "orb30":
        n = p["or_min"] // 5
        if len(rth) <= n:
            return None
        H, L, ref = max(x["h"] for x in rth[:n]), min(x["l"] for x in rth[:n]), rth[0]["o"]
        for i in range(n, len(rth)):
            x = rth[i]
            if x["m"] + 5 > now_m:
                return None  # bar not closed yet
            if x["m"] > p["last_entry"]:
                return "none"
            side = 1 if x["c"] > H else -1 if x["c"] < L else 0
            if not side:
                continue
            avgv = statistics.mean(max(y["v"], 1) for y in rth[:i])
            if max(x["v"], 1) <= avgv:
                return "none"  # breakout without volume: done for the day (as in the backtest)
            return side, x, X["adr"] / 100 * ref * p["stop_adr"]
        return None
    ext, side = None, 0  # sweep
    for x in rth:
        if x["m"] + 5 > now_m:
            return None
        if x["m"] > p["last_entry"]:
            return "none"
        if x["h"] > X["pH"]:
            ext = max(ext or x["h"], x["h"])
            if x["c"] < X["pH"]: side = -1
        elif x["l"] < X["pL"]:
            ext = min(ext or x["l"], x["l"])
            if x["c"] > X["pL"]: side = 1
        if side:
            buf = X["adr"] / 100 * x["c"] * p["buf"]; stop = ext + buf if side < 0 else ext - buf; dist = abs(x["c"] - stop)
            if dist <= 0 or dist > X["adr"] / 100 * x["c"] * p["maxrisk"]:
                return "none"
            return side, x, dist
    return None


def close_day(c, st, day):
    """End of a trading day for every account: trailing loss, pass/fail, winning days, payouts, fees."""
    for a in st["accounts"]:
        p = a["day"]; a["day"] = 0.0
        if a["phase"] == "failed":
            a.update(new_account(c, int(a["name"].split()[-1]), day) | {"attempt": a["attempt"] + 1, "fees": a["fees"] + c["eval_fee"],
                     "payouts": a["payouts"], "history": a["history"], "fee_due": (date.fromisoformat(day) + timedelta(days=28)).isoformat()})
            continue
        if a["phase"] == "eval":
            a["best_day"] = max(a["best_day"], p)
            if a["bal"] >= c["target"] and a["best_day"] <= c["consistency"] * a["bal"]:
                a["history"].append(f"{day}: passed eval at +${a['bal']:,.0f}")
                a.update(phase="funded", bal=0.0, peak=0.0, win_days=0, fees=a["fees"] + c["activation"])
                continue
        elif p >= c["win_day"]:
            a["win_days"] += 1
            if a["win_days"] >= c["payout_days"] and a["bal"] > 0:
                take = round(min(c["payout_frac"] * a["bal"], c["cap"] if a["payouts"] else c["first_cap"]), 2)
                a["bal"] -= take; a["win_days"] = 0
                a["payouts"].append({"date": day, "amount": take, "to_ryan": round(take * c["split"], 2)})
                a["history"].append(f"{day}: payout ${take:,.0f} (Ryan keeps ${take * c['split']:,.0f})")
        a["peak"] = max(a["peak"], a["bal"])


def floor(c, a):
    if a["phase"] == "funded" and a["payouts"]:
        return 0.0
    return min(a["peak"] - c["max_loss"], 0.0)


def news_plan(c, st, today):
    """Once a day: is this a non-FOMC news day? (shared by both accounts)"""
    N = st.setdefault("news", {})
    if N.get("date") != today:
        try:
            tags, n = news_tags(c, today); N.update(date=today, tags=tags, headlines=n, error=None)
        except Exception as e:
            N.update(date=today, tags=[], headlines=0, error=str(e)[:120])
        prim = next((k for k in ORDER if k in N["tags"]), "none")
        N["ok"] = prim not in ("none", "fomc")
    return N


def manage(c, a, s, sp, b1, today, log):
    """Walk 1-minute bars for this account's open trade. Returns the closed trade record or None."""
    tr = s["open"]; side = tr["side"]
    for x in [x for x in b1 if x["d"] == today and 570 <= x["m"] < 960 and x["t"] > tr["seen"]]:
        tr["seen"], tr["price"] = x["t"], x["c"]
        worst = x["l"] if side == 1 else x["h"]
        room = a["bal"] - floor(c, a); bp = tr["entry"] - side * room / (sp["pt"] * tr["qty"])
        out = how = None
        if (side == 1 and worst <= bp) or (side == -1 and worst >= bp):
            if (side == 1 and bp >= tr["stop"]) or (side == -1 and bp <= tr["stop"]):
                out, how = bp, "max loss hit"  # firm liquidates at the trailing max loss before our stop
        if out is None:
            if (side == 1 and x["l"] <= tr["stop"]) or (side == -1 and x["h"] >= tr["stop"]):
                out, how = tr["stop"] - side * sp["tick"], "stop"
            elif (side == 1 and x["h"] >= tr["target"]) or (side == -1 and x["l"] <= tr["target"]):
                out, how = tr["target"], "target"
            elif x["m"] >= c["flatten"]:
                out, how = x["c"], "end of day"
            elif x["m"] - tr["entry_min"] >= tr["time_stop"]:
                out, how = x["c"], "time stop"
        if out is None:
            continue
        pnl = round(side * (out - tr["entry"]) * sp["pt"] * tr["qty"] - c["fees"] * tr["qty"], 2)
        a["bal"] = round(a["bal"] + pnl, 2); a["day"] = round(a["day"] + pnl, 2)
        if how == "max loss hit" or a["bal"] <= floor(c, a):
            a["history"].append(f"{today}: {a['phase']} account blown ({pnl:+,.0f}), new eval tomorrow"); a["phase"] = "failed"
        s["open"] = None
        rec = {"date": today, "account": a["name"], "symbol": tr["sym"], "strategy": tr["strat"], "side": "long" if side == 1 else "short",
               "qty": tr["qty"], "entry": tr["entry"], "exit": round(out, 2), "stop": tr["stop"], "target": tr["target"],
               "exit_reason": how, "pnl": pnl, "pnl_per_account": pnl, "entry_time": tr["entry_time"], "exit_time": x["t"]}
        log(f"PROP {a['name']} EXIT {rec['side']} {tr['qty']} {tr['sym']} @ {rec['exit']} ({how}) {pnl:+.2f}")
        return rec
    return None


def tick(log):
    global _last
    if time.time() - _last < 60:
        return
    _last = time.time()
    c = cfg()
    if not c["enabled"]:
        return
    now = datetime.now(ET); today, m = now.date().isoformat(), now.hour * 60 + now.minute
    st = load_state(c, today)
    if st["date"] != today:
        if st["date"] and st.get("day_closed") != st["date"]:
            close_day(c, st, st["date"])
        st.update({"date": today, "day_closed": "", "slots": {}})
        for a in st["accounts"]:
            if a["phase"] == "eval" and today >= a["fee_due"]:
                a["fees"] += c["eval_fee"]; a["fee_due"] = (now.date() + timedelta(days=28)).isoformat()
    for k in ("open", "done", "plan"):
        st.pop(k, None)  # v1 single-trade keys
    slots = st["slots"]
    busy = any((slots.get(a["name"]) or {}).get("open") for a in st["accounts"])
    if m >= 960 and not busy and st.get("day_closed") != today and now.weekday() < 5:
        close_day(c, st, today); st["day_closed"] = today
    if now.weekday() >= 5 or m < 565 or (m >= 960 and not busy):
        json.dump(st, open(STATE, "w"), indent=1); return
    data = {}
    for i, a in enumerate(st["accounts"]):
        p = plan_of(c, i); sym = p["sym"]; sp = SPEC[sym]
        s = slots.setdefault(a["name"], {"sym": sym, "strat": p["strat"], "open": None, "done": False, "why": ""})
        if a["phase"] == "failed":
            s.update(done=True, why="account blown today, new eval tomorrow")
            continue
        if not s["open"] and (s["done"] or m < 575):
            continue
        try:
            if sym not in data:
                data[sym] = (yahoo(sym, "5m", "1mo"), yahoo(sym, "1m", "1d"))
        except Exception as e:
            st["last_error"] = f"{datetime.now(timezone.utc).isoformat()[:16]} {sym}: {e}"[:200]; continue
        st["last_data"] = datetime.now(timezone.utc).isoformat()[:16] + "Z"
        b5, b1 = data[sym]
        if s["open"]:
            rec = manage(c, a, s, sp, b1, today, log)
            if rec:
                st["trades"] = (st["trades"] + [rec])[-200:]
                open(LOG, "a").write(json.dumps(rec) + "\n")
            continue
        if "ok" not in s:  # decide once a day whether this account trades today
            N = news_plan(c, st, today); X = day_stats(b5, today)
            why = ("no same-day news" if not N["tags"] else "FOMC day" if not N["ok"] else "not enough history" if not X else
                   "pre-open data missing" if X["pm"] is None else
                   f"pre-open moved {X['pm']:+.2f}% (needs under {c['quiet'] * X['adr']:.2f}%)" if abs(X["pm"]) >= c["quiet"] * X["adr"] else
                   f"yesterday's range only {X['prng_rel']:.2f}x its median" if p.get("active") and X["prng_rel"] < p["active"] else "")
            s["ok"] = not why; s["X"] = X
            s["why"] = why or f"news day ({', '.join(N['tags'])}), quiet pre-open"
            log(f"PROP {a['name']} {sym} {p['strat']} plan: {'TRADE' if s['ok'] else 'SKIP'} - {s['why']}")
        if not s["ok"]:
            s["done"] = True; continue
        rth = [x for x in b5 if x["d"] == today and 570 <= x["m"] < 960]
        if p["strat"] == "orb30" and len(rth) >= p["or_min"] // 5:
            n = p["or_min"] // 5; s["range"] = [min(x["l"] for x in rth[:n]), max(x["h"] for x in rth[:n])]
        sig = signal(p, s["X"], rth, m)
        if sig is None:
            continue
        s["done"] = True
        if sig == "none":
            s["why"] = "no valid setup today"; continue
        side, x, dist = sig
        if x["m"] + 5 < m - 20:
            s["why"] = "signal came too late to chase (free data lags)"; continue
        e = round(x["c"] + side * sp["tick"], 2); stop = round(e - side * dist, 2)
        s["open"] = {"sym": sym, "strat": p["strat"], "side": side, "qty": p["contracts"], "entry": e, "stop": stop,
                     "target": round(e + side * p["tr"] * dist, 2), "entry_time": x["t"], "entry_min": x["m"],
                     "time_stop": p["time_stop"], "seen": x["t"], "price": x["c"]}
        log(f"PROP {a['name']} ENTRY {'long' if side == 1 else 'short'} {p['contracts']} {sym} @ {e} stop {stop} target {s['open']['target']}")
    busy = any((slots.get(a["name"]) or {}).get("open") for a in st["accounts"])
    if m >= 960 and not busy and st.get("day_closed") != today:
        close_day(c, st, today); st["day_closed"] = today
    json.dump(st, open(STATE, "w"), indent=1)


def summary():
    c = cfg(); today = datetime.now(ET).date().isoformat(); st = load_state(c, today)
    slots = st["slots"] if st.get("date") == today else {}
    accts, poss = [], []
    for i, a in enumerate(st["accounts"]):
        p = plan_of(c, i); s = slots.get(a["name"]) or {}; tr = s.get("open")
        unreal = round(tr["side"] * (tr["price"] - tr["entry"]) * SPEC[tr["sym"]]["pt"] * tr["qty"], 2) if tr else 0.0
        if tr:
            poss.append({"account": a["name"], "symbol": tr["sym"], "side": "long" if tr["side"] == 1 else "short", "qty": tr["qty"],
                         "entry": tr["entry"], "price": tr["price"], "stop": tr["stop"], "target": tr["target"], "pnl_per_account": unreal})
        paid = sum(x["to_ryan"] for x in a["payouts"])
        accts.append({"name": a["name"], "strategy": f"{p['contracts']} {p['sym']} {'30-min range breakout' if p['strat'] == 'orb30' else 'prior-day sweep reversal'}",
                      "phase": a["phase"], "attempt": a["attempt"], "balance": round(c["size"] + a["bal"] + (unreal if a["phase"] != "failed" else 0), 2),
                      "profit": round(a["bal"] + unreal, 2), "target": c["target"] if a["phase"] == "eval" else None,
                      "max_loss_level": round(c["size"] + floor(c, a), 2), "winning_days": a["win_days"], "payouts": a["payouts"],
                      "paid_to_ryan": round(paid, 2), "fees": a["fees"], "net_cash": round(paid - a["fees"], 2), "history": a["history"][-10:],
                      "today": {k: s.get(k) for k in ("ok", "why", "range", "done")}})
    return {"note": "PAPER prop-firm practice: 2 simulated $50k accounts (Topstep-style rules). Prop 1: 6 MES 30-min range breakout; Prop 2: 6 MYM prior-day sweep reversal; news days only. No money involved.",
            "accounts": accts, "net_cash_total": round(sum(x["net_cash"] for x in accts), 2),
            "position": poss[0] if poss else None, "positions": poss,
            "today": {"news": (st.get("news") or {}).get("tags", []) if (st.get("news") or {}).get("date") == today else [],
                      **{a["name"]: (slots.get(a["name"]) or {}).get("why", "") for a in st["accounts"]}},
            "trades": st["trades"][-50:], "last_data": st.get("last_data"), "last_error": st.get("last_error")}
