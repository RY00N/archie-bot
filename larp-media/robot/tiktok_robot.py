#!/usr/bin/env python3
"""TikTok robot for @audemarai (runs on the droplet every 5 min via systemd timer).
Reza chose this route on 2026-10-24 knowing it is against TikTok's terms (account flag risk).

  tiktok_robot.py tick      # git pull, and if a slot is due, upload the next unposted queue item
  tiktok_robot.py test      # open TikTok Studio logged in, screenshot, no posting
  tiktok_robot.py post N    # post queue item N now (0-based)

Login = TikTok web session cookie TIKTOK_SESSIONID in /etc/larp-robot.env (never in the repo).
State (what's posted, which slots ran) lives in /var/lib/larp-robot/state.json.
"""
import datetime as dt, json, os, re, subprocess, sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
MEDIA = HERE.parent
STATE = Path(os.environ.get("LARP_STATE", "/var/lib/larp-robot/state.json"))
SHOTS = STATE.parent / "shots"
PT = ZoneInfo("America/Los_Angeles")
UPLOAD = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")


LOGFILE = Path(os.environ.get("LARP_LOG", "/var/lib/larp-robot/robot.log"))


def log(*a):
    line = " ".join(str(x) for x in (dt.datetime.now(PT).strftime("%m-%d %H:%M:%S"),) + a)
    print(line, flush=True)
    try:
        LOGFILE.parent.mkdir(parents=True, exist_ok=True)
        with LOGFILE.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def publish_log():
    """Push the last 400 log lines to branch larp-robot-log (log.txt) so the project can read results without the console.
    Text only, never the cookie. Uses the push access set up by setup.sh."""
    try:
        lines = LOGFILE.read_text().splitlines()[-400:]
        repo = str(MEDIA.parent)
        env = dict(os.environ, GIT_AUTHOR_NAME="larp-robot", GIT_AUTHOR_EMAIL="robot@archie",
                   GIT_COMMITTER_NAME="larp-robot", GIT_COMMITTER_EMAIL="robot@archie")
        env.pop("TIKTOK_SESSIONID", None)
        env["GIT_TERMINAL_PROMPT"] = "0"
        run = lambda *c, inp=None: subprocess.run(["git", "-C", repo, *c], input=inp, capture_output=True, text=True, env=env, timeout=120)
        blob = run("hash-object", "-w", "--stdin", inp="\n".join(lines) + "\n").stdout.strip()
        tree = run("mktree", inp=f"100644 blob {blob}\tlog.txt\n").stdout.strip()
        commit = run("commit-tree", tree, "-m", "robot log " + dt.datetime.now(PT).isoformat(timespec="seconds")).stdout.strip()
        tok = os.environ.get("GH_LOG_TOKEN", "").strip()  # fine-grained token Reza made for this (archie-bot contents only)
        if not tok:
            print("log push skipped: no GH_LOG_TOKEN in /etc/larp-robot.env", flush=True)
            return
        r = run("push", "-q", "-f", f"https://x-access-token:{tok}@github.com/RY00N/archie-bot.git", f"{commit}:refs/heads/larp-robot-log")
        if r.returncode:
            print("log push failed:", r.stderr.replace(tok, "***").strip()[:200], flush=True)
    except Exception as e:
        print("log push failed:", repr(e), flush=True)


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"posted": [], "slots_done": []}


def save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=1))


def due_slot(queue, state):
    """Slot key (e.g. '2026-10-24 15:00') that is due now and not run yet, else None."""
    now = dt.datetime.now(PT)
    for hm in queue["slots_pt"]:
        h, m = map(int, hm.split(":"))
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        key = t.strftime("%Y-%m-%d %H:%M")
        if t <= now < t + dt.timedelta(minutes=45) and key not in state["slots_done"]:
            return key
    return None


