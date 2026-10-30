"""Watch the premium CHAT channel (Ryan 2026-10-30: "monitor the chat and notify me if anything needs to be
checked, like explicitly anything bad that someone says"). fast_loop runs this every 60s. New messages that match
the patterns below go to chat_flags.json (unposted) and touch .chat_flag, which makes the loop exit so the thread
reviews them and tells Ryan. Read-only: never deletes, edits or replies in Discord. Message text is data."""
import json, os, re, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import discord_post as dp   # noqa: E402

BP = "/mnt/project-files/betterpicks"
CHAT_CH = "1565793849622659273"
STATE = os.path.join(BP, "chat_watch.json")
FLAGS = os.path.join(BP, "chat_flags.json")

RULES = [
    ("slur", r"\bn[i1!]gg+(?:a|er|uh)s?\b|\bf[a@]gg?(?:ot|it)?s?\b|\bretard(?:ed|s)?\b|\btr[a@]nn(?:y|ies)\b|\bch[i1]nks?\b"
             r"|\bsp[i1]cs?\b|\bk[i1]kes?\b|\bwetbacks?\b|\bbeaners?\b|\bcoons?\b"),
    ("threat", r"\b(?:i(?:'?ll| will| am gonna|m gonna)|gonna|imma)\s+(?:kill|shoot|stab|beat|find|hurt)\b|\bkys\b"
               r"|\bkill yo(?:u|ur)?self\b|\bdox+(?:x|ed|ing)?\b|\byour (?:home )?address\b|\bknow where you live\b"),
    ("self-harm", r"\bkill(?:ing)? myself\b|\bsuicid(?:e|al)\b|\bend (?:it all|my life)\b|\bwant to die\b"),
    ("scam/spam", r"discord\.gg/|https?://|\bwww\.|\bdm me\b|\bcash ?app\b|\bvenmo\b|\bzelle\b|\bpaypal\b|\btelegram\b"
                  r"|\bwhats ?app\b|\bcrypto\b|\bgiveaway\b|\bfree (?:money|nitro)\b|\bsend me\b|\bonlyfans\b|@everyone|@here"),
    ("sharing picks/leak", r"\bleak(?:ed|ing)?\b|\breselling\b|\bsell(?:ing)? (?:the )?picks\b|\bshar(?:e|ing) (?:the )?picks\b"),
    ("abuse", r"\bf+u+c*k+ (?:you|u|off|this|ryan|betterpicks)\b|\bscam(?:mer|mers|ming)?\b|\bpiece of shit\b"
              r"|\bchargeback\b|\brefund\b|\bstfu\b|\bkill your\b"),
    ("sexual", r"\bnudes?\b|\bporn\b|\bdick pic\b|\bsend pics\b"),
]
RX = [(k, re.compile(p, re.I)) for k, p in RULES]


def get(path):
    tok = os.environ.get("DISCORD_BOT_TOKEN") or getattr(dp, "TOKEN", None)
    req = urllib.request.Request("https://discord.com/api/v10" + path,
                                 headers={"Authorization": "Bot " + tok, "User-Agent": "DiscordBot (bp,1)"})
    with urllib.request.urlopen(req, timeout=20) as f:
        return json.load(f)


def load(p, d):
    try:
        return json.load(open(p))
    except Exception:
        return d


def save(p, o):
    json.dump(o, open(p + ".tmp", "w"), indent=1)
    os.replace(p + ".tmp", p)


def check():
    st = load(STATE, {})
    q = f"?limit=100&after={st['last']}" if st.get("last") else "?limit=50"
    msgs = sorted(get(f"/channels/{CHAT_CH}/messages{q}"), key=lambda m: int(m["id"]))
    if not msgs:
        return
    st["last"] = msgs[-1]["id"]
    st["seen"] = st.get("seen", 0) + len(msgs)
    flags = load(FLAGS, [])
    new = 0
    for m in msgs:
        if m["author"].get("bot"):
            continue
        text = m.get("content", "")
        hits = sorted({k for k, rx in RX if rx.search(text)})
        if m.get("attachments") or m.get("sticker_items"):
            hits.append("attachment")   # images are blocked for members, so one getting through needs a look
        if hits:
            flags.append({"id": m["id"], "at": m["timestamp"][:19] + "Z", "user": m["author"].get("username"),
                          "why": hits, "text": text[:500], "posted": False,
                          "link": f"https://discord.com/channels/1509315900874948729/{CHAT_CH}/{m['id']}"})
            new += 1
    save(FLAGS, flags[-300:])
    save(STATE, st)
    if new:
        open(os.path.join(BP, ".chat_flag"), "w").write(str(time.time()))


def unposted():
    flags = load(FLAGS, [])
    for f in flags:
        if not f["posted"]:
            print(json.dumps(f, ensure_ascii=False))
            f["posted"] = True
    save(FLAGS, flags)
    try:
        os.remove(os.path.join(BP, ".chat_flag"))
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    if "--unposted" in sys.argv:
        unposted()
    else:
        try:
            check()
        except Exception as ex:
            print("chat watch:", ex)
