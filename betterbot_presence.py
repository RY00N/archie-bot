#!/usr/bin/env python3
"""Keep BetterPicks' Discord bot showing as online (green dot + "Watching for locks").

Discord only shows a bot online while it holds a gateway connection, so this runs
forever on the server. Standard library only (no pip). Needs DISCORD_BOT_TOKEN.
It never reads chat messages; picks are posted by discord_post.py. It also answers the /pnl slash
command (Ryan 2026-10-18): wins vs losses by player prop, from the project's graded feed.
"""
import base64, hashlib, json, os, random, socket, ssl, struct, sys, threading, time, urllib.request

TOKEN = os.environ.get("DISCORD_BOT_TOKEN") or sys.exit("DISCORD_BOT_TOKEN not set")
STATUS = os.environ.get("BETTERBOT_STATUS", "for locks")
UA = "BetterPicks (1.0)"
API = "https://discord.com/api/v10"
FEED_URL = "https://raw.githubusercontent.com/RY00N/archie-bot/claude/project-thread-8sdhk6/betterpicks/books_feed.json"
VERSION = os.path.join(os.environ.get("BP_WORK", "/home/archie/bp"), "presence.version")


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


def pnl_text():
    """Practice record by player prop (each leg), graded from Kalshi's own settlement."""
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    pr = (json.load(urllib.request.urlopen(req, timeout=15)).get("verdicts") or {}).get("props")
    if not pr:
        return "No graded props yet. (BetterPicks practice record)"
    w, l = pr["won"], pr["lost"]
    lines = [f"**BetterPicks prop record: {w}-{l}**" + (f" ({w / (w + l):.0%} hit rate)" if w + l else "")]
    lines += [f"{lg}: {a}-{b}" for lg, (a, b) in sorted(pr.get("by_league", {}).items())]
    lines.append(f"Parlay P&L: ${pr.get('parlay_pnl', 0):+.2f} practice")
    rec = pr.get("recent") or []
    if rec:
        lines.append("Last props:")
        for x in reversed(rec):
            mark = "\u2705" if x["result"] == "won" else "\u274c"
            side = "YES" if x["side"] == "over" else "NO"
            got = f", finished {x['actual']:g}" if isinstance(x.get("actual"), (int, float)) else ""
            lines.append(f"{mark} {side} {x['player']} {int(x['line'] + 0.5)}+ {STAT_WORDS.get(x['stat'], x['stat'].replace('_', ' '))} ({x['date'][5:]}){got}")
    lines.append("Practice picks, not real bets.")
    return "\n".join(lines)[:1990]


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

    def beat():
        time.sleep(interval * random.random())
        while alive[0]:
            ws.send({"op": 1, "d": seq[0]})
            time.sleep(interval)

    threading.Thread(target=beat, daemon=True).start()
    ws.send({"op": 2, "d": {
        "token": TOKEN, "intents": 0,
        "properties": {"os": "linux", "browser": "betterpicks", "device": "betterpicks"},
        "presence": {"status": "online", "afk": False, "since": None,
                     "activities": [{"name": STATUS, "type": 3}]}}})   # type 3 = "Watching ..."
    try:
        while True:
            m = ws.recv()
            if m.get("s") is not None:
                seq[0] = m["s"]
            if m.get("t") == "READY":
                print("online as", m["d"]["user"]["username"], flush=True)
                try:   # register (or refresh) the /pnl slash command
                    http("POST", f"{API}/applications/{m['d']['application']['id']}/commands",
                         {"name": "pnl", "description": "BetterPicks wins vs losses by player prop (practice)", "type": 1})
                except Exception as e:
                    print("could not register /pnl:", e, flush=True)
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
    wait = 5
    while True:
        try:
            run_once()
        except Exception as e:
            print("disconnected:", e, flush=True)
            if "4004" in str(e):
                sys.exit("bad bot token (4004); fix DISCORD_BOT_TOKEN")
        time.sleep(wait)
        wait = min(wait * 2, 300)
