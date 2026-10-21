"""Archie's X feed (Ryan 2026-10-21): recent posts from the accounts Ryan's spare X account follows, for QQQ context.

Reads each followed account's latest posts through X's own web API, logged in with that account's cookies, and writes
feeds/qqq-x.json (last LOOKBACK_H hours, newest first). Each post is tagged:
  headline - wire-style squawk (DeItaone, FirstSquawk, LiveSquawk, or "*"/BREAKING style text)
  flow     - options/dark-pool/gamma flow (unusual_whales, SpotGamma, or flow words)
  analysis - everything else (macro, charts, company breakdowns)
plus qqq=true when it mentions QQQ, the Nasdaq, the Fed/macro data, or a big QQQ holding.

INFO ONLY. Nothing here gates or places a trade. Who is read = whoever the X account follows (Ryan edits it on X).
Runs in the cloud (the cookies live only there, never in the repo): python3 xfeed.py  -> prints a short summary.
Cookies from env: auth_token and ct0 (also accepted as X_AUTH_TOKEN / X_CT0). Needs only the stdlib."""
import json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "feeds", "qqq-x.json")
CFG = os.path.join(HERE, "feeds", ".x-api-cache.json")  # X's public web query ids, refreshed daily (no secrets)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
LOOKBACK_H = 12
PER_USER = 20
# Read on top of whoever the X account follows (Ryan 2026-10-21: "follow more people ... anything to help archie").
# Read-only: they are not followed, so the spare account stays untouched.
EXTRA = ["Walter_Bloomberg", "financialjuice", "NickTimiraos", "zerohedge", "Fxhedgers", "TheTranscript_",
         "bespokeinvest", "MikeZaccardi", "Mr_Derivatives", "Barchart"]
SQUAWK = {"deitaone", "firstsquawk", "livesquawk", "walter_bloomberg", "financialjuice", "fxhedgers"}
FLOW_ACCTS = {"unusual_whales", "spotgamma", "mr_derivatives"}
FLOW_RE = re.compile(r"\b(flow|sweeps?|block trade|dark ?pool|gamma|gex|0dte|call (buying|volume)|put (buying|volume)|"
                     r"open interest|unusual (options|activity)|vanna|charm|dealer)\b", re.I)
HEADLINE_RE = re.compile(r"^\s*(\*|breaking|just in|🚨|\$?[A-Z]{2,5}:)", re.I)
QQQ_RE = re.compile(r"\b(qqq|nasdaq|ndx|nq|tech stocks|mega-?caps?|magnificent|mag ?7|semis?|semiconductors?|smh|soxx?|"
                    r"fed|fomc|powell|cpi|pce|ppi|jobs report|payrolls|nfp|yields?|treasur(y|ies)|tariffs?|"
                    r"nvda|nvidia|msft|microsoft|aapl|apple|amzn|amazon|avgo|broadcom|meta|googl?|alphabet|google|"
                    r"tsla|tesla|nflx|netflix|amd|pltr|palantir|costco|asml|tsmc|tsm)\b", re.I)


def _cookies():
    a = os.environ.get("auth_token") or os.environ.get("X_AUTH_TOKEN")
    c = os.environ.get("ct0") or os.environ.get("X_CT0")
    if not (a and c):
        sys.exit("xfeed: X cookies missing (auth_token / ct0)")
    return a, c


def _get(url, headers, timeout=30):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return f.read().decode("utf8", "replace")


