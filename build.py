#!/usr/bin/env python3
"""
Build the DegreeStep website into _site/.

Standard library only, so it runs the same on a laptop and in GitHub Actions:

    python build.py                 # build _site/
    python build.py --preview-ads   # also draw placeholder ad bays (local design review only)

The build refuses to finish if the data is malformed (a bad link, an unknown
funding type, an impossible deadline) or if any page links to a page that does
not exist. data/data.json is edited by a weekly bot, so every value from it is
validated here and HTML-escaped on the way out.
"""

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import secrets
import shutil
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
CONTENT = ROOT / "content"
DATA = ROOT / "data"
OUT = ROOT / "_site"

CONFIG = json.loads((ROOT / "site.json").read_text(encoding="utf-8"))
SITE = CONFIG["name"]
BASE = CONFIG["base_url"].rstrip("/") + "/"
TODAY = dt.date.today()

TYPES = ["Tuition-Free", "Full Scholarship", "Need-Based Aid", "Merit", "Low Tuition", "Fee Waiver"]
TYPE_COLORS = {"Tuition-Free": "#7FD1AE", "Full Scholarship": "#B3A1E8", "Need-Based Aid": "#7EB8E8",
               "Merit": "#E3C892", "Low Tuition": "#6FC7C0", "Fee Waiver": "#E8919E"}
LEVELS = ["Bachelor's", "Master's", "PhD"]
REGIONS = ["Europe", "USA", "Canada", "Global"]
FIELDS = ["Engineering & Tech", "Computer Science", "Natural Sciences", "Medicine & Health",
          "Business & Economics", "Social Sciences & Law", "Arts & Humanities", "Agriculture & Environment"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
HORIZON_DAYS = 120
PER_PAGE_ALL = 6    # must match PER_PAGE_ALL in src/assets/app.js
PER_PAGE_DIR = 10   # must match PER_PAGE_DIR in src/assets/app.js
PAST_CLASS = ' class="past"'
URL_RE = re.compile(r"^https://[^\s\"'<>]+$")

NONCE = secrets.token_urlsafe(18)  # new every build, so data written before a build can never know it
CSP = ("script-src 'nonce-%s' 'strict-dynamic' https: 'unsafe-inline'; object-src 'none'; "
       "base-uri 'none'; form-action 'self'; upgrade-insecure-requests" % NONCE)

ERRORS = []


def fail(msg):
    ERRORS.append(msg)


def e(value):
    return html.escape("" if value is None else str(value), quote=True)


def slugify(text):
    text = text.lower()
    for a, b in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("é", "e"), ("è", "e"), ("ı", "i"), ("ş", "s"), ("ç", "c"), ("ğ", "g"), ("ß", "ss")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:70]


def domain(url):
    host = urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


# ---------------------------------------------------------------- data

def load_routes():
    routes = json.loads((DATA / "data.json").read_text(encoding="utf-8"))
    base = len(routes)
    # new routes approved from the monthly refresh arrive one file each, so several
    # proposals can be merged in any order; the next refresh folds them into data.json
    incoming = sorted((DATA / "incoming").glob("*.json"))
    routes += [json.loads(p.read_text(encoding="utf-8")) for p in incoming]
    seen = set()
    for i, r in enumerate(routes):
        where = (f"data.json entry {i}" if i < base else f"data/incoming/{incoming[i - base].name}") + f" ({r.get('name', '?')})"
        for key in ("name", "flag", "country", "region", "funding", "deadline", "link", "kind", "scope"):
            if not isinstance(r.get(key), str) or not r[key].strip():
                fail(f"{where}: missing text field '{key}'")
        for key in ("name", "country", "funding", "deadline"):
            v = r.get(key) or ""
            if len(v) > 400 or re.search(r"[<>\x00-\x1f]", v):
                fail(f"{where}: '{key}' is too long or contains markup/control characters")
        for key in ("link", "check_url"):
            if r.get(key) and not URL_RE.match(r[key]):
                fail(f"{where}: '{key}' must be a plain https:// URL, got {r[key]!r}")
        if r.get("region") not in REGIONS:
            fail(f"{where}: unknown region {r.get('region')!r}")
        if r.get("kind") not in ("school", "program"):
            fail(f"{where}: kind must be school or program")
        if r.get("scope") not in ("all", "limited"):
            fail(f"{where}: scope must be all or limited")
        for key, allowed in (("types", TYPES), ("levels", LEVELS), ("fields", FIELDS)):
            vals = r.get(key)
            if not isinstance(vals, list) or not vals or any(v not in allowed for v in vals):
                fail(f"{where}: '{key}' must be a non-empty list drawn from {allowed}")
        if not r.get("rolling") and not r.get("tbc"):
            dls = r.get("deadlines")
            if not isinstance(dls, list) or not dls:
                fail(f"{where}: needs deadlines (or rolling: true, or tbc: true)")
            else:
                for dl in dls:
                    try:
                        dt.date(2024, int(dl["m"]), int(dl["d"]))  # 2024 is a leap year
                    except Exception:
                        fail(f"{where}: impossible deadline {dl!r}")
        if r.get("last_verified"):
            try:
                dt.date.fromisoformat(r["last_verified"])
            except ValueError:
                fail(f"{where}: last_verified must be YYYY-MM-DD")
        r["slug"] = r.get("slug") or slugify(r.get("name", f"route-{i}"))
        if not re.match(r"^[a-z0-9-]{2,70}$", r["slug"]):
            fail(f"{where}: bad slug {r['slug']!r}")
        if r["slug"] in seen:
            fail(f"{where}: duplicate slug {r['slug']}")
        seen.add(r["slug"])
    return routes


def load_schools(route_names):
    schools = json.loads((DATA / "schools.json").read_text(encoding="utf-8"))
    for i, s in enumerate(schools):
        where = f"schools.json entry {i} ({s.get('name', '?')})"
        for key in ("name", "country", "city", "region"):
            v = s.get(key)
            if not isinstance(v, str) or not v.strip() or re.search(r"[<>\x00-\x1f]", v):
                fail(f"{where}: bad '{key}'")
        if not URL_RE.match(s.get("link") or ""):
            fail(f"{where}: link must be a plain https:// URL")
        if s.get("atlasName") and s["atlasName"] not in route_names:
            fail(f"{where}: atlasName {s['atlasName']!r} matches no route")
    return schools


def next_deadline(route, today=TODAY):
    if route.get("rolling"):
        return None, None, "rolling"
    if route.get("tbc"):
        return None, None, "tbc"
    best = None
    for dl in route["deadlines"]:
        m, d = int(dl["m"]), int(dl["d"])
        for year in (today.year, today.year + 1):
            try:
                cand = dt.date(year, m, d)
            except ValueError:  # 29 Feb in a non-leap year
                cand = dt.date(year, m, 28)
            if cand >= today:
                break
        if best is None or cand < best:
            best = cand
    days = (best - today).days
    return best, days, ("approaching" if days <= HORIZON_DAYS else "closed")


def fmt_date(d, year=True):
    return f"{d.strftime('%b')} {d.day}, {d.year}" if year else f"{d.strftime('%b')} {d.day}"


def status_chip(route):
    nd, days, status = next_deadline(route)
    approx = "≈ " if route.get("approx") else ""
    if status == "rolling":
        text = "Rolling · open year-round"
    elif status == "tbc":
        return '<span class="chip closed">Next call not yet announced</span>'
    elif status == "approaching":
        text = f"Due {approx}in {days} day{'s' if days != 1 else ''}"
    else:
        text = f"Closed · next {approx}{fmt_date(nd)}"
    attrs = f' data-dl="{e(json.dumps(route.get("deadlines") or []))}"'
    if route.get("rolling"):
        attrs += ' data-rolling="1"'
    if route.get("approx"):
        attrs += ' data-approx="1"'
    return f'<span class="chip {status}"{attrs}>{e(text)}</span>'


# ---------------------------------------------------------------- content

FRONT = re.compile(r"^\s*<!--\s*(\{.*?\})\s*-->\s*", re.S)


def load_html_docs(folder):
    docs = []
    for path in sorted((CONTENT / folder).glob("*.html")):
        raw = path.read_text(encoding="utf-8")
        m = FRONT.match(raw)
        if not m:
            fail(f"{path.name}: missing <!--{{json front matter}}-->")
            continue
        meta = json.loads(m.group(1))
        meta["slug"] = path.stem
        meta["body"] = raw[m.end():]
        for key in ("title", "description"):
            if not meta.get(key):
                fail(f"{path.name}: front matter needs '{key}'")
        docs.append(meta)
    return docs


def load_json_docs(folder):
    out = {}
    for path in sorted((CONTENT / folder).glob("*.json")):
        out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return out


LINK_MD = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def inline(text, root):
    """Escape plain text from route notes, then allow [label](url) links only."""
    out, pos = [], 0
    for m in LINK_MD.finditer(text):
        out.append(e(text[pos:m.start()]))
        label, url = m.group(1), m.group(2)
        if url.startswith("{{root}}"):
            out.append(f'<a href="{e(root + url[8:])}">{e(label)}</a>')
        elif URL_RE.match(url):
            out.append(f'<a href="{e(url)}" rel="noopener" target="_blank">{e(label)}</a>')
        else:
            fail(f"route note link not allowed: {url}")
            out.append(e(label))
        pos = m.end()
    out.append(e(text[pos:]))
    return "".join(out).replace("**", "")


def words(html_text):
    return len(re.sub(r"<[^>]+>", " ", html_text).split())


# ---------------------------------------------------------------- page shell

ASSET_VERSIONS = {}


def asset(root, name):
    return f"{root}assets/{name}?v={ASSET_VERSIONS.get(name, '0')}"


MENU_GUIDES = []     # filled in main(): the featured guides, shown in the menu under short labels
MENU_COUNTRIES = []  # filled in main(): (slug, name) for every country guide
MENU_ICON = '<span class="menu-icon" aria-hidden="true"><i></i><i></i><i></i></span>'
THEME_SWITCH = ('<div class="menu-theme" hidden><span>Theme</span><div class="theme-seg" role="group" aria-label="Colour theme">'
                '<button type="button" data-theme-choice="dark" aria-pressed="true">Dark</button>'
                '<button type="button" data-theme-choice="light" aria-pressed="false">Light</button></div></div>')
# runs before the first paint so a saved light theme never flashes dark
THEME_BOOT = ("try{var t=localStorage.getItem('sd-theme');if(t==='light'){document.documentElement.setAttribute('data-theme','light');"
              "document.querySelector('meta[name=theme-color]').setAttribute('content','#F3F5F9')}}catch(e){}")


def menu_html(root, here):
    """The single Menu button and its panel: one short list, with Countries, Guides and About as drop-downs."""
    def link(href, label):
        cur = ' aria-current="page"' if href == here else ""
        return f'<li><a href="{root + href if href else root or "./"}"{cur}>{e(label)}</a></li>'

    def drop(label, items):
        opened = " open" if any(h == here for h, _ in items) or any(here.startswith(h) for h, _ in items[-1:]) else ""
        return (f'<li><details class="menu-sub"{opened}><summary>{label}</summary><ul>'
                + "".join(link(h, t) for h, t in items) + "</ul></details></li>")

    countries = [(f"countries/{slug}/", name) for slug, name in MENU_COUNTRIES] + [("countries/", "All countries \u2192")]
    guides = [(f"guides/{g['slug']}/", g.get("menu") or g["title"]) for g in MENU_GUIDES] + [("guides/", "All guides \u2192")]
    about = [("about/", "About us"), ("editorial-policy/", "How we verify"), ("updates/", "What's new"), ("contact/", "Contact")]
    return ('<details class="menu"><summary class="menu-btn">' + MENU_ICON + '<span class="menu-label">Menu</span></summary>'
            '<div class="menu-panel"><nav aria-label="Main"><ul class="menu-list">'
            + link("", "Home") + link("scholarships/", "Scholarship board") + link("routes/", "Funded routes")
            + link("deadlines/", "Deadlines") + link("positions/", "Funded positions") + link("news/", "Research news")
            + link("scholarships/#directory", "Schools directory")
            + drop("Countries", countries) + drop("Guides", guides) + drop("About", about)
            + "</ul></nav>" + THEME_SWITCH + "</div></details>")

