# DegreeStep

Source for [degreestep.com](https://degreestep.com): tuition-free universities and
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

## Monthly refresh

`.github/workflows/update.yml` runs at 06:00 UTC on the 1st of each month (or from the Actions tab)
and needs an `ANTHROPIC_API_KEY` repository secret:

1. `updater/sync_decisions.py` records last month's decisions: merged proposals are published,
   closed ones are remembered as rejected; merged new routes move from `data/incoming/` into `data/data.json`.
2. `updater/update_data.py` (Claude Haiku) re-checks every official page. Confident deadline changes are
   applied to `data/data.json`; uncertain ones go to `data/review_queue.json` with the old data kept live.
3. `updater/discover.py` (Claude Sonnet, verified by Haiku) searches for new routes → `data/candidates.json`.
4. Data changes are tested, committed to main and deployed.
5. `updater/drafts.py` (Claude Sonnet) drafts page updates — write-ups that present a passed deadline as
   live (`updater/stale.py`), whose official page changed, or that describe last year's round — and complete
   pages for new routes. Every change must quote the official page; unverifiable drafts are dropped.
6. `updater/open_prs.py` opens one pull request per proposal (labels `page-update`, `new-route`).
   **Merge to publish, close to reject.** Nothing drafted goes live without a merge.

The repository must allow Actions to open pull requests (Settings → Actions → General → Workflow permissions).
`updater/stale.py --summary` also runs in every daily build, for free, and lists write-ups with past dates.
