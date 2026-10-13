#!/usr/bin/env python3
"""BetterPicks lock scanner for the always-on server (practice picks only, never real bets).

Every SCAN_EVERY seconds (default 180): refresh sportsbook lines (books.py lines), PrizePicks/Kalshi/
Underdog props (books.py props), add singles and parlays that pass Ryan's rules.json bars
(betterpicks.py pick, parlays.py build), then post anything new to Discord (discord_post.py today).

Runs from a work folder (default /home/archie/bp) that holds the code plus its own book.json.
The code is refreshed from the repo checkout once an hour; book.json and discord_posted.json are
never overwritten. When GITHUB_TOKEN is set, book.json + discord_posted.json are pushed to the repo's
betterpicks-live branch after each change so the project can merge the picks and grade them.
Needs DISCORD_BOT_TOKEN + DISCORD_CHANNEL_ID (or DISCORD_WEBHOOK_URL). Standard library only.
"""
import gzip, hashlib, json, os, re, shutil, subprocess, sys, time

REPO = os.environ.get("BP_REPO", "/home/archie/bp-repo")
WORK = os.environ.get("BP_WORK", "/home/archie/bp")
EVERY = int(os.environ.get("SCAN_EVERY", "180"))
CODE = ["books.py", "betterpicks.py", "parlays.py", "discord_post.py", "news.py", "rules.json"]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120)


def refresh_code():
    if os.path.isdir(os.path.join(REPO, ".git")):
        r = git("pull", "-q", "--ff-only", cwd=REPO)
        if r.returncode:
            log("git pull failed:", r.stderr.strip()[:200])
    src = os.path.join(REPO, "betterpicks")
    os.makedirs(os.path.join(WORK, "data"), exist_ok=True)
    for f in CODE:
        shutil.copyfile(os.path.join(src, f), os.path.join(WORK, f))
    for seed, name in (("seed-book.json", "book.json"), ("seed-discord_posted.json", "discord_posted.json")):
        if not os.path.exists(os.path.join(WORK, name)) and os.path.exists(os.path.join(src, seed)):
            shutil.copyfile(os.path.join(src, seed), os.path.join(WORK, name))
    for gz in os.listdir(os.path.join(src, "data")):
        if not gz.endswith(".sqlite.gz"):
            continue
        out = os.path.join(WORK, "data", gz[:-3])
        if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(os.path.join(src, "data", gz)):
            with gzip.open(os.path.join(src, "data", gz)) as fi, open(out + ".tmp", "wb") as fo:
                shutil.copyfileobj(fi, fo)
            os.replace(out + ".tmp", out)
            log("unpacked", gz)


def step(*args, timeout=170):
    try:
        r = subprocess.run([sys.executable, "-I", *args], cwd="/tmp", capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log("timeout:", " ".join(args)); return ""
    if r.returncode:
        log("error:", " ".join(args), (r.stderr or r.stdout).strip()[-300:])
    return r.stdout


def digest(*names):
    h = hashlib.sha1()
    for n in names:
        p = os.path.join(WORK, n)
        if os.path.exists(p):
            h.update(open(p, "rb").read())
    return h.hexdigest()


def publish():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return
    url = git("remote", "get-url", "origin", cwd=REPO).stdout.strip()
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    if not m:
        return
    live = os.path.join(WORK, ".live")
    if not os.path.isdir(os.path.join(live, ".git")):
        os.makedirs(live, exist_ok=True); git("init", "-q", cwd=live)
    for n in ("book.json", "discord_posted.json"):
        shutil.copyfile(os.path.join(WORK, n), os.path.join(live, n))
    git("add", "-A", cwd=live)
    git("-c", "user.name=BetterPicks (server)", "-c", "user.email=noreply@anthropic.com",
        "commit", "-q", "--allow-empty", "-m", "BetterPicks server book", cwd=live)
    r = git("push", "-q", "-f", f"https://x-access-token:{token}@github.com/{m.group(1)}.git",
            "HEAD:refs/heads/betterpicks-live", cwd=live)
    log("published book" if r.returncode == 0 else "publish failed: " + r.stderr.replace(token, "***").strip()[:200])


def scan():
    w = lambda f: os.path.join(WORK, f)
    before = digest("book.json", "discord_posted.json")
    try:
        apps = json.load(open(w("rules.json"))).get("apps", ["sportsbook"])
    except Exception:
        apps = ["sportsbook"]
    sportsbook = "sportsbook" in apps   # Ryan 2026-10-12: Kalshi only for now, so no sportsbook singles
    if sportsbook:
        step(w("books.py"), "lines")
    step(w("books.py"), "props")
    out = (step(w("betterpicks.py"), "pick") if sportsbook else "") + step(w("parlays.py"), "build")
    posted = step(w("discord_post.py"), "today")
    if "posted" in posted:
        log(out.strip()[:500]); log(posted.strip())
    if digest("book.json", "discord_posted.json") != before:
        publish()


if __name__ == "__main__":
    last_code = 0
    while True:
        t0 = time.time()
        try:
            if t0 - last_code > 3600:
                refresh_code()
                if not last_code:
                    publish()   # first push right away so the project can see the server is connected
                last_code = t0
            scan()
        except Exception as e:
            log("scan failed:", e)
        time.sleep(max(10, EVERY - (time.time() - t0)))
