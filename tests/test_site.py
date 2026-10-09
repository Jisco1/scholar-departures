"""Checks that run before every deploy.

    python -m unittest discover -s tests -v

Each test builds the site (or a poisoned copy of it) and inspects the output,
so a regression in the builder, the data or the page scripts fails here
instead of on the live site.
"""
import html as html_mod
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COPY = ["build.py", "site.json", "src", "content", "data", "updater"]


def make_copy():
    tmp = Path(tempfile.mkdtemp(prefix="sd-test-"))
    for name in COPY:
        src = ROOT / name
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, tmp / name)
    return tmp


def source_routes(where):
    """Every route the builder publishes: data.json, plus the new routes a merged
    proposal adds as data/incoming/<slug>.json - loaded the same way build.py does."""
    routes = json.loads((where / "data" / "data.json").read_text(encoding="utf-8"))
    return routes + [json.loads(p.read_text(encoding="utf-8"))
                     for p in sorted((where / "data" / "incoming").glob("*.json"))]


def build(where, *args):
    return subprocess.run([sys.executable, str(where / "build.py"), *args], capture_output=True, text=True,
                          encoding="utf-8", cwd=where)


class BuiltSite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = make_copy()
        cls.result = build(cls.dir)
        cls.site = cls.dir / "_site"
        cls.pages = {p: p.read_text(encoding="utf-8") for p in cls.site.rglob("*.html")}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_build_passes(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)

    def test_every_route_has_a_page(self):
        for r in source_routes(self.dir):
            self.assertTrue((self.site / "routes" / r["slug"] / "index.html").exists(), r["slug"])

    def test_no_backdoors_in_output(self):
        for path in list(self.site.rglob("*.html")) + list(self.site.rglob("*.js")):
            text = path.read_text(encoding="utf-8")
            for secret in ("change-me-now", "DEMO-PREMIUM", "adminKey", "ADM_KEY", "?key="):
                self.assertNotIn(secret, text, f"{secret} found in {path.name}")

    def test_strict_csp_and_nonced_scripts(self):
        for path, html in self.pages.items():
            m = re.search(r'Content-Security-Policy" content="([^"]+)"', html)
            self.assertIsNotNone(m, f"no CSP on {path}")
            self.assertIn("'strict-dynamic'", m.group(1))
            self.assertIn("object-src 'none'", m.group(1))
            nonce = re.search(r"'nonce-([^']+)'", m.group(1)).group(1)
            for tag in re.findall(r"<script\b[^>]*>", html):
                if 'type="application/json"' in tag or 'type="application/ld+json"' in tag:
                    continue
                self.assertIn(f'nonce="{nonce}"', tag, f"script without nonce on {path}: {tag}")

    def test_no_inline_handlers_or_javascript_urls(self):
        for path, html in self.pages.items():
            self.assertNotRegex(html, r'href="\s*javascript:', path)
            self.assertNotRegex(html, r"<[a-z]+[^>]*\son[a-z]+=", f"inline event handler on {path}")

    def test_page_basics(self):
        for path, html in self.pages.items():
            self.assertEqual(len(re.findall(r"<h1[\s>]", html)), 1, f"h1 count on {path}")
            self.assertRegex(html, r"<title>[^<]{10,}</title>", path)
            self.assertRegex(html, r'<meta name="description" content="[^"]{40,}"', path)
            self.assertIn('id="main"', html, path)

    def test_structured_data_parses(self):
        for path, html in self.pages.items():
            for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
                json.loads(block)

    def test_board_data_keeps_status_fields(self):
        # regression: the board once lost "tbc" and showed "Closed · next —"
        js = (self.site / "data" / "data.js").read_text(encoding="utf-8")
        data = json.loads(js[len("window.SCHOLAR_DATA = "):].rstrip().rstrip(";"))
        source = source_routes(self.dir)
        for key in ("tbc", "rolling", "approx", "slug"):
            want = sum(1 for r in source if key in r)
            have = sum(1 for r in data if key in r)
            self.assertEqual(want, have, f"data.js dropped '{key}'")

    def test_no_ads_without_publisher_id(self):
        cfg = json.loads((self.dir / "site.json").read_text(encoding="utf-8"))
        if cfg["adsense"]["client"]:
            self.skipTest("AdSense configured")
        for path, html in self.pages.items():
            self.assertNotIn("adsbygoogle", html, path)
        self.assertFalse((self.site / "ads.txt").exists())

    def test_board_prerenders_first_page_only(self):
        # the static page must match what the script shows first, or the page jumps on load
        html = (self.site / "scholarships" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('class="card slim in ready"'), 6)
        self.assertEqual(html.count('class="dir-row in"'), 10)
        self.assertIn('id="gridPager"', html)
        self.assertIn('id="dirPager"', html)
        self.assertIn('href="../routes/"', html)  # without JavaScript, every route is still one click away
        self.assertIn('href="../routes/', html.split('id="grid"')[1].split('id="gridPager"')[0])  # card links climb to the root

    def test_phone_layout(self):
        html = (self.site / "scholarships" / "index.html").read_text(encoding="utf-8")
        for hook in ('id="filtersBtn"', 'id="filtersCount"', 'id="showResults"', 'id="panelClear"', 'id="boardMore"',
                     'id="lbRegion"', 'id="lbFunding"', 'class="vtxt"'):
            self.assertIn(hook, html, f"phone hook missing: {hook}")
        css = (ROOT / "src" / "assets" / "app.css").read_text(encoding="utf-8")
        js = (ROOT / "src" / "assets" / "app.js").read_text(encoding="utf-8")
        # the script and the stylesheet must agree on what counts as a phone
        js_bp = re.search(r'PHONE_MQ = window\.matchMedia\("\(max-width: (\d+)px\)"\)', js)
        self.assertIsNotNone(js_bp, "phone breakpoint missing from app.js")
        self.assertIn(f"@media (max-width: {js_bp.group(1)}px) {{\n  /* hero", css, "app.js and app.css disagree on the phone breakpoint")
        # phone-only controls stay out of the desktop layout
        self.assertIn(".filtersbtn, .flabel, .panelfoot { display: none; }", css)
        # the board's row limits in the script match the stylesheet (8 on desktop, 5 on phones)
        rows = {k: int(v) for k, v in re.findall(r"var (BOARD_\w+_ROWS) = (\d+);", js)}
        self.assertEqual(rows, {"BOARD_PHONE_ROWS": 5, "BOARD_DESKTOP_ROWS": 8})
        self.assertIn("#boardBody > .brow.r:nth-child(n+%d)" % (rows["BOARD_DESKTOP_ROWS"] + 1), css)
        self.assertIn("#boardBody > .brow.r:nth-child(n+%d)" % (rows["BOARD_PHONE_ROWS"] + 1), css)
        # 16px text in fields on touch screens, or iPhones zoom the page when you tap them
        coarse = css[css.index("@media (pointer: coarse) {"):]
        coarse = coarse[:coarse.index("\n}\n")]
        self.assertRegex(coarse, r"\.searchbox input, select \{ font-size: 16px; \}")
        self.assertIn("min-height: 44px", coarse)

    def test_one_menu_on_every_page(self):
        for path, html in self.pages.items():
            name = path.relative_to(self.site).as_posix()
            self.assertEqual(html.count('<details class="menu">'), 1, f"{name}: needs exactly one Menu button")
            self.assertNotIn('class="nav"', html, f"{name}: the old link bar is back")
            menu = html[html.index('<details class="menu">'):html.index("</header>")]
            for target in ("scholarships/", "routes/", "deadlines/", "positions/", "news/", "countries/", "guides/", "about/",
                           "updates/", "scholarships/#directory"):
                self.assertRegex(menu, r'href="(?:(?:\.\./)*|https://[^"]+/)' + re.escape(target) + '"', f"{name}: menu lacks {target}")
            self.assertEqual(menu.count('<details class="menu-sub"'), 3, f"{name}: Countries, Guides and About drop-downs")
            self.assertNotIn("menu-theme", menu, f"{name}: the theme switch belongs beside the menu, not in it")
            # light is the first theme (no script needed); the one button beside Menu switches to dark and back
            self.assertRegex(html, r'<html lang="en" data-theme="light">', f"{name}: light is not the default")
            self.assertIn('<meta name="theme-color" content="#F3F5F9">', html, name)
            top = html[html.index('<header class="topbar">'):html.index('<details class="menu">')]
            self.assertIn('<button type="button" class="theme-toggle"', top, f"{name}: no theme button beside the menu")
        board = (self.site / "scholarships" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("startguides", board)  # the board stays clean: no guide tiles or country strip
        self.assertNotIn("country-strip", board)

    def test_home_is_a_landing_page_and_the_board_moved(self):
        home = (self.site / "index.html").read_text(encoding="utf-8")
        board = (self.site / "scholarships" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="scholarships/"', home, "no way from the home page to the board")
        self.assertNotIn('id="grid"', home, "the board is still on the home page")
        self.assertIn('id="grid"', board)
        self.assertRegex(board, r'src="\.\./data/data\.js\?v=')
        self.assertIn('"root": "../"', board)
        # the board's script builds every route link from the site root, so links work from /scholarships/
        js = (ROOT / "src" / "assets" / "app.js").read_text(encoding="utf-8")
        self.assertNotRegex(js, r'href="routes/|(?<!ROOT \+ )"routes/" \+')
        self.assertEqual(js.count('ROOT + "routes/"') + js.count("esc(ROOT) + 'routes/"), 3)
        for name in ("level", "type", "kind", "region"):  # the home page's paths open a filtered board
            self.assertIn(f'p.get("{name}")', js)

    def test_home_hero_has_one_button_and_verification_sits_at_the_bottom(self):
        home = (self.site / "index.html").read_text(encoding="utf-8")
        hero = re.search(r'<section class="lp-hero">.*?</section>', home, re.S).group(0)
        band = re.search(r'<section class="lp-band">.*?</section>', home, re.S).group(0)
        self.assertEqual(re.findall(r'class="btn [^"]*"', hero), ['class="btn btn-primary btn-lg"'])
        self.assertNotIn("No account needed", home)
        self.assertNotIn("Nothing is updated silently", home)
        # section headings stand alone: no one-line notes under them (the owner asked for these gone)
        self.assertNotIn('class="lp-sub', home)
        self.assertNotIn("Scholarship lists are easy to find", home)
        self.assertNotIn("editorial-policy/", hero)
        self.assertIn('<a class="lp-newsbtn" href="news/">', hero)  # the news button sits above the boarding pass
        self.assertIn('href="editorial-policy/">How we verify</a>', band)

    def test_no_page_repeats_the_checked_claims(self):
        # the owner asked for these gone (5 Oct 2026): verification is explained once, on How we verify,
        # and no page tells readers how the research is done
        banned = re.compile(r"checked against (its|the) official page|checked against official sources|each (one |route )?(is |was )?checked|source-checked|"
                            r"every figure was checked|no account needed|nothing is updated silently|"
                            r"help of AI|including AI models|automated and AI|automated (check|search)|drafts are done", re.I)
        research = re.compile(r"help of AI|including AI models|automated and AI|automated (check|search)|drafts are done", re.I)
        home = self.pages[self.site / "index.html"]
        for line in ("How often is it updated", "re-checked every month", "no account to create", "Is DegreeStep free",
                     "How do you check the information"):
            self.assertNotIn(line, home, "the home page FAQ explains the site to itself")
        for path, text in self.pages.items():
            if path.parent.name == "editorial-policy":
                m = research.search(text)
                self.assertIsNone(m, f"editorial-policy: {m.group(0) if m else ''}")
                continue
            m = banned.search(text)
            self.assertIsNone(m, f"{path.relative_to(self.site)}: {m.group(0) if m else ''}")

    def test_country_guides_answer_application_fees(self):
        # Search Console (9 Oct 2026) showed people finding us for "netherlands universities without application fee"
        # and leaving: every country guide now answers what applying costs, and points to the cross-country guide
        guide = "guides/universities-with-no-application-fee/"
        self.assertIn(self.site / guide / "index.html", self.pages)
        countries = [p for p in self.pages if p.parent.parent.name == "countries"]
        self.assertGreaterEqual(len(countries), 10)
        for path in countries:
            text = self.pages[path]
            where = path.parent.name
            self.assertTrue("<dt>Application fee</dt>" in text, f"{where}: no application fee in At a glance")
            self.assertRegex(text, r'"@type": "Question", "name": "[^"]*application fee', f"{where}: no application-fee question")
            self.assertTrue(guide in text, f"{where}: no link to the application-fee guide")

    def test_no_em_dashes_anywhere(self):
        # the owner reads em dashes as a sign of AI writing (5 Oct 2026); none may reach a page, a script or a style
        for path in self.site.rglob("*"):
            if path.suffix in (".html", ".js", ".css", ".json", ".xml", ".txt"):
                text = path.read_text(encoding="utf-8")
                # the character itself, or written as an escape or an HTML entity
                m = re.search(r"\u2014|\\u2014|&mdash;|&#8212;|&#x2014;", text, re.I)
                self.assertIsNone(m, f"{path.relative_to(self.site)}: ...{text[max(0, m.start() - 40):m.start() + 40] if m else ''}...")

    def test_guides_name_their_author(self):
        # the owner asked to be named as the author (5 Oct 2026); bylines lead to his page, which says how pages are written
        def ld(text):
            return [json.loads(x) for x in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', text, re.S)]
        author = {"@type": "Person", "name": "Eric Arko", "url": "https://degreestep.com/about/eric-arko/"}
        guides = sorted(self.site.glob("guides/*/index.html")) + sorted(self.site.glob("countries/*/index.html"))
        self.assertGreaterEqual(len(guides), 20)
        for path in guides:
            text = self.pages[path]
            name = path.parent.relative_to(self.site).as_posix()
            head = text[text.index('<div class="byline">'):]
            self.assertIn('By <b><a href="../../about/eric-arko/">Eric Arko</a></b>', head[:head.index("</div>")], name)
            articles = [x for x in ld(text) if x.get("@type") == "Article"]
            self.assertEqual(len(articles), 1, name)
            self.assertEqual(articles[0]["author"], author, name)
        for path, text in self.pages.items():
            self.assertNotIn("DegreeStep editors", text, path.relative_to(self.site))
        profile = self.pages[self.site / "about" / "eric-arko" / "index.html"]
        person = next(x for x in ld(profile) if x.get("@type") == "ProfilePage")["mainEntity"]
        self.assertEqual((person["@type"], person["name"], person["url"]), ("Person", author["name"], author["url"]))
        for path in guides:
            self.assertIn(f'href="../../{path.parent.relative_to(self.site).as_posix()}/"', profile, "the author page lists his guides")
        self.assertIn('about/eric-arko/"', self.pages[self.site / "about" / "index.html"], "About should name who runs the site")
        home = next(x for x in ld(self.pages[self.site / "index.html"]) if x.get("@type") == "Organization")
        self.assertEqual(home.get("founder"), author)

    def test_choose_your_path_counts_match_the_board(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("site_build", self.dir / "build.py")
        home = (self.site / "index.html").read_text(encoding="utf-8")
        routes = source_routes(self.dir)
        rules = {"masters": lambda r: "Master's" in r["levels"], "phd": lambda r: "PhD" in r["levels"],
                 "bachelors": lambda r: "Bachelor's" in r["levels"],
                 "tuition-free": lambda r: r["kind"] != "program" and "Tuition-Free" in r["types"],
                 "programmes": lambda r: r["kind"] == "program"}
        self.assertEqual(home.count('role="tab"'), len(rules))
        for pid, rule in rules.items():
            n = sum(1 for r in routes if rule(r))
            panel = home[home.index(f'id="path-{pid}"'):]
            panel = panel[:panel.index("</section>")]
            self.assertIn(f"See all {n} on the board", panel, pid)
            self.assertIn('href="scholarships/?', panel, pid)
            self.assertGreater(panel.count('class="row"'), 0, pid)

    def test_boarding_pass_shows_the_next_deadline(self):
        import datetime as dt
        home = (self.site / "index.html").read_text(encoding="utf-8")
        due = re.search(r'data-due="(\d{4}-\d{2}-\d{2})"', home)
        self.assertIsNotNone(due, "no next deadline on the home page")
        self.assertGreaterEqual(dt.date.fromisoformat(due.group(1)), dt.date.today())
        soon = home[home.index("Closing soon"):]
        first = re.search(r'<div class="row-name">([^<]+)</div>', soon).group(1)
        name = re.search(r'<span class="pass-name">([^<]+)</span>', home).group(1)
        self.assertEqual(name, first, "the boarding pass and the closing-soon list disagree")

    def test_updates_page_lists_changes_newest_first(self):
        page = (self.site / "updates" / "index.html").read_text(encoding="utf-8")
        dates = re.findall(r'<time datetime="(\d{4}-\d{2}-\d{2})"', page)
        items = json.loads((self.dir / "content" / "updates.json").read_text(encoding="utf-8"))
        self.assertEqual(len(dates), len(items))
        self.assertEqual(dates, sorted(dates, reverse=True))
        home = (self.site / "index.html").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r'<li class="lp-upd">', home)), min(5, len(items)))

    def test_country_guides_answer_the_practical_questions(self):
        for path in sorted((self.dir / "content" / "countries").glob("*.json")):
            c = json.loads(path.read_text(encoding="utf-8"))
            html = (self.site / "countries" / path.stem / "index.html").read_text(encoding="utf-8")
            labels = [k for k, _ in c["glance"]]
            self.assertEqual(len(labels), 5, path.stem)
            self.assertIn("Application fee", labels, path.stem)
            self.assertTrue(any("Proof of funds" in k for k in labels), f"{path.stem}: no proof of funds")
            self.assertIn("Work while studying", labels, path.stem)
            self.assertIn("After you graduate", labels, path.stem)
            self.assertEqual(html.count("<div><dt>"), 5, path.stem)
            self.assertGreaterEqual(len(c["faq"]), 3, path.stem)
            ld = [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
            faq = [b for b in ld if b.get("@type") == "FAQPage"]
            self.assertEqual(len(faq), 1, f"{path.stem}: FAQ structured data")
            self.assertEqual([q["name"] for q in faq[0]["mainEntity"]], [q["q"] for q in c["faq"]], path.stem)
            for s in c["sources"]:
                self.assertTrue(s["url"].startswith("https://"), path.stem)

    def test_positions_list_only_open_checked_listings(self):
        import datetime as dt
        today = dt.date.today()
        items = json.loads((self.dir / "content" / "positions.json").read_text(encoding="utf-8"))
        live = sorted((p for p in items if dt.date.fromisoformat(p["deadline"]) >= today
                       and (today - dt.date.fromisoformat(p["posted"])).days <= 60), key=lambda p: (p["deadline"], p["title"]))
        page = (self.site / "positions" / "index.html").read_text(encoding="utf-8")
        ids = re.findall(r'<article class="pos" id="pos-([a-z0-9-]+)"', page)
        self.assertEqual(ids, [p["id"] for p in live], "open listings, soonest deadline first")
        for p in live:
            # the card opens our own page; the official posting is linked from there, after the write-up
            link = f'href="{html_mod.escape(p["link"], quote=True)}"'
            self.assertIn(f'href="{p["id"]}/"', page, p["id"])
            self.assertNotIn(link, page, f'{p["id"]}: the list sends readers straight to the official posting')
            own = (self.site / "positions" / p["id"] / "index.html").read_text(encoding="utf-8")
            for heading in ("About the position", "Who can apply", "What is offered", "How to apply"):
                self.assertIn(f">{heading}</h2>", own, f'{p["id"]}: no "{heading}" section')
            self.assertGreater(own.index(link), own.index(">How to apply</h2>"), f'{p["id"]}: the apply link comes before the write-up')
            self.assertTrue(p["link"].startswith("https://"), p["id"])
        built = sorted(d.name for d in (self.site / "positions").iterdir() if d.is_dir())
        self.assertEqual(built, sorted(p["id"] for p in live), "a page for each open position, and none for closed ones")
        cfg = json.loads((self.dir / "site.json").read_text(encoding="utf-8"))
        self.assertIn(f'href="mailto:{cfg["contact_email"]}?subject=', page, "no way to post a position")
        home = (self.site / "index.html").read_text(encoding="utf-8")
        if live:
            self.assertIn('href="positions/"', home)
            self.assertIn(f'href="positions/{live[0]["id"]}/"', home, "the home page should lead with the soonest deadline")

    def test_research_news_opens_our_own_stories(self):
        items = json.loads((self.dir / "content" / "news.json").read_text(encoding="utf-8"))
        newest_first = [n["id"] for n in sorted(items, key=lambda n: n["date"], reverse=True)]  # same-day items keep their order
        page = (self.site / "news" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'<a class="lp-news-card" href="([a-z0-9-]+)/">', page), newest_first, "every story, newest first")
        for n in items:
            source = f'href="{html_mod.escape(n["source_url"], quote=True)}"'
            self.assertTrue(n["source_url"].startswith("https://"), n["id"])
            self.assertNotIn(source, page, f'{n["id"]}: the list sends readers straight to the source')
            story = (self.site / "news" / n["id"] / "index.html").read_text(encoding="utf-8")
            article = story[story.index('<article class="prose">'):story.index("</article>")]
            self.assertGreaterEqual(article.count("<p>"), 2, f'{n["id"]}: the story is not written out')
            self.assertNotRegex(article, r'href="https?://', f'{n["id"]}: the story links away from the site')
            self.assertGreater(story.index(source), story.index("</article>"), f'{n["id"]}: the source is credited before the story ends')
        home = (self.site / "index.html").read_text(encoding="utf-8")
        self.assertIn(f'href="news/{newest_first[0]}/"', home, "the home page should show the latest news")

    def test_news_pictures_carry_their_credits(self):
        items = json.loads((self.dir / "content" / "news.json").read_text(encoding="utf-8"))
        for n in items:  # the pictures and their credits sit at the top of each story's page
            story = (self.site / "news" / n["id"] / "index.html").read_text(encoding="utf-8")
            card = story[story.index('class="news-story-media"'):story.index('<article class="prose">')]
            imgs = n.get("images") or []
            if not imgs:
                self.assertIn('class="news-art"', card, f'{n["id"]}: neither a photo nor a drawing')
                continue
            self.assertIn("via Wikimedia Commons", card, n["id"])
            for im in imgs:
                self.assertTrue((self.site / "assets" / im["file"]).exists(), im["file"])
                self.assertIn(f'href="{html_mod.escape(im["source_url"], quote=True)}"', card, f'{n["id"]}: {im["file"]} not credited')
                self.assertIn(html_mod.escape(im["license"]), card, f'{n["id"]}: {im["file"]} licence not shown')
                self.assertNotRegex(im["license"], r"NC|ND", f'{im["file"]}: licence does not allow use on this site')

    def test_light_theme_covers_every_colour(self):
        css = (ROOT / "src" / "assets" / "site.css").read_text(encoding="utf-8")
        dark = css[css.index(":root {"):css.index("}", css.index(":root {"))]
        light = css[css.index(':root[data-theme="light"] {'):]
        light = light[:light.index("}")]
        colours = {n for n, v in re.findall(r"(--[\w-]+):\s*([^;]+);", dark) if n not in ("--serif", "--sans", "--mono")}
        missing = colours - set(re.findall(r"(--[\w-]+):", light))
        self.assertFalse(missing, f"light theme lacks {sorted(missing)}")
        # outside the two token blocks, colours come from tokens, so a new rule cannot stay dark in the light theme
        rest = css.replace(dark, "").replace(light, "") + "".join(
            (ROOT / "src" / "assets" / name).read_text(encoding="utf-8") for name in ("app.css", "landing.css"))
        rest = re.sub(r'url\("data:[^"]*"\)', "", rest)
        stray = set(re.findall(r"#[0-9A-Fa-f]{6}\b|rgba\((?!215,169,76)[^)]*\)", rest)) - {"#000"}
        self.assertFalse(stray, f"hard-coded colours outside the theme tokens: {sorted(stray)}")
        # the saved theme is applied before first paint, by a nonced script
        home = (self.site / "index.html").read_text(encoding="utf-8")
        self.assertRegex(home, r'<script nonce="[^"]+">try\{var t=localStorage\.getItem\(.sd-theme.\);if\(t===.dark.\)')

    def test_share_on_every_route_guide_and_story(self):
        from urllib.parse import parse_qs, urlsplit
        shared = [p for p in self.pages if p.parent.parent.name in ("routes", "guides", "news", "positions")]
        self.assertGreaterEqual(len([p for p in shared if p.parent.parent.name == "positions"]), 10, "every funded position can be shared")
        self.assertGreaterEqual(len([p for p in shared if p.parent.parent.name == "news"]), 9, "every news story can be shared")
        self.assertGreaterEqual(len(shared), 70)
        for path in shared:
            html = self.pages[path]
            name = path.relative_to(self.site).as_posix()
            self.assertEqual(html.count('<details class="share"'), 1, f"{name}: needs one Share button")
            canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html).group(1)
            box = html[html.index('<details class="share"'):]
            box = box[:box.index("</details>")]
            self.assertIn(f'data-url="{canonical}"', box, f"{name}: shares a different address than the page")
            hrefs = [x.replace("&amp;", "&") for x in re.findall(r'href="([^"]+)"', box)]
            self.assertEqual(len(hrefs), 6, name)
            for h in hrefs:
                self.assertRegex(h, r"^(https://|mailto:\?)", f"{name}: odd share link {h}")
            wa = parse_qs(urlsplit(hrefs[0]).query)["text"][0]
            self.assertTrue(wa.endswith(canonical), f"{name}: WhatsApp message lacks the link")
            h1 = re.search(r"<h1>(.*?)</h1>", html).group(1)
            self.assertIn(html_mod.unescape(h1).split(":")[0], wa, f"{name}: WhatsApp message lacks the title")

    def test_official_page_button_comes_after_the_write_up(self):
        # the owner asked (6 Oct 2026) for the official link at the end, so visitors read the page before leaving
        routes = sorted(self.site.glob("routes/*/index.html"))
        self.assertGreaterEqual(len(routes), 50)
        for path in routes:
            html = self.pages[path]
            name = path.parent.name
            self.assertEqual(html.count('class="btn btn-primary route-official-btn"'), 1, f"{name}: one official-page button")
            glance = html[html.index('<section class="glance"'):html.index("</section>", html.index('<section class="glance"'))]
            self.assertNotIn("Official page on", glance, f"{name}: the official link is back at the top")
            self.assertIn('<details class="share"', glance, f"{name}: Share stays at the top")
            button = html.index("route-official-btn")
            self.assertGreater(button, html.index("<h2>How to apply</h2>"), f"{name}: official link before How to apply")
            for later in ("<h2>Common questions</h2>", "<h2>A working timeline</h2>"):
                if later in html:
                    self.assertGreater(button, html.index(later), f"{name}: official link before {later}")

    def test_sitemap_dates_are_each_pages_real_date(self):
        # a sitemap that stamps every page with the build date is ignored by Google; each page carries its own date
        sm = dict(re.findall(r"<loc>https://degreestep\.com/([^<]*)</loc><lastmod>([^<]+)</lastmod>",
                             (self.site / "sitemap.xml").read_text(encoding="utf-8")))
        self.assertGreaterEqual(len(sm), 100)
        front = lambda f: json.loads(re.match(r"\s*<!--\s*(\{.*?\})\s*-->", f.read_text(encoding="utf-8"), re.S).group(1))
        for f in (self.dir / "content" / "guides").glob("*.html"):
            self.assertEqual(sm[f"guides/{f.stem}/"], front(f)["updated"], f.stem)
        for f in (self.dir / "content" / "pages").glob("*.html"):
            self.assertEqual(sm[f"{f.stem}/"], front(f)["updated"], f.stem)
        for f in (self.dir / "content" / "countries").glob("*.json"):
            self.assertEqual(sm[f"countries/{f.stem}/"], json.loads(f.read_text(encoding="utf-8"))["reviewed"], f.stem)
        for n in json.loads((self.dir / "content" / "news.json").read_text(encoding="utf-8")):
            self.assertEqual(sm[f"news/{n['id']}/"], n["date"], n["id"])
        import datetime
        today = datetime.date.today().isoformat()
        self.assertTrue(all(d <= today for d in sm.values()), "a page dated in the future")
        self.assertGreater(len(set(sm.values())), 3, "every page carries the same date")

    def test_every_story_and_position_is_linked_from_its_neighbours(self):
        # the More boxes rotate, so no story or position hangs off a single link from its list page
        for folder, least in (("news", 3), ("positions", 2)):
            pages = {p.parent.name: self.pages[p] for p in self.site.glob(f"{folder}/*/index.html")}
            self.assertGreater(len(pages), 3)
            for name in pages:
                links = sum(1 for other, html in pages.items() if other != name and f'href="../{name}/"' in html)
                self.assertGreaterEqual(links, least, f"{folder}/{name}: linked from only {links} other {folder} pages")

    def test_shared_stories_show_their_own_photo(self):
        # a link shared on WhatsApp or Facebook should preview the story's photo, not the site-wide card
        items = json.loads((self.dir / "content" / "news.json").read_text(encoding="utf-8"))
        with_photo = [n for n in items if n.get("images")]
        self.assertGreaterEqual(len(with_photo), 5)
        for n in items:
            html = self.pages[self.site / "news" / n["id"] / "index.html"]
            img = re.search(r'<meta property="og:image" content="([^"]+)">', html).group(1)
            want = "https://degreestep.com/assets/" + (f"share-{n['id']}.jpg" if n.get("images") else "og.png")
            self.assertEqual(img, want, n["id"])
            self.assertTrue((self.site / "assets" / img.rsplit("/", 1)[1]).exists(), img)
        home = self.pages[self.site / "index.html"]
        self.assertIn('<meta property="og:image" content="https://degreestep.com/assets/og.png">', home)

    def test_shared_pages_show_their_own_card(self):
        # scholarships, guides, country guides and positions each preview their own card when shared
        checked = 0
        for folder, kind in (("routes", "route"), ("guides", "guide"), ("countries", "country"), ("positions", "position")):
            for path in self.site.glob(f"{folder}/*/index.html"):
                img = re.search(r'<meta property="og:image" content="([^"]+)">', self.pages[path]).group(1)
                card = f"share-{kind}-{path.parent.name}.jpg"
                if (self.site / "assets" / card).exists():
                    self.assertEqual(img, "https://degreestep.com/assets/" + card, path.parent.name)
                    checked += 1
                else:
                    self.assertEqual(img, "https://degreestep.com/assets/og.png", path.parent.name)
        self.assertGreaterEqual(checked, 80, "most pages should have their own card")

    def test_shared_positions_say_where_and_when(self):
        # a shared position reads like the shared scholarships: title, then where and what level, then when it closes
        from urllib.parse import parse_qs, urlsplit
        for p in json.loads((self.dir / "content" / "positions.json").read_text(encoding="utf-8")):
            path = self.site / "positions" / p["id"] / "index.html"
            if not path.exists():
                continue  # closed, so no page
            html = self.pages[path]
            box = html[html.index('<details class="share"'):]
            wa = [x.replace("&amp;", "&") for x in re.findall(r'href="(https://wa\.me/[^"]+)"', box)][0]
            text = parse_qs(urlsplit(wa).query)["text"][0]
            self.assertIn(p["title"], text, p["id"])
            self.assertIn(p["institution"], text, p["id"])
            self.assertIn("Closes", text, p["id"])
            self.assertIn('<p class="share-h">Share this position</p>', box, p["id"])

    def test_route_extras_render(self):
        html = (self.site / "routes" / "brown-university" / "index.html").read_text(encoding="utf-8")
        self.assertIn("<h2>What you will still pay</h2>", html)
        self.assertRegex(html, r'<table><tr><th>Cost</th><th>Who pays</th></tr>(<tr><td>.+?</td><td>.+?</td></tr>){3,}</table>')
        self.assertIn('<ul class="checklist">', html)
        faq = html[html.index('<div class="faq">'):]
        faq = faq[:faq.index("</div>")]
        self.assertGreaterEqual(faq.count("<details>"), 3)
        self.assertNotIn("<details open", faq, "questions must start closed so the page stays short")
        # any-discipline routes no longer repeat the eight-field list
        self.assertNotIn("Strongest in", html)

    def test_sitemap_lists_routes(self):
        sitemap = (self.site / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("/routes/chevening-scholarships/", sitemap)
        self.assertIn("/guides/scholarship-scams/", sitemap)
        self.assertNotIn("404", sitemap)


class AdsWired(unittest.TestCase):
    """With a publisher ID and ad units in site.json, every ad bay, the tag and ads.txt appear."""
    CLIENT, SLOTS = "ca-pub-1234567890123456", {"between": "1111111111", "article": "2222222222", "rail": "3333333333"}

    @classmethod
    def setUpClass(cls):
        cls.dir = make_copy()
        cfg_path = cls.dir / "site.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["adsense"]["client"], cfg["adsense"]["slots"] = cls.CLIENT, dict(cls.SLOTS)
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        cls.result = build(cls.dir)
        cls.site = cls.dir / "_site"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def html(self, rel):
        return (self.site / rel / "index.html").read_text(encoding="utf-8")

    def slots(self, html):
        return sorted(re.findall(r'<ins class="ad-fill" data-sd-ad[^>]*data-ad-slot="(\d+)"', html))

    def test_build_passes(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr + self.result.stdout)

    def test_tag_and_verification_meta(self):
        home = self.html(".")
        self.assertIn(f'<meta name="google-adsense-account" content="{self.CLIENT}">', home)
        self.assertRegex(home, r'<script async nonce="[^"]+" crossorigin="anonymous" src="https://pagead2\.googlesyndication\.com/'
                               r'pagead/js/adsbygoogle\.js\?client=' + self.CLIENT + '"></script>')
        self.assertIn("data-privacy-choices", home)

    def test_each_page_type_gets_its_bays(self):
        between, article, rail = self.SLOTS["between"], self.SLOTS["article"], self.SLOTS["rail"]
        self.assertEqual(self.slots(self.html(".")), [between])
        self.assertEqual(self.slots(self.html("scholarships")), [between])
        self.assertEqual(self.slots(self.html("routes/chevening-scholarships")), sorted([article, rail]))
        self.assertEqual(self.slots(self.html("routes")), [between])
        self.assertEqual(self.slots(self.html("guides/scholarship-scams")), sorted([article, rail]))
        self.assertIn(between, self.slots(self.html("deadlines")))
        self.assertEqual(self.slots(self.html("privacy")), [], "legal pages carry no ads")

    def test_ads_txt(self):
        self.assertEqual((self.site / "ads.txt").read_text(encoding="utf-8"),
                         "google.com, pub-1234567890123456, DIRECT, f08c47fec0942fa0\n")

    def test_lazy_loader_ships(self):
        js = (self.site / "assets" / "site.js").read_text(encoding="utf-8")
        self.assertIn('querySelectorAll("ins[data-sd-ad]")', js)
        # a bay becomes visible to Google only at its own turn, the moment before its push
        self.assertRegex(js, r'ins\.classList\.add\("adsbygoogle"\);\s*try \{ \(window\.adsbygoogle = window\.adsbygoogle \|\| \[\]\)\.push\(\{\}\)')
        # an ad blocker must not leave labelled empty boxes behind
        self.assertIn("window.adsbygoogle.loaded", js)
        self.assertIn('root.classList.add("ads-off")', js)
        css = (self.site / "assets" / "site.css").read_text(encoding="utf-8")
        self.assertIn(".ads-off .ad-bay, .ads-off .rail { display: none !important; }", css)
        for path in self.site.rglob("*.html"):
            self.assertNotRegex(path.read_text(encoding="utf-8"), r'<ins[^>]*class="[^"]*\badsbygoogle\b',
                                f"{path.name}: a bay built as ins.adsbygoogle can be filled by another bay's request")


class MonthlyRefresh(unittest.TestCase):
    """The monthly robot, run end to end with a fake Claude and fake web pages (no network, no cost)."""

    def setUp(self):
        self.dir = make_copy()
        sys.path.insert(0, str(self.dir / "updater"))
        for name in ("drafts", "stale", "sync_decisions"):
            sys.modules.pop(name, None)
        import drafts, stale, sync_decisions  # noqa: E401  (the copies, not the originals)
        self.drafts, self.stale, self.sync = drafts, stale, sync_decisions

    def tearDown(self):
        sys.path.remove(str(self.dir / "updater"))
        for name in ("drafts", "stale", "sync_decisions"):
            sys.modules.pop(name, None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_stale_dates_are_classified(self):
        import datetime as dt
        classify = self.stale.past_dates
        today = dt.date(2026, 10, 1)
        w = {"summary": "Apply by 27 September 2026.",
             "covers": ["TOEFL tests taken from 21 January 2026 use the new scale.",
                        "The 2026 call closed on 12 March 2026.", "Files were due by 8 January 2026."],
             "eligibility": ["Next deadline 1 December 2026."]}
        kinds = {raw: kind for raw, _, kind in classify(w, today)}
        self.assertEqual(kinds, {"27 September 2026": "outdated", "12 March 2026": "last_cycle", "8 January 2026": "last_cycle"})

    def test_validator_accepts_current_pages_and_rejects_bad_ones(self):
        v = self.drafts.validate_writeup
        for path in sorted((self.dir / "content" / "routes").glob("*.json")):
            w = json.loads(path.read_text(encoding="utf-8"))
            if "costs" in w:
                self.assertEqual(v(w), [], path.stem)
        good = json.loads((self.dir / "content" / "routes" / "brown-university.json").read_text(encoding="utf-8"))
        self.assertTrue(v({**good, "summary": "<script>x</script>"}))
        self.assertTrue(v({**good, "faq": []}))
        self.assertTrue(v({**good, "sources": [{"title": "x", "url": "http://example.com"}]}))
        self.assertTrue(v({**good, "covers": ["see [this](javascript:alert(1))", "two"]}))

    def fake_page(self, url):
        return ("Brown University admission. To apply you must submit an $85 non-refundable application fee, "
                "or a fee waiver. Early Decision closes November 1. " + url)

    def test_page_update_is_proposed_with_verified_quotes(self):
        d = self.drafts
        current = json.loads((self.dir / "content" / "routes" / "brown-university.json").read_text(encoding="utf-8"))
        revised = {k: v for k, v in current.items() if k not in ("related", "reviewed")}
        revised["costs"] = [["Application fee ($85)", "You, unless a fee waiver is accepted"]] + current["costs"][1:]
        reply = {"writeup": revised, "changes": [
            {"change": "The application fee rose from $80 to $85.", "evidence": "submit an $85 non-refundable application fee",
             "url": "https://admission.brown.edu/"},
            {"change": "An invented claim.", "evidence": "this sentence is not on the page", "url": "https://admission.brown.edu/"}]}
        d.fetch = self.fake_page
        d.call_model = lambda prompt, max_tokens=6000: json.dumps(reply)
        self.assertEqual(d.main(["--only", "brown-university", "--max-new", "0"]), 1)
        meta = json.loads((self.dir / "drafts" / "page-update-brown-university" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["branch"], "bot/page-update/brown-university")
        self.assertIn("✓ quote found", meta["body"])
        self.assertIn("⚠ quote NOT found", meta["body"])
        written = json.loads(meta["files"]["content/routes/brown-university.json"])
        self.assertEqual(written["related"], current["related"])  # bookkeeping kept
        # merging the proposal must leave a site that builds
        for rel, content in meta["files"].items():
            (self.dir / rel).write_text(content, encoding="utf-8")
        result = build(self.dir)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Application fee ($85)", (self.dir / "_site" / "routes" / "brown-university" / "index.html").read_text(encoding="utf-8"))

    def test_a_draft_without_any_verified_quote_is_dropped(self):
        d = self.drafts
        current = json.loads((self.dir / "content" / "routes" / "brown-university.json").read_text(encoding="utf-8"))
        reply = {"writeup": {k: v for k, v in current.items() if k not in ("related", "reviewed")},
                 "changes": [{"change": "Made up.", "evidence": "nothing like this appears", "url": "https://x.org/"}]}
        d.fetch = self.fake_page
        d.call_model = lambda prompt, max_tokens=6000: json.dumps(reply)
        self.assertEqual(d.main(["--only", "brown-university", "--max-new", "0"]), 0)

    def test_new_route_becomes_a_page_after_merge_and_fold(self):
        d = self.drafts
        # Start from no pending new routes, so the fold below is about this one only.
        # A pending route is two files: its data and its page.
        gone = set()
        for p in (self.dir / "data" / "incoming").glob("*.json"):
            p.unlink()
            (self.dir / "content" / "routes" / p.name).unlink(missing_ok=True)
            gone.add(p.stem)
        upd = self.dir / "content" / "updates.json"  # and the What's new entries about them
        upd.write_text(json.dumps([u for u in json.loads(upd.read_text(encoding="utf-8")) if u.get("slug") not in gone]),
                       encoding="utf-8")
        cand_path = self.dir / "data" / "candidates.json"
        cand = {"name": "Example Tech University Scholarship", "flag": "🇳🇱", "country": "Netherlands", "kind": "school",
                "scope": "all", "fields": ["Engineering & Tech"], "region": "Europe", "types": ["Full Scholarship"],
                "levels": ["Master's"], "funding": "Full tuition and a living allowance for non-EU master's students.",
                "deadline": "Feb 1", "deadlines": [{"m": 2, "d": 1}], "approx": False,
                "link": "https://www.example.edu/scholarship", "last_verified": "2026-09-26",
                "content_hash": None, "needs_review": False, "status": "pending", "theme": "test", "confidence": 0.9}
        cand_path.write_text(json.dumps([cand]), encoding="utf-8")
        page = "Example Tech University pays full tuition and a living allowance of EUR 1,200 per month. Apply by 1 February."
        writeup = {"summary": "A full scholarship for non-EU master's students.",
                   "covers": ["Full tuition.", "A living allowance of €1,200 a month."],
                   "eligibility": ["Non-EU applicants.", "Admission to a master's programme."],
                   "how_to_apply": ["Apply for the master's.", "Apply for the scholarship by 1 February."],
                   "watch_out": ["Places are limited.", "Check the programme list."],
                   "costs": [["Tuition", "The scholarship"], ["Living costs", "€1,200 a month from the scholarship"]],
                   "documents": ["Transcripts.", "A CV."],
                   "faq": [{"q": "When?", "a": "By 1 February."}, {"q": "Who?", "a": "Non-EU students."}, {"q": "How much?", "a": "€1,200 a month."}],
                   "sources": [{"title": "Example Tech — Scholarship", "url": "https://www.example.edu/scholarship"}]}
        d.fetch = lambda url: page
        d.call_model = lambda prompt, max_tokens=6000: json.dumps({"writeup": writeup, "changes": [
            {"change": "Living allowance", "evidence": "living allowance of EUR 1,200 per month", "url": "https://www.example.edu/scholarship"}]})
        self.assertEqual(d.main(["--max-updates", "0", "--max-new", "1"]), 1)
        slug = "example-tech-university-scholarship"
        meta = json.loads((self.dir / "drafts" / f"new-route-{slug}" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(set(meta["files"]), {f"data/incoming/{slug}.json", f"content/routes/{slug}.json"})
        # the owner merges: the files land on main and the site builds with the new page
        for rel, content in meta["files"].items():
            target = self.dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        result = build(self.dir)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue((self.dir / "_site" / "routes" / slug / "index.html").exists())
        # next month: the decision is recorded and the file folds into data.json
        cands = json.loads(cand_path.read_text(encoding="utf-8"))
        notes = self.sync.apply_decisions([{"headRefName": f"bot/new-route/{slug}", "state": "MERGED", "mergedAt": "2026-10-02"}],
                                          cands, [], {}, "2026-11-01")
        self.assertEqual(cands[0]["status"], "approved", notes)
        data = json.loads((self.dir / "data" / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(self.sync.fold_incoming(data, self.dir / "data" / "incoming"), [slug])
        self.assertEqual(data[-1]["slug"], slug)
        self.assertEqual(list((self.dir / "data" / "incoming").glob("*.json")), [])

    def test_model_replies_with_citations_are_still_read(self):
        sys.modules.pop("jsonpick", None)
        import jsonpick
        pick = jsonpick.extract_json
        noisy = ('I searched [1] and found sources [2]. The best match [see 3] is below.\n'
                 '[{"name": "A", "types": ["Full Scholarship"]}, {"name": "B", "types": ["Merit"]}]')
        self.assertEqual([x["name"] for x in pick(noisy, "[", "]")], ["A", "B"])
        fenced = 'Notes {not json} here.\n```json\n{"accept": true, "confidence": 0.9}\n```\nDone.'
        self.assertEqual(pick(fenced, "{", "}"), {"accept": True, "confidence": 0.9})
        with self.assertRaises(ValueError):
            pick("The search found nothing useful [1].", "{", "}")

    def test_model_replies_lose_their_em_dashes(self):
        # nothing the monthly robot writes may bring an em dash back to the site
        sys.modules.pop("jsonpick", None)
        import jsonpick
        reply = ('```json\n{"deadline_text": "Varies \u2014 each university sets its own", "watch_out": ["Apply early\u2014places fill"], "n": 3}\n```')
        got = jsonpick.extract_json(reply)
        self.assertNotIn("\u2014", json.dumps(got, ensure_ascii=False))
        self.assertEqual(got, {"deadline_text": "Varies, each university sets its own", "watch_out": ["Apply early, places fill"], "n": 3})

    def test_closed_proposals_are_remembered(self):
        cands = [{"name": "Some Award", "slug": "some-award", "status": "in-review", "link": "https://a.org/"}]
        rejected, state = [], {"brown-university": {"status": "open", "page_hash": "abc"}}
        self.sync.apply_decisions([
            {"headRefName": "bot/new-route/some-award", "state": "CLOSED", "mergedAt": None, "url": "u1"},
            {"headRefName": "bot/page-update/brown-university", "state": "CLOSED", "mergedAt": None, "url": "u2"},
        ], cands, rejected, state, "2026-11-01")
        self.assertEqual(cands[0]["status"], "rejected")
        self.assertEqual(rejected[0]["name"], "Some Award")
        self.assertEqual(state["brown-university"]["status"], "rejected")


class PoisonedData(unittest.TestCase):
    """A bad value written by the weekly bot must stop the build."""

    def poisoned_build(self, mutate):
        tmp = make_copy()
        try:
            path = tmp / "data" / "data.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            mutate(data)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return build(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def assertRejected(self, mutate, needle):
        result = self.poisoned_build(mutate)
        self.assertNotEqual(result.returncode, 0, "poisoned data was accepted")
        self.assertIn(needle, result.stderr)

    def test_javascript_link_rejected(self):
        self.assertRejected(lambda d: d[0].__setitem__("link", "javascript:alert(1)"), "plain https:// URL")

    def test_http_link_rejected(self):
        self.assertRejected(lambda d: d[0].__setitem__("link", "http://example.com"), "plain https:// URL")

    def test_markup_in_text_rejected(self):
        self.assertRejected(lambda d: d[0].__setitem__("name", "X <img src=x onerror=alert(1)>"), "markup")

    def test_update_for_a_missing_route_rejected(self):
        tmp = make_copy()
        try:
            path = tmp / "content" / "updates.json"
            items = json.loads(path.read_text(encoding="utf-8"))
            items[0]["slug"] = "no-such-route"
            path.write_text(json.dumps(items), encoding="utf-8")
            result = build(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertNotEqual(result.returncode, 0, "an update about a missing route was accepted")
        self.assertIn("matches no route", result.stderr)

    def test_markup_in_a_country_glance_rejected(self):
        tmp = make_copy()
        try:
            path = tmp / "content" / "countries" / "germany.json"
            c = json.loads(path.read_text(encoding="utf-8"))
            c["glance"][0][1] = "<script>alert(1)</script>"
            path.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
            result = build(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertNotEqual(result.returncode, 0, "markup in a country glance was accepted")
        self.assertIn("plain text", result.stderr)

    def positions_build(self, mutate):
        tmp = make_copy()
        try:
            path = tmp / "content" / "positions.json"
            items = json.loads(path.read_text(encoding="utf-8"))
            mutate(items)
            path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
            result = build(tmp)
            page = (tmp / "_site" / "positions" / "index.html")
            return result, (page.read_text(encoding="utf-8") if page.exists() else "")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_closed_position_is_not_listed(self):
        def close_first(items):
            items[0]["deadline"] = "2020-01-01"
            items[0]["title"] = "A position that closed long ago"
        result, page = self.positions_build(close_first)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("A position that closed long ago", page)

    def test_position_without_its_own_write_up_rejected(self):
        result, _ = self.positions_build(lambda items: items[0].pop("details"))
        self.assertNotEqual(result.returncode, 0, "a position with no write-up for its own page was accepted")
        self.assertIn("'details' must hold", result.stderr)

    def test_markup_in_a_position_write_up_rejected(self):
        result, _ = self.positions_build(lambda items: items[0]["details"]["requirements"].append("<script>alert(1)</script>"))
        self.assertNotEqual(result.returncode, 0, "markup in a position's write-up was accepted")
        self.assertIn("plain-text entries", result.stderr)

    def test_markup_in_a_position_rejected(self):
        result, _ = self.positions_build(lambda items: items[0].__setitem__("summary", "<img src=x onerror=alert(1)>"))
        self.assertNotEqual(result.returncode, 0, "markup in a position was accepted")
        self.assertIn("plain text", result.stderr)

    def test_position_with_an_unsafe_link_rejected(self):
        result, _ = self.positions_build(lambda items: items[0].__setitem__("link", "javascript:alert(1)"))
        self.assertNotEqual(result.returncode, 0, "a javascript: link was accepted")
        self.assertIn("plain https:// URL", result.stderr)

    def news_build(self, mutate):
        tmp = make_copy()
        try:
            path = tmp / "content" / "news.json"
            items = json.loads(path.read_text(encoding="utf-8"))
            mutate(items)
            path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
            return build(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_news_without_its_story_rejected(self):
        result = self.news_build(lambda items: items[0].pop("body"))
        self.assertNotEqual(result.returncode, 0, "a story with nothing written was accepted")
        self.assertIn("'body' must be", result.stderr)

    def test_news_story_linking_out_rejected(self):
        def link_out(items):
            items[0]["body"][0] += " Read [the original](https://example.com/news/)."
        result = self.news_build(link_out)
        self.assertNotEqual(result.returncode, 0, "a story linking to another site was accepted")
        self.assertIn("must be a page on this site", result.stderr)

    def test_story_link_to_a_closed_position_keeps_its_words(self):
        tmp = make_copy()
        try:
            news = json.loads((tmp / "content" / "news.json").read_text(encoding="utf-8"))
            found = [(n["id"], m.group(1), m.group(2)) for n in news for x in n["body"]
                     for m in re.finditer(r"\[([^\]]+)\]\(\{\{root\}\}positions/([a-z0-9-]+)/\)", x)]
            if not found:
                self.skipTest("no story links to a position")
            story_id, label, position_id = found[0]
            path = tmp / "content" / "positions.json"
            items = json.loads(path.read_text(encoding="utf-8"))
            next(p for p in items if p["id"] == position_id)["deadline"] = "2020-01-01"
            path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
            result = build(tmp)
            story = (tmp / "_site" / "news" / story_id / "index.html").read_text(encoding="utf-8")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(result.returncode, 0, result.stderr)  # no broken link once the position's page is gone
        self.assertIn(html_mod.escape(label, quote=False), story)
        self.assertNotIn(f"positions/{position_id}/", story)

    def test_news_with_markup_rejected(self):
        result = self.news_build(lambda items: items[0].__setitem__("summary", "<script>alert(1)</script>"))
        self.assertNotEqual(result.returncode, 0, "markup in news was accepted")
        self.assertIn("plain text", result.stderr)

    def test_news_image_with_a_noncommercial_licence_rejected(self):
        def nc(items):
            item = next(i for i in items if i.get("images"))
            item["images"][0]["license"] = "CC BY-NC 4.0"
        result = self.news_build(nc)
        self.assertNotEqual(result.returncode, 0, "a non-commercial image was accepted")
        self.assertIn("is not one we may use", result.stderr)

    def test_news_image_that_does_not_exist_rejected(self):
        def missing(items):
            item = next(i for i in items if i.get("images"))
            item["images"][0]["file"] = "news-no-such-picture.jpg"
        result = self.news_build(missing)
        self.assertNotEqual(result.returncode, 0, "a missing image was accepted")
        self.assertIn("must be an existing", result.stderr)

    def test_news_with_an_unknown_category_rejected(self):
        result = self.news_build(lambda items: items[0].__setitem__("category", "Gossip"))
        self.assertNotEqual(result.returncode, 0, "an unknown news category was accepted")
        self.assertIn("'category' must be one of", result.stderr)

    def test_impossible_deadline_rejected(self):
        self.assertRejected(lambda d: d[0].__setitem__("deadlines", [{"m": 2, "d": 31}]), "impossible deadline")

    def test_unknown_funding_type_rejected(self):
        self.assertRejected(lambda d: d[0].__setitem__("types", ["Free Money"]), "'types'")


class PageScript(unittest.TestCase):
    """The board's link filter, run in Node against the real app.js."""

    def test_safe_url(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        src = (ROOT / "src" / "assets" / "app.js").read_text(encoding="utf-8")
        fn = re.search(r"function safeUrl\(u\) \{.*?\n  \}", src, re.S)
        self.assertIsNotNone(fn, "safeUrl missing from app.js")
        script = fn.group(0) + """
const cases = {"https://ok.example/a": true, "javascript:alert(1)": false, "JAVASCRIPT:alert(1)": false,
  "http://plain.example": false, "data:text/html,x": false, "https://x.example/\\"onmouseover=1": false, "": false};
for (const [u, ok] of Object.entries(cases)) {
  if ((safeUrl(u) !== "#") !== ok) { console.error("wrong for " + u); process.exit(1); }
}"""
        r = subprocess.run([node, "-e", script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_pagination(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        src = (ROOT / "src" / "assets" / "app.js").read_text(encoding="utf-8")
        per = {k: int(v) for k, v in re.findall(r"var (PER_PAGE_\w+) = (\d+);", src)}
        self.assertEqual(per, {"PER_PAGE_ALL": 6, "PER_PAGE_FILTERED": 3, "PER_PAGE_DIR": 10})
        build_src = (ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn("PER_PAGE_ALL = 6", build_src)
        self.assertIn("PER_PAGE_DIR = 10", build_src)
        fns = [re.search(r"function %s\(.*?\n  \}" % name, src, re.S) for name in ("paginate", "pageList", "rangeText")]
        self.assertTrue(all(fns), "paginate/pageList/rangeText missing from app.js")
        script = "\n".join(f.group(0) for f in fns) + r"""
const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) { console.error(what, JSON.stringify(a), "!=", JSON.stringify(b)); process.exit(1); } };
// 52 routes, 6 per page: 9 pages, the last one holds 4
eq(paginate(52, 1, 6), {page: 1, pages: 9, start: 0, end: 6}, "first page");
eq(paginate(52, 9, 6), {page: 9, pages: 9, start: 48, end: 52}, "last page");
// out-of-range pages are pulled back in (e.g. after unsaving the last card on a page)
eq(paginate(52, 40, 6).page, 9, "too high");
eq(paginate(52, 0, 6).page, 1, "too low");
eq(paginate(3, 2, 3), {page: 1, pages: 1, start: 0, end: 3}, "clamp after shrink");
eq(paginate(0, 1, 3), {page: 1, pages: 1, start: 0, end: 0}, "empty");
// every item appears exactly once across the pages
for (const [total, per] of [[52, 6], [52, 3], [126, 10], [4, 3], [10, 10], [11, 10]]) {
  const seen = [];
  const pages = paginate(total, 1, per).pages;
  for (let p = 1; p <= pages; p++) { const g = paginate(total, p, per); for (let i = g.start; i < g.end; i++) seen.push(i); }
  eq(seen, Array.from({length: total}, (_, i) => i), "coverage " + total + "/" + per);
}
// page buttons: first, last, neighbours; a single skipped page is shown, longer runs become a gap
eq(pageList(1, 9), [1, 2, "gap", 9], "start");
eq(pageList(5, 9), [1, "gap", 4, 5, 6, "gap", 9], "middle");
eq(pageList(3, 9), [1, 2, 3, 4, "gap", 9], "near start");
eq(pageList(9, 9), [1, "gap", 8, 9], "end");
eq(pageList(1, 1), [1], "single");
eq(pageList(2, 3), [1, 2, 3], "short");
// the count line: a range, a single number for a one-item page, 0 when nothing matches
eq(rangeText(paginate(52, 2, 6)), "7–12", "range");
eq(rangeText(paginate(4, 2, 3)), "4", "single item");
eq(rangeText(paginate(0, 1, 3)), "0", "none");
"""
        r = subprocess.run([node, "-e", script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
