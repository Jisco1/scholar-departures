#!/usr/bin/env python3
"""
Build the Scholar Departures website into _site/.

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
from urllib.parse import urlsplit

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
    seen = set()
    for i, r in enumerate(routes):
        where = f"data.json entry {i} ({r.get('name', '?')})"
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


NAV = [("routes/", "Routes"), ("deadlines/", "Deadlines"), ("countries/", "Countries"), ("guides/", "Guides"), ("about/", "About")]

SEAL = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 10v6M2 10l10-5 10 5-10 5z"/>'
        '<path d="M6 12v5c3 3 9 3 12 0v-5"/></svg>')
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
                f'<ins class="adsbygoogle ad-fill" style="display:block" data-ad-client="{e(client)}" '
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
    current = ' aria-current="page"'
    nav_html = "".join(
        f'<a href="{root}{href}"{current if nav == href else ""}>{label}</a>' for href, label in NAV)
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
  <a class="brand" href="{root or './'}" aria-label="{e(SITE)} home"><span class="brand-mark">{SEAL}</span><span class="brand-name">Scholar <b>Departures</b></span></a>
  <nav class="nav" aria-label="Main">{nav_html}</nav>
</div></header>
<main id="main">
{body}
</main>
<footer class="sitefoot"><div class="wrap">
  <div class="foot-grid">
    <div class="foot-brand">
      <a class="brand" href="{root or './'}"><span class="brand-mark">{SEAL}</span><span class="brand-name">Scholar <b>Departures</b></span></a>
      <p>Free, independent listings of tuition-free universities and fully funded scholarships, each checked against its official page. We are not affiliated with any university or scholarship provider.</p>
    </div>
    <div><h2>Explore</h2><ul>
      <li><a href="{root}routes/">All funded routes</a></li><li><a href="{root}deadlines/">Deadline calendar</a></li>
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
        f'<span class="tag" style="color:{TYPE_COLORS[t]};border-color:{TYPE_COLORS[t]}">{e(t)}</span>' for t in types) + "</div>"


def route_row(r, root):
    return (f'<a class="row" href="{root}routes/{r["slug"]}/"><div><div class="row-name">{e(r["name"])}</div>'
            f'<div class="row-meta">{e(r["flag"])} {e(r["country"])} · {e(" · ".join(r["levels"]))} · {e(", ".join(r["types"]))}</div></div>'
            f'<div class="row-side">{status_chip(r)}<span class="row-arrow">Details →</span></div></a>')


# ---------------------------------------------------------------- pages

def build_home(routes, schools, guides, countries):
    root = ""
    tpl = (SRC / "home.html").read_text(encoding="utf-8")
    config_json = json.dumps({"premium": {
        "gumroadProductId": (CONFIG.get("premium") or {}).get("gumroad_product_id", ""),
        "purchaseUrl": (CONFIG.get("premium") or {}).get("purchase_url", "")}}).replace("</", "<\\/")

    def card(r):
        nd, days, status = next_deadline(r)
        stamps = "".join(f'<span class="stamp" style="color:{TYPE_COLORS[t]};border-color:{TYPE_COLORS[t]};--rot:{"1.6deg" if i % 2 else "-1.6deg"};--si:{i}">{e(t)}</span>'
                         for i, t in enumerate(r["types"]))
        return (f'<article class="card in ready"><div class="card-top"><div class="country">{e(r["flag"])} {e(r["country"])} · '
                f'{"PROGRAMME" if r["kind"] == "program" else "UNIVERSITY"}</div><div class="stamps">{stamps}</div></div>'
                f'<h3><a class="card-title" href="routes/{r["slug"]}/">{e(r["name"])}</a></h3><p class="funding">{e(r["funding"])}</p>'
                f'<a class="more-link" href="routes/{r["slug"]}/">Eligibility, costs &amp; how to apply <span aria-hidden="true">→</span></a>'
                f'<div class="card-bottom"><div class="meta">{status_chip(r)}<span class="line">{e(r["deadline"])}</span>'
                f'<span class="line">{e(" · ".join(r["levels"]))}</span></div>'
                f'<div class="actions"><a class="apply" href="{e(r["link"])}" target="_blank" rel="noopener noreferrer">Official page {EXT}</a></div></div></article>')

    uni = [r for r in routes if r["kind"] != "program"]
    prog = [r for r in routes if r["kind"] == "program"]
    grid = ('<div class="grid-sep">Universities — funding from the school itself</div>' + "".join(map(card, uni)) +
            '<div class="grid-sep">Funding programmes — awards you take to a school</div>' + "".join(map(card, prog)))
    dsorted = sorted(schools, key=lambda s: (s["country"], s["name"]))
    dir_rows = "".join(
        f'<div class="dir-row in"><span class="dname">{e(s["flag"])} {e(s["name"])}</span><span class="dloc">{e(s["city"])} · {e(s["country"])}</span>'
        f'<span class="dact"><a class="dvisit" href="{e(s["link"])}" target="_blank" rel="noopener noreferrer">Visit site {EXT}</a></span></div>'
        for s in dsorted)
    featured = [g for g in guides if g.get("featured")][:6] or guides[:6]
    guide_tiles = "".join(
        f'<a class="tile" href="guides/{g["slug"]}/"><span class="tile-kicker">{e(g.get("kicker", "Guide"))}</span><h3>{e(g["title"])}</h3>'
        f'<p>{e(g["description"])}</p><span class="more">Read the guide →</span></a>' for g in featured)
    country_links = " · ".join(f'<a href="countries/{slug}/">{e(c["name"])}</a>' for slug, c in countries)
    body = (tpl.replace("{{CONFIG_JSON}}", config_json)
            .replace("{{GRID}}", grid)
            .replace("{{DIR_ROWS}}", dir_rows)
            .replace("{{GUIDE_TILES}}", guide_tiles)
            .replace("{{COUNTRY_LINKS}}", country_links)
            .replace("{{AD_HOME}}", ad_bay("between", wide=True))
            .replace("{{N_ROUTES}}", str(len(routes)))
            .replace("{{N_SCHOOLS}}", str(len(schools)))
            .replace("{{N_COUNTRIES}}", str(len({r["country"] for r in routes})))
            .replace("{{LAST_CHECK}}", e(LAST_CHECK)))
    jsonld = [{"@context": "https://schema.org", "@type": "WebSite", "name": SITE, "url": BASE,
               "description": CONFIG["tagline"]},
              {"@context": "https://schema.org", "@type": "Organization", "name": SITE, "url": BASE,
               "logo": BASE + "assets/apple-touch-icon.png", "email": CONFIG["contact_email"]}]
    title = f"{SITE} — Tuition-Free & Fully Funded Universities in Europe, the USA and Canada"
    desc = (f"{len(routes)} tuition-free universities and fully funded scholarships for international students in Europe, "
            f"the USA and Canada — live deadline countdowns, plain-English eligibility and links to the official pages.")
    write("", page("", title, desc, body, root=root, body_class="home", jsonld=jsonld,
                   extra_head=f'<link rel="stylesheet" href="{asset(root, "app.css")}">\n',
                   scripts=["data/data.js?v=" + DATA_VERSION, "data/schools.js?v=" + DATA_VERSION, asset(root, "app.js")]))


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
        next_text = ("Positions open year-round" if status == "rolling"
                     else "Not yet announced" if status == "tbc"
                     else f"{'≈ ' if r.get('approx') else ''}{fmt_date(nd)}")
        glance = f"""<section class="glance" aria-label="At a glance">