SEAL = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 10v6M2 10l10-5 10 5-10 5z"/>'
        '<path d="M6 12v5c3 3 9 3 12 0v-5"/></svg>')
NL = "\n"
SHARE_ICON = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
              'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/>'
              '<circle cx="18" cy="19" r="3"/><path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4"/></svg>')


def share_box(path, title, text, noun="page"):
    """One Share button: the phone's own share sheet where there is one (site.js), otherwise a short list.
    The links are plain share URLs, so no third-party script ever loads."""
    url = BASE + path
    q = lambda s: quote(s, safe="")
    body = text + NL + NL + url
    links = [("WhatsApp", f"https://wa.me/?text={q(text + NL + url)}"),
             ("Facebook", f"https://www.facebook.com/sharer/sharer.php?u={q(url)}"),
             ("X", f"https://x.com/intent/post?text={q(text)}&url={q(url)}"),
             ("LinkedIn", f"https://www.linkedin.com/sharing/share-offsite/?url={q(url)}"),
             ("Telegram", f"https://t.me/share/url?url={q(url)}&text={q(text)}")]
    items = "".join(f'<li><a href="{e(h)}" target="_blank" rel="noopener noreferrer">{label}</a></li>' for label, h in links)
    items += f'<li><a href="{e("mailto:?subject=" + q(title) + "&body=" + q(body))}">Email</a></li>'
    items += '<li><button type="button" data-share-copy hidden>Copy link</button></li>'
    return (f'<details class="share" data-share data-url="{e(url)}" data-title="{e(title)}" data-text="{e(text)}">'
            f'<summary class="share-btn">{SHARE_ICON}<span>Share</span></summary>'
            f'<div class="share-panel"><p class="share-h">Share this {noun}</p><ul>{items}</ul></div></details>')


def and_list(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


EXT = ('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
       'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>')


def adsense_client():
    c = (CONFIG.get("adsense") or {}).get("client", "").strip()
    if c and not re.match(r"^ca-pub-\d{10,20}$", c):
        fail(f"site.json adsense.client must look like ca-pub-1234567890123456, got {c!r}")
        return ""
    return c


def ad_bay(kind, wide=False):
    """One labelled ad bay, or nothing when AdSense is not configured."""
    client = adsense_client()
    slot = ((CONFIG.get("adsense") or {}).get("slots") or {}).get(kind, "").strip()
    cls = "ad-bay" + (" wide" if wide else "")
    if client and slot:
        if not re.match(r"^\d{6,20}$", slot):
            fail(f"site.json adsense slot '{kind}' must be digits, got {slot!r}")
            return ""
        fmt = 'data-ad-format="auto" data-full-width-responsive="true"' if kind != "rail" else 'data-ad-format="vertical"'
        return (f'<aside class="{cls}" aria-label="Advertisements"><div class="ad-label">Advertisements</div>'
                f'<ins class="ad-fill" data-sd-ad style="display:block" data-ad-client="{e(client)}" '
                f'data-ad-slot="{e(slot)}" {fmt}></ins></aside>')
    if ARGS.preview_ads:
        size = "300 × 600 · desktop only, scrolls with the page" if kind == "rail" else "responsive · holds its height, collapses if unfilled"
        return (f'<aside class="{cls} preview" aria-label="Advertisements"><div class="ad-label">Advertisements</div>'
                f'<div class="ad-fill">Ad space ({e(kind)})<br>{e(size)}</div></aside>')
    return ""


def page(path, title, description, body, *, root, nav=None, body_class="", jsonld=None, extra_head="",
         scripts=(), og_type="website", noindex=False, ads=True):
    canonical = BASE + path
    full_title = title if title.endswith(SITE) or title.startswith(SITE) else f"{title} · {SITE}"
    client = adsense_client() if ads else ""
    head_ads = ""
    if client:
        head_ads = (f'<meta name="google-adsense-account" content="{e(client)}">\n'
                    f'<script async nonce="{NONCE}" crossorigin="anonymous" '
                    f'src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={e(client)}"></script>\n')
    ld = ""
    for block in (jsonld or []):
        ld += '<script type="application/ld+json">' + json.dumps(block, ensure_ascii=False).replace("</", "<\\/") + "</script>\n"
    nav_html = menu_html(root, path)
    privacy_btn = ('<li><button type="button" class="linkish" data-privacy-choices hidden>Privacy choices</button></li>'
                   if adsense_client() else "")
    script_tags = "".join(f'<script nonce="{NONCE}" src="{src}"></script>\n' for src in
                          [asset(root, "site.js")] + list(scripts))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{CSP}">
<meta name="referrer" content="strict-origin-when-cross-origin">
<title>{e(full_title)}</title>
<meta name="description" content="{e(description)}">
<link rel="canonical" href="{e(canonical)}">
{'<meta name="robots" content="noindex">' if noindex else ''}
<meta name="theme-color" content="#0B1322">
<script nonce="{NONCE}">{THEME_BOOT}</script>
<meta property="og:site_name" content="{e(SITE)}">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(description)}">
<meta property="og:type" content="{og_type}">
<meta property="og:url" content="{e(canonical)}">
<meta property="og:image" content="{BASE}assets/og.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="{root}assets/favicon.svg" type="image/svg+xml">
<link rel="icon" href="{root}assets/favicon-32.png" sizes="32x32" type="image/png">
<link rel="apple-touch-icon" href="{root}assets/apple-touch-icon.png">
<link rel="stylesheet" href="{asset(root, 'site.css')}">
{extra_head}{head_ads}{ld}</head>
<body class="{body_class}">
<a class="skip" href="#main">Skip to content</a>
<header class="topbar"><div class="wrap">
  <a class="brand" href="{root or './'}" aria-label="{e(SITE)} home"><span class="brand-mark">{SEAL}</span><span class="brand-name">Degree<b>Step</b></span></a>
  {nav_html}
</div></header>
<main id="main">
{body}
</main>
<footer class="sitefoot"><div class="wrap">
  <div class="foot-grid">
    <div class="foot-brand">
      <a class="brand" href="{root or './'}"><span class="brand-mark">{SEAL}</span><span class="brand-name">Degree<b>Step</b></span></a>
      <p>Independent listings of tuition-free universities and fully funded scholarships. We are not affiliated with any university or scholarship provider.</p>
    </div>
    <div><h2>Explore</h2><ul>
      <li><a href="{root}scholarships/">Scholarship board</a></li><li><a href="{root}routes/">All funded routes</a></li>
      <li><a href="{root}deadlines/">Deadline calendar</a></li><li><a href="{root}positions/">Funded positions</a></li>
      <li><a href="{root}news/">Research news</a></li>
      <li><a href="{root}updates/">What's new</a></li>
      <li><a href="{root}countries/">Countries</a></li><li><a href="{root}guides/">Guides</a></li></ul></div>
    <div><h2>About</h2><ul>
      <li><a href="{root}about/">About us</a></li><li><a href="{root}editorial-policy/">How we verify</a></li>
      <li><a href="{root}contact/">Contact &amp; corrections</a></li></ul></div>
    <div><h2>Legal</h2><ul>
      <li><a href="{root}privacy/">Privacy policy</a></li><li><a href="{root}terms/">Terms of use</a></li>{privacy_btn}</ul></div>
  </div>
  <div class="foot-legal"><span>© {TODAY.year} {e(SITE)}. Always confirm details on the official page before you apply.</span><span>Data last checked {e(LAST_CHECK)}</span></div>
