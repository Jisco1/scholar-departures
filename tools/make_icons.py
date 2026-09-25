"""Draw the favicon, touch icon and social preview image into src/assets/.
Run once (or after a brand change): python tools/make_icons.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "src" / "assets"
NAVY, CARD, LINE, TEXT, DIM, BRASS, AMBER = "#0B1322", "#121F35", "#243450", "#E9EEF7", "#A4B4CC", "#D7A94C", "#EFA94A"
FONTS = Path("C:/Windows/Fonts")


def font(name, size):
    try:
        return ImageFont.truetype(str(FONTS / name), size)
    except OSError:
        return ImageFont.load_default()


def mortarboard(draw, cx, cy, s, color, width):
    """The seal glyph: a mortarboard, as in the site's SVG (24-unit grid)."""
    def p(x, y):
        return (cx + (x - 12) * s, cy + (y - 12) * s)
    draw.line([p(2, 10), p(12, 5), p(22, 10), p(12, 15), p(2, 10)], fill=color, width=width, joint="curve")
    draw.line([p(22, 10), p(22, 16)], fill=color, width=width)
    pts = [p(6, 12), p(6, 17)]
    for t in range(0, 11):  # the cap's lower curve
        u = t / 10
        x = (1 - u) ** 3 * 6 + 3 * (1 - u) ** 2 * u * 9 + 3 * (1 - u) * u ** 2 * 15 + u ** 3 * 18
        y = (1 - u) ** 3 * 17 + 3 * (1 - u) ** 2 * u * 20 + 3 * (1 - u) * u ** 2 * 20 + u ** 3 * 17
        pts.append(p(x, y))
    pts.append(p(18, 12))
    draw.line(pts, fill=color, width=width, joint="curve")


def icon(size):
    img = Image.new("RGBA", (size, size), NAVY)
    d = ImageDraw.Draw(img)
    r = size * 0.44
    d.ellipse([size / 2 - r, size / 2 - r, size / 2 + r, size / 2 + r], fill="#1F2A36", outline=BRASS, width=max(1, size // 40))
    mortarboard(d, size / 2, size / 2 + size * 0.02, size / 34, BRASS, max(2, size // 22))
    return img


icon(180).convert("RGB").save(ASSETS / "apple-touch-icon.png", optimize=True)
icon(32).convert("RGB").save(ASSETS / "favicon-32.png", optimize=True)

(ASSETS / "favicon.svg").write_text(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="16" fill="#0B1322"/>'
    '<circle cx="16" cy="16" r="14" fill="#1F2A36" stroke="#D7A94C" stroke-width="1.2"/>'
    '<g transform="translate(4 4.4)" fill="none" stroke="#D7A94C" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M22 10v6M2 10l10-5 10 5-10 5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/></g></svg>\n', encoding="utf-8")

# social preview, 1200 x 630
W, H = 1200, 630
og = Image.new("RGB", (W, H), NAVY)
d = ImageDraw.Draw(og)
for i in range(0, W, 48):  # faint route lines
    d.line([(i, H), (i + 380, 0)], fill="#0F1A2E", width=1)
mono_s, mono_m, serif_l = font("consola.ttf", 22), font("consolab.ttf", 30), font("georgia.ttf", 76)
mortarboard(d, 108, 118, 3.2, BRASS, 6)
d.text((160, 100), "D E G R E E S T E P   ·   F U N D E D   S T U D Y   A B R O A D", font=mono_s, fill=BRASS)
d.text((80, 190), "Tuition-free & fully funded", font=serif_l, fill=TEXT)
d.text((80, 280), "study routes, checked at source", font=serif_l, fill=TEXT)
d.text((82, 382), "Europe · USA · Canada — live deadlines · official links only", font=font("segoeui.ttf", 30), fill=DIM)
# a departures-board strip
y0 = 460
d.rounded_rectangle([80, y0, W - 80, y0 + 110], radius=14, fill=CARD, outline=LINE, width=2)
cols = [("ROUTE", 110), ("STATUS", 800)]
for label, x in cols:
    d.text((x, y0 + 16), label, font=font("consola.ttf", 16), fill="#6E80A0")
row = [("FULLY FUNDED ROUTES", 110, TEXT), ("BOARDING", 800, AMBER)]
for text, x, col in row:
    cx = x
    for ch in text:
        if ch != " ":
            d.rounded_rectangle([cx - 2, y0 + 48, cx + 22, y0 + 88], radius=3, fill="#18243C")
        d.text((cx + 2, y0 + 52), ch, font=mono_m, fill=col)
        cx += 27
og.save(ASSETS / "og.png", optimize=True)
print("icons written:", sorted(p.name for p in ASSETS.glob("*.png")) + ["favicon.svg"])
