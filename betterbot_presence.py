#!/usr/bin/env python3
"""Keep BetterPicks' Discord bot showing as online (green dot + "Watching for locks").

Discord only shows a bot online while it holds a gateway connection, so this runs
forever on the server. Standard library only (no pip). Needs DISCORD_BOT_TOKEN.
It never reads chat messages; picks are posted by discord_post.py. It also answers the /pnl slash
command (Ryan 2026-10-18): wins vs losses by player prop, from the project's graded feed.
/bp <question> (Ryan 2026-10-18, picks channel only): files the question on the repo's betterpicks-ask
branch for the project's BetterPicks thread and posts the thread's answers (betterpicks/bp_answers.json)
back as replies.
"""
import base64, hashlib, json, os, random, re, socket, ssl, struct, sys, threading, time, urllib.error, urllib.request

TOKEN = os.environ.get("DISCORD_BOT_TOKEN") or sys.exit("DISCORD_BOT_TOKEN not set")
STATUS = os.environ.get("BETTERBOT_STATUS", "for locks")
UA = "BetterPicks (1.0)"
API = "https://discord.com/api/v10"
FEED_URL = "https://raw.githubusercontent.com/RY00N/archie-bot/claude/project-thread-8sdhk6/betterpicks/books_feed.json"
WORK = os.environ.get("BP_WORK", "/home/archie/bp")
VERSION = os.path.join(WORK, "presence.version")
CHANNEL = os.environ.get("DISCORD_CHANNEL_ID")
GH_TOKEN = os.environ.get("GITHUB_TOKEN")
GH = "https://api.github.com/repos/RY00N/archie-bot"
ASK_BRANCH, CODE_BRANCH = "betterpicks-ask", "claude/project-thread-8sdhk6"
ASKED, ANSWERED = os.path.join(WORK, "bp_asked.json"), os.path.join(WORK, "bp_answered.json")
state_lock = threading.Lock()