<dl class="glance-grid">
  <div><dt>Funding</dt><dd>{tags_html(r["types"])}</dd></div>
  <div><dt>Status</dt><dd>{status_chip(r)}</dd></div>
  <div><dt>Typical deadline</dt><dd>{e(r["deadline"])}<span class="sub">Next: <span data-next>{e(next_text)}</span>{' · varies by course or country' if r.get('approx') else ''}</span></dd></div>
  <div><dt>Study levels</dt><dd>{e(" · ".join(r["levels"]))}</dd></div>
  <div><dt>Fields</dt><dd>{e(scope_text)}<span class="sub">{"Strongest in" if r["scope"] == "all" else "Eligible"}: {e(", ".join(r["fields"]))}</span></dd></div>
  <div><dt>Who pays</dt><dd>{e(kind_text)}</dd></div>
</dl>
<div class="glance-cta"><a class="btn btn-primary" href="{e(r["link"])}" target="_blank" rel="noopener noreferrer">Official page on {e(domain(r["link"]))} {EXT}</a>
<small>Last verified {e(fmt_date(dt.date.fromisoformat(r["last_verified"])) if r.get("last_verified") else "—")}. Cycles shift each year — confirm dates on the official page.</small></div>
</section>"""

        def section(title, items, ordered=False):
            if not items:
                return ""
            tag = "ol" if ordered else "ul"
            return f"<h2>{e(title)}</h2><{tag}>" + "".join(f"<li>{inline(x, root)}</li>" for x in items) + f"</{tag}>"

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
{section("Who can apply", n.get("eligibility"))}
{ad_bay("article")}
{section("How to apply", n.get("how_to_apply"), ordered=True)}
{section("Before you apply: things to know", n.get("watch_out"))}
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
        body = f"""<div class="wrap"><div class="page-head">{crumbs(root, [("countries/", "Countries"), ("", c["name"])])}