</div></footer>
{script_tags}</body>
</html>
"""


def write(path, text):
    target = OUT / path / "index.html" if (path == "" or path.endswith("/")) else OUT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    PAGES.append(path)


def crumbs(root, items):
    parts = [f'<a href="{root or "./"}">Home</a>']
    for href, label in items:
        parts.append('<span aria-hidden="true">›</span>')
        parts.append(f'<a href="{root}{href}">{e(label)}</a>' if href else f"<span>{e(label)}</span>")
    return '<nav class="crumbs" aria-label="Breadcrumb">' + "".join(parts) + "</nav>"


def breadcrumb_ld(items):
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": name, "item": BASE + href}
                                for i, (href, name) in enumerate(items)]}


def tags_html(types):
    return '<div class="tags">' + "".join(
        f'<span class="tag" style="--tc:{TYPE_COLORS[t]}">{e(t)}</span>' for t in types) + "</div>"


def route_row(r, root):
    return (f'<a class="row" href="{root}routes/{r["slug"]}/"><div><div class="row-name">{e(r["name"])}</div>'
            f'<div class="row-meta">{e(r["flag"])} {e(r["country"])} · {e(" · ".join(r["levels"]))} · {e(", ".join(r["types"]))}</div></div>'
            f'<div class="row-side">{status_chip(r)}<span class="row-arrow">Details →</span></div></a>')


# ---------------------------------------------------------------- pages

def build_board(routes, schools):
    """The live board: search, filters, the departures board and the schools directory."""
    root = "../"
    tpl = (SRC / "board.html").read_text(encoding="utf-8")
    config_json = json.dumps({"root": root, "premium": {
        "gumroadProductId": (CONFIG.get("premium") or {}).get("gumroad_product_id", ""),
        "purchaseUrl": (CONFIG.get("premium") or {}).get("purchase_url", "")}}).replace("</", "<\\/")

    def card(r):
        return (f'<article class="card slim in ready"><div class="country">{e(r["flag"])} {e(r["country"])}</div>'
                f'<h3><a class="card-title" href="{root}routes/{r["slug"]}/">{e(r["name"])}</a></h3>'
                f'<span class="card-go" aria-hidden="true">→</span></article>')

    uni = [r for r in routes if r["kind"] != "program"]
    prog = [r for r in routes if r["kind"] == "program"]
    first = (uni + prog)[:PER_PAGE_ALL]  # the board's first page; the rest arrive through the pager
    grid = ""
    for kind, label in (("school", "Universities"), ("program", "Scholarship programmes")):
        cards = [r for r in first if (r["kind"] == "program") == (kind == "program")]
        if cards:
            grid += f'<div class="grid-sep">{label}</div>' + "".join(map(card, cards))
    dsorted = sorted(schools, key=lambda s: (s["country"], s["name"]))
    dir_rows = "".join(
        f'<div class="dir-row in"><span class="dname">{e(s["flag"])} {e(s["name"])}</span><span class="dloc">{e(s["city"])} · {e(s["country"])}</span>'
        f'<span class="dact"><a class="dvisit" href="{e(s["link"])}" target="_blank" rel="noopener noreferrer" aria-label="Visit site: {e(s["name"])}">'
        f'<span class="vtxt">Visit site</span> {EXT}</a></span></div>'
        for s in dsorted[:PER_PAGE_DIR])
    body = (tpl.replace("{{CONFIG_JSON}}", config_json)
            .replace("{{GRID}}", grid)
            .replace("{{DIR_ROWS}}", dir_rows)
            .replace("{{AD_HOME}}", ad_bay("between", wide=True))
            .replace("{{N_ROUTES}}", str(len(routes)))
            .replace("{{N_SCHOOLS}}", str(len(schools)))
            .replace("{{N_COUNTRIES}}", str(len({r["country"] for r in routes})))
            .replace("{{LAST_CHECK}}", e(LAST_CHECK))
            .replace("{{ROOT}}", root))
    jsonld = [breadcrumb_ld([("", "Home"), ("scholarships/", "Scholarship board")])]
    title = "Scholarship board: live deadlines for funded study abroad"
    desc = (f"Search and filter {len(routes)} tuition-free universities and fully funded scholarships by level, field, region "
            f"and funding type, with live deadline countdowns and a directory of {len(schools)} schools.")
    write("scholarships/", page("scholarships/", title, desc, body, root=root, nav="scholarships/", body_class="home", jsonld=jsonld,
                               extra_head=f'<link rel="stylesheet" href="{asset(root, "app.css")}">\n',
                               scripts=[f"{root}data/data.js?v=" + DATA_VERSION, f"{root}data/schools.js?v=" + DATA_VERSION,
                                        asset(root, "app.js")]))


def load_updates(route_slugs):
    """content/updates.json: what changed on the site, newest first. Shown on the home page and at /updates/."""
    path = CONTENT / "updates.json"
    if not path.exists():
        return []
    items = json.loads(path.read_text(encoding="utf-8"))
    for i, u in enumerate(items):
        where = f"content/updates.json entry {i}"
        try:
            dt.date.fromisoformat(u.get("date", ""))
        except ValueError:
            fail(f"{where}: date must be YYYY-MM-DD")
        for key, limit in (("title", 120), ("text", 400)):
            v = u.get(key)
            if not isinstance(v, str) or not v.strip() or len(v) > limit or re.search(r"[<>\x00-\x1f]", v):
                fail(f"{where}: '{key}' must be plain text of at most {limit} characters")
        if u.get("slug") and u["slug"] not in route_slugs:
            fail(f"{where}: slug {u['slug']!r} matches no route")
    return sorted(items, key=lambda u: u.get("date", ""), reverse=True)


def update_item(u, root):
    d = dt.date.fromisoformat(u["date"])
    title = (f'<a href="{root}routes/{u["slug"]}/">{e(u["title"])}</a>' if u.get("slug") else e(u["title"]))
    return (f'<li class="lp-upd"><time datetime="{u["date"]}">{fmt_date(d)}</time>'
            f'<div><h3>{title}</h3><p>{e(u["text"])}</p></div></li>')


# The home page's "Choose your path" tabs. Each tab's list and count use the same rule as
# the board link it ends with, so "See all 35" opens a board showing 35.
PATHS = [
    ("masters", "Master's", "Funded and tuition-free master's degrees",
     "Scholarships that pay tuition and living costs, and universities that charge international students little or nothing.",
     lambda r: "Master's" in r["levels"], "level=Master%27s"),
    ("phd", "PhD", "Funded PhDs and doctoral grants",
     "Doctoral scholarships with a monthly stipend, and salaried PhD positions.",
     lambda r: "PhD" in r["levels"], "level=PhD"),
    ("bachelors", "Bachelor's", "Funded bachelor's degrees",
     "Undergraduate scholarships and universities that give need-based aid to international students.",
     lambda r: "Bachelor's" in r["levels"], "level=Bachelor%27s"),
    ("tuition-free", "Tuition-free", "Tuition-free universities",
     "Public universities where international students pay no tuition, only a small semester fee.",
     lambda r: r["kind"] != "program" and "Tuition-Free" in r["types"], "kind=Universities&type=Tuition-Free"),
    ("programmes", "Scholarship programmes", "Government and foundation scholarships",
     "National and foundation programmes that fund you at the university of your choice.",
     lambda r: r["kind"] == "program", "kind=Programmes"),
]


def by_urgency(routes):
    """Approaching deadlines first, soonest first; then everything else by name."""
    def key(r):
        nd, days, status = next_deadline(r)
        return (0, days, r["name"]) if status == "approaching" else (1, 0, r["name"])
    return sorted(routes, key=key)


POSITION_LEVELS = ["PhD", "Postdoc", "Research assistant"]
POSITION_DAYS = 60  # a listing comes down at its deadline, or 60 days after we posted it
# Each position has its own page, written in our own words from the official posting: the paragraphs
# about the post, then bulleted lists. "tasks" may be empty; the rest need at least one entry.
POSITION_SECTIONS = (("about", "About the position", 1, 4, 900), ("tasks", "What you will do", 0, 8, 600),
                     ("requirements", "Who can apply", 1, 8, 600), ("offer", "What is offered", 1, 8, 600),
                     ("apply", "How to apply", 1, 8, 600))


def load_positions():
    """content/positions.json: funded PhD and research openings, each with the write-up for its own page."""
    path = CONTENT / "positions.json"
    if not path.exists():
        return []
    items = json.loads(path.read_text(encoding="utf-8"))
    seen = set()
    for i, p in enumerate(items):
        where = f"content/positions.json entry {i} ({p.get('id', '?')})"
        for key, limit in (("id", 80), ("title", 140), ("institution", 120), ("country", 60), ("flag", 8),
                           ("funding", 200), ("summary", 500), ("source", 60)):
            v = p.get(key)
            if not isinstance(v, str) or not v.strip() or len(v) > limit or re.search(r"[<>\x00-\x1f]", v):
                fail(f"{where}: '{key}' must be plain text of at most {limit} characters")
        if not re.match(r"^[a-z0-9-]+$", p.get("id") or ""):
            fail(f"{where}: 'id' must be lowercase letters, digits and hyphens")
        if p.get("id") in seen:
            fail(f"{where}: duplicate id")
        seen.add(p.get("id"))
        if p.get("level") not in POSITION_LEVELS:
            fail(f"{where}: 'level' must be one of {POSITION_LEVELS}")
        if p.get("field") not in FIELDS:
            fail(f"{where}: 'field' must be one of {FIELDS}")
        if not URL_RE.match(p.get("link") or ""):
            fail(f"{where}: 'link' must be a plain https:// URL")
        for key in ("deadline", "posted"):
            try:
                dt.date.fromisoformat(p.get(key) or "")
            except ValueError:
                fail(f"{where}: '{key}' must be YYYY-MM-DD")
        d = p.get("details")
        if not isinstance(d, dict) or set(d) - {k for k, *_ in POSITION_SECTIONS}:
            fail(f"{where}: 'details' must hold {', '.join(k for k, *_ in POSITION_SECTIONS)}")
            continue
        for key, _, least, most, limit in POSITION_SECTIONS:
            v = d.get(key, [])
            if (not isinstance(v, list) or not least <= len(v) <= most
                    or any(not isinstance(x, str) or not x.strip() or len(x) > limit or re.search(r"[<>\x00-\x1f]", x) for x in v)):
                fail(f"{where}: details.{key} must be {least} to {most} plain-text entries of at most {limit} characters")
    return items


def open_positions(items, today=None):
    """The listings still open today, soonest deadline first."""
    today = today or TODAY
    live = []
    for p in items:
        deadline, posted = dt.date.fromisoformat(p["deadline"]), dt.date.fromisoformat(p["posted"])
        if deadline >= today and (today - posted).days <= POSITION_DAYS:
            live.append(p)
    return sorted(live, key=lambda p: (p["deadline"], p["title"]))


def closes_text(deadline):
    days = (deadline - TODAY).days
    return "Closes today" if days == 0 else ("Closes tomorrow" if days == 1 else f"Closes in {days} days")


def position_card(p):
    d = dt.date.fromisoformat(p["deadline"])
    return f"""<article class="pos" id="pos-{e(p['id'])}" data-level="{e(p['level'])}" data-field="{e(p['field'])}">
  <div class="pos-top"><span class="pos-level">{e(p['level'])}</span><span class="pos-field">{e(p['field'])}</span><span class="chip approaching" data-closes="{p['deadline']}">{closes_text(d)}</span></div>
  <h3><a href="{e(p['id'])}/">{e(p['title'])}</a></h3>
  <p class="pos-where">{e(p['flag'])} {e(p['institution'])} · {e(p['country'])}</p>
  <p class="pos-sum">{e(p['summary'])}</p>
  <dl class="pos-meta"><div><dt>Funding</dt><dd>{e(p['funding'])}</dd></div><div><dt>Deadline</dt><dd>{fmt_date(d)}</dd></div></dl>
  <p class="pos-foot"><a class="btn btn-line" href="{e(p['id'])}/">Full details →</a><span>Listed {fmt_date(dt.date.fromisoformat(p['posted']))}</span></p>
</article>"""


def post_position_link():
    subject = "A funded position for DegreeStep"
    body = ("Position title:\nUniversity and department:\nCountry:\nLevel (PhD / Postdoc / Research assistant):\n"
            "Field:\nFunding (salary or stipend, and for how long):\nApplication deadline:\n"
            "Link to the official posting on your institution's website:\nYour name and role:\n\n"
            "Please send this from your university email address.")
    return f"mailto:{CONFIG['contact_email']}?subject={quote(subject)}&body={quote(body)}"


def build_positions(items):
    root = "../"
    live = open_positions(items)
    countries = sorted({p["country"] for p in live})
    fields = [f for f in FIELDS if any(p["field"] == f for p in live)]
    cards = "".join(position_card(p) for p in live) or (
        '<p class="lede">No funded positions are open right now. New ones are added regularly; see '
        f'<a href="{root}updates/">what\'s new</a>.</p>')
    levels = [lv for lv in POSITION_LEVELS if any(p["level"] == lv for p in live)]
    filters = ("" if len(live) < 4 else
               '<div class="pos-filters" hidden><div class="seg" role="group" aria-label="Position type">'
               + "".join(f'<button type="button" data-level="{e(lv)}">{e(lv)}</button>' for lv in ["All"] + levels)
               + '</div><label class="pos-fieldsel"><span class="sr">Field</span><select aria-label="Field">'
               + "".join(f'<option value="{e(f)}">{e(f)}</option>' for f in ["All fields"] + fields)
               + '</select></label><span class="pos-count" aria-live="polite"></span></div>')
    where = and_list(countries) if countries else "several countries"
    body = f"""<div class="wrap"><header class="page-head">{crumbs(root, [("", "Funded positions")])}
<p class="eyebrow-s">Funded positions · {len(live)} open</p><h1>Funded PhD and research positions</h1>
<p class="lede">Salaried PhD posts, doctoral fellowships and postdocs. In much of northern Europe a PhD is a paid job with a salary, so you apply for the position itself rather than for a separate scholarship.</p>
<div class="byline"><span>Open now in <b>{e(where)}</b></span><span>Listings come down at their deadline</span></div></header>
{filters}
<div class="pos-list">{cards}</div>
{ad_bay("between", wide=True)}
<section class="pos-post" id="post">
  <h2>Post a funded position, free</h2>
  <p>Are you a professor, research group leader or university recruiter with a funded PhD, postdoc or research assistant position? Send it to us and we will list it here at no cost.</p>
  <ul>
    <li><b>What we need:</b> the title, institution, funding and its length, the deadline, and a link to the official posting on your institution's website.</li>
    <li><b>How we check it:</b> we confirm the position and its funding on that official page, and reply to your institutional email address before anything goes live.</li>
    <li><b>What we publish:</b> a page in our own words with the project, who can apply, the funding, the deadline and how to apply, linking to your posting. Applications always go through your own process.</li>
    <li><b>When it comes down:</b> at the application deadline, or after {POSITION_DAYS} days. Tell us if the position is filled earlier.</li>
  </ul>
  <p><a class="btn btn-primary" href="{e(post_position_link())}">Post a position by email</a></p>
  <p class="muted">We list only funded positions. We never ask candidates for fees, and you should be wary of anyone who does: see our <a href="{root}guides/scholarship-scams/">scam guide</a>.</p>
