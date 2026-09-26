#!/usr/bin/env python3
"""
DegreeStep — monthly write-up drafter. Proposes changes; never publishes them.

1. Page updates. Routes are picked in this order, at most --max-updates per run:
     a. write-ups that still present a passed deadline as live (updater/stale.py),
     b. routes whose official page changed this month (data/changed_routes.json,
        written by update_data.py),
     c. write-ups that describe last year's round.
   Claude (WRITE_MODEL) reads the current write-up and the text of the official
   pages, and returns a revised write-up plus a list of changes. Each change must
   quote the official page; quotes are checked against the fetched text.
2. New routes. Every discovery candidate still "pending" gets a complete write-up
   drafted from its official page, and a route record for data/incoming/.

Each proposal is saved as drafts/<kind>-<slug>/meta.json: the files it would change,
and the title and body of its pull request. updater/open_prs.py opens the PRs;
the owner merges (publish) or closes (reject) each one.

Environment:
  ANTHROPIC_API_KEY   required
  WRITE_MODEL         optional, default claude-sonnet-5

Usage:
  python updater/drafts.py                    # the monthly run
  python updater/drafts.py --max-updates 2 --max-new 1
  python updater/drafts.py --only brown-university   # one page, whatever its state
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stale  # noqa: E402  (same folder)

ROOT = Path(__file__).resolve().parent.parent
DATA_JSON = ROOT / "data" / "data.json"
CANDIDATES_JSON = ROOT / "data" / "candidates.json"
CHANGED_JSON = ROOT / "data" / "changed_routes.json"
STATE_JSON = ROOT / "data" / "writeup_state.json"
ROUTES_DIR = ROOT / "content" / "routes"
DRAFTS_DIR = ROOT / "drafts"

API_URL = "https://api.anthropic.com/v1/messages"
WRITE_MODEL = os.environ.get("WRITE_MODEL", "claude-sonnet-5")
MAX_PAGES = 4          # official pages read per route
MAX_PAGE_CHARS = 12000

LINK_MD = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
LIST_KEYS = {"covers": (2, 6), "eligibility": (2, 6), "how_to_apply": (2, 6), "watch_out": (2, 6), "documents": (2, 7)}

UPDATE_PROMPT = """You maintain one page of DegreeStep, a free directory of funded study routes for international students. Today is {today}.

WHY THIS PAGE IS BEING REVIEWED: {reason}

CURRENT WRITE-UP (JSON):
{writeup}

DIRECTORY RECORD (JSON):
{record}

OFFICIAL PAGES, fetched just now (may be truncated):
{pages}