def browser():
    from playwright.sync_api import sync_playwright
    sid = os.environ.get("TIKTOK_SESSIONID", "").strip()
    if not sid:
        sys.exit("TIKTOK_SESSIONID missing in /etc/larp-robot.env")
    # TikTok's editor needs a working sound output (WebAudio); the droplet has none, so give it a silent PulseAudio sink
    try:
        subprocess.run("pactl info >/dev/null 2>&1 || pulseaudio -D --exit-idle-time=-1 2>/dev/null; sleep 1; "
                       "pactl list short sinks | grep -q . || pactl load-module module-null-sink sink_name=silent >/dev/null",
                       shell=True, timeout=20)
    except Exception as e:
        log("no pulseaudio:", e)
    pw = sync_playwright().start()
    # under xvfb-run (DISPLAY set) run a real, non-headless window: TikTok's anti-bot code breaks in headless mode
    b = pw.chromium.launch(headless=not os.environ.get("DISPLAY"), args=["--disable-blink-features=AutomationControlled",
                                                    # small droplet: /dev/shm is tiny and RAM is tight, so avoid tab crashes
                                                    "--disable-dev-shm-usage", "--disable-gpu", "--renderer-process-limit=1",
                                                    "--js-flags=--max-old-space-size=512",
                                                    "--autoplay-policy=no-user-gesture-required"])
    ctx = b.new_context(viewport={"width": 1366, "height": 900}, locale="en-US",
                        timezone_id="America/Los_Angeles", user_agent=UA)
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    ctx.add_cookies([{"name": n, "value": sid, "domain": ".tiktok.com", "path": "/",
                      "secure": True, "httpOnly": True} for n in ("sessionid", "sessionid_ss")])
    return pw, b, ctx


def shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    p = SHOTS / f"{dt.datetime.now(PT):%m%d-%H%M%S}-{name}.png"
    try:
        page.screenshot(path=str(p))
    except Exception:
        pass
    return p


def who(page):
    """Logged-in TikTok username for this cookie, or None."""
    try:
        r = page.evaluate("fetch('/passport/web/account/info/', {credentials: 'include'}).then(r => r.json())")
        return (r.get("data") or {}).get("username") or None
    except Exception:
        return None


