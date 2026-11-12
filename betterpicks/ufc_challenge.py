#!/usr/bin/env python3
"""UFC Challenge (Ryan 2026-11-12): numbered UFC events only (UFC 334, 335 ...), MAIN CARD only, pick the winner
of every fight and try to go perfect. It's a challenge, NOT part of the P&L or the Wins/Losses record.

State: ufc_challenge.json  {"events": {<espn event id>: {name, date, picks: [{fight_id, pick, opp, p, price, why,
        ticker}], posted_msg, result: {right, total, perfect}, result_posted}}, "record": {...}}

  ufc_challenge.py show                 print the stored picks
  ufc_challenge.py post [--dry]         post the challenge card to PICKS (once per event)
  ufc_challenge.py grade [--dry]        when every main-card fight is final, score it and post the result once
"""
import json, os, sys, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
STATE = os.path.join(HERE, "ufc_challenge.json")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard?dates="


def load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"events": {}}


def save(s):
    tmp = f"{STATE}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE)


def espn_event(ev):
    d = json.load(urllib.request.urlopen(ESPN + ev["date"].replace("-", ""), timeout=20))
    for e in d.get("events", []):
        if str(e["id"]) == str(ev["id"]):
            return e


def record(s):
    done = [e for e in s["events"].values() if e.get("result")]
    fights = sum(e["result"]["total"] for e in done)
    right = sum(e["result"]["right"] for e in done)
    perfect = sum(1 for e in done if e["result"]["perfect"])
    return {"events": len(done), "perfect": perfect, "fights_right": right, "fights": fights}


def rec_line(s):
    r = record(s)
    if not r["events"]:
        return "UFC Challenge · first event · not counted in the BetterPicks record"
    return (f"UFC Challenge: {r['perfect']}/{r['events']} perfect cards · fights {r['fights_right']}-"
            f"{r['fights'] - r['fights_right']} · not counted in the BetterPicks record")


def card(ev, s):
    all_p = 1.0
    fields = []
    for i, k in enumerate(ev["picks"], 1):
        all_p *= k["p"]
        nums = " · ".join(x for x in (f"{round(k['price'] * 100)}¢ on Kalshi" if k.get("price") else "",
                                      f"we have it at {k['p']:.0%}") if x)
        fields.append({"name": f"{i}. {k['pick']} to beat {k['opp']}"[:256],
                       "value": f"{k.get('weight', '')}{' · ' if k.get('weight') else ''}{nums}\n**Why:** {k['why']}"[:1024]})
    from zoneinfo import ZoneInfo
    when = datetime.fromisoformat(ev["start"].replace("Z", "+00:00")).astimezone(ZoneInfo("America/Los_Angeles"))
    return {"title": f"\U0001F94A UFC Challenge: {ev['name']} main card"[:256],
            "description": (f"Pick every main-card winner and go {len(ev['picks'])} for {len(ev['picks'])}. "
                            f"Main card starts {when.strftime('%a %b %-d, %-I%p').replace('PM', 'pm').replace('AM', 'am')} PT.\n"
                            f"Chance all {len(ev['picks'])} hit: {all_p:.0%}. Just a challenge, not a bet we track."),
            "color": 0xD20A0A, "fields": fields[:25], "footer": {"text": rec_line(s)}}


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    dry = "--dry" in sys.argv
    s = load()
    if cmd == "show":
        for ev in s["events"].values():
            print(json.dumps(card(ev, s), indent=1))
        return
    import discord_post as dp
    for eid, ev in s["events"].items():
        if cmd == "post" and not ev.get("posted_at"):
            emb = card(ev, s)
            if dry:
                print(json.dumps(emb, indent=1))
                continue
            dp.send("\U0001F94A **BetterPicks UFC Challenge**", [emb])
            ev["posted_at"] = datetime.now(timezone.utc).isoformat()[:19]
            save(s)
            print("posted", ev["name"])
        if cmd == "grade" and ev.get("posted_at") and not ev.get("result_posted"):
            e = espn_event(ev)
            if not e:
                continue
            comps = {str(c["id"]): c for c in e["competitions"]}
            lines, right, final = [], 0, True
            for k in ev["picks"]:
                c = comps.get(str(k["fight_id"]))
                if not c or not c.get("status", {}).get("type", {}).get("completed"):
                    final = False
                    break
                win = next((x["athlete"]["displayName"] for x in c["competitors"] if x.get("winner")), None)
                if win is None:   # draw / no contest counts as a miss for a perfect card
                    lines.append(f"➖ {k['pick']} vs {k['opp']}: no winner (draw/NC)")
                    continue
                ok = win == k["pick_espn"]
                right += ok
                lines.append(f"{'✅' if ok else '❌'} {k['pick']} vs {k['opp']}: {win} won")
            if not final:
                continue
            n = len(ev["picks"])
            ev["result"] = {"right": right, "total": n, "perfect": right == n}
            head = (f"\U0001F3C6 **UFC Challenge: PERFECT CARD, {right}/{n}** at {ev['name']}" if right == n else
                    f"\U0001F94A **UFC Challenge: {right}/{n}** at {ev['name']} main card")
            msg = head + "\n" + "\n".join(lines) + f"\n_{rec_line(s)}_"
            if dry:
                print(msg)
                ev.pop("result")
                continue
            dp.send(msg)
            ev["result_posted"] = datetime.now(timezone.utc).isoformat()[:19]
            save(s)
            print("graded", ev["name"], right, n)


if __name__ == "__main__":
    main()
