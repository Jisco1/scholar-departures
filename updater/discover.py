#!/usr/bin/env python3
"""
DegreeStep — discovery agent. Finds NEW funded routes, not just fresh
deadlines for existing ones.

How it works each run:
  1. Picks this week's search themes from a rotating pool (so different
     countries / levels / funding types get scanned over time).
  2. For each theme, makes ONE Claude API call with the web_search tool
     enabled — Claude searches, reads results, and returns candidate
     programmes as strict JSON in the site's exact schema.
  3. Each candidate is deduplicated against data.json, candidates.json and
     rejected.json (fuzzy name match + official-domain match), and its link
     is screened against an aggregator blocklist.
  4. Survivors get a verification pass: the official page is fetched and a
     second (cheap) Claude call must confirm the funding claim and extract
     the deadline from the page text itself.
  5. Verified candidates are appended to data/candidates.json as complete,
     ready-to-publish entries — they do NOT go live until approved with
     updater/approve.py (or --auto-publish, off by default; see README).

Environment:
  ANTHROPIC_API_KEY   required
  DISCOVER_MODEL      optional, default claude-sonnet-5 (search + judgment)
  VERIFY_MODEL        optional, default claude-haiku-4-5-20251001

Usage:
  python updater/discover.py                       # this week's themes
  python updater/discover.py --query "Denmark scholarships international"
  python updater/discover.py --max-candidates 4
  python updater/discover.py --auto-publish        # trust mode (careful)
"""

import argparse
import difflib
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Run: pip install requests beautifulsoup4")

from update_data import fetch_page_text  # reuse the same page reader

ROOT = Path(__file__).resolve().parent.parent
DATA_JSON = ROOT / "data" / "data.json"
CANDIDATES_JSON = ROOT / "data" / "candidates.json"
REJECTED_JSON = ROOT / "data" / "rejected.json"

API_URL = "https://api.anthropic.com/v1/messages"
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DISCOVER_MODEL = os.environ.get("DISCOVER_MODEL", "claude-sonnet-5")
VERIFY_MODEL = os.environ.get("VERIFY_MODEL", "claude-haiku-4-5-20251001")

VALID_TYPES = ["Tuition-Free", "Full Scholarship", "Need-Based Aid", "Merit", "Low Tuition", "Fee Waiver"]
VALID_LEVELS = ["Bachelor's", "Master's", "PhD"]
VALID_FIELDS = ["Engineering & Tech", "Computer Science", "Natural Sciences", "Medicine & Health",
                "Business & Economics", "Social Sciences & Law", "Arts & Humanities", "Agriculture & Environment"]
VERIFY_CONFIDENCE = 0.75

# Aggregator / listicle domains: fine as *sources*, never as the official link.
LINK_BLOCKLIST = {
    "scholarshipportal.com", "scholars4dev.com", "opportunitiesforafricans.com",
    "opportunitydesk.org", "scholarships360.org", "wemakescholars.com",
    "afterschoolafrica.com", "scholarshipscorner.website", "mladiinfo.eu",
    "youthop.com", "oyaop.com", "scholarshipsads.com",
}

# Query pool — two base themes every week + two rotating by ISO week number.
BASE_QUERIES = [
    "newly announced fully funded scholarship international students university 2027",
    "university becomes need-blind OR launches full scholarship international students",
]
ROTATING_QUERIES = [
    "Netherlands university full scholarship non-EU master's students",
    "Belgium university scholarship international students tuition waiver",
    "Denmark government scholarship non-EU international students university",
    "Ireland university full scholarship international students",
    "Poland NAWA scholarship international students fully funded",
    "Spain Portugal university scholarship international students tuition",
    "Estonia Slovenia tuition free English programmes international students",
    "France university excellence scholarship international master's",
    "Italy regional grant DSU international students university",
    "US college no application fee meets full need international students",
    "US university full ride merit scholarship open to international students",
    "fully funded PhD scholarships Europe international students government programme",
    "Germany university English master's scholarship stipend international",
    "Nordic university scholarship covering tuition non-EU students",
    "Canada university full scholarship international undergraduate students",
    "Canada fully funded graduate scholarship international PhD students",
    "Mastercard Foundation Scholars Program partner university applications open",
    "fully funded scholarship African students Ghana 2027 deadline university",
    "Commonwealth scholarship developing countries fully funded UK masters",
]

