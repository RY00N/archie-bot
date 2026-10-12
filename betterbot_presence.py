#!/usr/bin/env python3
"""Keep BetterPicks' Discord bot showing as online (green dot + "Watching for locks").

Discord only shows a bot online while it holds a gateway connection, so this runs
forever on the server. Standard library only (no pip). Needs DISCORD_BOT_TOKEN.
It never reads or sends messages; posting is done by discord_post.py.
"""
import base64, json, os, random, socket, ssl, struct, sys, threading, time, urllib.request

TOKEN = os.environ.get("DISCORD_BOT_TOKEN") or sys.exit("DISCORD_BOT_TOKEN not set")
STATUS = os.environ.get("BETTERBOT_STATUS", "for locks")
UA = "BetterPicks (1.0)"


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