</section></div>"""
    for p in live:
        build_position_page(p, live)
    write("positions/", page("positions/", "Funded PhD and research positions",
                             f"{len(live)} open funded PhD, doctoral fellowship and postdoc positions, with salaries, deadlines and links.",
                             body, root=root, nav="positions/", body_class="positions-page",
                             extra_head=f'<link rel="stylesheet" href="{asset(root, "landing.css")}">\n',
                             scripts=[asset(root, "positions.js")],
                             jsonld=[breadcrumb_ld([("", "Home"), ("positions/", "Funded positions")])]))


def build_position_page(p, live):
    root = "../../"
    path = f"positions/{p['id']}/"
    d, deadline = p["details"], dt.date.fromisoformat(p["deadline"])
    sections = ""
    for key, heading, *_ in POSITION_SECTIONS:
        items = d.get(key) or []
        if not items:
            continue
        body = ("".join(f"<p>{e(x)}</p>" for x in items) if key == "about"
                else "<ul>" + "".join(f"<li>{e(x)}</li>" for x in items) + "</ul>")
        sections += f'<h2 id="{key}">{heading}</h2>{body}'
        if key == "requirements":
            sections += ad_bay("article")
    # more positions: the same field first, then the soonest deadlines
    others = sorted((o for o in live if o["id"] != p["id"]),
                    key=lambda o: (o["field"] != p["field"], o["deadline"], o["title"]))[:3]
    more = "".join(
        f'<a class="row" href="../{e(o["id"])}/"><div><div class="row-name">{e(o["title"])}</div>'
        f'<div class="row-meta">{e(o["flag"])} {e(o["institution"])} · {e(o["level"])} · {e(o["field"])}</div></div>'
        f'<div class="row-side"><span class="chip approaching" data-closes="{o["deadline"]}">'
        f'{closes_text(dt.date.fromisoformat(o["deadline"]))}</span></div></a>' for o in others)
    more_html = (f'<section class="pos-more"><h2>More funded positions</h2><div class="rows">{more}</div>'
                 f'<p><a href="../">All open positions →</a></p></section>') if others else ""
    body = f"""<div class="wrap">
<div class="page-head">
{crumbs(root, [("positions/", "Funded positions"), ("", p["title"])])}
<div class="eyebrow-s">{e(p["level"])} · {e(p["field"])}</div>
<h1>{e(p["title"])}</h1>
<p class="lede">{e(p["summary"])}</p>
</div>
<div class="layout has-rail"><div>
<section class="glance" aria-label="At a glance">
<dl class="glance-grid">
  <div><dt>Where</dt><dd>{e(p["flag"])} {e(p["institution"])}<span class="sub">{e(p["country"])}</span></dd></div>
  <div><dt>Deadline</dt><dd>{fmt_date(deadline)}<span class="sub"><span data-closes="{p["deadline"]}">{closes_text(deadline)}</span></span></dd></div>
  <div><dt>Funding</dt><dd>{e(p["funding"])}</dd></div>
  <div><dt>Position</dt><dd>{e(p["level"])}<span class="sub">{e(p["field"])}</span></dd></div>
</dl>
<div class="glance-cta"><a class="btn btn-line" href="#apply">How to apply ↓</a></div>
</section>
<article class="prose">
{sections}
</article>
<p class="pos-apply"><a class="btn btn-primary" href="{e(p["link"])}" target="_blank" rel="noopener">Apply on the official posting {EXT}</a></p>
{more_html}
</div><aside class="rail" aria-label="Advertisements">{ad_bay("rail")}</aside></div>
</div>"""
    desc = f"{p['title']} at {p['institution']}, {p['country']}. {p['funding']}. Deadline {fmt_date(deadline)}."
    if len(desc) > 300:
        desc = desc[:297].rsplit(" ", 1)[0] + "…"
    write(path, page(path, f"{p['title']}, {p['institution']}", desc, body, root=root, nav="positions/",
                     body_class="position-page", extra_head=f'<link rel="stylesheet" href="{asset(root, "landing.css")}">\n',
                     scripts=[asset(root, "positions.js")],
                     jsonld=[breadcrumb_ld([("", "Home"), ("positions/", "Funded positions"), (path, p["title"])])]))


NEWS_CATEGORIES = ["Award", "Breakthrough", "Funding", "Policy"]


def load_news():
    """content/news.json: research awards, breakthroughs and funding news. Each story has its own page, written in
    our own words with links to our own pages; its official source is credited at the end."""
    path = CONTENT / "news.json"
    if not path.exists():
        return []
    items = json.loads(path.read_text(encoding="utf-8"))
    seen = set()
    for i, n in enumerate(items):
        where = f"content/news.json entry {i} ({n.get('id', '?')})"
        for key, limit in (("id", 80), ("title", 160), ("summary", 900), ("why", 300), ("source_title", 160)):
            v = n.get(key)
            if not isinstance(v, str) or not v.strip() or len(v) > limit or re.search(r"[<>\x00-\x1f]", v):
                fail(f"{where}: '{key}' must be plain text of at most {limit} characters")
        if not re.match(r"^[a-z0-9-]+$", n.get("id") or "") or n.get("id") in seen:
            fail(f"{where}: 'id' must be unique lowercase letters, digits and hyphens")
        seen.add(n.get("id"))
        if n.get("category") not in NEWS_CATEGORIES:
            fail(f"{where}: 'category' must be one of {NEWS_CATEGORIES}")
        if not URL_RE.match(n.get("source_url") or ""):
            fail(f"{where}: 'source_url' must be a plain https:// URL")
        body = n.get("body")
        if (not isinstance(body, list) or not 2 <= len(body) <= 10
                or any(not isinstance(x, str) or not x.strip() or len(x) > 1500 or re.search(r"[<>\x00-\x1f]", x) for x in body)):
            fail(f"{where}: 'body' must be 2 to 10 plain-text paragraphs of at most 1,500 characters")
        else:
            for x in body:
                for m in LINK_MD.finditer(x):
                    if not re.match(r"^\{\{root\}\}[a-z0-9-]+(/[a-z0-9-]+)*/$", m.group(2)):
                        fail(f"{where}: body link {m.group(2)!r} must be a page on this site, written as {{{{root}}}}path/")
        try:
            dt.date.fromisoformat(n.get("date") or "")
        except ValueError:
            fail(f"{where}: 'date' must be YYYY-MM-DD")
        if n.get("art") and n["art"] not in NEWS_ART:
            fail(f"{where}: 'art' must be one of {sorted(NEWS_ART)}")
        for im in n.get("images") or []:
            f = im.get("file") or ""
            if not re.match(r"^news-[a-z0-9-]+\.jpg$", f) or not (SRC / "assets" / f).exists():
                fail(f"{where}: image {f!r} must be an existing src/assets/news-*.jpg")
            if im.get("kind") not in ("portrait", "wide"):
                fail(f"{where}: image 'kind' must be portrait or wide")
            if "focus" in im and (not isinstance(im["focus"], int) or not 0 <= im["focus"] <= 100):
                fail(f"{where}: image 'focus' must be a whole number from 0 (top) to 100 (bottom)")
            for key in ("name", "credit", "license"):
                v = im.get(key)
                if not isinstance(v, str) or not v.strip() or len(v) > 200 or re.search(r"[<>\x00-\x1f]", v):
                    fail(f"{where}: image '{key}' must be plain text")
            if not IMAGE_LICENCE.match(im.get("license") or ""):
                fail(f"{where}: image licence {im.get('license')!r} is not one we may use (CC0, public domain, CC BY or CC BY-SA)")
            for key in ("source_url", "license_url"):
                if (key == "source_url" or im.get(key)) and not URL_RE.match(im.get(key) or ""):
                    fail(f"{where}: image '{key}' must be a plain https:// URL")
    # newest first; items from the same day keep the order they were written in
    return sorted(items, key=lambda n: n.get("date", ""), reverse=True)


def _svg(inner):
    return (f'<svg viewBox="0 0 160 100" width="160" height="100" fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{inner}</svg>')


def _network():
    layers = [(40, [25, 50, 75]), (80, [18, 39, 61, 82]), (120, [35, 65])]
    lines = "".join(f'<path d="M{x1} {y1}L{x2} {y2}" opacity=".45"/>'
                    for (x1, ys1), (x2, ys2) in zip(layers, layers[1:]) for y1 in ys1 for y2 in ys2)
    nodes = "".join(f'<circle cx="{x}" cy="{y}" r="5" fill="currentColor"/>' for x, ys in layers for y in ys)
    return _svg(lines + nodes)


def _atom():
    orbits = "".join(f'<ellipse cx="80" cy="50" rx="42" ry="14" transform="rotate({a} 80 50)"/>' for a in (0, 60, 120))
    return _svg(orbits + '<circle cx="80" cy="50" r="6" fill="currentColor"/>')


# Our own drawings, for news items with no freely licensed photo. Plain line art in the
# site's accent colour, so they suit both themes and raise no rights questions.
NEWS_ART = {
    "ai": _network(),
    "breakthrough": _atom(),
    "scholarship": _svg('<path d="M80 22 130 40 80 58 30 40Z"/><path d="M50 49v15c0 9 60 9 60 0V49"/>'
                        '<path d="M124 42v22"/><circle cx="124" cy="68" r="4" fill="currentColor"/>'),
    "award": _svg('<path d="M64 14l12 26M96 14 84 40"/><circle cx="80" cy="62" r="22"/><circle cx="80" cy="62" r="13"/>'
                  '<path d="M80 54l2.5 5 5.5.8-4 3.9 1 5.5-5-2.6-5 2.6 1-5.5-4-3.9 5.5-.8z" fill="currentColor"/>'),
}
NEWS_ART_FOR = {"Award": "award", "Breakthrough": "breakthrough", "Funding": "scholarship", "Policy": "scholarship"}
IMAGE_LICENCE = re.compile(r"^(CC0|Public domain|CC BY(-SA)? \d\.\d( [A-Z]{2,3})?)$")  # never NC or ND


def news_art(n):
    return NEWS_ART.get(n.get("art") or "") or NEWS_ART[NEWS_ART_FOR.get(n.get("category"), "award")]


def news_media(n, root):
    """Photos with their credits, or our own drawing when there are none."""
    imgs = n.get("images") or []
    if not imgs:
        return f'<div class="news-art">{news_art(n)}</div>'
    html_parts = ""
    for im in (i for i in imgs if i["kind"] == "wide"):
        html_parts += (f'<figure class="news-wide"><img src="{asset(root, im["file"])}" alt="{e(im["name"])}" loading="lazy" decoding="async">'
                       f'<figcaption>{e(im["name"])}</figcaption></figure>')
    people = [i for i in imgs if i["kind"] == "portrait"]
    if people:
        html_parts += '<div class="news-people">' + "".join(
            f'<figure><img src="{asset(root, im["file"])}" alt="{e(im["name"])}" loading="lazy" decoding="async">'
            f'<figcaption>{e(im["name"])}</figcaption></figure>' for im in people) + "</div>"

    def credit(im):
        who = f'<a href="{e(im["source_url"])}" target="_blank" rel="noopener">{e(im["credit"])}</a>'
        lic = (f'<a href="{e(im["license_url"])}" target="_blank" rel="noopener">{e(im["license"])}</a>'
               if im.get("license_url") else e(im["license"]))
        return (f'{e(im["name"])} by {who} ({lic})' if im["kind"] == "portrait" else f'{who} ({lic})')
    credits = "; ".join(credit(im) for im in imgs)
    return (f'<div class="news-media">{html_parts}'
            f'<p class="news-credit">{"Images" if len(imgs) > 1 else "Image"} via Wikimedia Commons, resized for the web: {credits}.</p></div>')


def news_thumb(n, root):
    imgs = n.get("images") or []
    if imgs:
        im = imgs[0]
        # "focus" moves the crop of a tall photo up or down, so the face stays in the short thumbnail
        focus = f' style="object-position:center {im["focus"]}%"' if "focus" in im else ""
        return f'<span class="lp-news-thumb"><img src="{asset(root, im["file"])}" alt="" loading="lazy" decoding="async"{focus}></span>'
    return f'<span class="lp-news-thumb art">{news_art(n)}</span>'


def news_card(n, root, href, summary=True):
    d = dt.date.fromisoformat(n["date"])
    return (f'<a class="lp-news-card" href="{href}">{news_thumb(n, root)}<span class="lp-news-meta"><span class="pos-level">{e(n["category"])}</span>'
            f'<time datetime="{n["date"]}">{fmt_date(d)}</time></span><span class="lp-news-title">{e(n["title"])}</span>'
            + (f'<span class="news-card-sum">{e(n["summary"])}</span><span class="news-card-more">Read the full story →</span>' if summary else "")
            + "</a>")


def news_inline(text, root, live_positions):
    """A story paragraph: plain text with [label]({{root}}path/) links to our own pages. A link to a position that
    has since closed keeps its words and loses the link, because that position's page is no longer built."""
    def keep(m):
        pos = re.match(r"^\{\{root\}\}positions/([a-z0-9-]+)/$", m.group(2))
        return m.group(1) if pos and pos.group(1) not in live_positions else m.group(0)
    return inline(LINK_MD.sub(keep, text), root)


