"""Backstop for the Members / Premium counter channels (Ryan 2026-10-31: "make sure members numbers are always
being updated"). BetterBot on the droplet renames them on every join/leave and every 10 min; if the bot is down or
missed an event, this run from fast_loop (every 10 min) recounts and renames whatever is off. Wins/Losses stay
with the bot (they count the Wins/Losses channel)."""
import json, os, urllib.request

API, G = "https://discord.com/api/v10", "1509315900874948729"
H = {"Authorization": "Bot " + os.environ["DISCORD_BOT_TOKEN"], "User-Agent": "DiscordBot (bp,1)",
     "Content-Type": "application/json"}
PAID = {"1563068488195772517", "1563068490162642974"}   # Premium, OG Premium
CH = {"members": ("1563072752607436900", "\U0001F464Members: {}"), "premium": ("1564731560387944569", "\U0001F464Premium: {}")}


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, headers=H, data=json.dumps(body).encode() if body else None)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def main():
    n = p = 0
    after = "0"
    while True:
        page = call("GET", f"/guilds/{G}/members?limit=1000&after={after}")
        hs = [m for m in page if not m["user"].get("bot")]
        n += len(hs)
        p += sum(bool(PAID & set(m.get("roles", []))) for m in hs)
        if len(page) < 1000:
            break
        after = page[-1]["user"]["id"]
    for k, v in (("members", n), ("premium", p)):
        ch, fmt = CH[k]
        name = fmt.format(v)
        if call("GET", f"/channels/{ch}")["name"] != name:
            call("PATCH", f"/channels/{ch}", {"name": name})
            print("counter fixed:", name, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        print("counters check:", ex)