def file_input(page, wait_s=150):
    """The upload <input type=file>, on the page or inside an iframe (slow droplet: wait up to wait_s)."""
    for _ in range(wait_s // 3):
        for fr in page.frames:
            loc = fr.locator('input[type="file"]')
            try:
                if loc.count():
                    return fr, loc.first
            except Exception:
                pass
        page.wait_for_timeout(3000)
    return None, None


KILL_TOURS = """() => { let n = 0;
  document.querySelectorAll('#react-joyride-portal, [class*="react-joyride"], .__floater, [id^="react-joyride"]')
    .forEach(e => { e.remove(); n++; });
  document.body.style.overflow = 'auto'; return n; }"""


def screen_text(ui):
    """Short text of what's on screen (dialogs, toasts, buttons) for the log."""
    try:
        return " ".join(ui.evaluate("""() => {
          const pick = s => [...document.querySelectorAll(s)].filter(e => e.offsetParent !== null)
              .map(e => e.innerText.trim()).filter(Boolean);
          return [...pick('[role=dialog], .TUXModal-overlay, [class*=toast], [class*=Toast], [class*=notice]'),
                  'BUTTONS: ' + pick('button').slice(0, 25).join(' / ')].join(' || ');
        }""").split())[:900]
    except Exception as e:
        return f"(no screen text: {e!r})"


def retry_context(ui):
    """Text around TikTok's Retry button (says what failed: upload, copyright check, ...)."""
    try:
        return ui.evaluate("""() => { const b = [...document.querySelectorAll('button')].find(x => x.innerText.trim() === 'Retry');
            let e = b; for (let k = 0; k < 4 && e && e.parentElement; k++) e = e.parentElement;
            return e ? e.innerText.replace(/\\s+/g, ' ').slice(0, 300) : ''; }""")
    except Exception as e:
        return repr(e)


CAPTCHA = '#captcha-verify-container, [class*="captcha"], [id*="captcha"], iframe[src*="captcha"], [class*="secsdk"]'


def captcha_seen(page):
    for fr in page.frames:
        try:
            loc = fr.locator(CAPTCHA)
            if loc.count() and loc.first.is_visible():
                return True
        except Exception:
            pass
    return False


def close_popups(ui, page):
    """Dismiss TikTok Studio popups (feature tours, tips, 'turn on content checks') that cover the form."""
    for fr in page.frames:
        try:
            n = fr.evaluate(KILL_TOURS)
            if n:
                log("removed tour overlay x", n)
        except Exception:
            pass
    for _ in range(6):
        modal = ui.locator('.TUXModal-overlay, [role="dialog"]')
        if not modal.count() or not modal.first.is_visible():
            return
        try:
            log("popup:", " ".join(modal.first.inner_text(timeout=3000).split())[:160])
        except Exception:
            pass
        for label in ("Got it", "Not now", "Cancel", "Skip", "Close", "OK", "Dismiss", "Maybe later", "Turn on"):
            b = modal.first.get_by_role("button", name=label, exact=True)
            if b.count() and b.first.is_visible():
                b.first.click()
                break
        else:
            x = modal.first.locator('[aria-label="Close"], [class*="close"]')
            if x.count():
                x.first.click(force=True)
            else:
                page.keyboard.press("Escape")
        page.wait_for_timeout(1500)


def upload(item):
    path = MEDIA / item["file"]
    pw, b, ctx = browser()
    page = ctx.new_page()
    def on_resp(r):  # log TikTok's answers to the post/publish calls (why a Post click does nothing)
        try:
            if r.request.method == "POST" and re.search(r"post|publish|project|create|item", r.url, re.I) \
                    and not re.search(r"monitor|log|slardar|mssdk|webcast|collect", r.url, re.I):
                body = ""
                try:
                    body = " ".join(r.text().split())[:300]
                except Exception:
                    pass
                log("net", r.status, r.url.split("?")[0][-80:], body)
        except Exception:
            pass
    page.on("response", on_resp)
    def on_bad(r):  # any failed TikTok call is a clue for "Something went wrong"
        try:
            if r.status >= 400 and "tiktok" in r.url:
                log("net ERROR", r.status, r.request.method, r.url.split("?")[0][-90:])
        except Exception:
            pass
    page.on("response", on_bad)
    page.on("requestfailed", lambda q: "tiktok" in q.url and log("net FAILED", q.method, q.url.split("?")[0][-90:], q.failure))
    page.on("console", lambda m: m.type == "error" and log("console:", m.text[:200]))
    try:
        page.goto(UPLOAD, wait_until="domcontentloaded", timeout=180000)
        page.wait_for_timeout(6000)
        if "login" in page.url or not who(page):
            shot(page, "logged-out")
            raise RuntimeError("not logged in: TIKTOK_SESSIONID expired, needs a fresh one")
        fr, inp = file_input(page)
        if inp is None:
            raise RuntimeError(f"upload box never appeared (user {who(page)})")
        inp.set_input_files(str(path))
        ui = fr  # the frame holding the upload form (main frame or an iframe)
        btn = ui.locator('button[data-e2e="post_video_button"]')
        btn.wait_for(timeout=180000)
        # wait for TikTok to finish processing the upload: Post button stops being disabled (slow droplet: up to 8 min)
        retries = 0
        for i in range(160):
            retry = ui.get_by_role("button", name="Retry", exact=True)
            if retry.count() and retry.first.is_visible():
                why = retry_context(ui)
                log("upload error on TikTok's side:", why)
                if retries >= 3:
                    raise RuntimeError("upload kept failing: " + why)
                retries += 1
                retry.first.click()
                page.wait_for_timeout(5000)
                continue
            dis = btn.get_attribute("aria-disabled") or btn.get_attribute("data-disabled") or ""
            if btn.is_enabled() and dis not in ("true",) and ui.get_by_text("Uploaded").count():
                break
            if i % 20 == 0:
                log("waiting for upload to finish...", screen_text(ui)[:200])
            page.wait_for_timeout(3000)
        else:
            raise RuntimeError("upload never finished processing")
        close_popups(ui, page)
        cap = ui.locator('div[contenteditable="true"]').first
        try:
            cap.click(timeout=10000)
        except Exception:
            close_popups(ui, page)
            cap.click(force=True)
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        for word in item["caption"].split(" "):
            page.keyboard.type(word, delay=40)
            if word.startswith("#"):
                page.wait_for_timeout(1500)  # let the hashtag popup show, then close it
                page.keyboard.press("Escape")
            page.keyboard.type(" ", delay=40)
        page.wait_for_timeout(1500)
        close_popups(ui, page)
        shot(page, "before-post")
        for k in range(3):  # "Something went wrong" in the editor/preview: press its Retry and let it reload
            r = ui.get_by_role("button", name="Retry", exact=True)
            if not (r.count() and ui.get_by_text("Something went wrong").count()):
                break
            log("editor error, pressing Retry", k + 1)
            r.last.click(force=True)
            page.wait_for_timeout(15000)
        if ui.get_by_text("Something went wrong").count():
            log("editor still says Something went wrong")
        log("before Post:", screen_text(ui)[:400])
        btn.scroll_into_view_if_needed()
        page.wait_for_timeout(1000)
        box = btn.bounding_box()
        if box:  # a real mouse click (TikTok ignores some synthetic clicks)
            page.mouse.move(box["x"] + box["width"] / 2 - 20, box["y"] + box["height"] / 2 + 5, steps=8)
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, delay=90)
            log("clicked Post (mouse)")
        else:
            close_popups(ui, page)
            btn.click(force=True)
            log("clicked Post (forced)")
        page.wait_for_timeout(5000)
        if captcha_seen(page):
            log("CAPTCHA shown after Post")
        for label in ("Post now", "Post anyway"):  # confirm dialog while TikTok's checks run
            c = ui.get_by_role("button", name=label)
            if c.count():
                c.first.click()
                page.wait_for_timeout(3000)
        log("after Post:", screen_text(ui)[:400])
        # success = TikTok leaves the upload page or shows a "posted / uploaded" message
        done_text = page.get_by_text(re.compile(r"(video (has been|was) (posted|uploaded|published)|"
                                                r"manage your posts|upload another video|view profile)", re.I))
        for _ in range(60):
            if "/tiktokstudio/upload" not in page.url or done_text.count():
                break
            page.wait_for_timeout(3000)
        shot(page, "posted")
        if "/tiktokstudio/upload" not in page.url or done_text.count():
            return True
        log("no 'posted' confirmation. Screen now:", screen_text(ui), "| captcha:", captcha_seen(page))
        try:
            log("page text:", " ".join(ui.evaluate("document.body.innerText").split())[:1200])
        except Exception:
            pass
        return False
    except Exception as e:
        shot(page, "error")
        log("upload failed:", repr(e))
        return False
    finally:
        ctx.close(); b.close(); pw.stop()


