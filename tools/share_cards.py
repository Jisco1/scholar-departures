"""Make the picture a shared link shows for each scholarship, guide, country guide and funded position, and the
site-wide one: a real photograph, cropped to 1200 x 630, the shape WhatsApp, LinkedIn, Facebook and X show large.

    python tools/share_cards.py

Run it after adding or renaming a route, guide, country guide or position. It needs Pillow (pip install pillow) and
a connection, since guide, country and institution photos come from Wikimedia Commons. It writes
src/assets/share-<kind>-<slug>.jpg and src/assets/og.jpg. Where the photos come from:
- a scholarship: its own photo in data/route_photos.json
- a guide, a country guide, a position's institution: data/share_photos.json
- everything else: the home page's graduation photo (public domain)
News stories have their own pictures, made by tools/share_images.py.
"""
import datetime as dt
import io
import json
import pathlib
import sys
import urllib.parse
import urllib.request

from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from share_images import cover  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "src" / "assets"
UA = {"User-Agent": "DegreeStepBot/1.0 (https://degreestep.com; share pictures)"}
W, H = 1200, 630
_downloads = {}


def commons(title):
    """A Commons file at 1600 px wide, fetched once per run."""
    if title not in _downloads:
        u = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "query", "format": "json", "titles": title, "prop": "imageinfo", "iiprop": "url", "iiurlwidth": 1600})
        info = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60))
        url = next(iter(info["query"]["pages"].values()))["imageinfo"][0]["thumburl"]
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read()
        _downloads[title] = Image.open(io.BytesIO(raw)).convert("RGB")
    return _downloads[title]


def photo(entry, route_photos):
    if "route" in entry:
        return Image.open(ASSETS / route_photos[entry["route"]]["file"]).convert("RGB")
    return commons(entry["commons"])


def save(im, name, focus=50):
    out = ASSETS / name
    cover(im, W, H, focus).save(out, "JPEG", quality=84, optimize=True, progressive=True)
    print(f"{name}  {out.stat().st_size // 1024} KB")
    return out.name


def main():
    route_photos = json.loads((ROOT / "data" / "route_photos.json").read_text(encoding="utf-8"))
    shares = json.loads((ROOT / "data" / "share_photos.json").read_text(encoding="utf-8"))
    routes = json.loads((ROOT / "data" / "data.json").read_text(encoding="utf-8"))
    made = set()
    for r in routes:
        ph = route_photos.get(r["slug"])
        if ph:
            made.add(save(Image.open(ASSETS / ph["file"]).convert("RGB"), f"share-route-{r['slug']}.jpg", ph.get("focus", 50)))
    for kind, group in (("guide", "guides"), ("country", "countries")):
        for slug, entry in shares[group].items():
            made.add(save(photo(entry, route_photos), f"share-{kind}-{slug}.jpg", entry.get("focus", 50)))
    today = dt.date.today()
    for p in json.loads((ROOT / "content" / "positions.json").read_text(encoding="utf-8")):
        entry = shares["institutions"].get(p["institution"])
        if entry and dt.date.fromisoformat(p["deadline"]) >= today:
            made.add(save(photo(entry, route_photos), f"share-position-{p['id']}.jpg", entry.get("focus", 50)))
    save(Image.open(ASSETS / "hero-graduation-1920.jpg").convert("RGB"), "og.jpg", 40)
    # a card nothing uses any more (a position that has closed, a route that was renamed) goes
    for old in ASSETS.glob("share-*-*.jpg"):
        if old.name.split("-")[1] in ("route", "guide", "country", "position") and old.name not in made:
            old.unlink()
            print(f"removed {old.name}")


if __name__ == "__main__":
    main()