def build_news_page(n, items, live_positions):
    root = "../../"
    path = f"news/{n['id']}/"
    d = dt.date.fromisoformat(n["date"])
    paras = [f"<p>{news_inline(x, root, live_positions)}</p>" for x in n["body"]]
    paras.insert(min(2, len(paras)), ad_bay("article"))
    others = [o for o in items if o["id"] != n["id"]][:3]
    more = (f'<section class="news-more"><h2>More research news</h2><div class="lp-news-grid">'
            + "".join(news_card(o, root, f'../{e(o["id"])}/', summary=False) for o in others)
            + '</div><p><a href="../">All research news →</a></p></section>') if others else ""
    body = f"""<div class="wrap">
<div class="page-head">
{crumbs(root, [("news/", "Research news"), ("", n["title"])])}
<div class="eyebrow-s">{e(n["category"])} · <time datetime="{n["date"]}">{fmt_date(d)}</time></div>
<h1>{e(n["title"])}</h1>
<p class="lede">{e(n["summary"])}</p>
</div>
<div class="layout has-rail"><div>
<div class="news-story-media">{news_media(n, root)}</div>
<article class="prose">
{"".join(paras)}
<div class="callout"><b>Why it matters:</b> {e(n["why"])}</div>
</article>
<p class="news-story-src">Source: <a href="{e(n["source_url"])}" target="_blank" rel="noopener">{e(n["source_title"])}</a></p>
{more}
</div><aside class="rail" aria-label="Advertisements">{ad_bay("rail")}</aside></div>
</div>"""
    desc = n["summary"] if len(n["summary"]) <= 300 else n["summary"][:297].rsplit(" ", 1)[0] + "…"
    article_ld = {"@context": "https://schema.org", "@type": "NewsArticle", "headline": n["title"], "description": desc,
                  "datePublished": n["date"], "dateModified": n["date"], "mainEntityOfPage": BASE + path,
                  "author": {"@type": "Organization", "name": SITE, "url": BASE},
                  "publisher": {"@type": "Organization", "name": SITE, "url": BASE,
                                "logo": {"@type": "ImageObject", "url": BASE + "assets/apple-touch-icon.png"}}}
    if n.get("images"):
        article_ld["image"] = [BASE + "assets/" + im["file"] for im in n["images"]]
    write(path, page(path, n["title"], desc, body, root=root, nav="news/", body_class="news-story",
                     extra_head=f'<link rel="stylesheet" href="{asset(root, "landing.css")}">\n', og_type="article",
                     jsonld=[breadcrumb_ld([("", "Home"), ("news/", "Research news"), (path, n["title"])]), article_ld]))


def build_news(items, live_positions):
    root = "../"
    for n in items:
        build_news_page(n, items, live_positions)
    months = {}
    for n in items:
        d = dt.date.fromisoformat(n["date"])
        months.setdefault(f"{MONTHS[d.month - 1]} {d.year}", []).append(n)
    sections = "".join(f'<h2 class="news-month">{e(m)}</h2><div class="news-list">'
                       + "".join(news_card(n, root, e(n["id"]) + "/") for n in ns) + "</div>"
                       for m, ns in months.items())
    body = f"""<div class="wrap"><header class="page-head">{crumbs(root, [("", "Research news")])}
<p class="eyebrow-s">Research and innovation</p><h1>Research and innovation news</h1>
<p class="lede">The year's major research prizes, discoveries and new funding for students and researchers, and why each one matters if you are planning a degree or a PhD.</p></header>
<div class="news-page">{sections or '<p class="lede">No news yet.</p>'}</div>
<div class="news-page-end">{ad_bay("between", wide=True)}</div></div>"""
    write("news/", page("news/", "Research and innovation news",
                        "Major research prizes, scientific breakthroughs and new funding for students and researchers, explained in plain English.",
                        body, root=root, nav="news/", body_class="news-page-body",
                        extra_head=f'<link rel="stylesheet" href="{asset(root, "landing.css")}">\n',
                        jsonld=[breadcrumb_ld([("", "Home"), ("news/", "Research news")])]))