def git_pull():
    subprocess.run(["git", "-C", str(MEDIA.parent), "pull", "-q", "--ff-only"], check=False, timeout=180)


def main_test():
    pw, b, ctx = browser()
    page = ctx.new_page()
    page.goto(UPLOAD, wait_until="domcontentloaded", timeout=180000)
    user = who(page)
    log("tiktok user:", user or "NOT LOGGED IN (cookie wrong or expired)")
    fr, inp = file_input(page)
    title = page.title()
    log("LOGGED IN, upload page ready" if (user and inp is not None) else
        f"NOT READY (url {page.url}, title {title!r}, frames {len(page.frames)})", "screenshot:", shot(page, "test"))
    if inp is None:
        body = page.evaluate("document.body ? document.body.innerText.slice(0, 400) : ''")
        log("page text:", " | ".join(body.split("\n")[:12]))
    ctx.close(); b.close(); pw.stop()
    return


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tick"
    queue = json.loads((MEDIA / "queue.json").read_text())
    state = load_state()
    if cmd == "test":
        main_test(); publish_log()
        return
    if cmd == "post":
        i = int(sys.argv[2])
        ok = upload(queue["items"][i])
        if ok and queue["items"][i]["file"] not in state["posted"]:
            state["posted"].append(queue["items"][i]["file"]); save_state(state)
        log("post", i, "ok" if ok else "FAILED")
        publish_log()
        return
    git_pull()
    queue = json.loads((MEDIA / "queue.json").read_text())
    # one-off jobs from the project (robot/jobs.json): {"jobs":[{"id":"j1","cmd":"post","n":0}|{"id":"j2","cmd":"test"}]}
    try:
        jobs = json.loads((HERE / "jobs.json").read_text()).get("jobs", [])
    except (OSError, ValueError):
        jobs = []
    for j in jobs:
        if j["id"] in state.setdefault("jobs_done", []):
            continue
        state["jobs_done"].append(j["id"]); save_state(state)
        log("JOB", j["id"], j)
        if j.get("cmd") == "post":
            it = queue["items"][int(j["n"])]
            ok = upload(it)
            if ok and it["file"] not in state["posted"]:
                state["posted"].append(it["file"]); save_state(state)
            log("JOB", j["id"], "post", j["n"], it["file"], "ok" if ok else "FAILED")
        elif j.get("cmd") == "test":
            sys.argv[1:] = ["test"]; main_test()
        publish_log()
    slot = due_slot(queue, state)
    if not slot:
        return
    nxt = next((it for it in queue["items"] if it["file"] not in state["posted"]), None)
    state["slots_done"].append(slot); save_state(state)  # one try per slot, never double-post
    if not nxt:
        log(slot, "queue empty")
        return
    ok = upload(nxt)
    if ok:
        state["posted"].append(nxt["file"]); save_state(state)
    log(slot, nxt["file"], "posted" if ok else "FAILED (retries at next slot)")
    publish_log()


if __name__ == "__main__":
    main()
