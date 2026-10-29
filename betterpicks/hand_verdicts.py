"""HIT/LOST for picks posted by hand (Ryan 2026-10-28: "make sure u post the wins").
Hand-posted picks (book.json manual_post) are not in the droplet's posted list, so BetterBot never posts their
result. The project posts it instead: verdict embed to WINS/LOSSES, win slip to WINNERS, then marks
verdict:<key> in /mnt/project-files/betterpicks/discord_posted.json so the feed leaves it out for the droplet.
early_hits.py runs this every 60s, before it pushes the feed. Practice only."""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import discord_post as dp, feed   # noqa: E402

BP = "/mnt/project-files/betterpicks"
DONE = os.path.join(BP, "discord_posted.json")


def post(dry=False):
    book = json.load(open(os.path.join(BP, "book.json")))
    done = set(json.load(open(DONE))) if os.path.exists(DONE) else set()
    todo = [feed.vkey(e) for e in book.get("parlays", []) if e.get("manual_post") and not e.get("retracted")
            and e.get("status") in ("won", "lost") and f"verdict:{feed.vkey(e)}" not in done]
    if not todo:
        return []
    doc = feed.verdicts() or {}
    posted = []
    for v in doc.get("parlays", []):
        if v["verdict_key"] not in todo:
            continue
        head, emb = dp.verdict_post(v, doc.get("record"))
        if dry:
            print(head); continue
        dp.send(head, [emb], channel=dp.RESULTS_CH)
        if v["status"] == "won":
            try:
                import win_slip
                names = ", ".join(l["player"] for l in v["legs"])
                day = v["date"][5:].replace("-", "/").lstrip("0")
                dp.send_file(f"✅ Cashed: {names} ({day})", win_slip.render(v), "betterpicks-win.png", dp.WINNERS_CH)
            except Exception as ex:
                print("win slip skipped:", ex)
        done.add(f"verdict:{v['verdict_key']}")
        tmp = DONE + ".tmp"
        json.dump(sorted(done), open(tmp, "w"), indent=1)
        os.replace(tmp, DONE)
        posted.append(head)
        print("hand verdict posted:", head)
    return posted


if __name__ == "__main__":
    post(dry="--dry" in sys.argv)
