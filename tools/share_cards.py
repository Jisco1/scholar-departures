"""Make the picture a shared link shows for each scholarship, guide, country guide and funded position.

    python tools/share_cards.py

Run it after adding or renaming a route, guide, country guide or position. It needs Google Chrome (to draw the
card, so the fonts and colours match the site) and Pillow (to save it small). It writes
src/assets/share-<kind>-<slug>.jpg at 1200 x 630: the page's name, what it is, and a boarding-pass strip,
in the same style as the site-wide card. Pages without a card keep the site-wide one.
"""
import datetime as dt
import html
import json
import os
import pathlib
import re
import subprocess
import tempfile

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "src" / "assets"
CHROME = os.environ.get("CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
CAP = ('<svg width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="#D7A94C" stroke-width="2" stroke-linecap="round" '
       'stroke-linejoin="round"><path d="M22 10v6M2 10l10-5 10 5-10 5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/></svg>')
e = lambda s: html.escape(str(s), quote=True)


def fit(text, width):
    return max(16, min(30, int(width / (max(len(text), 1) * 1.25))))


def tiles(text, size, brass=False):
    text = text.upper()
    color = "#E9A53F" if brass else "#E9EEF7"
    return "".join(f'<span style="display:inline-block;min-width:{size * 0.95:.0f}px;margin-right:{size * 0.22:.0f}px;'
                   f'padding:3px 0;text-align:center;background:#0E1A2E;border-radius:3px;color:{color};'
                   f'font:700 {size}px Consolas,monospace">{"&nbsp;" if ch == " " else e(ch)}</span>' for ch in text)


def card_html(title, subtitle, left, right):
    head, _, tail = title.partition(": ")
    size = 66 if len(title) <= 38 else 58 if len(title) <= 60 else 50 if len(title) <= 85 else 44
    tile = min(fit(left[1], 560), fit(right[1], 380))
    label = "font:13px Consolas,monospace;letter-spacing:.14em;text-transform:uppercase;color:#7D8EAD;margin-bottom:10px"
    title_html = (f'{e(head)}<br><em style="color:#D7A94C">{e(tail)}</em>' if tail else e(title))
    return f"""<html><head><meta charset="utf-8"></head><body style="margin:0;width:1200px;height:630px;overflow:hidden;
background:#0B1322 repeating-linear-gradient(115deg, rgba(255,255,255,.022) 0 1px, transparent 1px 30px)">
<div style="position:absolute;left:80px;top:78px;display:flex;align-items:center;gap:22px">{CAP}
<span style="font:17px Consolas,monospace;letter-spacing:.55em;color:#D7A94C">DEGREESTEP</span></div>
<div style="position:absolute;left:80px;right:80px;top:166px;height:270px;display:flex;flex-direction:column;justify-content:center;gap:26px">
<div style="font:400 {size}px/1.12 Georgia,serif;color:#E9EEF7;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden">{title_html}</div>
<div style="font:27px 'Segoe UI',sans-serif;color:#A4B4CC;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{e(subtitle)}</div></div>
<div style="position:absolute;left:80px;right:80px;bottom:56px;height:104px;background:#121F35;border:1px solid #243450;
border-radius:12px;display:flex;align-items:center;padding:0 30px;gap:40px">
<div style="flex:1.45;min-width:0"><div style="{label}">{e(left[0])}</div>{tiles(left[1], tile)}</div>
<div style="flex:1;min-width:0"><div style="{label}">{e(right[0])}</div>{tiles(right[1], tile, brass=True)}</div>
</div></body></html>"""


def front(path):
    return json.loads(re.match(r"\s*<!--\s*(\{.*?\})\s*-->", path.read_text(encoding="utf-8"), re.S).group(1))


def pages():
    routes = json.loads((ROOT / "data" / "data.json").read_text(encoding="utf-8"))
    routes += [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "data" / "incoming").glob("*.json"))]
    for r in routes:
        sub = " · ".join([r["country"], *r["types"], *r["levels"]])
        yield f"route-{r['slug']}", r["name"], sub, ("Destination", r["country"]), ("Funding", r["types"][0])
    for f in sorted((ROOT / "content" / "guides").glob("*.html")):
        g = front(f)
        words = len(re.sub(r"<[^>]+>", " ", f.read_text(encoding="utf-8")).split())
        mins = max(1, round(words / 220))
        yield (f"guide-{f.stem}", g["title"], f"Guide · {g.get('kicker', 'Guide')} · {mins} min read",
               ("Guide", g.get("kicker", "Guide")), ("Reading time", f"{mins} min"))
    slugify = lambda s: re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    for f in sorted((ROOT / "content" / "countries").glob("*.json")):
        c = json.loads(f.read_text(encoding="utf-8"))
        n = sum(1 for r in routes if slugify(r["country"]) == f.stem)
        yield f"country-{f.stem}", c["title"], c.get("card", ""), ("Country", c["name"]), ("Funded routes", str(n))
    today = dt.date.today()
    for p in json.loads((ROOT / "content" / "positions.json").read_text(encoding="utf-8")):
        d = dt.date.fromisoformat(p["deadline"])
        if d >= today:
            yield (f"position-{p['id']}", p["title"], f"{p['institution']} · {p['country']}",
                   ("Position", p["level"]), ("Closes", f"{d.day} {d.strftime('%b %Y')}"))


def main():
    made = set()
    with tempfile.TemporaryDirectory() as tmp:
        for key, title, sub, left, right in pages():
            page = pathlib.Path(tmp) / f"{key}.html"
            png = pathlib.Path(tmp) / f"{key}.png"
            page.write_text(card_html(title, sub, left, right), encoding="utf-8")
            subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                            "--window-size=1200,630", f"--screenshot={png}", page.as_uri()],
                           check=True, capture_output=True, timeout=60)
            out = ASSETS / f"share-{key}.jpg"
            Image.open(png).convert("RGB").crop((0, 0, 1200, 630)).save(out, "JPEG", quality=86, optimize=True, progressive=True)
            made.add(out.name)
            print(out.name)
    # a position that has closed no longer has a page, so its card goes too
    for old in ASSETS.glob("share-position-*.jpg"):
        if old.name not in made:
            old.unlink()


if __name__ == "__main__":
    main()
