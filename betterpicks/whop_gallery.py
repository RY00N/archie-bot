"""Keep the record on the BetterPicks VIP Whop gallery photos current (Ryan 2026-10-23: "the photo has to update too",
same background, 7-1 -> 8-1 after a win).

Draws the live record onto the two fixed background plates (brand/r1plate.png hero, brand/r3plate.png results list),
uploads them and sets the gallery to [hero, the fixed Discord-feed image, results]. Only runs when the record or the
latest results changed. Needs Pillow; fonts are bundled in brand/fonts. Key: env WHOP_BP_API.
Usage: python3 -I whop_gallery.py [--dry]   (--dry writes /tmp/whop_g1.png and /tmp/whop_g3.png)
"""
import hashlib, io, os, sys

BP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BP)
import whop_wins, whop_posts  # noqa: E402

PRODUCT = "prod_M44sTTPJ3OFlS"
FEED_IMAGE = "file_yVHEnX9n4gFEj"  # the phone/Discord picks image, never changes
BRAND = os.path.join(BP, "brand")
STATE = os.path.join(BP, ".whop_gallery_state")
START = "OCT 16"  # first pick counted under the current rules


def font(w, size):
    from PIL import ImageFont
    return ImageFont.truetype(os.path.join(BRAND, "fonts", f"Inter-{w}.otf"), size)


def tracked(d, xy, text, f, fill, gap=4.3):
    """Draw text with letter spacing, like the CSS letter-spacing on the original images."""
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + gap


def money(v):
    return f"{'+' if v >= 0 else '-'}${abs(v):.2f}"


def hero(r):
    from PIL import Image, ImageDraw
    img = Image.open(os.path.join(BRAND, "r1plate.png")).convert("RGB")
    d = ImageDraw.Draw(img)
    rec = f"{r['won']}–{r['lost']}"
    tracked(d, (100, 110), "BETTERPICKS · FIRST WEEK · OCT 16–22", font("Bold", 18), (207, 214, 228))
    d.text((92, 146), rec, font=font("Black", 190), fill=(255, 255, 255))
    d.text((100, 372), "Player prop picks with a real", font=font("Black", 54), fill=(255, 255, 255))
    d.text((100, 432), "edge.", font=font("Black", 54), fill=(255, 255, 255))
    d.text((100, 512), "Posted live in Discord the moment the numbers line up.", font=font("Regular", 26), fill=(212, 216, 224))
    d.text((100, 550), f"{money(r['pnl'])} at $5 a pick in our first week.", font=font("Regular", 26), fill=(212, 216, 224))
    return img


def results(r, settled):
    from PIL import Image, ImageDraw
    img = Image.open(os.path.join(BRAND, "r3plate.png")).convert("RGBA")
    rows = settled[:9]
    x0, y0, w = 100, 90, 640
    h = 200 + 52 * len(rows)
    glass = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glass)
    gd.rounded_rectangle((x0, y0, x0 + w, y0 + h), 20, fill=(12, 14, 18, 175), outline=(255, 255, 255, 30))
    img = Image.alpha_composite(img, glass).convert("RGB")
    d = ImageDraw.Draw(img)
    tracked(d, (x0 + 40, y0 + 38), "FIRST WEEK · OCT 16–22", font("Bold", 18), (207, 214, 228))
    rec = f"{r['won']}–{r['lost']}"
    d.text((x0 + 36, y0 + 66), rec, font=font("Black", 84), fill=(255, 255, 255))
    d.text((x0 + 56 + d.textlength(rec, font=font("Black", 84)), y0 + 98), money(r["pnl"]), font=font("Black", 44),
           fill=(62, 224, 138))
    y = y0 + 176
    for i, e in enumerate(rows):
        label = " + ".join(whop_wins.leg_text(l) for l in e.get("legs", []))
        while d.textlength(label, font=font("Regular", 21)) > w - 170:
            label = label[:-2]
        d.text((x0 + 40, y), label, font=font("Regular", 21), fill=(255, 255, 255))
        won = e["status"] == "won"
        tag = "HIT" if won else "MISS"
        d.text((x0 + w - 40 - d.textlength(tag, font=font("Bold", 21)), y), tag, font=font("Bold", 21),
               fill=(62, 224, 138) if won else (255, 107, 120))
        if i < len(rows) - 1:
            d.line((x0 + 40, y + 38, x0 + w - 40, y + 38), fill=(40, 42, 48))
        y += 52
    return img


def png(img):
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def main():
    dry = "--dry" in sys.argv
    if not dry and not os.environ.get("WHOP_BP_API"):
        print("whop_gallery: WHOP_BP_API not set, skipped")
        return
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("whop_gallery: Pillow missing, skipped")
        return
    c, r = whop_wins.first_week()
    settled = sorted([e for e in c if e["status"] in ("won", "lost")], key=lambda e: e.get("date", ""), reverse=True)
    key = hashlib.sha1(repr((r["won"], r["lost"], r["pnl"], [whop_posts.win_title(e) + e["status"] for e in settled[:9]])).encode()).hexdigest()
    g1, g3 = png(hero(r)), png(results(r, settled))
    if dry:
        open("/tmp/whop_g1.png", "wb").write(g1)
        open("/tmp/whop_g3.png", "wb").write(g3)
        print("whop_gallery: dry run written")
        return
    if os.path.exists(STATE) and open(STATE).read().strip() == key:
        return
    ids = [whop_posts.upload(g1, "betterpicks-record.png"), FEED_IMAGE, whop_posts.upload(g3, "betterpicks-results.png")]
    if not all(ids):
        print("whop_gallery: upload not ready, retry next run")
        return
    # A gallery-only PATCH doesn't bump updated_at, so Whop keeps serving the cached page with the old (now deleted)
    # files and the photos show broken. Touching the description refreshes it; the second PATCH puts the text back.
    text = whop_wins.build()
    whop_posts.call("PATCH", f"products/{PRODUCT}", {"gallery_images": [{"id": i} for i in ids], "description": text + "\n"})
    whop_posts.call("PATCH", f"products/{PRODUCT}", {"description": text})
    open(STATE, "w").write(key)
    print(f"whop_gallery: photos updated to {r['won']}-{r['lost']}")


if __name__ == "__main__":
    main()
