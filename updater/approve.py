#!/usr/bin/env python3
"""
Scholar Departures — candidate approval CLI.

  python updater/approve.py --list           show pending candidates
  python updater/approve.py --approve 1,3    publish candidates #1 and #3
  python updater/approve.py --reject 2       reject #2 (never re-suggested)
  python updater/approve.py --reject 2 --reason "partial award only"

Approving merges the entry into data/data.json (build.py turns it into the site),
and the site picks it up automatically — counters, departures board and
filters are all computed from the data.
"""

import argparse
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_JSON = ROOT / "data" / "data.json"
CANDIDATES_JSON = ROOT / "data" / "candidates.json"
REJECTED_JSON = ROOT / "data" / "rejected.json"

META_KEYS = ("status", "discovered", "theme", "source_note", "confidence")


def load(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return default


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def write_site_data(data):
    save(DATA_JSON, data)


def pending(candidates):
    return [c for c in candidates if c.get("status") == "pending"]


def parse_ids(s, n):
    out = []
    for part in s.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= n:
            out.append(int(part) - 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--approve", default="")
    ap.add_argument("--reject", default="")
    ap.add_argument("--reason", default="")
    args = ap.parse_args()

    candidates = load(CANDIDATES_JSON, [])
    pend = pending(candidates)

    if args.list or (not args.approve and not args.reject):
        if not pend:
            print("No pending candidates. (Discovery runs weekly — or run updater/discover.py)")
            return
        for i, c in enumerate(pend, 1):
            print(f"\n#{i}  {c.get('flag','')} {c['name']} — {c['country']} ({c['region']})")
            print(f"    {c['funding']}")
            print(f"    Deadline: {c['deadline']}  |  Types: {', '.join(c['types'])}  |  Levels: {', '.join(c['levels'])}")
            print(f"    Link: {c['link']}")
            print(f"    Found {c.get('discovered','?')} via \"{c.get('theme','')}\" · verifier confidence {c.get('confidence','?')}")
        print(f"\n{len(pend)} pending. Approve with: python updater/approve.py --approve 1,2")
        return

    data = load(DATA_JSON, [])
    rejected = load(REJECTED_JSON, [])
    today = date.today().isoformat()

    if args.approve:
        for idx in parse_ids(args.approve, len(pend)):
            c = pend[idx]
            entry = {k: v for k, v in c.items() if k not in META_KEYS}
            entry["last_verified"] = today
            data.append(entry)
            c["status"] = "approved"
            c["resolved"] = today
            print(f"✓ Published: {c['name']}")
        write_site_data(data)
        save(CANDIDATES_JSON, candidates)
        print(f"\ndata.json now has {len(data)} routes. Commit & push to deploy.")

    if args.reject:
        for idx in parse_ids(args.reject, len(pend)):
            c = pend[idx]
            c["status"] = "rejected"
            c["resolved"] = today
            rejected.append({"name": c["name"], "link": c.get("link", ""), "date": today,
                             "reason": args.reason or "rejected via CLI"})
            print(f"✗ Rejected: {c['name']}")
        save(CANDIDATES_JSON, candidates)
        save(REJECTED_JSON, rejected)


if __name__ == "__main__":
    main()
