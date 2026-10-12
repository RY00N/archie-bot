#!/usr/bin/env python3
"""BetterPicks: practice sports picks on a paper bankroll. No real bets, ever.

Data: ESPN public API (schedule, DraftKings odds, ESPN matchup predictor, final scores).
Singles (since 2026-10-12): only when a sportsbook's moneyline beats the de-vigged Pinnacle (sharp) line\nby sharp_min_ev in rules.json. ESPN = schedule and scores only.

  python3 betterpicks.py slate   # list upcoming games with edges -> slate.json
  python3 betterpicks.py pick    # add sharp-line value bets to book.json (run books.py lines first)
  python3 betterpicks.py grade   # settle finished picks, update bankroll
  python3 betterpicks.py report  # write picks-today.md + summary for P&L
"""
import json, os, sys, urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(HERE, "book.json")
SLATE = os.path.join(HERE, "slate.json")
RULES = os.path.join(HERE, "rules.json")
API = "https://site.api.espn.com/apis/site/v2/sports/{}/{}"
LEAGUES = {"nfl": "football/nfl", "ncaaf": "football/college-football",
           "nba": "basketball/nba", "mlb": "baseball/mlb", "nhl": "hockey/nhl"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "betterpicks"}), timeout=20) as r:
        return json.load(r)


def load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def save(path, data):
    tmp = path + ".tmp"
    json.dump(data, open(tmp, "w"), indent=1)
    os.replace(tmp, path)


def rules():
    return load(RULES, {"leagues": ["nfl", "ncaaf", "nba", "mlb", "nhl"], "bankroll": 1000, "unit": 10,
                        "min_edge": 0.05, "big_edge": 0.10, "max_picks_per_day": 5, "max_dog_odds": 250,
                        "max_fav_odds": -300, "hours_ahead": 36, "skip_preseason": True})


def american_to_prob(o):
    o = float(o)
    return 100 / (o + 100) if o > 0 else -o / (-o + 100)


def payout(o, stake):
    o = float(o)
    return stake * (o / 100 if o > 0 else 100 / -o)


def side_odds(market, side, key="odds"):
    m = market.get(side, {}) if market else {}
    v = (m.get("close") or {}).get(key) or (m.get("open") or {}).get(key)
    if v in (None, "", "OFF", "EVEN"):
        return 100 if v == "EVEN" else None
    return float(str(v).replace("o", "").replace("u", ""))


def slate():
    r = rules()
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=r["hours_ahead"])
    games = []
    for lg in r["leagues"]:
        try:
            sb = get(API.format(LEAGUES[lg], "scoreboard"))
        except Exception as e:
            print("skip", lg, e); continue
        if r.get("skip_preseason") and (sb.get("season") or {}).get("type") == 1:
            continue
        for ev in sb.get("events", []):
            t = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
            c = ev["competitions"][0]
            if c["status"]["type"]["state"] != "pre" or not (now < t < end):
                continue
            odds = (c.get("odds") or [{}])[0]
            ml = odds.get("moneyline") or {}
            h_ml, a_ml = side_odds(ml, "home"), side_odds(ml, "away")
            if h_ml is None or a_ml is None:
                continue
            teams = {x["homeAway"]: x["team"]["abbreviation"] for x in c["competitors"]}
            ph, pa = american_to_prob(h_ml), american_to_prob(a_ml)
            mkt_home = ph / (ph + pa)
            model_home = None
            try:
                pr = get(API.format(LEAGUES[lg], "summary") + f"?event={ev['id']}").get("predictor") or {}
                model_home = float(pr["homeTeam"]["gameProjection"]) / 100
            except Exception:
                pass
            ml_open = {s: (ml.get(s, {}).get("open") or {}).get("odds") for s in ("home", "away")}
            games.append({"id": ev["id"], "league": lg, "start": ev["date"], "name": ev["name"],
                          "home": teams["home"], "away": teams["away"], "details": odds.get("details"),
                          "total": odds.get("overUnder"), "ml_home": h_ml, "ml_away": a_ml, "ml_open": ml_open,
                          "market_home_pct": round(mkt_home, 3),
                          "model_home_pct": None if model_home is None else round(model_home, 3),
                          "edge_home": None if model_home is None else round(model_home - mkt_home, 3)})
    games.sort(key=lambda g: g["start"])
    save(SLATE, {"updated": now.isoformat(timespec="seconds"), "games": games})
    for g in games:
        print(g["league"], g["start"], g["name"], g["details"], "ML", g["ml_away"], g["ml_home"],
              "mkt", g["market_home_pct"], "model", g["model_home_pct"], "edge", g["edge_home"])
    return games


