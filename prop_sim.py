"""Archie's PAPER prop-firm practice (Ryan 2026-10-14: "Paper prop first"): two simulated $50k Topstep-style accounts.

No money, no broker: fills simulated on free CME prices like futures_sim.py. Both accounts take the same trade.
Strategy = MES news rules (newscombo.py backtest, 10/14): opening-range break of the 9:30-9:35 range on a 5-min close,
stop 12 pts, target 2R, 90-min time stop, entries to 11:30, flat 15:55, 15 MES per account; only when
  - there is same-day news (CPI/jobs day, or enough pre-open headlines on mega-cap earnings, tariffs, war/geo, data, Fed),
  - it is not an FOMC day, yesterday's range is 20-150 pts, and the pre-open was quiet (8:00 -> 9:25 move < 0.3%),
  - the 5-min RSI(14) is on the trade's side of 50 and the bar's volume beats its 20-bar average.
Prop rules (third-party summaries of Topstep, not verified on Topstep's site): eval passes at +$3,000 with best day
<= 50% of profit; fails when balance hits the trailing max loss (end-of-day peak - $2,000, stops at $50k);
funded account: same trailing loss, locks at $0 profit, payout after 5 days of >= $150 (50% of profit, first <= $2,000,
later <= $5,000, Ryan keeps 90%). Fees tracked as if real: $109 per 4 weeks of eval, $149 to activate.
Settings live in rules.json "prop". Runs on the server from daytrader.tick, next to futures_sim."""
import json, os, re, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "prop-state.json")
LOG = os.path.join(HERE, "prop-log.jsonl")
ET = ZoneInfo("America/New_York")
PT, TICK = 5.0, 0.25
DEFAULTS = {"enabled": True, "accounts": 2, "contracts": 15, "size": 50000, "target": 3000, "max_loss": 2000,
            "consistency": 0.5, "win_day": 150, "payout_days": 5, "payout_frac": 0.5, "first_cap": 2000, "cap": 5000,
            "split": 0.9, "eval_fee": 109, "activation": 149, "or_end": 575, "stop": 12, "tr": 2.0, "minp": 20,
            "maxp": 150, "pm_max": 0.3, "time_stop": 90, "last_entry": 690, "flatten": 955, "fees": 1.5,
            "cpi": [], "jobs": [], "fomc": []}