def build_home(routes, schools, updates, positions, research):
    """The landing page: what DegreeStep is, the next deadline, the paths in, and what changed."""
    root = ""
    countries_n = len({r["country"] for r in routes})
    upcoming = []
    for r in routes:
        nd, days, status = next_deadline(r)
        if nd is not None and status == "approaching":
            upcoming.append((days, r["name"], r, nd))
    upcoming.sort(key=lambda t: (t[0], t[1]))
    soon30 = sum(1 for t in upcoming if t[0] <= 30)

    # the boarding pass: the next deadline, recounted live by landing.js
    pass_html = ""
    if upcoming:
        days, _, r, nd = upcoming[0]
        approx = "≈ " if r.get("approx") else ""
        when = "Today" if days == 0 else ("Tomorrow" if days == 1 else f"In {days} days")
        frm = "Any country" if r["scope"] == "all" else "Eligible countries"
        pass_html = f"""<div class="pass-wrap">
  <a class="pass" href="routes/{r['slug']}/">
    <span class="pass-top"><span>Boarding pass</span><span>Next deadline</span></span>
    <span class="pass-name">{e(r['name'])}</span>
    <span class="pass-grid">
      <span><small>From</small>{e(frm)}</span>
      <span><small>To</small>{e(r['flag'])} {e(r['country'])}</span>
      <span><small>Closes</small>{approx}{fmt_date(nd)}</span>
      <span><small>Boarding</small><b class="hot" data-due="{nd.isoformat()}">{when}</b></span>
    </span>
    <span class="pass-stub"><span class="pass-code" aria-hidden="true"></span><span class="pass-go">Open this route →</span></span>
  </a>
</div>"""

    why = [
        ('<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>', "Straight to the official page",
         "Every route links to the university, government or foundation page where you apply."),
        ('<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5M9 2h6"/>', "Live deadline countdowns",
         "See what closes next, save the routes you like, and add their deadlines to your calendar with reminders."),
        ('<path d="M4 4h16v16H4z"/><path d="M8 9h8M8 13h8M8 17h5"/>', "Costs and eligibility in plain English",
         "What each award covers, what you still pay, who qualifies and which documents to prepare, on one page per route."),
    ]
    why_html = "".join(
        f'<article class="lp-card"><span class="lp-icon"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{icon}</svg></span>'
        f'<h3>{e(t)}</h3><p>{e(txt)}</p></article>' for icon, t, txt in why)

    tabs, panels = [], []
    for pid, label, heading, text, rule, query in PATHS:
        picked = [r for r in routes if rule(r)]
        if not picked:
            continue
        tab_state = 'aria-selected="false" tabindex="-1"' if tabs else 'aria-selected="true"'
        tabs.append(f'<button type="button" role="tab" id="tab-{pid}" aria-controls="path-{pid}" {tab_state}>{e(label)}</button>')
        rows = "".join(route_row(r, root) for r in by_urgency(picked)[:4])
        panels.append(f"""<section class="lp-panel" id="path-{pid}" role="tabpanel" aria-labelledby="tab-{pid}">
  <div class="lp-panel-text">
    <p class="eyebrow-s">{e(label)} · {len(picked)} routes</p>
    <h3>{e(heading)}</h3>
    <p>{e(text)}</p>
    <a class="btn btn-primary" href="scholarships/?{query}">See all {len(picked)} on the board →</a>
  </div>
  <div class="rows">{rows}</div>
</section>""")

    soon_rows = "".join(route_row(t[2], root) for t in upcoming[:5])
    steps = [("Find a route", "Filter the scholarship board by study level, field, region and type of funding."),
             ("Check you qualify", "Each route page sets out eligibility, costs, documents and a timeline for applying."),
             ("Apply on the official site", "Save the route, add its deadline to your calendar, and apply before it closes.")]
    steps_html = "".join(f'<li><span class="lp-step-n">0{i + 1}</span><h3>{e(t)}</h3><p>{e(x)}</p></li>'
                         for i, (t, x) in enumerate(steps))
    news = "".join(update_item(u, root) for u in updates[:5])
    news_cards = "".join(news_card(n, root, f'news/{e(n["id"])}/', summary=False) for n in research[:3])
    news_html = (f"""<section class="lp-section wrap">
  <h2 class="lp-h2">Research and innovation news</h2>
  <div class="lp-news-grid">{news_cards}</div>
  <p class="lp-more center"><a href="news/">All research news →</a></p>
</section>""" if research else "")
    live_pos = open_positions(positions)
    pos_rows = "".join(
        f'<a class="row" href="positions/{e(p["id"])}/"><div><div class="row-name">{e(p["title"])}</div>'
        f'<div class="row-meta">{e(p["flag"])} {e(p["institution"])} · {e(p["level"])} · {e(p["field"])}</div></div>'
        f'<div class="row-side"><span class="chip approaching" data-closes="{p["deadline"]}">'
        f'{closes_text(dt.date.fromisoformat(p["deadline"]))}</span></div></a>' for p in live_pos[:4])
    pos_html = (f"""<section class="lp-section wrap">
  <h2 class="lp-h2">Funded PhD and research positions</h2>
  <div class="rows lp-pos">{pos_rows}</div>
  <p class="lp-more center"><a href="positions/">All {len(live_pos)} open positions →</a><span class="lp-dot" aria-hidden="true">·</span><a href="positions/#post">Post a position</a></p>
</section>""" if live_pos else "")
    programmes = sorted((r for r in routes if r["kind"] == "program"), key=lambda r: r["name"])
    prog_html = "".join(f'<li><a href="routes/{r["slug"]}/">{e(r["name"])}</a></li>' for r in programmes)

    faq = [("Is DegreeStep free?",
            "Yes. Every page, the board and the deadline calendar are free, and there is no account to create."),
           ("Can students from any country use it?",
            "Yes. DegreeStep is for international students from anywhere. Most routes are open to every nationality; "
            "the ones limited to certain countries say so on their page."),
           ("How do you check the information?",
            f"Every route page links to the official page its facts come from, and the data was last reviewed {LAST_CHECK}. "
            "Our How we verify page explains the process, and you can report a correction through the contact page."),
           ("Do you handle applications?",
            "No. You apply directly to the university or scholarship provider. DegreeStep is independent and not affiliated with any of them."),
           ("How often is it updated?",
            "Deadlines and amounts are re-checked every month, new routes are added as they are verified, and every change is listed on the What's new page.")]
    faq_html = "".join(f'<details class="lp-faq"><summary>{e(q)}</summary><p>{e(a)}</p></details>' for q, a in faq)

    body = f"""<section class="lp-hero"><div class="wrap lp-hero-grid">
  <div class="lp-hero-text">
    <p class="eyebrow-s">Funded study abroad · Europe · USA · Canada</p>
    <h1>The world’s scholarships, <em>one step away</em></h1>
    <p class="lede">{len(routes)} tuition-free universities and fully funded scholarships in {countries_n} countries, for international students from anywhere, with live deadline countdowns.</p>
    <div class="lp-cta"><a class="btn btn-primary btn-lg" href="scholarships/">View scholarships →</a></div>
  </div>
  <div class="lp-hero-side">
    <a class="lp-newsbtn" href="news/"><span class="lp-newsbtn-dot" aria-hidden="true"></span>Research news<span aria-hidden="true">→</span></a>
    {pass_html}
  </div>
</div></section>

<div class="wrap"><section class="lp-stats" aria-label="DegreeStep in numbers">
  <div><b>{len(routes)}</b><span>Funded routes</span></div>
  <div><b>{countries_n}</b><span>Countries</span></div>
  <div><b>{len(schools)}</b><span>Schools indexed</span></div>
  <div><b class="hot">{soon30}</b><span>Closing within 30 days</span></div>
</section></div>

<section class="lp-section wrap">
  <h2 class="lp-h2">Find funding you can trust</h2>
  <div class="lp-cards">{why_html}</div>
</section>

<section class="lp-paths"><div class="wrap">
  <h2 class="lp-h2">Choose your path</h2>
  <div class="lp-tabs" role="tablist" aria-label="Choose your path" hidden>{"".join(tabs)}</div>
  {"".join(panels)}
</div></section>

{ad_bay("between", wide=True)}

<section class="lp-section wrap lp-two">
  <div>
    <h2 class="lp-h2 left">Closing soon</h2>
    <div class="rows">{soon_rows}</div>
    <p class="lp-more"><a href="deadlines/">Full deadline calendar →</a></p>
  </div>
  <div>
    <h2 class="lp-h2 left">How it works</h2>
    <ol class="lp-steps">{steps_html}</ol>
  </div>
</section>

{pos_html}

{news_html}

<section class="lp-section wrap">
  <h2 class="lp-h2">What’s new</h2>
  <ul class="lp-news">{news}</ul>
  <p class="lp-more center"><a href="updates/">All updates →</a></p>
</section>

<section class="lp-section wrap">
  <h2 class="lp-h2">Programmes on DegreeStep</h2>
  <ul class="lp-progs">{prog_html}</ul>
</section>

<section class="lp-section wrap lp-faqs">
  <h2 class="lp-h2">Questions</h2>
  {faq_html}
</section>

<section class="lp-band"><div class="wrap">
  <h2>Ready to find your scholarship?</h2>
  <div class="lp-cta center"><a class="btn btn-primary btn-lg" href="scholarships/">View scholarships →</a><a class="btn btn-line btn-lg" href="deadlines/">See deadlines</a><a class="btn btn-line btn-lg" href="editorial-policy/">How we verify</a></div>
</div></section>"""

    jsonld = [{"@context": "https://schema.org", "@type": "WebSite", "name": SITE, "url": BASE,
               "description": CONFIG["tagline"]},
              {"@context": "https://schema.org", "@type": "Organization", "name": SITE, "url": BASE,
               "logo": BASE + "assets/apple-touch-icon.png", "email": CONFIG["contact_email"]},
              {"@context": "https://schema.org", "@type": "FAQPage",
               "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq]}]
    title = f"{SITE} — Tuition-Free & Fully Funded Study Abroad"
    desc = (f"{len(routes)} tuition-free universities and fully funded scholarships for international students in Europe, "
            f"the USA and Canada, with live deadline countdowns.")
    write("", page("", title, desc, body, root=root, nav="", body_class="landing", jsonld=jsonld,
                   extra_head=f'<link rel="stylesheet" href="{asset(root, "landing.css")}">\n',
                   scripts=[asset(root, "landing.js")]))


def build_updates(updates):
    root = "../"
    months = {}
    for u in updates:
        d = dt.date.fromisoformat(u["date"])
        months.setdefault(f"{MONTHS[d.month - 1]} {d.year}", []).append(u)
    sections = "".join(f'<h2>{e(m)}</h2><ul class="lp-news">{"".join(update_item(u, root) for u in items)}</ul>'
                       for m, items in months.items())
    body = f"""<div class="wrap"><header class="page-head">{crumbs(root, [("", "What's new")])}
<p class="eyebrow-s">Changelog</p><h1>What’s new on {e(SITE)}</h1>
<p class="lede">Every change to the routes and pages, newest first: new routes, corrected deadlines and amounts, and closed calls.</p></header>
<div class="layout"><div class="updates">{sections or '<p class="lede">No updates yet.</p>'}</div></div></div>"""
    write("updates/", page("updates/", f"What's new on {SITE}",
                           "A dated list of every change to DegreeStep: new funded routes, corrected deadlines and amounts, and closed scholarship calls.",
                           body, root=root, nav="updates/", body_class="updates-page",
                           extra_head=f'<link rel="stylesheet" href="{asset(root, "landing.css")}">\n',
                           jsonld=[breadcrumb_ld([("", "Home"), ("updates/", "What's new")])]))


def build_route_pages(routes, notes, country_pages):
    by_slug = {r["slug"]: r for r in routes}
    for r in routes:
        root = "../../"
        n = notes.get(r["slug"])
        if not n:
            if ARGS.draft:
                n = {}
            else:
                fail(f"content/routes/{r['slug']}.json is missing — every route needs its own write-up")
                continue
        nd, days, status = next_deadline(r)
        cslug = slugify(r["country"])
        has_country = cslug in country_pages
        crumb_items = [("routes/", "Routes")]
        if has_country:
            crumb_items.append((f"countries/{cslug}/", r["country"]))
        crumb_items.append(("", r["name"]))
        kind_text = ("The university itself funds you — you apply for admission and the funding together."
                     if r["kind"] == "school" else
                     "An external award — you win the scholarship and take it to a university that admits you.")
        scope_text = ("Any discipline may apply" if r["scope"] == "all" else "Only certain fields are eligible")
        fields_sub = "" if r["scope"] == "all" else f'<span class="sub">Eligible: {e(", ".join(r["fields"]))}</span>'
        next_text = ("Positions open year-round" if status == "rolling"
                     else "Not yet announced" if status == "tbc"
                     else f"{'≈ ' if r.get('approx') else ''}{fmt_date(nd)}")
        # one short line each, the way it reads in a WhatsApp chat; the link goes underneath
        share_when = ("Applications open year-round" if status == "rolling"
                      else "Next call not yet announced" if status == "tbc"
                      else f"Next deadline: {'around ' if r.get('approx') else ''}{fmt_date(nd)}")
        share_text = NL.join([f"\U0001F393 {r['name']}", f"{r['country']} · {and_list(r['types'])} · {and_list(r['levels'])}", share_when])
        glance = f"""<section class="glance" aria-label="At a glance">
<dl class="glance-grid">
  <div><dt>Funding</dt><dd>{tags_html(r["types"])}</dd></div>
  <div><dt>Status</dt><dd>{status_chip(r)}</dd></div>
  <div><dt>Typical deadline</dt><dd>{e(r["deadline"])}<span class="sub">Next: <span data-next>{e(next_text)}</span>{' · varies by course or country' if r.get('approx') else ''}</span></dd></div>
  <div><dt>Study levels</dt><dd>{e(" · ".join(r["levels"]))}</dd></div>
  <div><dt>Fields</dt><dd>{e(scope_text)}{fields_sub}</dd></div>
  <div><dt>Who pays</dt><dd>{e(kind_text)}</dd></div>
</dl>
<div class="glance-cta"><a class="btn btn-primary" href="{e(r["link"])}" target="_blank" rel="noopener noreferrer">Official page on {e(domain(r["link"])).replace(".", ".<wbr>")} {EXT}</a>
{share_box(f"routes/{r['slug']}/", r["name"], share_text, "opportunity")}
<small>Last verified {e(fmt_date(dt.date.fromisoformat(r["last_verified"])) if r.get("last_verified") else "—")}. Cycles shift each year — confirm dates on the official page.</small></div>
</section>"""

        def section(title, items, ordered=False):
            if not items:
                return ""
            tag = "ol" if ordered else "ul"
            return f"<h2>{e(title)}</h2><{tag}>" + "".join(f"<li>{inline(x, root)}</li>" for x in items) + f"</{tag}>"

        costs = n.get("costs") or []
        if not all(isinstance(c, list) and len(c) == 2 and all(isinstance(x, str) and x.strip() for x in c) for c in costs):
            fail(f"{r['slug']}: costs must be [what, who pays] pairs of text")
            costs = []
        costs_html = ('<h2>What you will still pay</h2><div class="table-wrap"><table><tr><th>Cost</th><th>Who pays</th></tr>'
                      + "".join(f"<tr><td>{inline(a, root)}</td><td>{inline(b, root)}</td></tr>" for a, b in costs)
                      + "</table></div>") if costs else ""
        docs = n.get("documents") or []
        if not all(isinstance(x, str) and x.strip() for x in docs):
            fail(f"{r['slug']}: documents must be a list of text")
            docs = []
        docs_html = ('<h2>Documents you will need</h2><ul class="checklist">'
                     + "".join(f"<li>{inline(x, root)}</li>" for x in docs) + "</ul>") if docs else ""
        faq = n.get("faq") or []
        if not all(isinstance(x, dict) and isinstance(x.get("q"), str) and isinstance(x.get("a"), str) and x["q"].strip() and x["a"].strip() for x in faq):
            fail(f"{r['slug']}: faq entries need a question (q) and an answer (a)")
            faq = []
        faq_html = ('<h2>Common questions</h2><div class="faq">' + "".join(
            f'<details><summary>{e(x["q"])}</summary><p>{inline(x["a"], root)}</p></details>' for x in faq) + "</div>") if faq else ""

        plan_html = ""
        if nd:
            steps = [(12, "Research the route in depth — confirm eligibility, costs and every required document"),
                     (10, "Ask your referees (for a PhD, email potential supervisors with a short, specific pitch)"),
                     (8, "Write the first full draft of your motivation letter or statement of purpose"),
                     (6, "Order transcripts and certified translations · book any language test"),
                     (4, "Second draft — get feedback from someone who reads critically"),
                     (2, "Finalise every document and complete the online form"),
                     (1, "Submit — never on the last day"),
                     (0, "Typical deadline")]
            items = ""
            for w, text in steps:
                d = nd - dt.timedelta(weeks=w)
                cls = " final" if w == 0 else (" due" if d <= TODAY else "")
                items += (f'<div class="plan-item{cls}"><span class="plan-date">{fmt_date(d)}{" · NOW" if cls == " due" else ""}</span>'
                          f'<span class="plan-dot"></span><span class="plan-text">{e(text)}</span></div>')
            plan_html = (f'<h2>A working timeline</h2><p>Counting back from the next typical deadline '
                         f'({"≈ " if r.get("approx") else ""}{fmt_date(nd)}). Steps marked NOW are already due if you start today.</p>'
                         f'<div class="plan-list" data-plan="{e(json.dumps(r["deadlines"]))}">{items}</div>')

        related = [by_slug[s] for s in n.get("related", []) if s in by_slug]
        if len(related) < 3:
            pool = [x for x in routes if x is not r and x not in related and
                    (x["country"] == r["country"] or set(x["levels"]) & set(r["levels"]) and set(x["types"]) & set(r["types"]))]
            related += pool[: 3 - len(related)]
        related_html = ('<h2>Related routes</h2><div class="rows">' + "".join(route_row(x, root) for x in related[:4]) + "</div>") if related else ""
        sources = n.get("sources") or []
        for s in sources:
            if not URL_RE.match(s.get("url", "")):
                fail(f"{r['slug']}: source URL must be https: {s!r}")
        sources_html = ('<h2>Sources</h2><ul>' + "".join(
            f'<li><a href="{e(s["url"])}" target="_blank" rel="noopener">{e(s["title"])}</a></li>' for s in sources) + "</ul>") if sources else ""
        reviewed = n.get("reviewed", r.get("last_verified", ""))
        article = f"""<div class="wrap">
<div class="page-head">
{crumbs(root, crumb_items)}
<div class="eyebrow-s">{e(r["flag"])} {e(r["country"])} · {"Funding programme" if r["kind"] == "program" else "University"}</div>
<h1>{e(r["name"])}</h1>
<p class="lede">{inline(n.get("summary") or r["funding"], root)}</p>
</div>
<div class="layout has-rail"><div>
{glance}
<article class="prose">
{section("What the funding covers", n.get("covers"))}
{costs_html}
{section("Who can apply", n.get("eligibility"))}
{ad_bay("article")}
{docs_html}
{section("How to apply", n.get("how_to_apply"), ordered=True)}
{section("Before you apply: things to know", n.get("watch_out"))}
{faq_html}
{plan_html}
{related_html}
{sources_html}
<p class="muted">Page reviewed {e(fmt_date(dt.date.fromisoformat(reviewed)) if reviewed else "—")}. Spotted something out of date? <a href="{root}contact/">Tell us</a> and we will check it against the official source.</p>
</article>
</div><aside class="rail" aria-label="Advertisements">{ad_bay("rail")}</aside></div>
</div>"""
        desc = f"{r['name']} ({r['country']}): {r['funding']} Eligibility, how to apply and the typical deadline ({r['deadline']})."
        if len(desc) > 300:
            desc = desc[:297].rsplit(" ", 1)[0] + "…"
        ld = [breadcrumb_ld([("", "Home"), ("routes/", "Routes")] + ([(f"countries/{cslug}/", r["country"])] if has_country else [])
                            + [(f"routes/{r['slug']}/", r["name"])]),
              {"@context": "https://schema.org", "@type": "WebPage", "name": r["name"], "description": desc,
               "dateModified": reviewed or None, "url": BASE + f"routes/{r['slug']}/"}]
        write(f"routes/{r['slug']}/", page(f"routes/{r['slug']}/", f"{r['name']}: funding, eligibility and deadlines", desc,
                                            article, root=root, nav="routes/", jsonld=ld, og_type="article"))


def build_routes_index(routes, country_pages):
    root = "../"
    groups = {}
    for r in routes:
        groups.setdefault(r["region"], {}).setdefault(r["country"], []).append(r)
    order = ["Europe", "USA", "Canada", "Global"]
    body = ""
    for region in order:
        if region not in groups:
            continue
        body += f'<h2 class="section-title sr">{e(region)}</h2>'
        for country in sorted(groups[region]):
            cs = slugify(country)
            label = f'<a href="{root}countries/{cs}/">{e(country)} →</a>' if cs in country_pages else e(country)
            body += f'<div class="group-title" id="{cs}">{label}</div><div class="rows">' + "".join(
                route_row(r, root) for r in sorted(groups[region][country], key=lambda x: x["name"])) + "</div>"
    html_body = f"""<div class="wrap"><div class="page-head">{crumbs(root, [("", "Routes")])}
<div class="eyebrow-s">{len(routes)} routes · {len({r["country"] for r in routes})} countries</div>
<h1>Every funded route, by country</h1>
<p class="lede">Universities that charge international students no tuition, and scholarships that pay tuition and living costs. Each route has its own page with eligibility, how to apply and a working timeline. Prefer to filter? Use the <a href="{root}" style="color:var(--brass)">live board</a>.</p>
</div><div class="layout">{body}{ad_bay("between", wide=True)}</div></div>"""
    write("routes/", page("routes/", "All tuition-free and fully funded routes by country",
                          f"All {len(routes)} tuition-free universities and fully funded scholarships on {SITE}, grouped by country, each with eligibility, deadlines and how to apply.",
                          html_body, root=root, nav="routes/",
                          jsonld=[breadcrumb_ld([("", "Home"), ("routes/", "Routes")])]))


def build_countries(routes, countries):
    for slug, c in countries:
        root = "../../"
        rs = [r for r in routes if slugify(r["country"]) == slug]
        if not rs:
            fail(f"country page {slug} has no routes")
        sections = ""
        for i, s in enumerate(c["sections"]):
            sections += f'<h2 id="{slugify(s["h"])}">{e(s["h"])}</h2>{s["html"].replace("{{root}}", root)}'
            if i == 1:
                sections += ad_bay("article")
        sources = "".join(f'<li><a href="{e(s["url"])}" target="_blank" rel="noopener">{e(s["title"])}</a></li>' for s in c.get("sources", []))
        # the numbers people come for, then the questions they ask; both plain text, checked here
        glance, faq = c.get("glance") or [], c.get("faq") or []
        for item in glance:
            if (not isinstance(item, list) or len(item) != 2 or not all(isinstance(x, str) and x.strip() for x in item)
                    or any(re.search(r"[<>]", x) for x in item)):
                fail(f"content/countries/{slug}.json: each 'glance' item must be [label, plain text]")
        for q in faq:
            if not all(isinstance(q.get(k), str) and q[k].strip() and not re.search(r"[<>]", q[k]) for k in ("q", "a")):
                fail(f"content/countries/{slug}.json: each 'faq' item needs plain-text 'q' and 'a'")
        glance_html = ('<div class="glance"><p class="eyebrow-s">At a glance · international students</p><dl class="glance-grid">'
                       + "".join(f"<div><dt>{e(k)}</dt><dd>{e(v)}</dd></div>" for k, v in glance) + "</dl></div>") if glance else ""
        faq_html = ('<h2 id="common-questions">Common questions</h2>'
                    + "".join(f"<h3>{e(q['q'])}</h3><p>{e(q['a'])}</p>" for q in faq)) if faq else ""
        faq_ld = ([{"@context": "https://schema.org", "@type": "FAQPage",
                    "mainEntity": [{"@type": "Question", "name": q["q"], "acceptedAnswer": {"@type": "Answer", "text": q["a"]}}
                                   for q in faq]}] if faq else [])
        body = f"""<div class="wrap"><div class="page-head">{crumbs(root, [("countries/", "Countries"), ("", c["name"])])}
<div class="eyebrow-s">{e(c.get("flag", ""))} {len(rs)} funded route{"s" if len(rs) != 1 else ""}</div>
<h1>{e(c["title"])}</h1><p class="lede">{e(c["lede"])}</p>
<div class="byline"><span>Reviewed <b>{e(fmt_date(dt.date.fromisoformat(c["reviewed"])))}</b></span><span>Sources: official government and university pages</span></div></div>
<div class="layout has-rail"><div>{glance_html}
<h2 class="group-title">Funded routes in {e(c["name"])}</h2><div class="rows">{"".join(route_row(r, root) for r in rs)}</div>
<article class="prose" style="margin-top:12px">{sections}{faq_html}
{"<h2>Sources</h2><ul>" + sources + "</ul>" if sources else ""}</article>
</div><aside class="rail" aria-label="Advertisements">{ad_bay("rail")}</aside></div></div>"""
        write(f"countries/{slug}/", page(f"countries/{slug}/", c["title"], c["description"], body, root=root, nav="countries/",
                                          jsonld=[breadcrumb_ld([("", "Home"), ("countries/", "Countries"), (f"countries/{slug}/", c["name"])])] + faq_ld,
                                          og_type="article"))
    root = "../"
    tiles = "".join(
        f'<a class="tile" href="{slug}/"><span class="tile-kicker">{e(c.get("flag", ""))} {len([r for r in routes if slugify(r["country"]) == slug])} routes</span>'
        f'<h2>{e(c["name"])}</h2><p>{e(c["card"])}</p><span class="more">Country guide →</span></a>' for slug, c in countries)
    others = sorted({r["country"] for r in routes} - {c["name"] for _, c in countries})
    other_html = ", ".join(f'<a href="{root}routes/#{slugify(o)}">{e(o)}</a>' for o in others)
    body = f"""<div class="wrap"><div class="page-head">{crumbs(root, [("", "Countries")])}
<div class="eyebrow-s">Country guides</div><h1>Where tuition is free — and where scholarships pay</h1>
<p class="lede">How tuition, living costs and funding work for international students in each country with several funded routes.</p></div>
<div class="tiles three">{tiles}</div>
<p class="muted" style="margin:28px 0 48px;line-height:1.7">Also on the board, with one route each: {other_html}.</p></div>"""
    write("countries/", page("countries/", "Country guides: free tuition and full scholarships",
                             "Country-by-country guides to tuition-free study and fully funded scholarships for international students in Europe, the USA and Canada.",
                             body, root=root, nav="countries/", jsonld=[breadcrumb_ld([("", "Home"), ("countries/", "Countries")])]))


def add_heading_ids(body):
    toc = []

    def repl(m):
        text = re.sub(r"<[^>]+>", "", m.group(1))
        hid = slugify(text)
        toc.append((hid, text))
        return f'<h2 id="{hid}">{m.group(1)}</h2>'
    return re.sub(r"<h2>(.*?)</h2>", repl, body), toc


def build_guides(guides, routes):
    for g in guides:
        root = "../../"
        body, toc = add_heading_ids(g["body"].replace("{{root}}", root))
        body = body.replace("<!--ad-->", ad_bay("article"))
        mins = max(1, round(words(body) / 220))
        toc_html = ('<nav class="toc" aria-label="On this page"><p>On this page</p><ol>' +
                    "".join(f'<li><a href="#{hid}">{e(t)}</a></li>' for hid, t in toc) + "</ol></nav>") if len(toc) >= 4 else ""
        others = [x for x in guides if x is not g][:3]
        more = '<h2>Keep reading</h2><div class="tiles">' + "".join(
            f'<a class="tile" href="{root}guides/{x["slug"]}/"><span class="tile-kicker">{e(x.get("kicker", "Guide"))}</span>'
            f'<h3>{e(x["title"])}</h3><p>{e(x["description"])}</p></a>' for x in others) + "</div>"
        html_body = f"""<div class="wrap"><div class="page-head">{crumbs(root, [("guides/", "Guides"), ("", g["title"])])}
<div class="eyebrow-s">{e(g.get("kicker", "Guide"))}</div><h1>{e(g["title"])}</h1><p class="lede">{e(g["description"])}</p>
<div class="byline"><span>By the <b>{e(SITE)} editors</b></span><span>Updated {e(fmt_date(dt.date.fromisoformat(g["updated"])))}</span><span>{mins} min read</span></div>
<div class="head-share">{share_box(f"guides/{g['slug']}/", g["title"], g["title"], "guide")}</div></div>
<div class="layout has-rail"><div><article class="prose">{toc_html}{body}</article>
<div class="prose" style="margin-top:8px">{more}</div></div><aside class="rail" aria-label="Advertisements">{ad_bay("rail")}</aside></div></div>"""
        ld = [breadcrumb_ld([("", "Home"), ("guides/", "Guides"), (f"guides/{g['slug']}/", g["title"])]),
              {"@context": "https://schema.org", "@type": "Article", "headline": g["title"], "description": g["description"],
               "dateModified": g["updated"], "datePublished": g.get("published", g["updated"]),
               "author": {"@type": "Organization", "name": f"{SITE} editors", "url": BASE + "about/"},
               "publisher": {"@type": "Organization", "name": SITE, "url": BASE},
               "mainEntityOfPage": BASE + f"guides/{g['slug']}/"}]
        write(f"guides/{g['slug']}/", page(f"guides/{g['slug']}/", g["title"], g["description"], html_body, root=root,
                                            nav="guides/", jsonld=ld, og_type="article"))
    root = "../"
    tiles = "".join(
        f'<a class="tile" href="{g["slug"]}/"><span class="tile-kicker">{e(g.get("kicker", "Guide"))}</span><h2>{e(g["title"])}</h2>'
        f'<p>{e(g["description"])}</p><span class="more">Read · {max(1, round(words(g["body"]) / 220))} min →</span></a>' for g in guides)
    body = f"""<div class="wrap"><div class="page-head">{crumbs(root, [("", "Guides")])}<div class="eyebrow-s">Guides</div>
<h1>How to win a funded place, step by step</h1><p class="lede">Practical guides for international applicants: what the funding labels really mean, what "free" tuition still costs, how to plan a year out, and how to write the documents that decide it.</p></div>
<div class="tiles three" style="margin-bottom:48px">{tiles}</div></div>"""
    write("guides/", page("guides/", "Guides for funded study abroad",
                          "Practical guides for international students applying to tuition-free universities and fully funded scholarships.",
                          body, root=root, nav="guides/", jsonld=[breadcrumb_ld([("", "Home"), ("guides/", "Guides")])]))


def build_deadlines(routes):
    root = "../"
    by_month = {m: [] for m in range(1, 13)}
    rolling, tbc = [], []
    for r in routes:
        if r.get("rolling"):
            rolling.append(r)
            continue
        if r.get("tbc"):
            tbc.append(r)
            continue
        for dl in r["deadlines"]:
            by_month[int(dl["m"])].append((int(dl["d"]), r))
    cards = ""
    start = TODAY.month
    for k in range(12):
        m = (start - 1 + k) % 12 + 1
        items = sorted(by_month[m], key=lambda x: (x[0], x[1]["name"]))
        lis = ""
        for d, r in items:
            past = k == 0 and d < TODAY.day
            lis += (f'<li{PAST_CLASS if past else ""}><span class="day">{"≈" if r.get("approx") else ""}{d}</span>'
                    f'<span><a href="{root}routes/{r["slug"]}/">{e(r["name"])}</a>'
                    f'<span class="note">{e(r["flag"])} {e(r["country"])} · {"passed this year — " if past else ""}{e(r["deadline"])}</span></span></li>')
        cards += (f'<section class="month{" now" if k == 0 else ""}" data-month="{m}"><h2>{MONTHS[m - 1]} <small>{len(items)} deadline{"s" if len(items) != 1 else ""}</small></h2>'
                  + (f"<ul>{lis}</ul>" if lis else '<p class="empty-m">No typical deadlines this month — a good month to prepare documents.</p>') + "</section>")
        if k == 5:
            cards += "</div>" + ad_bay("between", wide=True) + '<div class="months">'
    roll = ", ".join(f'<a href="{root}routes/{r["slug"]}/" style="color:var(--brass)">{e(r["name"])}</a>' for r in rolling)
    body = f"""<div class="wrap"><div class="page-head">{crumbs(root, [("", "Deadlines")])}<div class="eyebrow-s">Deadline calendar</div>
<h1>Every typical deadline, month by month</h1>
<p class="lede">The next twelve months, starting now. Dates are each route's usual annual deadline — ≈ marks dates that vary by course or country, so always confirm on the official page. Plan to submit at least a week early.</p></div>
<div class="months">{cards}</div>
<p class="muted" style="margin:24px 0 8px;line-height:1.7">Open year-round (no fixed deadline): {roll}.</p>
<p class="muted" style="margin:0 0 48px;line-height:1.7">Next call not yet announced: {", ".join(f'<a href="{root}routes/{r["slug"]}/" style="color:var(--brass)">{e(r["name"])}</a>' for r in tbc) or "none"}.</p></div>"""
    write("deadlines/", page("deadlines/", "Scholarship and admission deadline calendar",
                             "Month-by-month calendar of typical deadlines for tuition-free universities and fully funded scholarships for international students.",
                             body, root=root, nav="deadlines/", jsonld=[breadcrumb_ld([("", "Home"), ("deadlines/", "Deadlines")])]))


def build_static_pages(pages):
    for p in pages:
        root = "../"
        body = p["body"].replace("{{root}}", root).replace("{{email}}", e(CONFIG["contact_email"])) \
            .replace("{{site}}", e(SITE)).replace("{{base}}", e(BASE))
        html_body = f"""<div class="wrap"><div class="page-head">{crumbs(root, [("", p["title"])])}
<h1>{e(p["title"])}</h1>{f'<p class="lede">{e(p["lede"])}</p>' if p.get("lede") else ""}
<div class="byline"><span>Last updated <b>{e(fmt_date(dt.date.fromisoformat(p["updated"])))}</b></span></div></div>
<div class="layout"><article class="prose">{body}</article></div></div>"""
        write(f"{p['slug']}/", page(f"{p['slug']}/", p["title"], p["description"], html_body, root=root,
                                     nav=f"{p['slug']}/" if p["slug"] == "about" else None, ads=p.get("ads", False)))


def build_404():
    body = f"""<div class="wrap center" style="padding:80px 0 64px"><div class="big404">404</div>
<h1 style="font-family:var(--serif);font-weight:400;font-size:32px;margin-top:12px">This gate doesn't exist</h1>
<p class="lede" style="margin:14px auto 28px">The page may have moved when a route was renamed. Try the full list or the scholarship board.</p>
<p><a class="btn btn-primary" href="{BASE}routes/">All funded routes</a> <a class="btn btn-line" href="{BASE}scholarships/">Scholarship board</a></p></div>"""
    # served at any depth, so links and assets are absolute
    text = page("404.html", "Page not found", "The page you were looking for isn't on DegreeStep — try the full list of funded routes or the live deadline board.",
                body, root=BASE, noindex=True, ads=False)
    write("404.html", text)


def write_meta_files(routes):
    # data files for the live board — only the fields the page uses
    keep = ("name", "slug", "flag", "country", "region", "kind", "scope", "types", "levels", "fields", "funding",
            "deadline", "deadlines", "approx", "rolling", "tbc", "link", "last_verified")
    slim = [{k: r[k] for k in keep if k in r} for r in ROUTES]
    (OUT / "data").mkdir(parents=True, exist_ok=True)
    (OUT / "data" / "data.js").write_text("window.SCHOLAR_DATA = " + json.dumps(slim, ensure_ascii=False).replace("</", "<\\/") + ";\n", encoding="utf-8")
    (OUT / "data" / "schools.js").write_text("window.SCHOLAR_SCHOOLS = " + json.dumps(SCHOOLS, ensure_ascii=False).replace("</", "<\\/") + ";\n", encoding="utf-8")
    urls = "".join(f"<url><loc>{e(BASE + p)}</loc><lastmod>{TODAY.isoformat()}</lastmod></url>\n"
                   for p in PAGES if p.endswith("/") or p == "")
    (OUT / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                                     + urls + "</urlset>\n", encoding="utf-8")
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {BASE}sitemap.xml\n", encoding="utf-8")
    client = adsense_client()
    if client:
        (OUT / "ads.txt").write_text(f"google.com, {client.replace('ca-', '')}, DIRECT, f08c47fec0942fa0\n", encoding="utf-8")
    (OUT / ".well-known").mkdir(exist_ok=True)
    expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=365)).strftime("%Y-%m-%dT00:00:00Z")
    (OUT / ".well-known" / "security.txt").write_text(
        f"Contact: mailto:{CONFIG['contact_email']}\nExpires: {expires}\nPreferred-Languages: en\nCanonical: {BASE}.well-known/security.txt\n",
        encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")


def check_links():
    """Every internal href/src on every page must point at a file we built."""
    for f in OUT.rglob("*.html"):
        rel_dir = f.parent.relative_to(OUT)
        text = f.read_text(encoding="utf-8")
        for attr, url in re.findall(r'\s(href|src)="([^"]+)"', text):
            url = html.unescape(url)
            base_dir = rel_dir
            if url.startswith(BASE):  # canonical links and the 404 page use absolute URLs
                url = url[len(BASE):] or "./"
                base_dir = Path(".")
            elif url.startswith(("http://", "https://", "mailto:", "#", "data:")):
                continue
            path = url.split("#")[0].split("?")[0]
            if not path:
                continue
            target = (OUT / base_dir / path).resolve()
            if path.endswith("/") or target.is_dir():
                target = target / "index.html"
            if not target.exists():
                msg = f"broken link in {f.relative_to(OUT)}: {attr}={url}"
                if ARGS.draft:
                    print("  (draft) " + msg)
                else:
                    fail(msg)


# ---------------------------------------------------------------- main

def main():
    global ROUTES, SCHOOLS, LAST_CHECK, DATA_VERSION, PAGES, ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview-ads", action="store_true", help="draw placeholder ad bays for design review")
    ap.add_argument("--draft", action="store_true", help="local only: build route pages even without a write-up")
    ARGS = ap.parse_args()
    PAGES = []

    ROUTES = load_routes()
    SCHOOLS = load_schools({r["name"] for r in ROUTES})
    checked = [r.get("last_checked") or r.get("last_verified") for r in ROUTES if r.get("last_checked") or r.get("last_verified")]
    LAST_CHECK = fmt_date(dt.date.fromisoformat(max(checked))) if checked else "—"
    DATA_VERSION = hashlib.sha256(b"".join(p.read_bytes() for p in [DATA / "data.json", DATA / "schools.json",
                                                                     *sorted((DATA / "incoming").glob("*.json"))])).hexdigest()[:10]

    notes = load_json_docs("routes")
    for slug in notes:
        if slug not in {r["slug"] for r in ROUTES}:
            fail(f"content/routes/{slug}.json matches no route")
    countries_raw = load_json_docs("countries")
    countries = sorted(countries_raw.items(), key=lambda kv: kv[1]["name"])
    guides = sorted(load_html_docs("guides"), key=lambda g: g.get("order", 99))
    static_pages = load_html_docs("pages")
    MENU_GUIDES[:] = [g for g in guides if g.get("featured")][:6] or guides[:6]
    MENU_COUNTRIES[:] = [(slug, c["name"]) for slug, c in countries]
    if ERRORS:
        return report()

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "assets").mkdir(parents=True)
    for f in (SRC / "assets").iterdir():
        shutil.copy2(f, OUT / "assets" / f.name)
        ASSET_VERSIONS[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()[:10]

    updates = load_updates({r["slug"] for r in ROUTES})
    positions = load_positions()
    news = load_news()
    if ERRORS:
        return report()
    build_home(ROUTES, SCHOOLS, updates, positions, news)
    build_positions(positions)
    build_news(news, {p["id"] for p in open_positions(positions)})
    build_board(ROUTES, SCHOOLS)
    build_updates(updates)
    build_route_pages(ROUTES, notes, dict(countries))
    build_routes_index(ROUTES, dict(countries))
    build_countries(ROUTES, countries)
    build_guides(guides, ROUTES)
    build_deadlines(ROUTES)
    build_static_pages(static_pages)
    build_404()
    write_meta_files(ROUTES)
    check_links()
    return report()


def report():
    if ERRORS:
        print(f"BUILD FAILED — {len(ERRORS)} problem(s):", file=sys.stderr)
        for m in ERRORS:
            print("  • " + m, file=sys.stderr)
        return 1
    n = len(list(OUT.rglob("*.html")))
    print(f"Built {n} pages into {OUT.relative_to(ROOT)}/ (ads: {'on' if adsense_client() else 'preview' if ARGS.preview_ads else 'off'}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