Rules:
- Use ONLY facts stated in the official pages above. Keep an existing fact if the pages do not contradict it. Never invent a number, date, fee or requirement.
- Update what the pages show has changed: the new cycle's dates, fees, amounts, requirements.
- Never leave a passed deadline written as upcoming. If the pages give the new date, use it; if not, put the old one in the past tense ("the 2026 call closed on ...") or remove it.
- Keep the style: short, plain English, second person, British spelling, no marketing words. Keep list sizes: covers, eligibility, how_to_apply and watch_out 2-6 items each; costs 2-7 rows of ["what it is", "who pays"]; documents 2-7 items; faq exactly 3 items of {{"q": ..., "a": ...}}.
- Plain text only, no HTML. Links only as [label](https://...).
- sources: keep the existing ones that still apply and add every official page you used, as {{"title": ..., "url": "https://..."}}.

Reply with ONLY a JSON object:
{{"writeup": {{"summary": ..., "covers": [...], "eligibility": [...], "how_to_apply": [...], "watch_out": [...], "costs": [...], "documents": [...], "faq": [...], "sources": [...]}},
  "changes": [{{"change": "<one sentence>", "evidence": "<short exact quote from an official page>", "url": "<that page>"}}]}}
If nothing needs to change, reply {{"writeup": null, "changes": []}}."""

NEW_PROMPT = """You write pages for DegreeStep, a free directory of funded study routes for international students. Today is {today}.

A new route was found and verified. Write its page from the official page text ONLY.

ROUTE RECORD (JSON):
{record}

OFFICIAL PAGE, fetched just now (may be truncated):
{pages}

Rules:
- Use ONLY facts stated in the page text. Never invent a number, date, fee or requirement; if the page does not say, leave it out.
- Style: short, plain English, second person, British spelling, no marketing words.
- summary: 1-2 sentences. covers, eligibility, how_to_apply, watch_out: 2-6 items each. costs: 2-7 rows of ["what it is", "who pays"]. documents: 2-7 items. faq: exactly 3 items of {{"q": ..., "a": ...}}.
- Plain text only, no HTML. Links only as [label](https://...).
- sources: every official page you used, as {{"title": ..., "url": "https://..."}}.

Reply with ONLY a JSON object:
{{"writeup": {{"summary": ..., "covers": [...], "eligibility": [...], "how_to_apply": [...], "watch_out": [...], "costs": [...], "documents": [...], "faq": [...], "sources": [...]}},
  "changes": [{{"change": "<the key fact>", "evidence": "<short exact quote from the page>", "url": "<the page>"}}]}}"""


def log(msg):
    print(msg, flush=True)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def slugify(text):
    text = text.lower()
    for a, b in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("é", "e"), ("è", "e"), ("ı", "i"), ("ş", "s"), ("ç", "c"), ("ğ", "g"), ("ß", "ss")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:70]


# ---------------------------------------------------------------- I/O the tests replace

def call_model(prompt, max_tokens=6000):
    """One Claude call; returns the reply text."""
    import requests
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    resp = requests.post(API_URL, timeout=240, json={
        "model": WRITE_MODEL, "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }, headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    if resp.status_code != 200:
        raise RuntimeError(f"API {resp.status_code}: {resp.text[:300]}")
    return "".join(b.get("text", "") for b in resp.json().get("content", []) if b.get("type") == "text")


def fetch(url):
    from update_data import fetch_page_text
    return fetch_page_text(url)[:MAX_PAGE_CHARS]


# ---------------------------------------------------------------- checks

def check_text(s, where, problems, limit):
    if not isinstance(s, str) or not s.strip():
        problems.append(f"{where}: empty or not text")
        return
    if len(s) > limit:
        problems.append(f"{where}: longer than {limit} characters")
    if re.search(r"[<>\x00-\x1f]", s):
        problems.append(f"{where}: contains markup or control characters")
    for m in LINK_MD.finditer(s):
        url = m.group(2)
        if not (url.startswith("https://") or url.startswith("{{root}}")):
            problems.append(f"{where}: link must be https:// — {url}")


def validate_writeup(w):
    """Problems that would stop a write-up being proposed ([] means fine)."""
    problems = []
    if not isinstance(w, dict):
        return ["write-up is not an object"]
    check_text(w.get("summary"), "summary", problems, 600)
    for key, (lo, hi) in LIST_KEYS.items():
        items = w.get(key)
        if not isinstance(items, list) or not lo <= len(items) <= hi:
            problems.append(f"{key}: needs {lo}-{hi} items")
            continue
        for i, s in enumerate(items):
            check_text(s, f"{key}[{i}]", problems, 450)
    costs = w.get("costs")
    if not isinstance(costs, list) or not 2 <= len(costs) <= 7:
        problems.append("costs: needs 2-7 rows")
    else:
        for i, row in enumerate(costs):
            if not (isinstance(row, list) and len(row) == 2):
                problems.append(f"costs[{i}]: must be [what, who pays]")
                continue
            for s in row:
                check_text(s, f"costs[{i}]", problems, 300)
    faq = w.get("faq")
    if not isinstance(faq, list) or not 2 <= len(faq) <= 4:
        problems.append("faq: needs 2-4 questions")
    else:
        for i, x in enumerate(faq):
            if not isinstance(x, dict):
                problems.append(f"faq[{i}]: must have q and a")
                continue
            check_text(x.get("q"), f"faq[{i}].q", problems, 200)
            check_text(x.get("a"), f"faq[{i}].a", problems, 700)
    sources = w.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 16:
        problems.append("sources: needs 1-16 entries")
    else:
        for i, s in enumerate(sources):
            if not (isinstance(s, dict) and isinstance(s.get("url"), str) and s["url"].startswith("https://")):
                problems.append(f"sources[{i}]: needs an https:// url")
            else:
                check_text(s.get("title"), f"sources[{i}].title", problems, 200)
    return problems


def squash(s):
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def verify_changes(changes, pages):
    """Mark each change whose quote really appears in a fetched official page."""
    texts = {url: squash(t) for url, t in pages.items()}
    everything = " ".join(texts.values())
    out = []
    for c in changes if isinstance(changes, list) else []:
        if not isinstance(c, dict) or not c.get("change"):
            continue
        quote = squash(c.get("evidence", ""))
        found = len(quote) >= 8 and (quote in texts.get(c.get("url"), "") or quote in everything)
        out.append({"change": str(c["change"]), "evidence": str(c.get("evidence", "")), "url": str(c.get("url", "")), "verified": found})
    return out


def parse_reply(text):
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e < s:
        raise ValueError("no JSON object in the reply")
    return json.loads(text[s:e + 1])


# ---------------------------------------------------------------- choosing what to review

def pick_updates(today, only, limit, state):
    """[(slug, reason), ...] in priority order."""
    if only:
        return [(only, "requested by hand")]
    report = stale.stale_routes(today, ROUTES_DIR)
    outdated = stale.outdated_slugs(report)
    changed = [s for s in load_json(CHANGED_JSON, []) if (ROUTES_DIR / f"{s}.json").exists()]
    last_cycle = [s for s in report if s not in outdated]
    picked, seen = [], set()
    for slugs, why in ((outdated, "the write-up still presents a passed deadline as upcoming"),
                       (changed, "the official page changed since last month"),
                       (last_cycle, "the write-up describes last year's round")):
        for slug in slugs:
            if slug in seen or state.get(slug, {}).get("status") == "open":
                continue
            seen.add(slug)
            picked.append((slug, why))
    return picked[:limit]


def official_urls(record, writeup):
    urls = [record.get("check_url"), record.get("link")]
    urls += [s.get("url") for s in writeup.get("sources", []) if isinstance(s, dict)]
    out = []
    for u in urls:
        if u and u.startswith("https://") and u not in out and "drive.google.com" not in u:
            out.append(u)
    return out[:MAX_PAGES]


def read_pages(urls):
    pages = {}
    for u in urls:
        try:
            pages[u] = fetch(u)
        except Exception as e:  # a blocked page is common; carry on with the others
            log(f"   could not read {u}: {str(e)[:120]}")
    return pages


def pages_block(pages):
    return "\n\n".join(f"=== {url} ===\n{text}" for url, text in pages.items())


def page_hash(pages):
    return hashlib.sha256("".join(pages[u] for u in sorted(pages)).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the pull-request text

def changes_md(changes):
    lines = []
    for c in changes:
        mark = "✓ quote found on the page" if c["verified"] else "⚠ quote NOT found — check this one"
        src = f" ([source]({c['url']}))" if c["url"].startswith("https://") else ""
        lines.append(f"- {c['change']}  \n  > {c['evidence']}{src} — {mark}")
    return "\n".join(lines) or "- (no individual changes listed)"


def footer(kind):
    reject = ("it will not be proposed again until the official page changes" if kind == "page-update"
              else "it goes on the rejected list and is never suggested again")
    return (f"\n---\n**Merge** to publish · **Close** to reject ({reject}).\n\n"
            f"Drafted automatically by Claude ({WRITE_MODEL}) from the official pages; the site's build passed on this branch.")


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-updates", type=int, default=6)
    ap.add_argument("--max-new", type=int, default=3)
    ap.add_argument("--only", default="", help="review one route slug, whatever its state")
    args = ap.parse_args(argv)

    today = date.today()
    data = load_json(DATA_JSON, [])
    by_slug = {r.get("slug") or slugify(r["name"]): r for r in data}
    state = load_json(STATE_JSON, {})
    candidates = load_json(CANDIDATES_JSON, [])
    made = 0

    # 1. page updates
    for slug, reason in pick_updates(today, args.only, args.max_updates, state):
        record, path = by_slug.get(slug), ROUTES_DIR / f"{slug}.json"
        if not record or not path.exists():
            continue
        log(f"→ {slug}: {reason}")
        current = json.loads(path.read_text(encoding="utf-8"))
        pages = read_pages(official_urls(record, current))
        if not pages:
            log("   no official page could be read — skipped")
            continue
        h = page_hash(pages)
        if state.get(slug, {}).get("status") == "rejected" and state[slug].get("page_hash") == h:
            log("   you rejected a proposal for these same pages — skipped")
            continue
        editable = {k: v for k, v in current.items() if k not in ("related", "reviewed")}
        try:
            reply = parse_reply(call_model(UPDATE_PROMPT.format(
                today=today.isoformat(), reason=reason,
                writeup=json.dumps(editable, ensure_ascii=False, indent=1),
                record=json.dumps({k: record.get(k) for k in ("name", "country", "funding", "deadline", "levels", "types", "link")}, ensure_ascii=False),
                pages=pages_block(pages))))
        except Exception as e:
            log(f"   drafting failed: {e}")
            continue
        new = reply.get("writeup")
        if not new:
            log("   Claude found nothing to change")
            continue
        problems = validate_writeup(new)
        if problems:
            log("   draft rejected by checks: " + "; ".join(problems[:5]))
            continue
        changes = verify_changes(reply.get("changes"), pages)
        if not any(c["verified"] for c in changes):
            log("   no change could be matched to a quote on the official pages — skipped")
            continue
        merged = {**new, "related": current.get("related", []), "reviewed": today.isoformat()}
        body = (f"## Proposed update: {record['name']}\n\n**Why now:** {reason}.\n\n### What changed\n{changes_md(changes)}\n\n"
                f"Live page after merging: https://degreestep.com/routes/{slug}/\n" + footer("page-update"))
        save_json(DRAFTS_DIR / f"page-update-{slug}" / "meta.json", {
            "kind": "page-update", "slug": slug, "branch": f"bot/page-update/{slug}", "page_hash": h,
            "title": f"Update {record['name']} — {changes[0]['change'][:60]}",
            "body": body,
            "files": {f"content/routes/{slug}.json": json.dumps(merged, ensure_ascii=False, indent=2) + "\n"},
        })
        made += 1
        log(f"   ✓ proposal saved ({sum(c['verified'] for c in changes)}/{len(changes)} quotes verified)")

    # 2. new routes found by discovery
    new_count = 0
    for cand in candidates:
        if cand.get("status") != "pending" or new_count >= args.max_new or args.only:
            continue
        slug = cand.get("slug") or slugify(cand["name"])
        if slug in by_slug or (ROUTES_DIR / f"{slug}.json").exists():
            continue
        log(f"→ new route: {cand['name']}")
        record = {k: v for k, v in cand.items() if k not in ("status", "discovered", "theme", "source_note", "confidence", "resolved")}
        record["slug"] = slug
        pages = read_pages([record["link"]])
        if not pages:
            log("   official page could not be read — skipped")
            continue
        try:
            reply = parse_reply(call_model(NEW_PROMPT.format(
                today=today.isoformat(), record=json.dumps(record, ensure_ascii=False, indent=1), pages=pages_block(pages))))
        except Exception as e:
            log(f"   drafting failed: {e}")
            continue
        w = reply.get("writeup")
        problems = validate_writeup(w)
        if problems:
            log("   draft rejected by checks: " + "; ".join(problems[:5]))
            continue
        changes = verify_changes(reply.get("changes"), pages)
        cand["slug"] = slug
        w = {**w, "related": [], "reviewed": today.isoformat()}
        body = (f"## New route: {record.get('flag', '')} {record['name']} ({record['country']})\n\n"
                f"**Funding:** {record['funding']}  \n**Deadline:** {record['deadline']}  \n"
                f"**Levels:** {', '.join(record['levels'])} · **Types:** {', '.join(record['types'])}  \n"
                f"**Official page:** {record['link']}\n\n"
                f"Found by the monthly search (theme: \"{cand.get('theme', '')}\"), checked against the official page "
                f"(confidence {cand.get('confidence', '?')}).\n\n### Key facts, with quotes\n{changes_md(changes)}\n"
                + footer("new-route"))
        save_json(DRAFTS_DIR / f"new-route-{slug}" / "meta.json", {
            "kind": "new-route", "slug": slug, "branch": f"bot/new-route/{slug}", "page_hash": page_hash(pages),
            "title": f"Add {record['name']} ({record['country']})",
            "body": body,
            "files": {f"data/incoming/{slug}.json": json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                      f"content/routes/{slug}.json": json.dumps(w, ensure_ascii=False, indent=2) + "\n"},
        })
        made += 1
        new_count += 1
        log("   ✓ proposal saved")

    save_json(CANDIDATES_JSON, candidates)
    log(f"\n{made} proposal(s) written to {DRAFTS_DIR.name}/")
    return made


if __name__ == "__main__":
    main()
