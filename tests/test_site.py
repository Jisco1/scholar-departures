"""Checks that run before every deploy.

    python -m unittest discover -s tests -v

Each test builds the site (or a poisoned copy of it) and inspects the output,
so a regression in the builder, the data or the page scripts fails here
instead of on the live site.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COPY = ["build.py", "site.json", "src", "content", "data"]


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
                self.assertRegex(menu, r'href="(?:(?:\.\./)*|https://[^"/]+/)' + re.escape(target) + '"', f"{name}: menu lacks {target}")
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

    def test_sitemap_lists_routes(self):
        sitemap = (self.site / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("/routes/chevening-scholarships/", sitemap)
        self.assertIn("/guides/scholarship-scams/", sitemap)
        self.assertNotIn("404", sitemap)


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