<div class="eyebrow-s">{e(c.get("flag", ""))} {len(rs)} funded route{"s" if len(rs) != 1 else ""}</div>
<h1>{e(c["title"])}</h1><p class="lede">{e(c["lede"])}</p>
<div class="byline"><span>Reviewed <b>{e(fmt_date(dt.date.fromisoformat(c["reviewed"])))}</b></span><span>Sources: official government and university pages</span></div></div>
<div class="layout has-rail"><div>
<h2 class="group-title">Funded routes in {e(c["name"])}</h2><div class="rows">{"".join(route_row(r, root) for r in rs)}</div>
<article class="prose" style="margin-top:12px">{sections}
{"<h2>Sources</h2><ul>" + sources + "</ul>" if sources else ""}</article>
</div><aside class="rail" aria-label="Advertisements">{ad_bay("rail")}</aside></div></div>"""
        write(f"countries/{slug}/", page(f"countries/{slug}/", c["title"], c["description"], body, root=root, nav="countries/",
                                          jsonld=[breadcrumb_ld([("", "Home"), ("countries/", "Countries"), (f"countries/{slug}/", c["name"])])],
                                          og_type="article"))
    root = "../"
    tiles = "".join(
        f'<a class="tile" href="{slug}/"><span class="tile-kicker">{e(c.get("flag", ""))} {len([r for r in routes if slugify(r["country"]) == slug])} routes</span>'
        f'<h2>{e(c["name"])}</h2><p>{e(c["card"])}</p><span class="more">Country guide →</span></a>' for slug, c in countries)
    others = sorted({r["country"] for r in routes} - {c["name"] for _, c in countries})
    other_html = ", ".join(f'<a href="{root}routes/#{slugify(o)}">{e(o)}</a>' for o in others)
    body = f"""<div class="wrap"><div class="page-head">{crumbs(root, [("", "Countries")])}
<div class="eyebrow-s">Country guides</div><h1>Where tuition is free — and where scholarships pay</h1>
<p class="lede">How tuition, living costs and funding work for international students in each country with several funded routes. Every guide lists its routes and the official sources we checked.</p></div>
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
<div class="byline"><span>By the <b>{e(SITE)} editors</b></span><span>Updated {e(fmt_date(dt.date.fromisoformat(g["updated"])))}</span><span>{mins} min read</span></div></div>
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
<h1>How to win a funded place, step by step</h1><p class="lede">Practical, source-checked guides for international applicants: what the funding labels really mean, what "free" tuition still costs, how to plan a year out, and how to write the documents that decide it.</p></div>
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
<p class="lede" style="margin:14px auto 28px">The page may have moved when a route was renamed. Try the full list or the live board.</p>
<p><a class="btn btn-primary" href="{BASE}routes/">All funded routes</a> <a class="btn btn-line" href="{BASE}">Live board</a></p></div>"""
    # served at any depth, so links and assets are absolute
    text = page("404.html", "Page not found", "The page you were looking for isn't on Scholar Departures — try the full list of funded routes or the live deadline board.",
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
    DATA_VERSION = hashlib.sha256((DATA / "data.json").read_bytes() + (DATA / "schools.json").read_bytes()).hexdigest()[:10]

    notes = load_json_docs("routes")
    for slug in notes:
        if slug not in {r["slug"] for r in ROUTES}:
            fail(f"content/routes/{slug}.json matches no route")
    countries_raw = load_json_docs("countries")
    countries = sorted(countries_raw.items(), key=lambda kv: kv[1]["name"])
    guides = sorted(load_html_docs("guides"), key=lambda g: g.get("order", 99))
    static_pages = load_html_docs("pages")
    if ERRORS:
        return report()

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "assets").mkdir(parents=True)
    for f in (SRC / "assets").iterdir():
        shutil.copy2(f, OUT / "assets" / f.name)
        ASSET_VERSIONS[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()[:10]

    build_home(ROUTES, SCHOOLS, guides, countries)
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
