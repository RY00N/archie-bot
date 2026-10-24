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


def log(*a):
    print(dt.datetime.now(PT).strftime("%m-%d %H:%M:%S"), *a, flush=True)


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
    pw = sync_playwright().start()
    b = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled",
                                                    # small droplet: /dev/shm is tiny and RAM is tight, so avoid tab crashes
                                                    "--disable-dev-shm-usage", "--disable-gpu", "--renderer-process-limit=1",
                                                    "--js-flags=--max-old-space-size=512"])
    ctx = b.new_context(viewport={"width": 1366, "height": 900}, locale="en-US",
                        timezone_id="America/Los_Angeles", user_agent=UA)
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
        for i in range(160):
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
        try:
            btn.click(timeout=15000)
            log("clicked Post")
        except Exception:
            close_popups(ui, page)
            btn.click(force=True)
        page.wait_for_timeout(4000)
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
        log("no 'posted' confirmation. Screen now:", screen_text(ui))
        return False
    except Exception as e:
        shot(page, "error")
        log("upload failed:", repr(e))
        return False
    finally:
        ctx.close(); b.close(); pw.stop()


def git_pull():
    subprocess.run(["git", "-C", str(MEDIA.parent), "pull", "-q", "--ff-only"], check=False, timeout=180)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tick"
    queue = json.loads((MEDIA / "queue.json").read_text())
    state = load_state()
    if cmd == "test":
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
    if cmd == "post":
        i = int(sys.argv[2])
        ok = upload(queue["items"][i])
        if ok and queue["items"][i]["file"] not in state["posted"]:
            state["posted"].append(queue["items"][i]["file"]); save_state(state)
        log("post", i, "ok" if ok else "FAILED")
        return
    git_pull()
    queue = json.loads((MEDIA / "queue.json").read_text())
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


if __name__ == "__main__":
    main()