MEGA = {"NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "AVGO"}
RX = dict(  # same patterns and counts as the backtest (newscombo.py)
    mega_earn=(re.compile(r"\b(earnings|q[1-4] (results|eps|revenue)|quarterly results|beats|misses|tops estimates|guidance|eps \$|sales \$)"), 2),
    trade=(re.compile(r"\b(tariff|tariffs|trade war|trade deal|trade talks|export controls?|sanction)"), 3),
    geo=(re.compile(r"\b(iran|israel|missile|airstrike|invasion|war\b|ukraine|russia|middle east|strike on|ceasefire|shutdown)"), 3),
    data=(re.compile(r"\b(ppi|producer price|retail sales|gdp|pce|jobless claims|ism |pmi|consumer sentiment|housing starts)"), 2),
    fed=(re.compile(r"\b(powell|fed chair|fed's|fed governor|fomc|rate cut|rate hike|waller|williams|goolsbee|bowman|jefferson)"), 3))
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
    return st


def yahoo(interval, rng):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/MES=F?interval={interval}&range={rng}"
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


def indicators(b5):
    """RSI(14) of closes and 20-bar average volume (before the bar), over all 5-min bars incl. overnight, like the backtest."""
    out = {}; ch = []; vols = []
    for i, x in enumerate(b5):
        if i: ch.append(x["c"] - b5[i - 1]["c"])
        g = sum(y for y in ch[-14:] if y > 0); lo = -sum(y for y in ch[-14:] if y < 0)
        out[x["t"]] = (50 if g + lo == 0 else 100 * g / (g + lo), sum(vols[-20:]) / len(vols[-20:]) if len(vols) >= 5 else None)
        vols.append(x["v"])
    return out


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
        st.update({"date": today, "done": False, "plan": {}, "day_closed": ""})
        for a in st["accounts"]:
            if a["phase"] == "eval" and today >= a["fee_due"]:
                a["fees"] += c["eval_fee"]; a["fee_due"] = (now.date() + timedelta(days=28)).isoformat()
    if m >= 960 and not st.get("open") and st.get("day_closed") != today and now.weekday() < 5:
        close_day(c, st, today); st["day_closed"] = today
    if now.weekday() >= 5 or m < 565 or (m >= 960 and not st.get("open")):
        json.dump(st, open(STATE, "w"), indent=1); return
    try:
        b5 = yahoo("5m", "5d"); b1 = yahoo("1m", "1d")
    except Exception as e:
        st["last_error"] = f"{datetime.now(timezone.utc).isoformat()[:16]} {e}"[:200]; json.dump(st, open(STATE, "w"), indent=1); return
    st["last_data"] = datetime.now(timezone.utc).isoformat()[:16] + "Z"
    tr = st.get("open")
    if tr:  # manage the open trade (1-minute bars after the last one seen)
        for x in [x for x in b1 if x["d"] == today and 570 <= x["m"] < 960 and x["t"] > tr["seen"]]:
            tr["seen"], tr["price"] = x["t"], x["c"]
            s = tr["side"]; out = how = None
            worst = x["l"] if s == 1 else x["h"]
            for a in st["accounts"]:  # firm liquidates the moment the balance touches the trailing max loss
                if a["phase"] in ("eval", "funded"):
                    room = a["bal"] - floor(c, a)
                    bp = tr["entry"] - s * room / (PT * tr["qty"])
                    if (s == 1 and worst <= bp) or (s == -1 and worst >= bp):
                        a.setdefault("breach", round(bp, 2))
            if (s == 1 and x["l"] <= tr["stop"]) or (s == -1 and x["h"] >= tr["stop"]):
                out, how = tr["stop"] - s * TICK, "stop"
            elif (s == 1 and x["h"] >= tr["target"]) or (s == -1 and x["l"] <= tr["target"]):
                out, how = tr["target"], "target"
            elif x["m"] >= c["flatten"]:
                out, how = x["c"], "end of day"
            elif x["m"] - tr["entry_min"] >= c["time_stop"]:
                out, how = x["c"], "time stop"
            if out is None and not any("breach" in a for a in st["accounts"]):
                continue
            last_px = out
            for a in st["accounts"]:
                if a["phase"] not in ("eval", "funded"):
                    continue
                px, why = (a.pop("breach"), "max loss hit") if "breach" in a and (out is None or s * (a["breach"] - out) > 0) else (out, how)
                a.pop("breach", None)
                if px is None:
                    continue
                last_px = px if out is None else last_px
                pnl = round(s * (px - tr["entry"]) * PT * tr["qty"] - c["fees"] * tr["qty"], 2)
                a["bal"] = round(a["bal"] + pnl, 2); a["day"] = round(a["day"] + pnl, 2)
                if why == "max loss hit" or a["bal"] <= floor(c, a):
                    a["history"].append(f"{today}: {a['phase']} account blown ({pnl:+,.0f}), new eval tomorrow"); a["phase"] = "failed"
                a.setdefault("closed", {})[tr["entry_time"]] = (px, why, pnl)
            if out is not None or all(tr["entry_time"] in a.get("closed", {}) or a["phase"] == "failed" for a in st["accounts"]):
                rec = {"date": today, "symbol": "MES", "side": "long" if s == 1 else "short", "qty": tr["qty"], "entry": tr["entry"],
                       "exit": round(last_px, 2), "exit_reason": how or "max loss hit",
                       "pnl_per_account": round(s * (last_px - tr["entry"]) * PT * tr["qty"] - c["fees"] * tr["qty"], 2),
                       "entry_time": tr["entry_time"], "exit_time": x["t"], "news": st["plan"].get("news", [])}
                st["trades"] = (st["trades"] + [rec])[-200:]
                open(LOG, "a").write(json.dumps(rec) + "\n")
                log(f"PROP EXIT {rec['side']} {tr['qty']} MES @ {rec['exit']} ({rec['exit_reason']}) {rec['pnl_per_account']:+.2f}/account")
                for a in st["accounts"]: a.pop("closed", None)
                st["open"] = None
                break
    elif not st["done"] and m >= c["or_end"]:
        P = st["plan"]
        if "ok" not in P:  # decide once per day whether this is a tradable news day
            day5 = [x for x in b5 if x["d"] == today]; prev = sorted({x["d"] for x in b5 if x["d"] < today})
            rthp = [x for x in b5 if prev and x["d"] == prev[-1] and 570 <= x["m"] < 960]
            o8 = next((x["o"] for x in day5 if x["m"] == 480), None); c925 = next((x["c"] for x in day5 if x["m"] == 565), None)
            P["prev_range"] = round(max(x["h"] for x in rthp) - min(x["l"] for x in rthp), 2) if rthp else None
            P["pre_open_move"] = round((c925 / o8 - 1) * 100, 3) if o8 and c925 else None
            try:
                P["news"], P["headlines"] = news_tags(c, today)
            except Exception as e:
                P["news"], P["news_error"] = [], str(e)[:120]
            why = ("no same-day news" if not P["news"] else "FOMC day" if "fomc" in P["news"] else
                   "pre-open data missing" if P["pre_open_move"] is None or P["prev_range"] is None else
                   f"pre-open moved {P['pre_open_move']:+.2f}% (needs < {c['pm_max']}%)" if abs(P["pre_open_move"]) >= c["pm_max"] else
                   f"yesterday's range {P['prev_range']} outside {c['minp']}-{c['maxp']}" if not c["minp"] <= P["prev_range"] <= c["maxp"] else "")
            P["ok"], P["why"] = not why, why or f"news day ({', '.join(P['news'])}), quiet pre-open"
            log(f"PROP plan: {'TRADE' if P['ok'] else 'SKIP'} - {P['why']}")
        if not P["ok"]:
            st["done"] = True
        else:
            day5 = [x for x in b5 if x["d"] == today and 570 <= x["m"] < 960]
            orb = [x for x in day5 if x["m"] < c["or_end"]]
            if orb:
                hi, lo = max(x["h"] for x in orb), min(x["l"] for x in orb); P["range"] = [lo, hi]
                ind = indicators(b5)
                for x in [x for x in day5 if x["m"] >= c["or_end"] and x["m"] + 5 <= m]:
                    if x["m"] > c["last_entry"]:
                        st["done"] = True; break
                    if x["t"] <= P.get("checked", ""):
                        continue
                    P["checked"] = x["t"]
                    s = 1 if x["c"] > hi else -1 if x["c"] < lo else 0
                    if not s:
                        continue
                    rsi, vavg = ind[x["t"]]
                    if (rsi <= 50 if s == 1 else rsi >= 50) or (vavg and x["v"] <= vavg):
                        log(f"PROP break at {x['m']} passed on: RSI {rsi:.0f}, volume {x['v']} vs avg {vavg or 0:.0f}")
                        continue
                    if x["m"] + 5 < m - 20:
                        st["done"] = True; break  # signal is stale (free data lags): don't chase
                    e = x["c"] + s * TICK; stop = e - s * c["stop"]
                    st["open"] = {"side": s, "qty": c["contracts"], "entry": round(e, 2), "stop": stop, "target": round(e + s * c["tr"] * c["stop"], 2),
                                  "entry_time": x["t"], "entry_min": x["m"] + 5, "seen": x["t"], "price": x["c"]}
                    st["done"] = True
                    log(f"PROP ENTRY {'long' if s == 1 else 'short'} {c['contracts']} MES @ {e} stop {stop} target {st['open']['target']} ({P['why']})")
                    break
    if m >= 960 and not st.get("open") and st.get("day_closed") != today:
        close_day(c, st, today); st["day_closed"] = today
    json.dump(st, open(STATE, "w"), indent=1)


def summary():
    c = cfg(); st = load_state(c, datetime.now(ET).date().isoformat()); tr = st.get("open")
    unreal = round(tr["side"] * (tr["price"] - tr["entry"]) * PT * tr["qty"], 2) if tr else 0.0
    accts = []
    for a in st["accounts"]:
        paid = sum(p["to_ryan"] for p in a["payouts"])
        accts.append({"name": a["name"], "phase": a["phase"], "attempt": a["attempt"], "balance": round(c["size"] + a["bal"] + (unreal if a["phase"] != "failed" else 0), 2),
                      "profit": round(a["bal"] + unreal, 2), "target": c["target"] if a["phase"] == "eval" else None,
                      "max_loss_level": round(c["size"] + floor(c, a), 2), "winning_days": a["win_days"], "payouts": a["payouts"],
                      "paid_to_ryan": round(paid, 2), "fees": a["fees"], "net_cash": round(paid - a["fees"], 2), "history": a["history"][-10:]})
    return {"note": "PAPER prop-firm practice: 2 simulated $50k accounts (Topstep-style rules), 15 MES each on the news rules. No money involved.",
            "accounts": accts, "net_cash_total": round(sum(x["net_cash"] for x in accts), 2),
            "position": ({"symbol": "MES", "side": "long" if tr["side"] == 1 else "short", "qty": tr["qty"], "entry": tr["entry"], "price": tr["price"],
                          "stop": tr["stop"], "target": tr["target"], "pnl_per_account": unreal} if tr else None),
            "today": st.get("plan", {}), "trades": st["trades"][-50:], "last_data": st.get("last_data"), "last_error": st.get("last_error")}
