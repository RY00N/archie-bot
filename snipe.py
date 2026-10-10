"""Memecoin SNIPER on practice money (Ryan 2026-10-10: "how can I make you a sniper", "find the next 50-100x").
Live PumpPortal stream: every pump.fun launch, then every buy and sell of each new coin (per-trade data needs
PUMPPORTAL_API_KEY, a key funded with 0.02 SOL; data only, it never trades). Paper-buys coins whose first minutes
look organic (many different buyers, buys beating sells, dev not selling, not bundled at launch), cuts losers fast,
sells half at 2x and lets the rest ride with a trailing stop. No wallet, no orders: state goes to snipe_state.json."""
import base64, json, os, socket, ssl, struct, sys, threading, time, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "snipe_state.json")
LOG = os.path.join(HERE, "snipe-log.jsonl")
R = {
    "start": 1000.0, "size": 25.0, "max_open": 5, "max_trades_day": 60,
    "watch_s": 180,                 # follow each launch's trades for 3 minutes before giving up on it
    "entry_after_s": 45,            # earliest entry (let the launch snipers and bundles show themselves)
    "min_buyers": 12, "min_buy_sol": 3.0, "buy_sell_ratio": 2.0,
    "max_dev_buy_sol": 2.0,         # a huge dev buy usually means a dump
    "max_launch_buys": 5,           # buys in the first 2 s = bundled wallets: skip
    "tp": 1.0, "sl": -0.30, "max_hold_min": 10, "stale_gain": 0.20, "trail": 0.40,
    "cost_side": 0.04,              # 1% fee + 3% slippage/priority each way
    "max_subs": 120,
}
LOOP_NAME = "snipe-2"


def _now():
    return datetime.now(timezone.utc).isoformat()[:19] + "Z"


def api_key():
    k = os.environ.get("PUMPPORTAL_API_KEY", "")
    if not k:  # the server loads its env at start; read the file so a key added later works without a restart
        try:
            for line in open("/etc/archie.env"):
                if line.startswith("PUMPPORTAL_API_KEY="):
                    k = line.split("=", 1)[1].strip().strip('"')
        except Exception:
            pass
    return k


