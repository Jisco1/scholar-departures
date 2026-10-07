"""Make the picture a shared link shows (WhatsApp, Facebook, LinkedIn, X) for each research news story.

    python tools/share_images.py            # every story that has photos
    python tools/share_images.py <story-id> # just one

Run it after adding a story with photos; it needs Pillow (pip install pillow), so it runs here, not in the build.
It writes src/assets/share-<id>.jpg at 1200 x 630, the shape the apps show as a large preview:
- one wide photo: cropped to fill the frame
- one portrait: the photo sharp in the middle, a blurred copy of it filling the sides
- several portraits: side by side
The build points each story's og:image at its share picture.
"""
import json
import pathlib
import sys

from PIL import Image, ImageEnhance, ImageFilter

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "src" / "assets"
W, H = 1200, 630


def cover(im, w, h, focus=50):
    """Scale to fill w x h, centred across and placed at focus% down the picture."""
    s = max(w / im.width, h / im.height)
    im = im.resize((max(w, round(im.width * s)), max(h, round(im.height * s))), Image.LANCZOS)
    x = (im.width - w) // 2
    y = round((im.height - h) * focus / 100)
    return im.crop((x, y, x + w, y + h))


def card(images):
    photos = [(Image.open(ASSETS / i["file"]).convert("RGB"), i) for i in images]
    wide = [(im, i) for im, i in photos if i.get("kind") == "wide"]
    if wide:
        im, i = wide[0]
        return cover(im, W, H, i.get("focus", 50))
    portraits = photos[:4]
    if len(portraits) == 1:
        im, _ = portraits[0]
        back = cover(im, W, H, 30).filter(ImageFilter.GaussianBlur(28))
        back = ImageEnhance.Brightness(back).enhance(0.55)
        front = im.resize((round(im.width * H / im.height), H), Image.LANCZOS)
        back.paste(front, ((W - front.width) // 2, 0))
        return back
    out = Image.new("RGB", (W, H))
    step = W // len(portraits)
    for k, (im, i) in enumerate(portraits):
        width = step if k < len(portraits) - 1 else W - step * k
        out.paste(cover(im, width, H, i.get("focus", 20)), (step * k, 0))
    return out


def main(only):
    items = json.loads((ROOT / "content" / "news.json").read_text(encoding="utf-8"))
    for n in items:
        if not n.get("images") or (only and n["id"] not in only):
            continue
        path = ASSETS / f"share-{n['id']}.jpg"
        card(n["images"]).save(path, "JPEG", quality=84, optimize=True, progressive=True)
        print(f"{path.relative_to(ROOT)}  {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(set(sys.argv[1:]))
