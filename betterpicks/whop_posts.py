"""Post every win to the BetterPicks Whop home feed (Ryan 2026-10-23: "post about it in the Home section as the
announcements page for the whop, each win" + "add previous ones for now with pictures").

Uses whop_wins.counted() for the graded picks that count toward the record. Each won pick gets one forum post with
a win card image in the "Public forum" experience, oldest first. Dedupe is by title against the posts already in
the feed, so it is safe to run from more than one place. Cards need Pillow; without it the post goes up text-only.
Key: env WHOP_BP_API. Usage: python3 -I whop_posts.py [--dry]   (--dry writes cards to /tmp and posts nothing)
"""
import io, json, os, sys, time, urllib.request

BP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BP)
import whop_wins  # noqa: E402

EXPERIENCE = "exp_OV5LT3IHDprl8l"
API = "https://api.whop.com/api/v1/"
BRAND = os.path.join(BP, "brand")
FONTS = ["/usr/share/fonts/opentype/inter/Inter-{w}.otf", "/usr/share/fonts/truetype/dejavu/DejaVuSans{d}.ttf"]


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + os.environ["WHOP_BP_API"],
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def existing_titles():
    titles, after = set(), None
    while True:
        d = call("GET", f"forum_posts?experience_id={EXPERIENCE}" + (f"&after={after}" if after else ""))
        titles |= {p.get("title") or "" for p in d.get("data", [])}
        pi = d.get("page_info") or {}
        if not pi.get("has_next_page"):
            return titles
        after = pi["end_cursor"]


def win_title(e):
    return ("✅ HIT: " if e["status"] == "won" else "❌ MISS: ") + " + ".join(whop_wins.leg_text(l) for l in e.get("legs", []))


def leg_bits(l):
    bits = []
    if l.get("p") is not None:
        bits.append(f"win chance {l['p'] * 100:.0f}%")
    if l.get("edge"):
        bits.append(f"edge +{l['edge'] * 100:.1f}%")
    return bits


def nice_date(e):
    try:
        return time.strftime("%b %-d", time.strptime(e.get("date", ""), "%Y-%m-%d"))
    except ValueError:
        return e.get("date", "")


def record_text(r):
    return f"{r['won']}-{r['lost']}, {'+' if r['pnl'] >= 0 else '-'}${abs(r['pnl']):.2f} at $5 a pick"


def win_body(e, r):
    lines = [f"Called on {nice_date(e)} and it " + ("hit." if e["status"] == "won" else "missed. We post the losses too.")]
    for l in e.get("legs", []):
        lines.append(f"**{whop_wins.leg_text(l)}**" + (f" ({l['game']})" if l.get("game") else "")
                     + ("\n" + ", ".join(leg_bits(l)) if leg_bits(l) else ""))
    lines += [f"Record on picks meeting our current rules: **{record_text(r)}**.",
              "Members get every pick live in the Discord PICKS channel."]
    return "\n\n".join(lines)


def font(weight, size):
    from PIL import ImageFont
    for f in (FONTS[0].format(w=weight), FONTS[1].format(d="-Bold" if weight in ("Bold", "Black", "ExtraBold") else "")):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def card(e):
    """1200x675 PNG bytes: photo background by league, dark fade, HIT badge, pick, numbers. None without Pillow."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    W, H = 1200, 675
    league = ((e.get("legs") or [{}])[0].get("league") or "").lower()
    bg_path = os.path.join(BRAND, f"bg-{league}.jpg")
    if not os.path.exists(bg_path):
        bg_path = os.path.join(BRAND, "bg-other.jpg")
    img = Image.new("RGB", (W, H), (8, 9, 12))
    if os.path.exists(bg_path):
        bg = Image.open(bg_path).convert("RGB")
        s = max(W / bg.width, H / bg.height)
        bg = bg.resize((int(bg.width * s) + 1, int(bg.height * s) + 1))
        img.paste(bg.crop(((bg.width - W) // 2, (bg.height - H) // 2, (bg.width - W) // 2 + W, (bg.height - H) // 2 + H)))
    fade = Image.new("L", (W, 1))
    fade.putdata([int(235 - 120 * x / W) for x in range(W)])
    img.paste(Image.new("RGB", (W, H), (0, 0, 0)), mask=fade.resize((W, H)))
    d = ImageDraw.Draw(img)
    green = (62, 224, 138)
    d.rounded_rectangle((70, 70, 250, 128), 29, fill=green)
    d.text((160, 99), "✓ HIT" if font("Bold", 30).getbbox("✓") else "HIT", font=font("Bold", 30), fill=(0, 0, 0), anchor="mm")
    d.text((275, 99), nice_date(e), font=font("SemiBold", 26), fill=(200, 205, 215), anchor="lm")
    y = 190
    for l in e.get("legs", []):
        t = whop_wins.leg_text(l)
        size = 64 if len(t) < 26 else 52 if len(t) < 34 else 42
        d.text((70, y), t, font=font("Black", size), fill=(255, 255, 255))
        y += size + 18
        sub = " · ".join(x for x in [l.get("game")] + leg_bits(l) if x)
        if sub:
            d.text((70, y), sub, font=font("Medium", 28), fill=(205, 210, 220))
            y += 56
    d.text((70, H - 80), "BETTERPICKS", font=font("Bold", 24), fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def upload(png, name):
    f = call("POST", "files", {"filename": name})
    urllib.request.urlopen(urllib.request.Request(f["upload_url"], method="PUT", data=png,
                                                  headers={"Content-Type": "image/png"}), timeout=60).read()
    for _ in range(20):
        if call("GET", "files/" + f["id"]).get("upload_status") == "ready":
            return f["id"]
        time.sleep(3)
    return None


def main():
    dry = "--dry" in sys.argv
    if not dry and not os.environ.get("WHOP_BP_API"):
        print("whop_posts: WHOP_BP_API not set, skipped")
        return
    c, r = whop_wins.counted()
    wins = sorted([e for e in c if e["status"] in ("won", "lost")], key=lambda e: e.get("date", ""))  # Ryan: losses too
    have = set() if dry else existing_titles()
    for i, e in enumerate(wins):
        t = win_title(e)
        if t in have:
            continue
        if dry:
            png = card(e)
            if png:
                open(f"/tmp/whop_card_{i}.png", "wb").write(png)
            print(t, "\n", win_body(e, r), "\n")
            continue
        # Whop refuses attachments from account API keys, so posts are text-only (Ryan chose that 2026-10-23)
        body = {"experience_id": EXPERIENCE, "title": t, "content": win_body(e, r)}
        call("POST", "forum_posts", body)
        have.add(t)
        print("whop_posts: posted", t)


if __name__ == "__main__":
    main()
