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
        routes = json.loads((self.dir / "data" / "data.json").read_text(encoding="utf-8"))
        for r in routes:
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
        source = json.loads((self.dir / "data" / "data.json").read_text(encoding="utf-8"))
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

    def test_home_prerenders_first_page_only(self):
        # the static page must match what the script shows first, or the page jumps on load
        html = (self.site / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('class="card slim in ready"'), 6)
        self.assertEqual(html.count('class="dir-row in"'), 10)
        self.assertIn('id="gridPager"', html)
        self.assertIn('id="dirPager"', html)
        self.assertIn('href="routes/"', html)  # without JavaScript, every route is still one click away

    def test_phone_layout(self):
        html = (self.site / "index.html").read_text(encoding="utf-8")
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
            for target in ("routes/", "deadlines/", "countries/", "guides/", "about/", "#directory"):
                self.assertRegex(menu, r'href="(?:(?:\.\./)*|https://[^"]+/)' + re.escape(target) + '"', f"{name}: menu lacks {target}")
            self.assertEqual(menu.count('<details class="menu-sub"'), 3, f"{name}: Countries, Guides and About drop-downs")
            self.assertIn('data-theme-choice="light"', menu, f"{name}: no theme switch")
        home = (self.site / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("startguides", home)
        self.assertNotIn("country-strip", home)

    def test_light_theme_covers_every_colour(self):
        css = (ROOT / "src" / "assets" / "site.css").read_text(encoding="utf-8")
        dark = css[css.index(":root {"):css.index("}", css.index(":root {"))]
        light = css[css.index(':root[data-theme="light"] {'):]
        light = light[:light.index("}")]
        colours = {n for n, v in re.findall(r"(--[\w-]+):\s*([^;]+);", dark) if n not in ("--serif", "--sans", "--mono")}
        missing = colours - set(re.findall(r"(--[\w-]+):", light))
        self.assertFalse(missing, f"light theme lacks {sorted(missing)}")
        # outside the two token blocks, colours come from tokens, so a new rule cannot stay dark in the light theme
        rest = css.replace(dark, "").replace(light, "") + (ROOT / "src" / "assets" / "app.css").read_text(encoding="utf-8")
        rest = re.sub(r'url\("data:[^"]*"\)', "", rest)
        stray = set(re.findall(r"#[0-9A-Fa-f]{6}\b|rgba\((?!215,169,76)[^)]*\)", rest)) - {"#000"}
        self.assertFalse(stray, f"hard-coded colours outside the theme tokens: {sorted(stray)}")
        # the saved theme is applied before first paint, by a nonced script
        home = (self.site / "index.html").read_text(encoding="utf-8")
        self.assertRegex(home, r'<script nonce="[^"]+">try\{var t=localStorage\.getItem\(.sd-theme.\)')

    def test_share_on_every_route_and_guide(self):
        from urllib.parse import parse_qs, urlsplit
        shared = [p for p in self.pages if p.parent.parent.name in ("routes", "guides")]
        self.assertGreaterEqual(len(shared), 60)
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
