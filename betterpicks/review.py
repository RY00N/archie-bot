#!/usr/bin/env python3
"""Daily leg-bar review (paper only). Grades the shadow log (every Kalshi leg that cleared pricing and the
injury/matchup check, posted or not) by Kalshi's own settlement, then asks: if we had bet $1 on every leg at
or above each bar, buying at the ask with Kalshi's fee, what would the hit rate and ROI be?

Verdict for the current bar (rules.json parlay_min_win_p): keep, sharpen (raise it) or loosen (lower it).
It only prints a suggestion; changing the bar stays Ryan's call.
  python3 review.py            grade + print
"""
import json, os, sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import parlays  # noqa: E402

BARS = (0.55, 0.57, 0.60, 0.65, 0.70)
MIN_N = 15   # fewer graded legs than this at a bar = too early to call


def leg_roi(x):
    """$1 at the ask: Kalshi fee is 7% * price * (1 - price) per contract."""
    cost = x["price"] + 0.07 * x["price"] * (1 - x["price"])
    return (1 - cost) / cost if x["result"] == "won" else -1.0


def main():
    book = parlays.load(parlays.BOOK, None)
    sh = book.get("shadow", [])
    ago = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    for x in sh:
        if x.get("result") is None and x["start"].replace("Z", "+00:00") < ago:   # only games that should be over
            k = parlays.kalshi_result(x)
            if k in ("won", "lost", "void"):
                x["result"] = k
    parlays.save(parlays.BOOK, book)
    bar = json.load(open(os.path.join(HERE, "rules.json"))).get("parlay_min_win_p", 0.60)
    done = [x for x in sh if x.get("result") in ("won", "lost")]
    print(f"shadow legs: {len(sh)} logged, {len(done)} graded")
    stats = {}
    for b in sorted(set(BARS) | {bar}):
        g = [x for x in done if x["p"] >= b]
        if not g:
            continue
        w = sum(x["result"] == "won" for x in g)
        roi = sum(leg_roi(x) for x in g) / len(g)
        avg_p = sum(x["p"] for x in g) / len(g)
        stats[b] = (len(g), roi)
        print(f"  {b:.0%}+ bar: {w}-{len(g) - w} ({w / len(g):.0%} hit, we said {avg_p:.0%}), ROI at the ask {roi:+.1%}")
    for side in ("over", "under"):   # Ryan 2026-10-21: posted unders went 4-6, overs 7-3; watch it on the full log
        g = [x for x in done if x["p"] >= bar and x["side"] == side]
        if g:
            w = sum(x["result"] == "won" for x in g)
            print(f"  {side}s at {bar:.0%}+: {w}-{len(g) - w}, ROI at the ask {sum(leg_roi(x) for x in g) / len(g):+.1%}")
    n, roi = stats.get(bar, (0, 0.0))
    higher = [(b, s) for b, s in stats.items() if b > bar and s[0] >= MIN_N]
    lower = [(b, s) for b, s in stats.items() if b < bar and s[0] >= MIN_N]
    if n < MIN_N:
        verdict = f"too early ({n} graded legs at {bar:.0%}, need {MIN_N})"
    elif roi < 0 and higher and max(s[1] for _, s in higher) > 0:
        verdict = f"SHARPEN: {bar:.0%} legs lose at the ask, {max(higher, key=lambda t: t[1][1])[0]:.0%}+ wins"
    elif roi > 0 and lower and min(s[1] for _, s in lower) > roi:
        verdict = f"LOOSEN: lower bars did even better than {bar:.0%}"
    elif roi < 0:
        verdict = f"SHARPEN: {bar:.0%} legs lose at the ask and no higher bar has enough legs yet"
    else:
        verdict = f"KEEP {bar:.0%}"
    print("Bar review:", verdict)


if __name__ == "__main__":
    main()