def _api_config(base):
    try:
        cfg = json.load(open(CFG))
        if time.time() - cfg["at"] < 86400:
            return cfg
    except Exception:
        pass
    html = _get("https://x.com/home", base)
    js = re.search(r"https://abs\.twimg\.com/responsive-web/client-web/main\.[0-9a-f]+\.js", html).group(0)
    m = _get(js, {"User-Agent": UA})
    cfg = {"at": time.time(), "bearer": re.search(r'"(AAAAAAAAAAAAAAAAAAAAA[^"]+)"', m).group(1), "ops": {}}
    # HomeLatestTimeline lives in a lazy-loaded chunk named after the home timeline; add those chunks to the search.
    names = dict(re.findall(r'(\d+):"([\w~.-]*HomeTimeline[\w~.-]*)"', html))
    hashes = dict(re.findall(r'(\d+):"([0-9a-f]{12,20})"', html))
    for cid, name in names.items():
        if cid in hashes and "Adblock" not in name:
            try:
                m += _get(f"https://abs.twimg.com/responsive-web/client-web/{name}.{hashes[cid]}a.js", {"User-Agent": UA})
            except Exception:
                pass
    for op in ("Following", "UserTweets", "UserByScreenName", "HomeLatestTimeline"):
        mm = re.search(r'queryId:"([^"]+)",operationName:"%s",operationType:"query",metadata:\{featureSwitches:\[([^\]]*)\]'
                       % op, m)
        cfg["ops"][op] = {"id": mm.group(1), "features": re.findall(r'"([a-z_0-9]+)"', mm.group(2))}
    vid = re.search(r'"user_id":"(\d+)"', html)
    cfg["viewer"] = vid.group(1) if vid else None
    os.makedirs(os.path.dirname(CFG), exist_ok=True)
    json.dump(cfg, open(CFG, "w"))
    return cfg


class X:
    def __init__(self):
        a, c = _cookies()
        base = {"User-Agent": UA, "Cookie": f"auth_token={a}; ct0={c}"}
        self.cfg = _api_config(base)
        self.h = dict(base, **{"Authorization": "Bearer " + self.cfg["bearer"], "x-csrf-token": c,
                               "x-twitter-auth-type": "OAuth2Session", "x-twitter-active-user": "yes"})

    def gql(self, op, variables):
        o = self.cfg["ops"][op]
        q = urllib.parse.urlencode({"variables": json.dumps(variables),
                                    "features": json.dumps({k: True for k in o["features"]})})
        return json.loads(_get(f"https://x.com/i/api/graphql/{o['id']}/{op}?{q}", self.h))

    def following(self):
        r = self.gql("Following", {"userId": self.cfg["viewer"], "count": 100, "includePromotedContent": False})
        out = []
        for u in _walk(r["data"]["user"]["result"].get("timeline"), "User"):  # skip the viewer's own wrapper
            core = u.get("core") or u.get("legacy") or {}
            sn = core.get("screen_name")
            if sn and u.get("rest_id") != self.cfg["viewer"] and all(sn != x[1] for x in out):
                out.append((u["rest_id"], sn, core.get("name", sn)))
        return out

    def user(self, sn):
        r = self.gql("UserByScreenName", {"screen_name": sn})["data"]["user"]["result"]
        return r["rest_id"], r["core"]["screen_name"], r["core"].get("name", sn)

    def follow(self, uid):
        req = urllib.request.Request("https://x.com/i/api/1.1/friendships/create.json",
                                     data=urllib.parse.urlencode({"user_id": uid}).encode(),
                                     headers=dict(self.h, **{"Content-Type": "application/x-www-form-urlencoded"}))
        urllib.request.urlopen(req, timeout=30).read()

    def home(self, count=40):
        """The account's Following tab, newest first: one call covers every followed account."""
        r = self.gql("HomeLatestTimeline", {"count": count, "includePromotedContent": False,
                                            "latestControlAvailable": True, "requestContext": "launch"})
        return list(_walk(r, "Tweet"))

    def tweets(self, uid):
        r = self.gql("UserTweets", {"userId": uid, "count": PER_USER, "includePromotedContent": False,
                                    "withQuickPromoteEligibilityTweetFields": False, "withVoice": False})
        return list(_walk(r, "Tweet"))


def _walk(node, typename):
    """Yield each outermost dict with __typename == typename (X nests timelines deeply and changes layout often)."""
    if isinstance(node, dict):
        if node.get("__typename") == typename:
            yield node  # not inside it: a retweet's or quote's inner original is not a separate post
            return
        for v in node.values():
            yield from _walk(v, typename)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, typename)