class WS:
    def __init__(s, path):
        host = "pumpportal.fun"
        raw = socket.create_connection((host, 443), timeout=60)
        s.s = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        s.s.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: Mozilla/5.0\r\n\r\n").encode())
        h = b""
        while b"\r\n\r\n" not in h:
            c = s.s.recv(1)
            if not c:
                raise ConnectionError("closed during handshake")
            h += c
        if b" 101 " not in h.split(b"\r\n")[0]:
            raise ConnectionError(h.split(b"\r\n")[0].decode())

    def _rx(s, n):
        b = b""
        while len(b) < n:
            c = s.s.recv(n - len(b))
            if not c:
                raise ConnectionError("closed")
            b += c
        return b

    def _frame(s, op, data):
        m, n = os.urandom(4), len(data)
        ln = bytes([0x80 | n]) if n < 126 else (bytes([0x80 | 126]) + struct.pack(">H", n))
        s.s.sendall(bytes([0x80 | op]) + ln + m + bytes(b ^ m[i % 4] for i, b in enumerate(data)))

    def send(s, obj):
        s._frame(1, json.dumps(obj).encode())

    def recv(s):
        while True:
            h = s._rx(2)
            n = h[1] & 127
            if n == 126:
                n = struct.unpack(">H", s._rx(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", s._rx(8))[0]
            d, op = s._rx(n), h[0] & 15
            if op == 1:
                return json.loads(d)
            if op == 9:
                s._frame(10, d)
            if op == 8:
                raise ConnectionError("server closed")


def load():
    st = {"start": R["start"], "cash": R["start"], "positions": [], "trades": [], "last_error": "", "mode": "",
          "launches_seen": 0, "checked": 0, "skips": {}}
    try:
        st.update(json.load(open(STATE)))
    except Exception:
        pass
    return st


def save(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE)


def _log(event, row):
    with open(LOG, "a") as f:
        f.write(json.dumps({"t": _now(), "event": event, **row}) + "\n")


class Sniper:
    def __init__(s):
        s.st = load()
        s.tokens = {}    # mint -> launch stats while we watch it
        s.sol_usd = 0.0
        s.sol_t = 0

    def sol(s):
        if time.time() - s.sol_t > 300:
            try:
                req = urllib.request.Request("https://frontend-api-v3.pump.fun/sol-price", headers={"User-Agent": "Mozilla/5.0"})
                s.sol_usd = float(json.loads(urllib.request.urlopen(req, timeout=10).read())["solPrice"])
                s.sol_t = time.time()
            except Exception:
                s.sol_t = time.time() - 240  # retry in a minute
        return s.sol_usd or 150.0

    # --- launch tracking -------------------------------------------------------------------------------------
    def on_create(s, m, ws):
        s.st["launches_seen"] += 1
        s.tokens[m["mint"]] = {"sym": (m.get("symbol") or "?")[:12], "t0": time.time(), "dev": m.get("traderPublicKey"),
                               "dev_buy": m.get("solAmount", 0), "mc0": m.get("marketCapSol", 0),
                               "mc": m.get("marketCapSol", 0), "buyers": set(), "buy_sol": 0.0, "sell_sol": 0.0,
                               "launch_buys": 0, "dev_sold": False, "done": False}
        if len(s.tokens) <= R["max_subs"]:
            ws.send({"method": "subscribeTokenTrade", "keys": [m["mint"]]})

    def on_trade(s, m, ws):
        mint = m.get("mint")
        t = s.tokens.get(mint)
        mc = m.get("marketCapSol") or 0
        sol_amt = m.get("solAmount") or 0
        if t:
            t["mc"] = mc or t["mc"]
            if m["txType"] == "buy":
                t["buyers"].add(m.get("traderPublicKey"))
                t["buy_sol"] += sol_amt
                if time.time() - t["t0"] < 2:
                    t["launch_buys"] += 1
            else:
                t["sell_sol"] += sol_amt
                if m.get("traderPublicKey") == t["dev"]:
                    t["dev_sold"] = True
        for pos in [p for p in s.st["positions"] if p["mint"] == mint]:
            if m["txType"] == "sell" and m.get("traderPublicKey") == pos.get("dev"):
                s.close(pos, mc, "dev sold")
            elif mc:
                s.manage(pos, mc)
        if t and not t["done"]:
            s.maybe_enter(mint, t, ws)

    def maybe_enter(s, mint, t, ws):
        age = time.time() - t["t0"]
        if age < R["entry_after_s"]:
            return
        why = None
        if t["dev_sold"]:
            why = "dev sold"
        elif t["launch_buys"] > R["max_launch_buys"]:
            why = "bundled launch"
        elif t["dev_buy"] > R["max_dev_buy_sol"]:
            why = "big dev buy"
        elif len(t["buyers"]) < R["min_buyers"] or t["buy_sol"] < R["min_buy_sol"]:
            why = "not enough buyers"
        elif t["buy_sol"] < R["buy_sell_ratio"] * max(t["sell_sol"], 0.01):
            why = "sellers too strong"
        elif t["mc"] <= t["mc0"]:
            why = "price not rising"
        if why is None:
            today = _now()[:10]
            n_today = sum(1 for x in s.st["trades"] + s.st["positions"] if x["at"][:10] == today and not x.get("partial"))
            if len(s.st["positions"]) < R["max_open"] and n_today < R["max_trades_day"] and s.st["cash"] >= R["size"]:
                s.enter(mint, t)
            else:
                why = "book full"
        if why is None or age >= R["watch_s"]:
            t["done"] = True
            s.st["checked"] += 1
            if why:
                s.st["skips"][why] = s.st["skips"].get(why, 0) + 1
            if not any(p["mint"] == mint for p in s.st["positions"]):
                ws.send({"method": "unsubscribeTokenTrade", "keys": [mint]})
                s.tokens.pop(mint, None)

    # --- paper positions --------------------------------------------------------------------------------------
    def enter(s, mint, t):
        entry = t["mc"] * (1 + R["cost_side"])
        pos = {"symbol": t["sym"], "mint": mint, "dev": t["dev"], "entry_mc": entry, "last_mc": t["mc"], "peak_mc": t["mc"],
               "usd": R["size"], "mc_usd": round(t["mc"] * s.sol()), "at": _now(), "at_s": time.time(),
               "buyers": len(t["buyers"]), "buy_sol": round(t["buy_sol"], 2), "sell_sol": round(t["sell_sol"], 2),
               "url": f"https://pump.fun/coin/{mint}"}
        s.st["cash"] -= R["size"]
        s.st["positions"].append(pos)
        _log("buy", pos)

    def manage(s, pos, mc):
        pos["last_mc"] = mc
        pos["peak_mc"] = max(pos["peak_mc"], mc)
        ch = mc / pos["entry_mc"] - 1
        if pos.get("half_sold"):
            if mc <= pos["peak_mc"] * (1 - R["trail"]):
                s.close(pos, mc, f"moonbag trail (peak {pos['peak_mc'] / pos['entry_mc']:.1f}x)")
        elif ch >= R["tp"]:
            half = pos["usd"] / 2
            net = half * mc / pos["entry_mc"] * (1 - R["cost_side"])
            s.st["cash"] += net
            row = {**pos, "usd": half, "exit_mc": mc, "exit_at": _now(), "why": "half at 2x", "pnl": round(net - half, 2), "partial": True}
            s.st["trades"].append(row)
            _log("sell_half", row)
            pos["usd"] -= half
            pos["half_sold"] = True
        elif ch <= R["sl"]:
            s.close(pos, mc, "stop")

    def close(s, pos, mc, why):
        net = pos["usd"] * mc / pos["entry_mc"] * (1 - R["cost_side"])
        s.st["cash"] += net
        row = {**pos, "exit_mc": mc, "exit_at": _now(), "why": why, "pnl": round(net - pos["usd"], 2)}
        s.st["trades"].append(row)
        s.st["positions"].remove(pos)
        _log("sell", row)

    def timers(s, ws):
        now = time.time()
        for pos in list(s.st["positions"]):
            held = (now - pos["at_s"]) / 60
            if not pos.get("half_sold") and held >= R["max_hold_min"] and pos["last_mc"] / pos["entry_mc"] - 1 < R["stale_gain"]:
                s.close(pos, pos["last_mc"], "no move")
                ws.send({"method": "unsubscribeTokenTrade", "keys": [pos["mint"]]})
        for mint, t in list(s.tokens.items()):
            if not t["done"] and now - t["t0"] >= R["watch_s"]:
                s.maybe_enter(mint, t, ws)


def _run():
    s = Sniper()
    while True:
        key = api_key()
        try:
            ws = WS("/api/data" + (f"?api-key={key}" if key else ""))
            s.st["mode"] = "LIVE TRADES" if key else "LAUNCHES ONLY (waiting for PUMPPORTAL_API_KEY)"
            ws.send({"method": "subscribeNewToken"})
            for p in s.st["positions"]:
                ws.send({"method": "subscribeTokenTrade", "keys": [p["mint"]]})
            last_save = 0
            while True:
                if sys._snipe_loop != LOOP_NAME:  # a newer version took over
                    return
                m = ws.recv()
                tx = m.get("txType")
                if tx == "create":
                    s.on_create(m, ws) if key else s.st.__setitem__("launches_seen", s.st["launches_seen"] + 1)
                elif tx in ("buy", "sell"):
                    s.on_trade(m, ws)
                elif "message" in m and "only available" in m["message"]:
                    s.st["last_error"] = f"{_now()} {m['message'][:120]}"
                if time.time() - last_save > 10:
                    if api_key() != key:  # key added or changed: reconnect with it
                        raise ConnectionError("API key changed, reconnecting")
                    s.timers(ws)
                    save(s.st)
                    last_save = time.time()
        except Exception as e:
            s.st["last_error"] = f"{_now()} {e}"
            save(s.st)
            time.sleep(10)


def start():
    if getattr(sys, "_snipe_loop", None) != LOOP_NAME:
        sys._snipe_loop = LOOP_NAME
        threading.Thread(target=_run, daemon=True, name=LOOP_NAME).start()


def summary():
    start()
    st = load()
    equity = st["cash"] + sum(p["usd"] * p["last_mc"] / p["entry_mc"] for p in st["positions"])
    closed = [t for t in st["trades"] if not t.get("partial")]
    best = max(st["trades"], key=lambda t: t.get("exit_mc", 0) / t["entry_mc"], default=None)
    return {"mode": st.get("mode", ""), "start": st["start"], "equity": round(equity, 2), "total_pnl": round(equity - st["start"], 2),
            "launches_seen": st.get("launches_seen", 0), "checked": st.get("checked", 0), "skips": st.get("skips", {}),
            "positions": [{"symbol": p["symbol"], "entry": p["entry_mc"], "price": p["last_mc"],
                           "pnl": round(p["usd"] * (p["last_mc"] / p["entry_mc"] - 1), 2), "x": round(p["last_mc"] / p["entry_mc"], 2),
                           "peak_x": round(p["peak_mc"] / p["entry_mc"], 2), "moonbag": bool(p.get("half_sold")), "at": p["at"],
                           "url": p["url"]} for p in st["positions"]],
            "trades": [{"symbol": t["symbol"], "pnl": t["pnl"], "x": round(t["exit_mc"] / t["entry_mc"], 2), "why": t["why"],
                        "at": t["at"], "exit_at": t["exit_at"]} for t in st["trades"][-30:]],
            "closed": len(closed), "wins": sum(1 for t in closed if t["pnl"] > 0),
            "best_x": round(best["exit_mc"] / best["entry_mc"], 1) if best else None, "last_error": st.get("last_error", "")}
