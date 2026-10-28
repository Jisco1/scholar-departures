"""Tell IndexNow search engines (Bing, Yandex, Seznam, Naver, Yep) which pages changed.

usage: python tools/indexnow.py [--days 1] [--all] [--dry-run]
Reads the live sitemap and submits every page whose lastmod falls within the last
--days days (or every page with --all). The key comes from site.json and the build
publishes it at /<key>.txt, which is how IndexNow checks the site is ours.
"""
import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "site.json").read_text(encoding="utf-8"))
BASE = CONFIG["base_url"].rstrip("/") + "/"
KEY = (CONFIG.get("indexnow") or {}).get("key")
UA = "DegreeStepBot/1.0 (" + CONFIG["contact_email"] + ")"


def changed_pages(days, everything):
    sitemap = urllib.request.urlopen(urllib.request.Request(BASE + "sitemap.xml", headers={"User-Agent": UA}), timeout=30).read().decode("utf-8")
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    pages = re.findall(r"<loc>([^<]+)</loc>(?:<lastmod>([^<]+)</lastmod>)?", sitemap)
    return [loc for loc, lastmod in pages if everything or (lastmod and lastmod[:10] >= since)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not KEY:
        print("no indexnow key in site.json; nothing to do")
        return 0
    urls = changed_pages(args.days, args.all)
    print(f"{len(urls)} pages to submit")
    for u in urls:
        print("  " + u)
    if not urls or args.dry_run:
        return 0
    host = BASE.split("/")[2]
    body = json.dumps({"host": host, "key": KEY, "keyLocation": f"{BASE}{KEY}.txt", "urlList": urls[:10000]}).encode("utf-8")
    req = urllib.request.Request("https://api.indexnow.org/indexnow", data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"IndexNow answered {r.status}")  # 200 or 202 means received
    except urllib.error.HTTPError as e:
        print(f"IndexNow answered {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