def _text(t):
    note = (((t.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {}).get("text")
    return note or t.get("legacy", {}).get("full_text", "")


def _post(t, since, who):
    """One timeline Tweet -> feed row, or None (too old, someone else's quoted/retweeted original, or a reply)."""
    lg = t.get("legacy") or {}
    uid = lg.get("user_id_str")
    if who is None:  # home timeline: author comes with the post
        u = ((t.get("core") or {}).get("user_results") or {}).get("result") or {}
        c = u.get("core") or u.get("legacy") or {}
        who = {u.get("rest_id"): (c.get("screen_name", "?"), c.get("name", "?"))}
    if not lg.get("created_at") or uid not in who:
        return None
    sn, name = who[uid]
    at = datetime.strptime(lg["created_at"], "%a %b %d %H:%M:%S %z %Y")
    if at < since or lg.get("in_reply_to_user_id_str") not in (None, uid):
        return None
    rt = lg.get("retweeted_status_result")
    text = _text((rt or {}).get("result", {})) if rt else _text(t)
    text = re.sub(r"\s*https://t\.co/\w+$", "", text).strip()
    if not text:
        return None
    return {"at": at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "handle": sn, "name": name,
            "tag": tag(sn, text), "qqq": bool(QQQ_RE.search(text)), "retweet": bool(rt), "text": text[:1000],
            "likes": lg.get("favorite_count", 0), "reposts": lg.get("retweet_count", 0),
            "url": f"https://x.com/{sn}/status/{t['rest_id']}"}


def tag(handle, text):
    h = handle.lower()
    if h in SQUAWK or HEADLINE_RE.search(text):
        return "headline"
    if h in FLOW_ACCTS or FLOW_RE.search(text):
        return "flow"
    return "analysis"


def run():
    x = X()
    since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_H)
    posts, accounts, errors = {}, [], []
    users = x.following()
    have = {u[1].lower() for u in users}
    for sn in EXTRA:
        if sn.lower() not in have:
            try:
                users.append(x.user(sn))
            except Exception as e:
                errors.append(f"{sn}: lookup failed ({str(e)[:40]})")
    for uid, sn, name in users:
        accounts.append(sn)
        try:
            tws = x.tweets(uid)
        except urllib.error.HTTPError as e:
            errors.append(f"{sn}: HTTP {e.code}")
            continue
        for t in tws:
            p = _post(t, since, {uid: (sn, name)})
            if p:
                posts[t["rest_id"]] = p
        time.sleep(1)
    items = sorted(posts.values(), key=lambda p: p["at"], reverse=True)
    out = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "lookback_hours": LOOKBACK_H,
           "note": "Info only, never a trade signal. Posts from accounts the spare X account follows.",
           "accounts": accounts, "errors": errors,
           "counts": {k: sum(p["tag"] == k for p in items) for k in ("headline", "flow", "analysis")},
           "posts": items}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    json.dump(out, open(tmp, "w"), indent=1, ensure_ascii=False)
    os.replace(tmp, OUT)
    return out


# --- Live mode (droplet): the Following tab every LIVE_EVERY seconds in market hours, hourly otherwise -----------------
# Ryan 2026-10-21: "update archie on everything every second during trading hours, then once an hour when its not".
# X allows about one Following-tab read per ~2 s on a logged-in account, so 3 s is as close to live as is safe.
LIVE_OUT = os.path.join(HERE, "feeds", "qqq-x-live.json")  # droplet-only (gitignored), so git pulls never conflict
LIVE_EVERY, IDLE_EVERY, LIVE_KEEP_H = 3, 3600, 12
_live = {"t": 0, "busy": False, "x": None, "err": 0}


def market_hours(now=None):
    from zoneinfo import ZoneInfo
    n = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    return n.weekday() < 5 and "09:30" <= n.strftime("%H:%M") < "16:00"