DISCOVERY_PROMPT = """You are the research agent for "DegreeStep", a curated directory of funded study routes in Europe, the USA and Canada for international students. Search the web on this theme and find programmes that are NOT in the directory yet.

Theme: {query}

The directory's quality bar — a candidate must meet at least one:
- a university where tuition is genuinely free (or under ~€1,000/semester) for international/non-EU students,
- a scholarship covering full tuition AND most living costs,
- a US school that is need-blind or meets 100% demonstrated need for internationals,
- a notable application-fee-waiver / no-fee policy at a strong school.

Hard exclusions:
- anything whose only evidence is an aggregator/listicle site — you must find the official university/government page,
- partial awards under ~50% of costs,
- known myths: Norway public unis (charge non-EU since 2023), TU Munich (non-EU fees since 2024), Baden-Württemberg state unis (€1,500/sem non-EU),
- programmes that no longer exist or whose last call was before 2025.

Already in the directory (do not return these): {existing_names}

Return AT MOST 3 candidates. End your reply with ONLY a JSON array (no prose after it):
[{{
  "name": "<programme or university name, short>",
  "flag": "<country flag emoji>",
  "country": "<country>",
  "region": "Europe", "USA", "Canada", or "Global" for a programme spanning many countries,
  "kind": "school" if the institution itself provides the money, or "program" if it is an external scholarship taken to a school,
  "scope": "all" if any discipline may apply, or "limited" if eligibility is restricted to certain fields,
  "fields": [<subset of {valid_fields}> — for "all" routes list where it is strongest, for "limited" list the eligible fields],
  "types": [<subset of {valid_types}>],
  "levels": [<subset of {valid_levels}>],
  "funding": "<one concrete sentence: what is covered, for whom>",
  "deadline": "<human-readable typical deadline>",
  "deadlines": [{{"m": <1-12>, "d": <1-31>}}],
  "approx": <true if date varies>,
  "official_link": "<the official university/government page URL>",
  "source_note": "<one line: how you confirmed it>"
}}]
If nothing solid is found, return []."""

VERIFY_PROMPT = """A discovery agent proposed adding this entry to a scholarship directory:

{candidate}

Here is the text of the official page at {url} (fetched just now, may be truncated):
---
{page_text}
---

From the page text ALONE, judge whether the entry is accurate enough to publish. Be strict: if the page does not support the funding claim, reject.

Respond with ONLY a JSON object:
{{
  "accept": <true/false>,
  "reason": "<one sentence>",
  "funding": "<refined one-sentence funding summary based on the page>",
  "deadline_text": "<deadline line from the page, or the candidate's if page omits it>",
  "deadlines": [{{"m": <1-12>, "d": <1-31>}}],
  "approx": <true/false>,
  "confidence": <0.0-1.0>
}}"""


def log(msg):
    print(msg, flush=True)