def http(method, url, body=None, auth=True):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "User-Agent": UA, **({"Content-Type": "application/json"} if data else {}),
        **({"Authorization": f"Bot {TOKEN}"} if auth else {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


STAT_WORDS = {"pitcher_ks": "strikeouts", "hrr": "hits + runs + RBIs", "rush_rec_yds": "rushing + receiving yards",
              "rush_yds": "rushing yards", "rec_yds": "receiving yards", "pass_yds": "passing yards", "pitcher_outs": "outs",
              "total_bases": "total bases", "hr": "home runs", "rbi": "RBIs", "pra": "points + rebounds + assists",
              "threes": "3-pointers", "rush_att": "rushing attempts", "batter_ks": "strikeouts", "earned_runs": "earned runs"}


def load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def save(path, data):
    json.dump(data, open(path + ".tmp", "w"), indent=1)
    os.replace(path + ".tmp", path)


def gh(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(GH + path, data=data, method=method, headers={
        "User-Agent": UA, "Accept": "application/vnd.github+json", "Authorization": f"Bearer {GH_TOKEN}",
        **({"Content-Type": "application/json"} if data else {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"null")


def file_question(q):
    """Append a /bp question to questions.json on the betterpicks-ask branch (the project thread reads it)."""
    for _ in range(3):
        try:
            try:
                gh("GET", f"/git/ref/heads/{ASK_BRANCH}")
            except urllib.error.HTTPError:
                sha = gh("GET", "/git/ref/heads/main")["object"]["sha"]
                gh("POST", "/git/refs", {"ref": f"refs/heads/{ASK_BRANCH}", "sha": sha})
            try:
                cur = gh("GET", f"/contents/questions.json?ref={ASK_BRANCH}")
                items, sha = json.loads(base64.b64decode(cur["content"])), cur["sha"]
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
                items, sha = [], None
            items = (items + [{k: q[k] for k in ("id", "user", "text", "at", "auto") if k in q}])[-50:]
            gh("PUT", "/contents/questions.json", {"message": "BetterPicks: /bp question", "branch": ASK_BRANCH,
               "content": base64.b64encode(json.dumps(items, indent=1).encode()).decode(), **({"sha": sha} if sha else {})})
            print("filed /bp question", q["id"], flush=True); return
        except Exception as e:
            print("file question failed:", e, flush=True); time.sleep(5)


def on_bp(d):
    cb = f"{API}/interactions/{d['id']}/{d['token']}/callback"
    if d.get("channel_id") != CHANNEL:
        http("POST", cb, {"type": 4, "data": {"content": "Use /bp in the picks channel.", "flags": 64}}, auth=False); return
    text = str({o["name"]: o.get("value") for o in d["data"].get("options", [])}.get("question") or "").strip()[:1500]
    who = d.get("member", {}).get("user") or d.get("user") or {}
    q = {"id": d["id"], "user": who.get("global_name") or who.get("username") or "someone", "text": text,
         "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    http("POST", cb, {"type": 5}, auth=False)   # "BetterBot is thinking..." right away; Discord needs an ack in 3s
    try:
        quick = quick_answer(text)
    except Exception as e:
        print("quick answer failed:", e, flush=True); quick = None
    if quick:   # answered on the spot; still filed (auto) so the project thread sees what was asked
        q["auto"] = True
        reply = f"**{q['user']} asked BetterPicks:** {text}\n{quick}"
    else:
        reply = (f"**{q['user']} asked BetterPicks:** {text}\n"
                 "Got it, BetterPicks is on it. The answer will post here as a reply.")
    try:
        q["message_id"] = http("PATCH", f"{API}/webhooks/{d['application_id']}/{d['token']}/messages/@original",
                               {"content": reply[:1990], "allowed_mentions": {"parse": []}}, auth=False)["id"]
    except Exception as e:
        print("bp reply failed:", e, flush=True)
    with state_lock:
        save(ASKED, (load(ASKED, []) + [q])[-200:])
    if GH_TOKEN:
        threading.Thread(target=file_question, args=(q,), daemon=True).start()


def bp_safe(d):
    try:
        on_bp(d)
    except Exception as e:
        print("/bp failed:", e, flush=True)


def answers_loop():
    """Every 4 seconds (Ryan 2026-10-21: /bp should take seconds), post new answers from the project thread
    as replies in the picks channel. Authenticated contents API, so no raw-CDN cache delay."""
    while True:
        try:
            req = urllib.request.Request(f"{GH}/contents/betterpicks/bp_answers.json?ref={CODE_BRANCH}", headers={
                "User-Agent": UA, "Accept": "application/vnd.github.raw+json",
                **({"Authorization": f"Bearer {GH_TOKEN}"} if GH_TOKEN else {})})
            items = json.load(urllib.request.urlopen(req, timeout=20))
            with state_lock:
                done, asked = set(load(ANSWERED, [])), {q["id"]: q for q in load(ASKED, [])}
            for a in items:
                if a["id"] in done or not CHANNEL:
                    continue
                q, text, first = asked.get(a["id"], {}), a["answer"].strip(), True
                while text:
                    part, text = text[:1900], text[1900:]
                    body = {"content": part, "allowed_mentions": {"parse": []}}
                    if first and q.get("message_id"):
                        body["message_reference"] = {"message_id": q["message_id"], "fail_if_not_exists": False}
                    http("POST", f"{API}/channels/{CHANNEL}/messages", body)
                    first = False
                with state_lock:
                    save(ANSWERED, sorted(set(load(ANSWERED, [])) | {a["id"]}))
                print("answered /bp", a["id"], flush=True)
            late_notes()
        except urllib.error.HTTPError as e:
            if e.code != 404:
                print("answers check failed:", e, flush=True)
        except Exception as e:
            print("answers check failed:", e, flush=True)
        time.sleep(4 if GH_TOKEN else 30)


def feed():
    """The project's graded feed. With GH_TOKEN it reads the contents API (fresh); raw GitHub is cached for minutes."""
    if GH_TOKEN:
        req = urllib.request.Request(f"{GH}/contents/betterpicks/books_feed.json?ref={CODE_BRANCH}", headers={
            "User-Agent": UA, "Accept": "application/vnd.github.raw+json", "Authorization": f"Bearer {GH_TOKEN}"})
    else:
        req = urllib.request.Request(FEED_URL, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    return json.load(urllib.request.urlopen(req, timeout=10))


def leg_text(x):
    side = "YES" if x["side"] == "over" else "NO"
    mark = {"won": "\u2705 ", "lost": "\u274c "}.get(x.get("result"), "")
    got = f", finished {x['actual']:g}" if isinstance(x.get("actual"), (int, float)) else ""
    return f"{mark}{side} {x['player']} {int(x['line'] + 0.5)}+ {STAT_WORDS.get(x['stat'], x['stat'].replace('_', ' '))}{got}"


# Ryan 2026-10-22: auto-replies sometimes "send some random bullshit that doesn't even answer my question".
# The droplet now answers by itself only when the question plainly asks for one of four things; anything
# else (analysis, advice, "what should I", follow-ups) goes to the project thread for a real answer.
RECORD_RE = re.compile(r"\b(record|pnl|p&l|p and l|profit|bankroll|are we up|are we down|how much (are we|have we|did we))\b")
RESULT_RE = re.compile(r"\b(did\b.*\b(hit|win|cash|lose)|how did\b.*\b(do|go|end)|how'd\b.*\b(do|go)|results?\b|what hit|what (won|lost)|any (wins|hits))")
PICKS_RE = re.compile(r"^(what|whats|what's|wheres|where's|show|list|send|post|give)\b.{0,25}\b(picks?|locks?|plays?|board)\b|^(picks|locks|board)\b|"
                      r"\bwhat (are|is) (the |our |your )?(picks?|locks?|plays?)\b|\bwhat do we have\b|\bwhat'?s open\b")
WHY_WORDS = ("why", "explain", "explanation", "reason", "how come", "break down", "breakdown")
ADVICE_RE = re.compile(r"\b(should|would|could|think|opinion|add|change|tomorrow|make|build|recommend|best|instead|better|fade|tail|rules?)\b")


def why_text(e):
    """One pick's reasoning from the project's own notes (feed board), worded for Discord."""
    out = [f"**{e['type'].split(':')[0]} ({e['date'][5:]})**"]
    for l in e["legs"]:
        out.append(f"{leg_text(l)}" + (f" ({l['game']})" if l.get("game") else ""))
        if isinstance(l.get("p"), (int, float)):
            price = f", Kalshi price {round(l['price'] * 100)}c" if isinstance(l.get("price"), (int, float)) else ""
            edge = f", edge {l['edge']:+.0%}" if isinstance(l.get("edge"), (int, float)) else ""
            out.append(f"  Our win chance {l['p']:.0%}{price}{edge}.")
        for k in ("case", "news"):
            if l.get(k):
                out.append(f"  {l[k]}")
    return "\n".join(out)


def named_picks(t, board):
    return [e for e in board if any(re.search(rf"\b{re.escape(w)}\b", t) for l in e["legs"]
            for w in {l["player"].lower(), l["player"].lower().split()[-1]} if len(w) > 2)]


def quick_answer(text):
    """/bp answered by the droplet itself in seconds (Ryan 2026-10-21), only for questions that plainly ask
    for our record, results, the current picks, or why we made a named pick (Ryan 2026-10-22). Returns None
    for anything else so the project's BetterPicks thread writes a real answer."""
    t = re.sub(r"\s+", " ", text.lower()).strip()
    b = (feed().get("verdicts") or {}).get("board")
    if not t or b is None:
        return None
    if any(w in t for w in WHY_WORDS):
        named = named_picks(t, b)
        if not named:
            m = re.search(r"\blast (\d|two|three|four|five)\b", t)
            if m:
                n = {"two": 2, "three": 3, "four": 4, "five": 5}.get(m.group(1)) or int(m.group(1))
                named = sorted(b, key=lambda e: e["date"])[-n:]
            elif re.search(r"\b(today|tonight|these|those|current|open)('?s)?\b.{0,20}\b(picks?|locks?|plays?)\b|\b(picks?|locks?) (for )?(today|tonight)\b", t):
                named = [e for e in b if e["status"] == "open"]
        if named and len(t.split()) <= 20 and not ADVICE_RE.search(t):
            return "\n\n".join(why_text(e) for e in named)
        return None
    if ADVICE_RE.search(t) or len(t.split()) > 12:
        return None
    if RECORD_RE.search(t):
        return pnl_text()
    if RESULT_RE.search(t):
        named = named_picks(t, b)
        if named:
            return "\n".join(result_text(e) for e in named)
        return results_text(b)
    if PICKS_RE.search(t):
        return open_text(b)
    return None


def result_text(e):
    word = {"won": "HIT \u2705", "lost": "LOST \u274c", "void": "VOID (stake back)", "open": "still open"}.get(e["status"], e["status"])
    money = f", {'+' if e['pnl'] >= 0 else '-'}${abs(e['pnl']):.2f}" if e["status"] in ("won", "lost") and isinstance(e.get("pnl"), (int, float)) else ""
    return f"**{e['type'].split(':')[0]} ({e['date'][5:]}): {word}{money}**\n" + "\n".join(f"  {leg_text(l)}" for l in e["legs"])


def results_text(b):
    yday = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 31 * 3600))
    settled = [e for e in b if e["status"] in ("won", "lost", "void") and max((l.get("start") or e["date"]) for l in e["legs"])[:10] >= yday]
    if not settled:
        return f"Nothing has settled since {yday[5:]}.\n" + open_text(b)
    return "**Latest results:**\n" + "\n".join(result_text(e) for e in settled)


def open_text(b):
    live = [e for e in b if e["status"] == "open"]
    if not live:
        return "No open picks right now. New ones post here as soon as we find them."
    return "**Open picks:**\n" + "\n".join(f"{e['type'].split(':')[0]} ({e['date'][5:]}): " + ", ".join(
        f"{leg_text(l)} ({l['game']})" for l in e["legs"]) for e in live)


def late_notes():
    """If the project thread hasn't answered a /bp question in 10 minutes, say so once from the droplet."""
    if not CHANNEL:
        return
    cut = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))
    with state_lock:
        done, asked = set(load(ANSWERED, [])), load(ASKED, [])
    for q in asked:
        if q.get("auto") or q["id"] in done or f"late:{q['id']}" in done or q["at"] > cut or q["at"] < "2026-10-21T22:00":
            continue
        body = {"content": "Still working on this one. For instant answers, ask /bp about results, tonight's picks, "
                "our record, or why we made a pick (name the player).",
                "allowed_mentions": {"parse": []}}
        if q.get("message_id"):
            body["message_reference"] = {"message_id": q["message_id"], "fail_if_not_exists": False}
        try:
            http("POST", f"{API}/channels/{CHANNEL}/messages", body)
        except Exception as e:
            print("late note failed:", e, flush=True); continue
        with state_lock:
            save(ANSWERED, sorted(set(load(ANSWERED, [])) | {f"late:{q['id']}"}))


def pnl_text():
    """Ryan 2026-10-22: the record is the current rules' record (record.py on the project side), pick by pick."""
    rec = (feed().get("verdicts") or {}).get("record") or {}
    if "won" not in rec:
        return "Couldn't load the record right now, try again in a minute."
    w, l = rec["won"], rec["lost"]
    sign = "-" if rec["pnl"] < 0 else "+"
    lines = [f"**BetterPicks record: {w}-{l}** ({sign}${abs(rec['pnl']):.2f})" + (f", {rec['open']} open" if rec.get("open") else "")]
    for e in reversed(rec.get("recent") or []):
        mark = {"won": "\u2705", "lost": "\u274c"}.get(e["status"], "")
        lines.append(f"{mark} {e['date'][5:]}: " + ", ".join(leg_text(dict(x, result=None)) for x in e["legs"]))
    return "\n".join(lines)[:1990]


# GUILD_MEMBERS (2) for the OG giveaway, GUILD_MESSAGES (512) + MESSAGE_CONTENT (32768) for the image-only
# WINNERS channel. On close 4014 it steps down a level, so the bot stays online; the image filter only runs
# with MESSAGE_CONTENT, since without it every attachment list arrives empty and real images would be deleted.
INTENT_LEVELS = [2 | 512 | 32768, 2, 0]
INTENTS = [INTENT_LEVELS[0]]
WINNERS = os.environ.get("BP_WINNERS_CHANNEL_ID", "1509697436480700537")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp")


def winners_filter(d):
    """Ryan 2026-10-23: WINNERS is image only; a message without an image gets deleted."""
    if d.get("channel_id") != WINNERS or (d.get("author") or {}).get("bot") or d.get("type") not in (0, 19):
        return
    for a in d.get("attachments") or []:
        if (a.get("content_type") or "").startswith("image/") or a.get("filename", "").lower().endswith(IMAGE_EXT):
            return
    try:
        http("DELETE", f"{API}/channels/{WINNERS}/messages/{d['id']}")
        print("WINNERS: deleted a text-only message from", (d.get("author") or {}).get("username"), flush=True)
    except Exception as e:
        print("WINNERS delete failed:", e, flush=True)
OG_ROLE, OG_MAX = os.environ.get("BP_OG_ROLE_ID", "1563068490162642974"), 15
OG_GIVEN = os.path.join(WORK, "og_given.json")


def og_on_join(d):
    """Ryan 2026-10-23: the next 15 people who join get the OG Premium role (free access), then no more.
    Counted by people given it, so someone leaving doesn't open a new spot."""
    u = d.get("user") or {}
    if u.get("bot") or not d.get("guild_id"):
        return
    with state_lock:
        given = load(OG_GIVEN, [])
        if len(given) >= OG_MAX or u["id"] in given:
            return
        given.append(u["id"]); save(OG_GIVEN, given)
    try:
        http("PUT", f"{API}/guilds/{d['guild_id']}/members/{u['id']}/roles/{OG_ROLE}", {})
        print(f"OG Premium {len(given)}/{OG_MAX}:", u.get("username"), flush=True)
    except Exception as e:
        print("OG role failed:", e, flush=True)
        with state_lock:
            save(OG_GIVEN, [x for x in load(OG_GIVEN, []) if x != u["id"]])
    STATUS_INFO["og_given"] = len(load(OG_GIVEN, []))


# Ryan 2026-10-26: people joining through a special invite link get a role (old-server members -> OG Premium, free).
# Discord doesn't say which invite someone used, so we keep each invite's use count and see which one went up.
INVITE_ROLES = {"857TCcDMj": OG_ROLE}   # invite code -> role id ("BetterPicks (old)" members, free OG Premium)
INVITE_USES, invite_lock = {}, threading.Lock()


def invite_snapshot():
    return {i["code"]: i.get("uses", 0) for i in http("GET", f"{API}/guilds/{GUILD}/invites")}


def invite_role_on_join(d):
    """True when the member came in through one of INVITE_ROLES (then the OG giveaway leaves them alone)."""
    u = d.get("user") or {}
    if u.get("bot") or not INVITE_ROLES:
        return False
    with invite_lock:
        before = dict(INVITE_USES)
        try:
            now = invite_snapshot()
        except Exception as e:
            print("invite check failed:", e, flush=True)
            return False
        INVITE_USES.clear(); INVITE_USES.update(now)
        STATUS_INFO["invite_roles"] = {c: now.get(c) for c in INVITE_ROLES}
    # a single-use invite disappears once used, so a vanished code counts as the one used
    used = [c for c in INVITE_ROLES if c in before and now.get(c, before[c] + 1) > before[c]]
    if not used:
        return False
    try:
        http("PUT", f"{API}/guilds/{d['guild_id']}/members/{u['id']}/roles/{INVITE_ROLES[used[0]]}", {})
        print(f"invite {used[0]} role given:", u.get("username"), flush=True)
    except Exception as e:
        print("invite role failed:", e, flush=True)
    return True


def on_join(d):
    if not invite_role_on_join(d):
        og_on_join(d)


def invite_prime():
    with invite_lock:
        try:
            INVITE_USES.update(invite_snapshot())
            STATUS_INFO["invite_roles"] = {c: INVITE_USES.get(c) for c in INVITE_ROLES}
        except Exception as e:
            print("invite snapshot failed:", e, flush=True)


GUILD = os.environ.get("BP_GUILD_ID", "1509315900874948729")
# Ryan 2026-10-23: three counter voice channels at the top of the server. Members updates when someone joins
# or leaves, Wins/Losses when a pick settles (the record in the feed changes; checked every few minutes).
# Discord allows only 2 renames per channel per 10 minutes, so a change that lands sooner waits its turn.
COUNTERS = {"members": (os.environ.get("BP_MEMBERS_CH", "1563072752607436900"), "\U0001F464Members: {}"),
            "wins": (os.environ.get("BP_WINS_CH", "1563072753773318224"), "\U0001F4B0Wins: {}"),
            "losses": (os.environ.get("BP_LOSSES_CH", "1563072754536677388"), "\u274cLosses: {}")}
MEMBERS_CHANGED = threading.Event()


def human_count():
    n, after = 0, "0"
    while True:
        page = http("GET", f"{API}/guilds/{GUILD}/members?limit=1000&after={after}")
        n += sum(not m["user"].get("bot") for m in page)
        if len(page) < 1000:
            return n
        after = page[-1]["user"]["id"]


def counters_loop():
    want, shown, renamed, last_rec = {}, {}, {}, 0
    MEMBERS_CHANGED.set()   # count once at start
    while True:
        if MEMBERS_CHANGED.is_set():
            MEMBERS_CHANGED.clear()
            try:
                want["members"] = human_count()
            except Exception as e:
                print("member count failed:", e, flush=True); MEMBERS_CHANGED.set()
        if time.time() - last_rec >= 180:
            last_rec = time.time()
            try:
                rec = (feed().get("verdicts") or {}).get("record") or {}
            except Exception as e:
                rec = {}; print("record fetch failed:", e, flush=True)
            if "won" in rec:
                want["wins"], want["losses"] = rec["won"], rec["lost"]
        for k, n in want.items():
            ch, fmt = COUNTERS[k]
            if shown.get(k) == n or time.time() - renamed.get(k, 0) < 300:
                continue
            try:
                if shown.get(k) is None and http("GET", f"{API}/channels/{ch}")["name"] == fmt.format(n):
                    shown[k] = n; continue
                http("PATCH", f"{API}/channels/{ch}", {"name": fmt.format(n)})
                shown[k], renamed[k] = n, time.time()
            except Exception as e:
                print(f"{k} counter failed:", e, flush=True)
        MEMBERS_CHANGED.wait(20)


# Ryan 2026-10-23: Whop buyers get Premium. Whop's own Discord app couldn't be mapped, so BetterBot does it:
# /verify <email> links a valid BetterPicks VIP membership to the Discord account (one account per membership),
# and every 10 minutes it rechecks the links and removes Premium once a membership stops being valid.
# Memberships that already carry a Discord id get Premium without /verify. OG Premium is never touched.
WHOP_KEY = os.environ.get("WHOP_BP_API")
WHOP_PRODUCT = "prod_M44sTTPJ3OFlS"
PREMIUM_ROLE = os.environ.get("BP_PREMIUM_ROLE_ID", "1563068488195772517")
WHOP_LINKS = os.path.join(WORK, "whop_links.json")   # {discord_id: membership_id}


WHOP_COMPANY = "biz_QKCBbSx5I55xKm"


def whop_get(path):
    req = urllib.request.Request(f"https://api.whop.com/api/v1{path}", headers={"Authorization": f"Bearer {WHOP_KEY}", "User-Agent": UA})
    return json.load(urllib.request.urlopen(req, timeout=20))   # ignore its "recommended_action" upsell text


def whop_memberships():
    out, after = [], None
    while True:
        doc = whop_get(f"/memberships?company_id={WHOP_COMPANY}&product_ids={WHOP_PRODUCT}"
                       + (f"&after={after}" if after else ""))
        out += [m for m in doc.get("data") or [] if (m.get("product") or {}).get("id", WHOP_PRODUCT) == WHOP_PRODUCT]
        pi = doc.get("page_info") or {}
        if not pi.get("has_next_page") or not pi.get("end_cursor"):
            return out
        after = pi["end_cursor"]


def whop_membership(mid):
    """One membership, for a link missing from the list; None only when Whop says it doesn't exist."""
    try:
        return whop_get(f"/memberships/{mid}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def m_email(m):
    return ((m.get("user") or {}).get("email") or "").strip().lower()


def m_discord(m):
    d = (m.get("user") or {}).get("discord") or m.get("discord") or {}
    return str(d.get("id")) if isinstance(d, dict) and d.get("id") else None


def m_valid(m):
    """True = has Premium, False = take it away, None = leave as is (unresolved / drafted)."""
    st = m.get("status")
    if st in ("trialing", "active", "completed", "canceling", "past_due"):   # past_due: Whop is still retrying
        return True
    if st in ("canceled", "expired"):
        return False
    return None


def set_premium(uid, on):
    http("PUT" if on else "DELETE", f"{API}/guilds/{GUILD}/members/{uid}/roles/{PREMIUM_ROLE}", {} if on else None)


def on_verify(d):
    uid = ((d.get("member") or {}).get("user") or d.get("user") or {}).get("id")
    email = next((o["value"] for o in d["data"].get("options", []) if o["name"] == "email"), "").strip().lower()
    reply = lambda text: http("PATCH", f"{API}/webhooks/{d['application_id']}/{d['token']}/messages/@original",
                              {"content": text, "allowed_mentions": {"parse": []}}, auth=False)
    http("POST", f"{API}/interactions/{d['id']}/{d['token']}/callback", {"type": 5, "data": {"flags": 64}}, auth=False)
    if not WHOP_KEY:
        return reply("Verification is down for a moment, try again later.")
    try:
        allm = whop_memberships()
        ms = [m for m in allm if m_email(m) == email and m_valid(m)]
    except Exception as e:
        print("/verify Whop lookup failed:", e, flush=True)
        return reply("Couldn't reach Whop right now, try again in a minute.")
    with state_lock:
        links = load(WHOP_LINKS, {})
        taken = {mid for k, mid in links.items() if k != uid}
        m = next((m for m in ms if m["id"] not in taken), None)
        if m:
            links[uid] = m["id"]; save(WHOP_LINKS, links)
    if not ms and allm and not any(m_email(m) for m in allm):   # key can't read emails
        print("/verify: Whop returned no member emails", flush=True)
        return reply("Couldn't verify right now. Ping an admin and they'll add your role.")
    if not ms:
        return reply("No active BetterPicks VIP membership found for that email. Use the email you bought with on Whop.")
    if not m:
        return reply("That membership is already linked to another Discord account. Open a support ticket if that's wrong.")
    try:
        set_premium(uid, True)
    except Exception as e:
        print("/verify role failed:", e, flush=True)
        return reply("Found your membership but couldn't add the role. Open a support ticket.")
    print("Premium verified:", uid, m["id"], flush=True)
    reply("\u2705 Verified! You have Premium now. The picks channel is open to you.")


def verify_safe(d):
    try:
        on_verify(d)
    except Exception as e:
        print("/verify failed:", e, flush=True)


def whop_loop():
    while WHOP_KEY:
        try:
            ms = {m["id"]: m for m in whop_memberships()}
            with state_lock:
                links = load(WHOP_LINKS, {})
                linked = set(links.values())
                for m in ms.values():   # Whop already knows their Discord account
                    did = m_discord(m)
                    if did and m_valid(m) is True and m["id"] not in linked and did not in links:
                        links[did] = m["id"]; linked.add(m["id"])
                save(WHOP_LINKS, links)
            for uid, mid in list(links.items()):
                m = ms.get(mid) or whop_membership(mid)
                ok = m_valid(m) if m else False
                if ok is None:
                    continue
                try:
                    set_premium(uid, ok)
                except urllib.error.HTTPError as e:
                    if e.code != 404:   # 404 = they left the server
                        print("Premium sync failed:", uid, e, flush=True)
                if not ok:
                    print("Premium removed (membership ended):", uid, mid, flush=True)
                    with state_lock:
                        cur = load(WHOP_LINKS, {}); cur.pop(uid, None); save(WHOP_LINKS, cur)
            STATUS_INFO["whop_linked"] = len(load(WHOP_LINKS, {}))
        except Exception as e:
            print("Whop sync failed:", e, flush=True)
        time.sleep(600)


PURCHASE_CH = os.environ.get("BP_PURCHASE_CHANNEL_ID", "1509754956666634382")


def purchase_ping(d):
    """Ryan 2026-10-23: @ each new member in Purchase, then delete it, so the channel lights up for them."""
    u = d.get("user") or {}
    if u.get("bot"):
        return
    try:
        msg = http("POST", f"{API}/channels/{PURCHASE_CH}/messages",
                   {"content": f"<@{u['id']}>", "allowed_mentions": {"users": [u["id"]]}})   # deleted right away
        http("DELETE", f"{API}/channels/{PURCHASE_CH}/messages/{msg['id']}")
    except Exception as e:
        print("purchase ping failed:", e, flush=True)


STATUS_INFO = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "reconnects": 0}
READY_SEEN = [False]


def free_file_loop():
    """Every 2 min, push the day's free pick (written by discord_post.py) to free_pick_today.json on the ask
    branch, so the social thread can put it in the morning Reel (Ryan 2026-10-23)."""
    path, last = os.path.join(WORK, "free_pick_today.json"), None
    while True:
        time.sleep(120)
        try:
            if not os.path.exists(path):
                continue
            raw = open(path, "rb").read()
            if raw == last:
                continue
            try:
                sha = gh("GET", f"/contents/free_pick_today.json?ref={ASK_BRANCH}")["sha"]
            except urllib.error.HTTPError:
                sha = None
            gh("PUT", "/contents/free_pick_today.json", {"message": "BetterPicks free pick", "branch": ASK_BRANCH,
               "content": base64.b64encode(raw).decode(), **({"sha": sha} if sha else {})})
            last = raw
        except Exception as e:
            print("free pick push failed:", e, flush=True)


def status_loop():
    """Every 15 min, put the bot's health in bot_status.json on the ask branch, so the project thread
    can tell from GitHub whether BetterBot is connected (it can't see the droplet's logs)."""
    while True:
        time.sleep(60 if "pushed" not in STATUS_INFO else 900)
        try:
            body = dict(STATUS_INFO, at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            ws_state = os.path.join(WORK, ".whop_wins_state")   # written by whop_wins.py after each Whop page update
            body["whop_key"] = bool(os.environ.get("WHOP_BP_API"))
            try:
                import PIL  # noqa: F401  (whop_gallery.py needs Pillow on the droplet)
                body["pillow"] = True
            except ImportError:
                body["pillow"] = False
            for k, f in (("whop_gallery_updated", ".whop_gallery_state"),):
                if os.path.exists(os.path.join(WORK, f)):
                    body[k] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(os.path.join(WORK, f))))
            if os.path.exists(ws_state):
                body["whop_page_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(ws_state)))
            body.pop("pushed", None)
            try:
                sha = gh("GET", f"/contents/bot_status.json?ref={ASK_BRANCH}")["sha"]
            except urllib.error.HTTPError:
                sha = None
            gh("PUT", "/contents/bot_status.json", {"message": "BetterBot status", "branch": ASK_BRANCH,
               "content": base64.b64encode(json.dumps(body, indent=1).encode()).decode(), **({"sha": sha} if sha else {})})
            STATUS_INFO["pushed"] = True
        except Exception as e:
            print("status push failed:", e, flush=True)


def gateway_host():
    req = urllib.request.Request("https://discord.com/api/v10/gateway/bot",
                                 headers={"Authorization": f"Bot {TOKEN}", "User-Agent": UA})
    url = json.load(urllib.request.urlopen(req, timeout=20))["url"]
    return url.replace("wss://", "").strip("/")


class WS:
    """Minimal websocket client: text frames, ping/pong, close."""

    def __init__(self, host):
        raw = socket.create_connection((host, 443), timeout=30)
        self.s = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET /?v=10&encoding=json HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                        f"User-Agent: {UA}\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.s.recv(4096)
            if not chunk:
                raise ConnectionError("handshake closed")
            head += chunk
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ConnectionError(head.split(b"\r\n")[0].decode())
        self.buf = head.split(b"\r\n\r\n", 1)[1]
        self.lock = threading.Lock()
        self.s.settimeout(None)

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise ConnectionError("socket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send(self, op, data):
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x80 | op])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self.lock:
            self.s.sendall(head + mask + body)

    def send(self, obj):
        self._send(0x1, json.dumps(obj).encode())

    def recv(self):
        msg = b""
        while True:
            b1, b2 = self._read(2)
            op, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            data = self._read(n)
            if op == 0x8:
                code = struct.unpack(">H", data[:2])[0] if len(data) >= 2 else 0
                raise ConnectionError(f"closed by Discord ({code})")
            if op == 0x9:
                self._send(0xA, data); continue
            if op == 0xA:
                continue
            msg += data
            if b1 & 0x80:
                return json.loads(msg)


def run_once():
    ws = WS(gateway_host())
    hello = ws.recv()
    interval = hello["d"]["heartbeat_interval"] / 1000
    seq = [None]
    alive = [True]

    acked = [True]

    def beat():
        # A silently dead connection blocked recv forever and /bp stopped working (Ryan 2026-10-22).
        # No heartbeat ACK since the last beat, or a failed send, closes the socket so we reconnect.
        time.sleep(interval * random.random())
        while alive[0]:
            if not acked[0]:
                print("no heartbeat ack; reconnecting", flush=True); break
            acked[0] = False
            try:
                ws.send({"op": 1, "d": seq[0]})
            except Exception as e:
                print("heartbeat send failed:", e, flush=True); break
            time.sleep(interval)
        try:
            ws.s.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass

    ws.s.settimeout(interval * 2 + 15)   # recv raises instead of hanging if Discord goes quiet
    threading.Thread(target=beat, daemon=True).start()
    ws.send({"op": 2, "d": {
        "token": TOKEN, "intents": INTENTS[0],   # see INTENT_LEVELS
        "properties": {"os": "linux", "browser": "betterpicks", "device": "betterpicks"},
        "presence": {"status": "online", "afk": False, "since": None,
                     "activities": [{"name": STATUS, "type": 3}]}}})   # type 3 = "Watching ..."
    try:
        while True:
            m = ws.recv()
            STATUS_INFO["last_event"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if m.get("op") == 11:
                acked[0] = True
            if m.get("s") is not None:
                seq[0] = m["s"]
            if m.get("t") == "READY":
                threading.Thread(target=invite_prime, daemon=True).start()
                print("online as", m["d"]["user"]["username"], flush=True)
                STATUS_INFO["online_since"] = STATUS_INFO["last_event"]; READY_SEEN[0] = True
                for cmd in ({"name": "pnl", "description": "BetterPicks record and recent results", "type": 1},
                            {"name": "bp", "description": "Ask BetterPicks a question", "type": 1, "options": [
                                {"type": 3, "name": "question", "description": "What do you want to ask?", "required": True}]},
                            {"name": "verify", "description": "Get Premium with the email you used on Whop", "type": 1, "options": [
                                {"type": 3, "name": "email", "description": "The email on your Whop purchase", "required": True}]}):
                    try:   # register (or refresh) the slash commands
                        http("POST", f"{API}/applications/{m['d']['application']['id']}/commands", cmd)
                    except Exception as e:
                        print("could not register /" + cmd["name"], e, flush=True)
            if m.get("t") == "MESSAGE_CREATE" and INTENTS[0] & 32768:
                threading.Thread(target=winners_filter, args=(m["d"],), daemon=True).start()
            if m.get("t") in ("GUILD_MEMBER_ADD", "GUILD_MEMBER_REMOVE"):
                MEMBERS_CHANGED.set()
            if m.get("t") == "GUILD_MEMBER_ADD":
                threading.Thread(target=purchase_ping, args=(m["d"],), daemon=True).start()
                threading.Thread(target=on_join, args=(m["d"],), daemon=True).start()
            if m.get("t") == "INTERACTION_CREATE" and m["d"].get("type") == 2 and m["d"]["data"].get("name") == "verify":
                threading.Thread(target=verify_safe, args=(m["d"],), daemon=True).start()
            if m.get("t") == "INTERACTION_CREATE" and m["d"].get("type") == 2 and m["d"]["data"].get("name") == "bp":
                threading.Thread(target=bp_safe, args=(m["d"],), daemon=True).start()
            if m.get("t") == "INTERACTION_CREATE" and m["d"].get("type") == 2 and m["d"]["data"].get("name") == "pnl":
                d = m["d"]
                try:
                    text = pnl_text()
                except Exception as e:
                    text = "Couldn't load the record right now, try again in a minute."; print("/pnl failed:", e, flush=True)
                try:
                    http("POST", f"{API}/interactions/{d['id']}/{d['token']}/callback",
                         {"type": 4, "data": {"content": text, "allowed_mentions": {"parse": []}}}, auth=False)
                except Exception as e:
                    print("/pnl reply failed:", e, flush=True)
            if m.get("op") in (7, 9):   # reconnect / invalid session
                raise ConnectionError(f"gateway op {m['op']}")
            if m.get("op") == 1:
                ws.send({"op": 1, "d": seq[0]})
    finally:
        alive[0] = False
        try:
            ws.s.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:   # lets discord_post.py notice when the repo has newer code and restart this service
        os.makedirs(os.path.dirname(VERSION), exist_ok=True)
        open(VERSION, "w").write(hashlib.sha1(open(os.path.abspath(__file__), "rb").read()).hexdigest())
    except Exception as e:
        print("could not write version:", e, flush=True)
    threading.Thread(target=answers_loop, daemon=True).start()
    threading.Thread(target=counters_loop, daemon=True).start()
    threading.Thread(target=whop_loop, daemon=True).start()
    if GH_TOKEN:
        threading.Thread(target=status_loop, daemon=True).start()
        threading.Thread(target=free_file_loop, daemon=True).start()
    wait = 5
    while True:
        READY_SEEN[0] = False
        try:
            run_once()
        except Exception as e:
            print("disconnected:", e, flush=True)
            STATUS_INFO["reconnects"] += 1; STATUS_INFO["last_error"] = str(e)[:200]
            if "4014" in str(e) and INTENTS[0]:
                INTENTS[0] = INTENT_LEVELS[INTENT_LEVELS.index(INTENTS[0]) + 1]
                print("intent refused; stepping down to", INTENTS[0], flush=True)
            if "4004" in str(e):
                sys.exit("bad bot token (4004); fix DISCORD_BOT_TOKEN")
        wait = 5 if READY_SEEN[0] else min(wait * 2, 300)
        time.sleep(wait)