def new_book():
    r = rules()
    return {"note": "PRACTICE bankroll only. No real money is ever wagered.", "start_bankroll": r["bankroll"],
            "bankroll": r["bankroll"], "unit": r["unit"], "picks": []}


LINES = os.path.join(HERE, "lines.json")
SHARP = "Pinnacle"
SKIP_BOOKS = {"Pinnacle", "AN Consensus", "AN Open", "Polymarket", "Kalshi", "Kalshi (direct)"}


def fair_from_sharp(ml):
    """De-vigged win chance from the sharp book's two-way moneyline."""
    ph, pa = american_to_prob(ml["home"]), american_to_prob(ml["away"])
    return {"home": ph / (ph + pa), "away": pa / (ph + pa)}


def pick(reason_overrides=None):
    # Ryan 2026-10-12: only bet sportsbook lines where we beat the sharp Vegas price; otherwise skip
    # sports lines. ESPN's win model is no longer used to pick (it went 3-6-1). ESPN is only used for
    # the schedule (game ids) and final scores.
    r = rules()
    book = load(BOOK, None) or new_book()
    games = load(SLATE, {}).get("games") or slate()
    lines = load(LINES, {}).get("games", [])
    today = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-7))).date().isoformat()
    have = {p["game_id"] for p in book["picks"]}
    today_n = sum(1 for p in book["picks"] if p["date"] == today)
    min_ev, big_ev = r.get("sharp_min_ev", 0.03), r.get("sharp_big_ev", 0.06)
    cands = []
    for g in games:
        if g["id"] in have:
            continue
        gs = datetime.fromisoformat(g["start"].replace("Z", "+00:00"))
        ln = next((x for x in lines if x.get("league") == g["league"]
                   and f'{x.get("away")} at {x.get("home")}' == g["name"] and abs((datetime.fromisoformat(
                       x["start_time"].replace("Z", "+00:00")) - gs).total_seconds()) < 4 * 3600), None)
        sharp = ((ln or {}).get("books", {}).get(SHARP) or {}).get("moneyline") or {}
        if sharp.get("home") is None or sharp.get("away") is None:
            continue   # no sharp line, no bet
        fair = fair_from_sharp(sharp)
        for side, team in (("home", ln["home"]), ("away", ln["away"])):
            best = None
            for name, b in ln["books"].items():
                o = (b.get("moneyline") or {}).get(side)
                if name in SKIP_BOOKS or o is None:
                    continue
                ev = fair[side] * (1 + payout(o, 1)) - 1
                if best is None or ev > best[0]:
                    best = (ev, name, o)
            if best and best[0] >= min_ev and r["max_fav_odds"] <= best[2] <= r["max_dog_odds"]:
                cands.append((best[0], g, side, best[2], team, best[1], fair[side], sharp[side]))
    cands.sort(key=lambda c: -c[0])
    added = []
    for ev, g, side, odds, team, bk, fair_p, sh in cands[: max(0, r["max_picks_per_day"] - today_n)]:
        units = 2 if ev >= big_ev else 1
        p = {"date": today, "game_id": g["id"], "league": g["league"], "start": g["start"], "game": g["name"],
             "bet": f"{team} ML", "market": "ml", "side": side, "team": team, "odds": odds, "units": units,
             "stake": units * book["unit"], "model_pct": round(fair_p, 3), "edge": round(ev, 3), "app": bk,
             "why": f"{bk} pays {odds:+.0f} on {team}, while sharp Pinnacle has them at {sh:+.0f} "
                    f"({fair_p:.0%} fair after removing its cut). That price is worth {ev:.1%} more than it should be.",
             "status": "open", "pnl": 0}
        if reason_overrides and g["id"] in reason_overrides:
            p["why"] = reason_overrides[g["id"]]
        book["picks"].append(p); added.append(p)
    save(BOOK, book)
    for p in added:
        print(p["league"], p["bet"], f"{p['odds']:+.0f}", f"{p['units']}u", p["why"])
    if not added:
        print("no sportsbook price beats the sharp line today; no singles")
    return added