def call_claude(model, prompt, max_tokens, use_search=False):
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if use_search:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}]
    headers = {"x-api-key": API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    resp = requests.post(API_URL, json=body, headers=headers, timeout=180)
    if resp.status_code != 200:
        raise RuntimeError(f"API {resp.status_code}: {resp.text[:300]}")
    blocks = resp.json().get("content", [])
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def extract_json(text, opener, closer):
    s, e = text.find(opener), text.rfind(closer)
    if s == -1 or e == -1 or e < s:
        raise ValueError("no JSON found in model output")
    return json.loads(text[s:e + 1])


def norm(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def domain(url):
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def is_dup(candidate, known_names, known_domains):
    n = norm(candidate.get("name", ""))
    if not n:
        return True
    for k in known_names:
        if not k:
            continue
        if difflib.SequenceMatcher(None, n, k).ratio() > 0.85:
            return True
        # catches abbreviation variants: "RWTH Aachen Univ." vs "RWTH Aachen University"
        if len(n) >= 10 and len(k) >= 10 and (n in k or k in n):
            return True
    d = domain(candidate.get("official_link", ""))
    if d and d in known_domains:
        # same domain AND similar-ish name -> duplicate; same domain alone is
        # allowed (one university can host several distinct programmes)
        for k in known_names:
            if difflib.SequenceMatcher(None, n, k).ratio() > 0.6:
                return True
    return False


def valid_shape(c):
    try:
        if c.get("region") not in ("Europe", "USA", "Canada", "Global"):
            return False
        if c.get("kind") not in ("school", "program"):
            return False
        if not c.get("official_link", "").startswith("http"):
            return False
        if not (30 <= len(c.get("funding", "")) <= 320):
            return False
        if not c.get("types") or not set(c["types"]).issubset(VALID_TYPES):
            return False
        if not c.get("levels") or not set(c["levels"]).issubset(VALID_LEVELS):
            return False
        for dl in c.get("deadlines", []):
            if not (1 <= int(dl["m"]) <= 12 and 1 <= int(dl["d"]) <= 31):
                return False
        return bool(c.get("deadlines"))
    except (KeyError, TypeError, ValueError):
        return False


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return default


def write_site_data(data):
    DATA_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", default="", help="run a single custom theme instead of the rotation")
    ap.add_argument("--max-candidates", type=int, default=6, help="cap on new candidates per run")
    ap.add_argument("--auto-publish", action="store_true",
                    help="merge verified candidates straight into data.json (default: queue for approval)")
    args = ap.parse_args()

    if not API_KEY:
        log("ANTHROPIC_API_KEY not set — discovery needs it. Skipping (exit 0).")
        return

    data = load_json(DATA_JSON, [])
    candidates = load_json(CANDIDATES_JSON, [])
    rejected = load_json(REJECTED_JSON, [])

    incoming = [load_json(p, {}) for p in sorted((ROOT / "data" / "incoming").glob("*.json"))]
    known_names = ({norm(x["name"]) for x in data + incoming if x.get("name")} | {norm(x["name"]) for x in candidates}
                   | {norm(x.get("name", "")) for x in rejected})
    known_domains = {domain(x.get("link", "")) for x in data + incoming} | {domain(x.get("link", "")) for x in candidates}
    known_domains.discard("")

    if args.query:
        queries = [args.query]
    else:
        week = date.today().isocalendar()[1]
        queries = BASE_QUERIES + [
            ROTATING_QUERIES[week % len(ROTATING_QUERIES)],
            ROTATING_QUERIES[(week + 7) % len(ROTATING_QUERIES)],
        ]

    today = date.today().isoformat()
    accepted, seen_this_run = [], 0

    for q in queries:
        if len(accepted) >= args.max_candidates:
            break
        log(f"\n🔭 Theme: {q}")
        try:
            text = call_claude(
                DISCOVER_MODEL,
                DISCOVERY_PROMPT.format(
                    query=q,
                    existing_names=", ".join(sorted(x["name"] for x in data))[:2500],
                    valid_types=VALID_TYPES, valid_levels=VALID_LEVELS,
                    valid_fields=VALID_FIELDS,
                ),
                max_tokens=2500, use_search=True,
            )
            found = extract_json(text, "[", "]")
        except Exception as e:
            log(f"   discovery call failed: {e}")
            continue

        log(f"   model proposed {len(found)} candidate(s)")
        for c in found:
            seen_this_run += 1
            name = c.get("name", "?")
            if not valid_shape(c):
                log(f"   ✗ {name}: bad shape, skipped"); continue
            if domain(c["official_link"]) in LINK_BLOCKLIST:
                log(f"   ✗ {name}: link is an aggregator, skipped"); continue
            if is_dup(c, known_names, known_domains):
                log(f"   – {name}: already known, skipped"); continue

            # verification pass against the official page
            try:
                page = fetch_page_text(c["official_link"])
                vtext = call_claude(
                    VERIFY_MODEL,
                    VERIFY_PROMPT.format(candidate=json.dumps(c, ensure_ascii=False), url=c["official_link"], page_text=page),
                    max_tokens=700,
                )
                v = extract_json(vtext, "{", "}")
            except Exception as e:
                log(f"   ✗ {name}: verification failed ({e})"); continue

            if not (v.get("accept") and isinstance(v.get("confidence"), (int, float)) and v["confidence"] >= VERIFY_CONFIDENCE):
                log(f"   ✗ {name}: rejected by verifier ({v.get('reason', 'low confidence')})"); continue

            entry = {
                "name": c["name"], "flag": c.get("flag", "🎓"), "country": c["country"], "kind": c.get("kind", "school"), "scope": c.get("scope", "all"),
                "fields": [f for f in c.get("fields", []) if f in VALID_FIELDS] or VALID_FIELDS,
                "region": c["region"], "types": c["types"], "levels": c["levels"],
                "funding": v.get("funding") or c["funding"],
                "deadline": v.get("deadline_text") or c["deadline"],
                "deadlines": v.get("deadlines") or c["deadlines"],
                "approx": bool(v.get("approx", c.get("approx", True))),
                "link": c["official_link"],
                "last_verified": today, "content_hash": None, "needs_review": False,
            }
            meta = {"status": "pending", "discovered": today, "theme": q,
                    "source_note": c.get("source_note", ""), "confidence": v["confidence"]}
            accepted.append({**entry, **meta})
            known_names.add(norm(entry["name"]))
            log(f"   ✓ {name} verified (confidence {v['confidence']:.2f})")
            if len(accepted) >= args.max_candidates:
                break
        time.sleep(1)

    log(f"\n— Discovery summary — proposed: {seen_this_run}, verified new: {len(accepted)}")

    if not accepted:
        log("No new candidates this run.")
        return

    if args.auto_publish:
        meta_keys = ("status", "discovered", "theme", "source_note", "confidence")
        clean = [{k: v for k, v in e.items() if k not in meta_keys} for e in accepted]
        data.extend(clean)
        write_site_data(data)
        log(f"AUTO-PUBLISHED {len(clean)} new route(s) into data.json.")
    else:
        candidates.extend(accepted)
        CANDIDATES_JSON.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"Queued {len(accepted)} candidate(s) in data/candidates.json — review with: python updater/approve.py --list")


if __name__ == "__main__":
    main()