def poll_live(log=print):
    """One Following-tab read merged into feeds/qqq-x-live.json; returns how many posts were new."""
    if _live["x"] is None:
        _live["x"] = X()
    since = datetime.now(timezone.utc) - timedelta(hours=LIVE_KEEP_H)
    try:
        old = {p["url"]: p for p in json.load(open(LIVE_OUT))["posts"] if p["at"] >= since.strftime("%Y-%m-%dT%H:%M:%SZ")}
    except Exception:
        old = {}
    if time.time() - _live.get("who_at", 0) > 3600:  # only followed accounts (drops ads / suggested posts)
        _live["who"] = {u[0]: (u[1], u[2]) for u in _live["x"].following()}
        _live["who_at"] = time.time()
    new = 0
    for t in _live["x"].home():
        p = _post(t, since, _live["who"])
        if p and p["url"] not in old:
            old[p["url"]] = p
            new += 1
    items = sorted(old.values(), key=lambda p: p["at"], reverse=True)
    out = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "lookback_hours": LIVE_KEEP_H,
           "mode": "live" if market_hours() else "hourly",
           "note": "Info only, never a trade signal. Following tab of the spare X account.",
           "counts": {k: sum(p["tag"] == k for p in items) for k in ("headline", "flow", "analysis")}, "posts": items}
    os.makedirs(os.path.dirname(LIVE_OUT), exist_ok=True)
    tmp = LIVE_OUT + ".tmp"
    json.dump(out, open(tmp, "w"), indent=1, ensure_ascii=False)
    os.replace(tmp, LIVE_OUT)
    return new


def maybe_run(log=print):
    """Call from the bot loop (intel.maybe_run). Does nothing until the droplet has the X cookies in its env."""
    _live["calls"] = _live.get("calls", 0) + 1
    if _live["busy"] or not (os.environ.get("auth_token") or os.environ.get("X_AUTH_TOKEN")):
        return
    every = LIVE_EVERY if market_hours() else IDLE_EVERY
    every *= min(2 ** _live["err"], 100)  # back off on errors (429s, expired cookies)
    if time.time() - _live["t"] < every:
        return
    _live["t"], _live["busy"] = time.time(), True

    def job():
        try:
            n = poll_live(log)
            _live["err"] = 0
            if n and not market_hours():
                log(f"XFEED +{n} posts")
        except Exception as e:
            _live["err"] = min(_live["err"] + 1, 7)
            _live["x"] = None
            _live["last_error"] = str(e)[:120]
            log(f"XFEED failed: {str(e)[:120]}")
        finally:
            _live["busy"] = False
    import threading
    threading.Thread(target=job, daemon=True, name="xfeed").start()


def status():
    """Small health line for the published P&L snapshot (no secrets): is the live feed running on this machine?"""
    out = {"cookies": bool(os.environ.get("auth_token") or os.environ.get("X_AUTH_TOKEN")), "errors": _live["err"],
           "last_error": _live.get("last_error"), "calls": _live.get("calls", 0), "busy": _live["busy"],
           "last_try": datetime.fromtimestamp(_live["t"], timezone.utc).strftime("%H:%M:%SZ") if _live["t"] else None}
    try:
        d = json.load(open(LIVE_OUT))
        out.update(updated=d["updated"], posts=len(d["posts"]), mode=d.get("mode"))
    except Exception:
        pass
    return out


def follow_extras():
    x = X()
    have = {u[1].lower() for u in x.following()}
    for sn in EXTRA:
        if sn.lower() in have:
            continue
        try:
            uid, real, _ = x.user(sn)
            x.follow(uid)
            print("followed", real)
        except Exception as e:
            print("skip", sn, str(e)[:60])
        time.sleep(2)


if __name__ == "__main__":
    if "follow" in sys.argv:
        follow_extras(); sys.exit()
    if "live" in sys.argv:
        print(poll_live(), "new posts ->", LIVE_OUT); sys.exit()
    o = run()
    print(f"{len(o['posts'])} posts from {len(o['accounts'])} accounts {o['counts']} errors={o['errors']}")
    for p in [p for p in o["posts"] if p["qqq"]][:8]:
        print(f"  {p['at'][11:16]}Z [{p['tag']}] @{p['handle']}: {p['text'][:110]!r}")
