#!/usr/bin/env python3
"""Quick backtest of Archie's momentum rotation on daily closes.
Usage: python3 backtest.py [overrides.json]   e.g. {"max_positions": 3, "trail_pct": 12}
Approximations: trades at the close, trailing stop checked on closes, no slippage or fees.
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
import archie_bot as bot

def backtest(rules, closes, years=2):
    n = min(len(c) for c in closes.values() if len(c) > 300)
    syms = [s for s, c in closes.items() if len(c) >= n]
    C = {s: closes[s][-n:] for s in syms}
    start = n - int(252 * years)
    cash, pos, peak_eq, mdd, trades = 10000.0, {}, 10000.0, 0.0, 0
    for t in range(start, n):
        px = {s: C[s][t] for s in syms}
        for s in list(pos):                       # trailing stops
            q, hi = pos[s]; hi = max(hi, px[s]); pos[s] = [q, hi]
            if px[s] <= hi * (1 - rules["trail_pct"] / 100):
                cash += q * px[s]; del pos[s]; trades += 1
        bot.RULES = rules
        ranked = bot.rank({s: C[s][:t + 1] for s in syms})
        keep = {s for _, s, _ in ranked[:rules["keep_rank"]]}
        for s in list(pos):
            if s not in keep:
                cash += pos[s][0] * px[s]; del pos[s]; trades += 1
        eq = cash + sum(q * px[s] for s, (q, _) in pos.items())
        slot = eq / rules["max_positions"]
        for _, s, _ in ranked[:rules["max_positions"]]:
            if s not in pos and len(pos) < rules["max_positions"] and cash >= slot * 0.5:
                q = min(slot, cash) / px[s]; cash -= q * px[s]; pos[s] = [q, px[s]]; trades += 1
        if len(pos) < rules["max_positions"] and cash > 1 and rules["fallback"] in px:
            fb = rules["fallback"]; q = cash / px[fb]
            pos[fb] = [pos.get(fb, [0, px[fb]])[0] + q, max(pos.get(fb, [0, px[fb]])[1], px[fb])]; cash = 0
        eq = cash + sum(q * px[s] for s, (q, _) in pos.items())
        peak_eq = max(peak_eq, eq); mdd = max(mdd, 1 - eq / peak_eq)
    spy = C["SPY"][-1] / C["SPY"][start] - 1
    return {"return_pct": round((eq / 10000 - 1) * 100, 1), "max_drawdown_pct": round(mdd * 100, 1),
            "trades": trades, "spy_return_pct": round(spy * 100, 1), "years": years}

if __name__ == "__main__":
    rules = dict(bot.RULES)
    if len(sys.argv) > 1 and sys.argv[1].endswith(".json"):
        rules.update(json.load(open(sys.argv[1])))
    elif len(sys.argv) > 1:
        rules.update(json.loads(sys.argv[1]))
    closes = bot.daily_closes(rules["universe"], days=1100)
    print(json.dumps(backtest(rules, closes), indent=1))
