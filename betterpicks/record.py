"""Which posted picks count toward BetterPicks' record (Ryan 2026-10-22).

Ryan: "change our PNL record to the new rules PNL". The record (city P&L, /pnl, the HIT/LOST footer)
counts only picks that pass the rules approved 2026-10-21 05:52Z (rules.json loss_min_note):
  - Kalshi only (Ryan 2026-10-12)
  - 1-leg locks: win chance >= lock_single_min_p and edge >= lock_single_edge
  - combos: at most max_legs legs, every leg >= combo_min_leg_p
  - every NO/under leg >= under_min_p
  - the daily pick of the day (Ryan 2026-10-13) counts as is
  - Ryan's own slips, test entries and taken-back picks never count
Everything else stays in the book as history ("old rules") with its result, just outside the record.
"""
import json, os

RULES = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.json")))


def counts(e, r=RULES):
    if e.get("app") != "Kalshi" or e.get("test") or e.get("retracted") or e["type"].startswith("Ryan's"):
        return False
    legs = e.get("legs") or []
    # Ryan 2026-10-29 "we should be 19-6": the record = every HIT/LOST in the Wins/Losses channel, i.e. all singles
    # from Oct 16 on and combos from the Oct 28 combo rule on, whatever bar they were picked at
    if r.get("record_mode") == "posted":
        if e.get("record_skip") or not legs or len(legs) > r.get("max_legs", 2):
            return False
        return e["date"] >= (r["record_singles_from"] if len(legs) == 1 else r["record_combos_from"])
    if not legs or len(legs) > r.get("max_legs", 2):
        return False
    if e["type"].startswith("pick of the day"):
        return True
    p = lambda l: l.get("p") if l.get("p") is not None else 0
    if any(l.get("side") == "under" and p(l) < r.get("under_min_p", 0.65) for l in legs):
        return False
    if len(legs) == 1:
        return p(legs[0]) >= r.get("lock_single_min_p", 0.6) and (legs[0].get("edge") or 0) >= r.get("lock_single_edge", 0.05)
    return all(p(l) >= r.get("combo_min_leg_p", 0.65) for l in legs)


def record(parlays):
    """won-lost and P&L of the picks that count, plus how many are still open."""
    c = [e for e in parlays if counts(e)]
    done = [e for e in c if e["status"] in ("won", "lost")]
    return {"won": sum(e["status"] == "won" for e in done), "lost": sum(e["status"] == "lost" for e in done),
            "open": sum(e["status"] == "open" for e in c), "pnl": round(sum(e["pnl"] for e in done), 2),
            "staked": sum(e["stake"] for e in done)}
