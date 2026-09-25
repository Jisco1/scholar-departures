#!/usr/bin/env python3
"""
DegreeStep — automatic data updater.

What it does, in order, for every route in data/data.json:
  1. Fetches the official page (the route's `check_url`, falling back to `link`).
  2. Hashes the page text. If nothing changed since last run -> just bumps
     `last_checked` and moves on (no API cost).
  3. If the page DID change -> sends the page text + the current record to the
     Claude API and asks for a strict-JSON extraction of the deadline info and
     whether the funding description still holds.
  4. Applies the extraction ONLY if confidence >= CONFIDENCE_THRESHOLD and the
     funding summary is still consistent. Anything uncertain goes to
     data/review_queue.json with the OLD data kept live — a stale entry is
     better than a silently wrong one.
  5. Writes data/data.json; build.py validates it and regenerates the website.

Environment:
  ANTHROPIC_API_KEY   required for extraction (script still runs change
                      detection without it, queueing changes for review)
  MODEL               optional, defaults to claude-haiku-4-5-20251001

Usage:
  python updater/update_data.py             # full run
  python updater/update_data.py --dry-run   # no writes, just report
  python updater/update_data.py --only "RWTH"   # routes whose name contains
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Missing dependencies. Run: pip install requests beautifulsoup4")

ROOT = Path(__file__).resolve().parent.parent
DATA_JSON = ROOT / "data" / "data.json"
REVIEW_JSON = ROOT / "data" / "review_queue.json"

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("MODEL", "claude-haiku-4-5-20251001")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

CONFIDENCE_THRESHOLD = 0.8
FETCH_TIMEOUT = 25
MAX_PAGE_CHARS = 12000
USER_AGENT = (
    "Mozilla/5.0 (compatible; DegreeStepBot/1.0; +https://degreestep.com/editorial-policy/; "
    "+deadline verification for a free scholarship directory)"
)

EXTRACTION_PROMPT = """You are verifying one entry of a scholarship directory against the live text of its official web page.

CURRENT RECORD (what the directory says today):
{record}

OFFICIAL PAGE TEXT (fetched just now, may be truncated):
---
{page_text}
---

Task: from the page text alone, determine the application deadline(s) and whether the record's funding summary is still consistent with what the page says. Do not guess beyond the page text. If the page does not state a deadline, keep confidence low.

Respond with ONLY a JSON object, no markdown fences, no commentary:
{{
  "deadline_text": "<short human-readable deadline line, or null if page doesn't say>",
  "deadlines": [{{"m": <month 1-12>, "d": <day 1-31>}}],
  "approx": <true if the date varies by course/country or is stated vaguely>,
  "funding_consistent": <true if nothing on the page contradicts the record's funding summary>,
  "contradiction": "<one sentence describing any contradiction, or null>",
  "confidence": <0.0-1.0, how sure you are about the deadline extraction>
}}"""


def log(msg):
    print(msg, flush=True)


def fetch_page_text(url):
    resp = requests.get(url, timeout=FETCH_TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    return text[:MAX_PAGE_CHARS]


def call_claude(record, page_text):
    if not API_KEY:
        return None, "no ANTHROPIC_API_KEY set"
    slim = {k: record.get(k) for k in ("name", "country", "funding", "deadline", "deadlines", "approx")}
    prompt = EXTRACTION_PROMPT.format(
        record=json.dumps(slim, ensure_ascii=False, indent=2),
        page_text=page_text,
    )
    body = {
        "model": MODEL,
        "max_tokens": 600,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    resp = requests.post(API_URL, json=body, headers=headers, timeout=60)
    if resp.status_code != 200:
        return None, f"API {resp.status_code}: {resp.text[:200]}"
    blocks = resp.json().get("content", [])
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"unparseable extraction: {e}"


def valid_extraction(x):
    if not isinstance(x, dict):
        return False
    dls = x.get("deadlines")
    if not isinstance(dls, list) or not dls:
        return False
    for dl in dls:
        if not isinstance(dl, dict):
            return False
        m, d = dl.get("m"), dl.get("d")
        if not (isinstance(m, int) and 1 <= m <= 12 and isinstance(d, int) and 1 <= d <= 31):
            return False
    conf = x.get("confidence")
    return isinstance(conf, (int, float)) and 0 <= conf <= 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--only", default="", help="only process routes whose name contains this")
    args = ap.parse_args()

    data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    review_queue = []
    if REVIEW_JSON.exists():
        try:
            review_queue = json.loads(REVIEW_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            review_queue = []

    today = date.today().isoformat()
    stats = {"checked": 0, "unchanged": 0, "applied": 0, "queued": 0, "errors": 0}

    for entry in data:
        name = entry.get("name", "?")
        if args.only and args.only.lower() not in name.lower():
            continue
        url = entry.get("check_url") or entry.get("link")
        stats["checked"] += 1
        log(f"→ {name}")

        try:
            page_text = fetch_page_text(url)
        except Exception as e:
            stats["errors"] += 1
            entry["last_checked"] = today
            entry["fetch_error"] = str(e)[:200]
            log(f"   fetch failed: {e}")
            continue
        entry.pop("fetch_error", None)

        new_hash = hashlib.sha256(page_text.encode("utf-8")).hexdigest()
        if entry.get("content_hash") == new_hash:
            stats["unchanged"] += 1
            entry["last_checked"] = today
            log("   unchanged")
            continue

        if entry.get("rolling"):
            entry["content_hash"] = new_hash
            entry["last_checked"] = today
            log("   rolling entry — hash refreshed, no fixed deadline to extract")
            continue

        first_run = entry.get("content_hash") is None
        log("   page changed — extracting…" if not first_run else "   first hash — baselining + verifying…")
        extraction, err = call_claude(entry, page_text)
        entry["content_hash"] = new_hash
        entry["last_checked"] = today

        if err or not valid_extraction(extraction):
            stats["queued"] += 1
            entry["needs_review"] = True
            review_queue.append({
                "name": name, "url": url, "date": today,
                "reason": err or "extraction failed validation",
                "extraction": extraction,
            })
            log(f"   ⚠ queued for review ({err or 'invalid extraction'})")
            continue

        ok = (
            extraction["confidence"] >= CONFIDENCE_THRESHOLD
            and extraction.get("funding_consistent", False)
        )
        if ok:
            stats["applied"] += 1
            entry["deadlines"] = extraction["deadlines"]
            if extraction.get("deadline_text"):
                entry["deadline"] = extraction["deadline_text"]
            entry["approx"] = bool(extraction.get("approx"))
            entry.pop("tbc", None)
            entry["last_verified"] = today
            entry["needs_review"] = False
            log(f"   ✓ verified (confidence {extraction['confidence']:.2f})")
        else:
            stats["queued"] += 1
            entry["needs_review"] = True
            review_queue.append({
                "name": name, "url": url, "date": today,
                "reason": extraction.get("contradiction")
                or f"low confidence ({extraction.get('confidence')})",
                "extraction": extraction,
            })
            log("   ⚠ change detected but not confident — old data kept, queued for review")

        time.sleep(1)  # be polite to the schools' servers

    log("\n— Summary —")
    for k, v in stats.items():
        log(f"  {k}: {v}")

    if args.dry_run:
        log("(dry run — nothing written)")
        return

    DATA_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REVIEW_JSON.write_text(json.dumps(review_queue, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\nWrote {DATA_JSON.name}, {REVIEW_JSON.name}")


if __name__ == "__main__":
    main()