def grade():
    book = load(BOOK, None) or new_book()
    changed = 0
    for p in book["picks"]:
        if p["status"] != "open":
            continue
        try:
            d = get(API.format(LEAGUES[p["league"]], "summary") + f"?event={p['game_id']}")
            comp = d["header"]["competitions"][0]
        except Exception:
            continue
        st = comp["status"]["type"]
        if st.get("name") in ("STATUS_POSTPONED", "STATUS_CANCELED"):
            p["status"], p["pnl"] = "void", 0; changed += 1; continue
        if not st.get("completed"):
            continue
        sc = {x["homeAway"]: float(x.get("score") or 0) for x in comp["competitors"]}
        mine, theirs = (sc["home"], sc["away"]) if p["side"] == "home" else (sc["away"], sc["home"])
        if p["market"] == "spread":
            mine += float(p["line"])
        if p["market"] == "total":
            tot = sc["home"] + sc["away"]
            mine, theirs = (tot, float(p["line"])) if p["side"] == "over" else (float(p["line"]), tot)
        if mine > theirs:
            p["status"], p["pnl"] = "won", round(payout(p["odds"], p["stake"]), 2)
        elif mine < theirs:
            p["status"], p["pnl"] = "lost", -p["stake"]
        else:
            p["status"], p["pnl"] = "push", 0
        p["final"] = f"{p['game']}: {int(sc['away'])}-{int(sc['home'])}"
        changed += 1
    book["bankroll"] = round(book["start_bankroll"] + sum(p["pnl"] for p in book["picks"])
                             + sum(e["pnl"] for e in book.get("parlays", [])), 2)
    save(BOOK, book)
    print("graded", changed, "bankroll", book["bankroll"])


def apps_view(book):
    """Per-app drill-down for the city P&L: app -> totals + every parlay with legs, stake, multiplier, result."""
    apps = {}
    for e in book.get("parlays", []):
        a = apps.setdefault(e["app"], {"pnl": 0, "won": 0, "lost": 0, "open": 0, "staked": 0, "parlays": []})
        a["pnl"] = round(a["pnl"] + e["pnl"], 2); a["staked"] += e["stake"]
        a[e["status"] if e["status"] in ("won", "lost", "open") else "open"] += e["status"] != "void"
        a["parlays"].append({"date": e["date"], "type": e["type"], "stake": e["stake"], "multiplier": e["payout_x"],
                             "to_win": round(e["stake"] * e["payout_x"], 2) if e["payout_x"] else None, "status": e["status"], "pnl": e["pnl"],
                             "hit_chance": e["win_p"], "breakdown": e.get("breakdown"),
                             "legs": [{"pick": f"{l['player']} {l['side']} {l['line']} {l['stat'].replace('_', ' ')}",
                                       "game": l["game"], "league": l["league"].upper(), "price": l.get("price"),
                                       "actual": l.get("actual"), "result": l.get("result") or "open",
                                       "why": l.get("case") or l.get("why"), "chance": l.get("p")}
                                      for l in e["legs"]]})
    singles = [p for p in book["picks"]]
    if singles:
        apps["Sportsbook singles"] = {
            "pnl": round(sum(p["pnl"] for p in singles), 2), "won": sum(p["status"] == "won" for p in singles),
            "lost": sum(p["status"] == "lost" for p in singles), "open": sum(p["status"] == "open" for p in singles),
            "staked": sum(p["stake"] for p in singles),
            "parlays": [{"date": p["date"], "type": "single", "stake": p["stake"],
                         "multiplier": round(1 + (p["odds"] / 100 if p["odds"] > 0 else 100 / -p["odds"]), 2),
                         "to_win": round(p["stake"] + payout(p["odds"], p["stake"]), 2), "status": p["status"],
                         "pnl": p["pnl"], "legs": [{"pick": f"{p['bet']} {p['odds']:+.0f}", "game": p["game"],
                                                    "league": p["league"].upper(), "result": p["status"],
                                                    "final": p.get("final")}]} for p in singles]}
    for a in apps.values():
        a["parlays"].sort(key=lambda x: x["date"], reverse=True)
    return apps


