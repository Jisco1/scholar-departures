#!/usr/bin/env python3
"""
DegreeStep — stale-date finder. Free: no API, no network.

Route write-ups mention real dates ("closes 1 December 2026"). Once such a date
has passed, the sentence is out of date even though the board's countdown rolls
over by itself. This lists every route whose write-up still mentions a past date,
so the monthly run can refresh it and the daily build can report it.

Usage:
  python updater/stale.py              # plain list
  python updater/stale.py --summary    # Markdown for the GitHub Actions job summary
"""

import argparse
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTES_DIR = ROOT / "content" / "routes"

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9
MONTH_RE = "(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?"

# "27 September 2026", "1st Dec 2026"  |  "September 27, 2026", "Nov 2 2026"
DAY_FIRST = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + MONTH_RE + r",?\s+(\d{4})\b", re.I)
MONTH_FIRST = re.compile(r"\b" + MONTH_RE + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)

# fields that describe the route; "reviewed" and sources are bookkeeping, not claims
TEXT_KEYS = ("summary", "covers", "eligibility", "how_to_apply", "watch_out", "costs", "documents", "faq")


def dates_in(text):
    """Every full date written in the text, as (matched text, date)."""
    found = []
    for m in DAY_FIRST.finditer(text):
        d, mon, y = int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3))
        found.append((m.group(0), d, mon, y))
    for m in MONTH_FIRST.finditer(text):
        mon, d, y = MONTHS[m.group(1).lower()], int(m.group(2)), int(m.group(3))
        found.append((m.group(0), d, mon, y))
    out = []
    for raw, d, mon, y in found:
        try:
            out.append((raw, date(y, mon, d)))
        except ValueError:
            continue  # "31 February" is not a date; ignore rather than guess
    return out


def flatten(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from flatten(v)
    elif isinstance(value, dict):
        for v in value.values():
            yield from flatten(v)


# What the words just before a past date say about it:
#   rule       - a policy date that stays true ("TOEFL tests taken from 21 January 2026", "as of ...")
#   last_cycle - written as history ("the 2026 call closed on ...", "it ran from ... to ...")
#   outdated   - still phrased as a live deadline ("apply by ...", "closes on ...") — wrong once passed
RULE_CUE = re.compile(r"\b(?:taken|tests?|scale|used)\s+(?:from|before)\s*$|\bas of\s*$", re.I)
PAST_CUE = re.compile(r"\b(?:closed|opened|ran|was|were|ended|announced|launched|started|began)\b", re.I)
LIVE_CUE = re.compile(r"\b(?:by|closes?|deadline|due|until|before|apply|submit|opens?)\b", re.I)


def classify(text, start):
    window = text[max(0, start - 50):start]
    window = re.split(r"[.;!?]\s", window)[-1]  # stay inside the sentence
    if RULE_CUE.search(window):
        return "rule"
    if PAST_CUE.search(window):
        return "last_cycle"
    if LIVE_CUE.search(window):
        return "outdated"
    return "last_cycle"


def past_dates(writeup, today):
    """Past dates in one write-up as (text, date, kind), oldest first, without repeats or rules."""
    seen, out = set(), []
    for key in TEXT_KEYS:
        for text in flatten(writeup.get(key)):
            for raw, when in dates_in(text):
                if when >= today or raw in seen:
                    continue
                kind = classify(text, text.find(raw))
                if kind == "rule":
                    continue
                seen.add(raw)
                out.append((raw, when, kind))
    return sorted(out, key=lambda x: x[1])


def stale_routes(today=None, routes_dir=ROUTES_DIR):
    """{slug: [(text, date, kind), ...]} for every write-up that mentions a past date."""
    today = today or date.today()
    report = {}
    for path in sorted(routes_dir.glob("*.json")):
        hits = past_dates(json.loads(path.read_text(encoding="utf-8")), today)
        if hits:
            report[path.stem] = hits
    return report


def outdated_slugs(report):
    return [slug for slug, hits in report.items() if any(kind == "outdated" for _, _, kind in hits)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", action="store_true", help="Markdown for a GitHub Actions job summary")
    args = ap.parse_args()
    report = stale_routes()
    outdated = outdated_slugs(report)
    last_cycle = [s for s in report if s not in outdated]
    if args.summary:
        print(f"## Route write-ups: {len(outdated)} outdated, {len(last_cycle)} describing last cycle\n")
        if outdated:
            print("**Outdated** — a deadline that has passed is still written as live. The monthly refresh fixes these first:\n")
            for slug in outdated:
                print(f"- **{slug}**: " + ", ".join(raw for raw, _, kind in report[slug] if kind == "outdated"))
        if last_cycle:
            print("\nDescribing last year's round (true, but due a refresh when the new round is announced): "
                  + ", ".join(last_cycle))
        return
    for slug, hits in report.items():
        print(f"{slug}: " + ", ".join(f"{raw} ({kind})" for raw, _, kind in hits))
    print(f"{len(outdated)} outdated, {len(last_cycle)} last-cycle.")


if __name__ == "__main__":
    main()
