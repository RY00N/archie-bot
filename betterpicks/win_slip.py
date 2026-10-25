"""BetterPicks win slip image for the WINNERS channel (Ryan 2026-10-25: "Yes do that", after his old TikTok's
biggest proof post was a real winning slip). One portrait PNG per real HIT: the legs that cleared, $5 stake to the
payout, BetterPicks branding only (never an app's look). Needs Pillow and brand/ (fonts + arena backgrounds).
Usage: python3 -I win_slip.py   (writes /tmp/win_slip.png from a sample win)
"""
import io, math, os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1080, 1350
SHOW = 100   # dollars shown on the slip
GREEN, WHITE, SOFT, DIM = (0, 179, 126), (255, 255, 255), (206, 212, 222), (150, 156, 168)
WORDS = {"threes": "threes", "rebounds": "rebounds", "points": "points", "assists": "assists", "pra": "pts + reb + ast",
         "pts_reb": "pts + reb", "pts_ast": "pts + ast", "reb_ast": "reb + ast", "receptions": "receptions",
         "rush_yds": "rush yards", "rec_yds": "receiving yards", "pass_yds": "pass yards", "strikeouts": "strikeouts"}


def brand():
    for d in (os.path.join(HERE, "brand"), os.path.join(os.environ.get("BP_REPO", "/home/archie/bp-repo"), "betterpicks", "brand")):
        if os.path.isdir(os.path.join(d, "fonts")):
            return d
    return None


def font(w, size):
    from PIL import ImageFont
    return ImageFont.truetype(os.path.join(brand(), "fonts", f"Inter-{w}.otf"), size)


def pick_text(l):
    words = WORDS.get(l["stat"], l["stat"].replace("_", " "))
    return f"{math.ceil(l['line'])}+ {words}" if l["side"] == "over" else f"Under {l['line']:g} {words}"


def fit(d, text, w, size, maxw):
    while size > 20 and d.textlength(text, font=font(w, size)) > maxw:
        size -= 2
    return font(w, size)


def render(v):
    """v = a feed verdict (status won): date, legs [player, stat, side, line, game, actual], stake, pnl. -> PNG bytes."""
    from PIL import Image, ImageDraw, ImageFilter
    b = brand()
    sport = (v.get("legs") or [{}])[0].get("league") or "nba"
    bg = os.path.join(b, f"bg-{sport}.jpg") if os.path.exists(os.path.join(b, f"bg-{sport}.jpg")) else os.path.join(b, "bg-nba.jpg")
    img = Image.open(bg).convert("RGB")
    s = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * s) + 1, int(img.height * s) + 1)).crop((0, 0, W, H)).filter(ImageFilter.GaussianBlur(14))
    img = Image.blend(img, Image.new("RGB", (W, H), (8, 10, 16)), 0.72).convert("RGBA")
    legs = v["legs"][:4]
    card_h = 340 + 190 * len(legs)
    y0 = (H - card_h) // 2
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle((60, y0, W - 60, y0 + card_h), 36, fill=(14, 17, 25, 235), outline=(40, 46, 60, 255), width=2)
    img = Image.alpha_composite(img, layer)
    d = ImageDraw.Draw(img)
    x = 110
    d.text((x, y0 + 56), "BETTERPICKS", font=font("Black", 40), fill=WHITE)
    day = datetime.fromisoformat(v["date"]).strftime("%b %-d").upper()
    d.text((x, y0 + 108), f"{len(legs)}-LEG {'LOCK' if len(legs) == 1 else 'PICK'} · {day}", font=font("Bold", 26), fill=DIM)
    pill = font("Black", 40)
    pw = d.textlength("WIN", font=pill) + 64
    d.rounded_rectangle((W - 110 - pw, y0 + 54, W - 110, y0 + 120), 16, fill=GREEN)
    d.text((W - 110 - pw + 32, y0 + 62), "WIN", font=pill, fill=(6, 20, 14))
    # Ryan 2026-10-25: show it as a $100 bet ("way more dramatic"); same real payout multiple as our $5 pick
    stake = v.get("stake") or 5
    pay = SHOW * (stake + (v.get("pnl") or 0)) / stake
    d.text((x, y0 + 168), f"${SHOW} to ${pay:,.0f}", font=font("Black", 92), fill=WHITE)
    d.text((x, y0 + 270), f"What ${SHOW} on this pick paid at our posted price", font=font("Regular", 24), fill=DIM)
    y = y0 + 350
    for l in legs:
        d.line((x, y - 24, W - 110, y - 24), fill=(40, 46, 60), width=2)
        d.text((x, y), l["player"], font=fit(d, l["player"], "Black", 46, 620), fill=WHITE)
        d.text((x, y + 60), pick_text(l), font=font("Bold", 32), fill=GREEN)
        d.text((x, y + 104), l.get("game") or "", font=font("Regular", 26), fill=DIM)
        act = l.get("actual")
        right = f"{act:g}" if isinstance(act, (int, float)) else "HIT"
        rf = font("Black", 64)
        d.text((W - 110 - d.textlength(right, font=rf), y + 8), right, font=rf, fill=GREEN)
        d.rounded_rectangle((W - 330, y + 112, W - 110, y + 124), 6, fill=GREEN)   # cleared bar
        y += 190
    foot = "Picks posted live in the BetterPicks Discord"
    d.text(((W - d.textlength(foot, font=font("Bold", 28))) / 2, y0 + card_h + 36), foot, font=font("Bold", 28), fill=SOFT)
    out = io.BytesIO()
    img.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


if __name__ == "__main__":
    open("/tmp/win_slip.png", "wb").write(render({
        "date": "2026-10-23", "stake": 5, "pnl": 3.35, "legs": [
            {"player": "Brandin Podziemski", "stat": "threes", "side": "over", "line": 1.5, "game": "MEM @ GSW", "league": "nba"}]}))
    print("wrote /tmp/win_slip.png")