def report():
    book = load(BOOK, None) or new_book()
    ps = book["picks"]
    done = [p for p in ps if p["status"] in ("won", "lost", "push")]
    w = sum(p["status"] == "won" for p in done); l = sum(p["status"] == "lost" for p in done)
    risked = sum(p["stake"] for p in done)
    pnl = round(sum(p["pnl"] for p in done), 2)
    pars = book.get("parlays", [])
    pdone = [e for e in pars if e["status"] in ("won", "lost")]
    ppnl = round(sum(e["pnl"] for e in pdone), 2)
    summary = {"bankroll": book["bankroll"], "start": book["start_bankroll"], "record": f"{w}-{l}-{len(done)-w-l}",
               "pnl": pnl, "roi_pct": round(100 * pnl / risked, 1) if risked else 0,
               "open": len([p for p in ps if p["status"] == "open"]),
               "parlay_record": f"{sum(e['status'] == 'won' for e in pdone)}-{sum(e['status'] == 'lost' for e in pdone)}",
               "parlay_pnl": ppnl, "parlays_open": len([e for e in pars if e["status"] == "open"]),
               "total_pnl": round(pnl + ppnl, 2),
               "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    summary["apps"] = apps_view(book)
    book["summary"] = summary
    save(BOOK, book)
    lines = [f"# BetterPicks (practice only)", "",
             f"Bankroll ${book['bankroll']:,.2f} (started ${book['start_bankroll']:,}), record {summary['record']}, "
             f"P&L ${pnl:+,.2f}, ROI {summary['roi_pct']}%", "", "## Open picks"]
    for p in [p for p in ps if p["status"] == "open"]:
        lines.append(f"- **{p['bet']} {p['odds']:+.0f}** ({p['units']}u, {p['league'].upper()}, {p['game']}, {p['start']}): {p['why']}")
    lines += ["", "## Practice parlays (PrizePicks / Kalshi style)"]
    for e in [e for e in pars if e["status"] == "open"]:
        pay = f"to pay {e['payout_x']}x" if e["payout_x"] else "(multiplier: tell BetterPicks what Kalshi shows)"
        ev = f", EV {e['ev_pct']:+}%" if e.get("ev_pct") is not None else ""
        lines.append(f"- **{e['app']} {e['type']}**, ${e['stake']:.0f} {pay}, hit chance {e['win_p']:.0%}{ev}")
        if e.get("breakdown"):
            lines.append(f"  {e['breakdown']}")
        for l in e["legs"]:
            lines.append(f"  - **{l['player']} {l['side']} {l['line']} {l['stat'].replace('_', ' ')}** ({l['p']:.0%}): "
                         + (l.get("case") or l["why"]) + (f" Kalshi price {l['price']:.2f}." if l.get("price") else ""))
    for e in [e for e in pars if e["status"] != "open"][-5:][::-1]:
        lines.append(f"- {e['status'].upper()} {e['app']} {e['type']} ${e['pnl']:+.2f}")
    lines += ["", "## Recent results"]
    for p in done[-10:][::-1]:
        lines.append(f"- {p['status'].upper()} {p['bet']} {p['odds']:+.0f} ({p['units']}u) ${p['pnl']:+.2f}. {p.get('final','')}")
    open(os.path.join(HERE, "picks-today.md"), "w").write("\n".join(lines) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "slate"
    {"slate": slate, "pick": pick, "grade": grade, "report": report}[cmd]()
