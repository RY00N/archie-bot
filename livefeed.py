"""Fast live feed for the Agent City: a small {"updated", "meme", "futures"} snapshot pushed every few seconds
to branch `tick` of the repo (the full pnl.json on branch `live` still goes out every ~5 min).

The city can't fetch arbitrary URLs from claude.ai, it reads GitHub through the GitHub connector (API, not the
CDN), so a fresh push there shows up on its next poll. Practice books only, same data as the public `live` feed.
Started from publish_pnl.snapshot(); runs as a daemon thread, guarded by sys._live_loop like meme/snipe."""
import json, os, subprocess, sys, threading, time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PUB_DIR = os.path.expanduser("~/tick-pub")
STATE = os.path.join(HERE, "livefeed_state.json")
EVERY_S = 5
LOOP_NAME = "tick-1"


def _now():
    return datetime.now(timezone.utc).isoformat()[:19] + "Z"


def _build():
    snap = {"updated": _now(), "every_s": EVERY_S}
    for key, mod in (("meme", "meme"), ("futures", "futures_sim")):
        try:
            m = sys.modules.get(mod) or __import__(mod)
            snap[key] = m.summary()
        except Exception as e:
            snap[key] = {"status": f"error: {e}"}
    return snap


def _git(*args):
    return subprocess.run(["git", "-C", PUB_DIR, *args], capture_output=True, text=True, timeout=30)


def _push(snap, token, slug):
    os.makedirs(PUB_DIR, exist_ok=True)
    if not os.path.isdir(os.path.join(PUB_DIR, ".git")):
        _git("init", "-q")
        _git("symbolic-ref", "HEAD", "refs/heads/tick")
    json.dump(snap, open(os.path.join(PUB_DIR, "tick.json"), "w"))
    _git("update-ref", "-d", "HEAD")  # new root commit each time: the branch stays one commit long
    _git("add", "tick.json")
    _git("-c", "user.name=Archie", "-c", "user.email=archie@localhost", "commit", "-q", "-m", "tick " + snap["updated"])
    r = _git("push", "-q", "-f", f"https://x-access-token:{token}@github.com/{slug}.git", "HEAD:refs/heads/tick")
    if r.returncode:
        raise RuntimeError("push failed: " + r.stderr.replace(token, "***")[:200])


def _save(**kw):
    tmp = STATE + ".tmp"
    json.dump(kw, open(tmp, "w")); os.replace(tmp, STATE)


def _run():
    import publish_pnl
    n = 0
    while getattr(sys, "_live_loop", None) == LOOP_NAME:
        t0 = time.time()
        try:
            token = os.environ.get("GITHUB_TOKEN")
            if token:
                _push(_build(), token, publish_pnl.repo_slug())
                n += 1
                if n % 500 == 0:
                    _git("gc", "-q", "--prune=now")
                _save(ok=_now(), error="")
        except Exception as e:
            _save(ok="", error=f"{_now()} {e}"[:200])
            time.sleep(30)
        time.sleep(max(0.5, EVERY_S - (time.time() - t0)))


def start():
    if getattr(sys, "_live_loop", None) == LOOP_NAME:
        return
    sys._live_loop = LOOP_NAME
    threading.Thread(target=_run, daemon=True, name=LOOP_NAME).start()


def url():
    try:
        st = json.load(open(STATE))
        return "branch tick / tick.json", st.get("error", "")
    except Exception:
        return "branch tick / tick.json", ""
