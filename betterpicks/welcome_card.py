"""BetterPicks welcome card (Ryan 2026-10-26: "embed link welcomes like sapphire", then sent the welcome art to use
or beat). Ryan's art (brand/welcome-bg.jpg, "Welcome to BetterPicksAI") with its bottom "Verify" badge replaced by a
personal one: the new member's round avatar, name and member number. betterbot_presence.py posts it in WELCOME
inside an embed with link buttons. Needs Pillow and brand/.
Usage: python3 -I welcome_card.py   (writes /tmp/welcome_card.png from a sample)
"""
import io, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from win_slip import brand, font, fit

INK, GREY = (17, 17, 17), (110, 110, 110)


def render(name, avatar_png, number):
    """name: display name, avatar_png: image bytes (or None), number: member count -> PNG bytes (1672x941)."""
    from PIL import Image, ImageDraw
    img = Image.open(os.path.join(brand(), "welcome-bg.jpg")).convert("RGB")
    W, H = img.size
    d = ImageDraw.Draw(img)
    # cover the art's "Verify to get started" badge, keep the frame line above it
    d.rectangle((575, 846, 1100, H), fill=(250, 250, 250))
    x0, y0, x1, y1 = 560, 836, 1112, 930
    d.rounded_rectangle((x0, y0, x1, y1), 47, fill=INK)
    size = 74
    try:
        av = Image.open(io.BytesIO(avatar_png)).convert("RGB").resize((size, size))
    except Exception:
        av = Image.new("RGB", (size, size), (70, 70, 70))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    ax, ay = x0 + 12, y0 + (y1 - y0 - size) // 2
    d.ellipse((ax - 3, ay - 3, ax + size + 3, ay + size + 3), fill=(255, 255, 255))
    img.paste(av, (ax, ay), mask)
    tx, maxw = ax + size + 22, x1 - (ax + size + 22) - 30
    d.text((tx, y0 + 12), name, font=fit(d, name, "Bold", 38, maxw), fill=(255, 255, 255))
    d.text((tx, y0 + 56), f"Member #{number}  •  welcome in!", font=font("Medium", 24), fill=(190, 190, 190))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


if __name__ == "__main__":
    open("/tmp/welcome_card.png", "wb").write(render("ryn.ai real ///M", None, 18))
    print("/tmp/welcome_card.png")
