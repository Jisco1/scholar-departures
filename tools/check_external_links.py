"""Check every external link in the built site (official pages, sources, schools).

usage: python tools/check_external_links.py [_site]
Prints links that fail. A 403/405/429 often means a site blocks automated
checks rather than a dead page, so those are listed separately to check by hand.
"""
import html
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SITE = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "_site")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36"
SKIP = ("https://scholardepartures.com", "https://www.google.com/settings", "https://adssettings.google.com")

pages = defaultdict(set)
for f in SITE.rglob("*.html"):
    for url in re.findall(r'href="(https?://[^"]+)"', f.read_text(encoding="utf-8")):
        url = html.unescape(url)
        if not url.startswith(SKIP):
            pages[url].add(str(f.relative_to(SITE)))
extra = SITE / "data" / "schools.js"
if extra.exists():
    for url in re.findall(r'"link": ?"(https://[^"]+)"', extra.read_text(encoding="utf-8")):
        pages[url].add("data/schools.js")


def check(url):
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return url, r.status, r.geturl()
        except urllib.error.HTTPError as e:
            if method == "HEAD" and e.code in (403, 404, 405, 400, 500, 501, 503):
                continue
            return url, e.code, ""
        except Exception as e:
            if method == "HEAD":
                continue
            return url, type(e).__name__, ""
    return url, "?", ""


with ThreadPoolExecutor(max_workers=12) as ex:
    results = list(ex.map(check, sorted(pages)))

bad, blocked = [], []
for url, status, final in results:
    if status == 200:
        continue
    (blocked if status in (401, 403, 405, 429) else bad).append((url, status))
print(f"checked {len(results)} external links")
print(f"FAILED ({len(bad)}):")
for url, status in bad:
    print(f"  {status}  {url}   <- {', '.join(sorted(pages[url]))[:120]}")
print(f"BLOCKED BY SITE — check by hand ({len(blocked)}):")
for url, status in blocked:
    print(f"  {status}  {url}")
