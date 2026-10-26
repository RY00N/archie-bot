#!/usr/bin/env python3
"""One-time invite DMs for BetterPicks (Ryan 2026-10-26).

DMs every human member of the old server "BetterPicks (old)" one at a time, slowly, from the separate
throwaway invite bot (INVITE_BOT_TOKEN), never from BetterBot. Members already in the new server are
skipped (read with BetterBot's DISCORD_BOT_TOKEN, read only).

- One DM about every 90 seconds (75-105 random), any hour (Ryan 2026-10-26).
- sent.json keeps who got it (or can't get DMs) so a restart resumes where it left off.
- Stops for good on a rate limit (429), 40003 (opening DMs too fast), 40002/401 (bot flagged or token
  dead) or any other Discord error, and writes the reason to STOPPED. Delete STOPPED to let it run again.
- Touch STOP in the work folder to pause it by hand.
- Pushes counts only (no user ids) to the invite-dm-status branch every 30 min when GITHUB_TOKEN is set.
Standard library only.  Usage: invite_dm.py [--dry-run]
"""
import json, os, random, subprocess, sys, time, urllib.error, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

API = "https://discord.com/api/v10"
OLD_GUILD = os.environ.get("OLD_GUILD_ID", "1044695203908431882")
NEW_GUILD = os.environ.get("BP_GUILD_ID", "1509315900874948729")
WORK = os.environ.get("INVITE_WORK", "/home/archie/invite-dm")
LINK = os.environ.get("INVITE_LINK", "https://discord.gg/857TCcDMj")
GAP = (75, 105)
PT = ZoneInfo("America/Los_Angeles")
TEXT = ("Hey! \U0001F44B Thanks for being part of the old BetterPicks server. We just launched a brand new "
        "BetterPicks server with daily picks, live injury and lineup updates, and win slips. As a thank-you to "
        "everyone from the original crew, you get a free membership if you join through this link: "
        f"{LINK} See you in there! \U0001F525")


class Stop(Exception):
    pass


def log(*a):
    print(datetime.now(PT).strftime("%m-%d %H:%M:%S PT"), *a, flush=True)


def path(n):
    return os.path.join(WORK, n)


def load(n, default):
    try:
        return json.load(open(path(n)))
    except Exception:
        return default


def save(n, data):
    with open(path(n) + ".tmp", "w") as f:
        json.dump(data, f)
    os.replace(path(n) + ".tmp", path(n))


def call(method, url, token, body=None):
    req = urllib.request.Request(API + url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bot " + token, "Content-Type": "application/json",
                                          "User-Agent": "DiscordBot (https://github.com/RY00N/archie-bot, 1)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:
            return e.code, None


def members(guild, token):
    out, after = [], "0"
    while True:
        st, b = call("GET", f"/guilds/{guild}/members?limit=1000&after={after}", token)
        if st != 200:
            raise Stop(f"member list {guild}: HTTP {st} {b}")
        out += b
        if len(b) < 1000:
            return out
        after = b[-1]["user"]["id"]
        time.sleep(1)


def dm(uid, token):
    """'sent', 'closed' (DMs off, blocked us, or left the old server since the list was read) or raises Stop."""
    st, ch = call("POST", "/users/@me/channels", token, {"recipient_id": uid})
    if st != 200:
        raise Stop(f"open DM: HTTP {st} {ch}")
    st, b = call("POST", f"/channels/{ch['id']}/messages", token, {"content": TEXT})
    if st == 200:
        return "sent"
    if st == 403 and (b or {}).get("code") in (50007, 50278):   # 50278 = no mutual server (they left)
        return "closed"
    raise Stop(f"send DM: HTTP {st} {b}")


def push_status(status):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return
    live = path(".status")
    os.makedirs(live, exist_ok=True)
    git = lambda *a: subprocess.run(["git", *a], cwd=live, capture_output=True, text=True, timeout=60)
    if not os.path.isdir(os.path.join(live, ".git")):
        git("init", "-q")
    json.dump(status, open(os.path.join(live, "status.json"), "w"), indent=1)
    git("add", "-A")
    git("-c", "user.name=BetterPicks invite (server)", "-c", "user.email=noreply@anthropic.com", "commit", "-q", "-m", "invite status")
    r = git("push", "-q", "-f", f"https://x-access-token:{token}@github.com/RY00N/archie-bot.git", "HEAD:refs/heads/invite-dm-status")
    if r.returncode:
        log("status push failed:", r.stderr.replace(token, "***").strip()[:200])


def main():
    os.makedirs(WORK, exist_ok=True)
    if os.path.exists(path("STOPPED")):
        log("STOPPED earlier:", open(path("STOPPED")).read().strip(), "- delete it to run again"); return
    tok = os.environ["INVITE_BOT_TOKEN"]
    sent = load("sent.json", {})
    old = [m["user"] for m in members(OLD_GUILD, tok) if not m["user"].get("bot")]
    try:
        already = {m["user"]["id"] for m in members(NEW_GUILD, os.environ["DISCORD_BOT_TOKEN"])}
    except Exception as e:
        log("couldn't read new server members, not skipping any:", e); already = set()
    todo = [u for u in old if u["id"] not in sent and u["id"] not in already]
    status = {"old_members": len(old), "already_in_new": len([u for u in old if u["id"] in already]),
              "sent": sum(v == "sent" for v in sent.values()), "dms_closed": sum(v == "closed" for v in sent.values()),
              "left": len(todo), "stopped": None}
    log("status", status)
    if "--dry-run" in sys.argv:
        print(TEXT); return
    last_push = 0
    try:
        for u in todo:
            if os.path.exists(path("STOP")):
                raise Stop("paused by hand (STOP file)")
            res = dm(u["id"], tok)
            sent[u["id"]] = res; save("sent.json", sent)
            status["sent" if res == "sent" else "dms_closed"] += 1; status["left"] -= 1
            status["updated"] = datetime.now(PT).isoformat(timespec="seconds")
            log(res, u.get("username"), f"({status['left']} left)")
            if time.time() - last_push > 1800:
                push_status(status); last_push = time.time()
            time.sleep(random.uniform(*GAP))
        log("done, everyone reached")
        status["done"] = True
    except Stop as e:
        status["stopped"] = str(e)[:300]
        open(path("STOPPED"), "w").write(str(e))
        log("STOPPED:", e)
    push_status(status)


if __name__ == "__main__":
    main()
