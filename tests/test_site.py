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


if __name__ == "__main__":
    unittest.main()
