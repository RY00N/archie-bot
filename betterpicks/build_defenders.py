"""Builds nba_defenders.json from NBA.com's season defense dashboards (Ryan 2026-10-28: pulled through his Chrome
into data/nba_defense/, since stats.nba.com blocks scripts and game-by-game matchups are no longer on the site).
For each defender: DIFF% = field-goal % he allowed minus what those shooters normally hit, blended over this season
and last (weighted by shots defended, last season capped so this season takes over as it grows).
  python3 build_defenders.py [defense folder]"""
import json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else "/mnt/project-files/betterpicks/data/nba_defense"
CUR, PRIOR, OLDER = "2026-27", "2025-26", "2024-25"
PRIOR_CAP = 300   # shots defended (GP x DFGA) that last season can count for at most


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def season(page, s):
    """{name lower: (diff, weight)} for one season of one dashboard page."""
    path = os.path.join(SRC, f"{page}_{s}.json")
    if not os.path.exists(path):
        return {}
    out = {}
    for r in json.load(open(path))["rows"]:
        gp, a, d = num(r.get("GP")), num(r.get("DFGA")), num(r.get("DIFF%"))
        if gp and a and d is not None:
            out[r["PLAYER"].lower()] = (d, gp * a)
    return out


def blend(page):
    cur, prior, older = season(page, CUR), season(page, PRIOR), season(page, OLDER)
    out = {}
    for name in set(cur) | set(prior) | set(older):
        parts = [cur.get(name), prior.get(name) or older.get(name)]
        if parts[1]:
            parts[1] = (parts[1][0], min(parts[1][1], PRIOR_CAP))
        parts = [p for p in parts if p and p[1] > 0]
        w = sum(p[1] for p in parts)
        if w:
            out[name] = [round(sum(p[0] * p[1] for p in parts) / w, 2), round(w)]
    return out


if __name__ == "__main__":
    data = {"built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "seasons": [OLDER, PRIOR, CUR],
            "overall": blend("defense-dash-overall"), "three": blend("defense-dash-3pt"), "rim": blend("defense-dash-lt6")}
    json.dump(data, open(os.path.join(HERE, "nba_defenders.json"), "w"), separators=(",", ":"))
    print({k: len(v) for k, v in data.items() if isinstance(v, dict)})
