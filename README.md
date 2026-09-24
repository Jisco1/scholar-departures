# Scholar Departures

Source for [scholardepartures.com](https://scholardepartures.com): tuition-free universities and
fully funded scholarships for international students in Europe, the USA and Canada, with a live
deadline board, a page for every route, country guides and application guides.

It is a static site. `build.py` (Python standard library only) turns the data and content into
`_site/`, and GitHub Actions tests, builds and publishes it to GitHub Pages.

```
build.py                    builds _site/ — validates data, renders every page, checks every internal link
site.json                   site settings: domain, contact email, AdSense IDs, optional premium tools
data/data.json              the routes (edited by hand and by the updater bots)
data/schools.json           the schools directory
content/routes/*.json       the write-up behind each route page (one per route, required)
content/countries/*.json    country guides
content/guides/*.html       long-form guides
content/pages/*.html        about, editorial policy, contact, privacy, terms
src/home.html               the live board (home page) template
src/assets/                 styles, scripts, icons
tests/test_site.py          checks that run before every deploy
updater/                    data refresh and discovery bots (see "Data refresh")
tools/                      helper scripts (icons, data corrections, external link check)
```

## Build and preview locally

```bash
python build.py
python -m http.server 8000 --directory _site
```

`python build.py --preview-ads` draws placeholder ad bays so you can review the layout.
`python -m unittest discover -s tests -v` runs the same checks as the deploy workflow.

## How a deploy works

Every push to `main` runs `.github/workflows/pages.yml`: tests → build → publish. If a test fails or
the data is invalid (a link that isn't `https://`, markup in a name, an impossible date, an unknown
funding type, a page linking to a page that doesn't exist), nothing is published and the live site
stays as it was. The site is also rebuilt daily so dates printed in the pages stay current.

## Editing a route

1. Change the entry in `data/data.json` (keep its `slug` — it is the page's URL).
2. Update `content/routes/<slug>.json` with the facts and the official sources you checked.
3. Set `last_verified` to today's date, then commit.

A route whose next call has not been announced gets `"tbc": true` instead of `deadlines`;
a route that is open all year gets `"rolling": true`.

## Security model

- No secrets in the site. There is no admin mode in the page; the repository is the admin.
- Every value from `data.json` is validated at build time and HTML-escaped when rendered; the board
  also refuses any link that is not `https://`.
- Each page carries a strict Content-Security-Policy with a new nonce every build, so injected
  scripts, inline event handlers and `javascript:` links do not run.
- GitHub Actions are pinned to exact commits, and workflows get the least permissions they need.

## Ads

Ads are off until `site.json` has an AdSense publisher ID (`ca-pub-…`). With an ID set, the build adds
the AdSense tag, the site-verification meta tag and `ads.txt`. Ad units appear only in labelled
"Advertisements" bays (`between`, `article`, `rail`) whose slot IDs are set in `site.json`; a bay
with no slot ID is not rendered. Bays are never placed next to buttons or link lists, hold their
height so the page never jumps, collapse if unfilled, and the sidebar bay appears only on wide screens.

## Data refresh

`.github/workflows/update.yml` re-checks every official page and looks for new routes with the
Anthropic API. It needs an `ANTHROPIC_API_KEY` repository secret and runs only when started from the
Actions tab. Uncertain changes go to `data/review_queue.json` and the old data stays live; new
routes are queued in `data/candidates.json` until approved with `python updater/approve.py`.
