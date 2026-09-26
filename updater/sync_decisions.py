#!/usr/bin/env python3
"""
DegreeStep — record what the owner decided about last month's proposals.

Reads the pull requests on bot/* branches (GitHub CLI) and updates, on main:
  new route merged    -> candidate "approved"; data/incoming/<slug>.json folded into data/data.json
  new route closed    -> candidate "rejected" and added to data/rejected.json (never suggested again)
  page update merged  -> writeup_state "merged"
  page update closed  -> writeup_state "rejected" (not proposed again until the official page changes)
Runs first in the monthly workflow, so decisions are respected before anything new is drafted.
"""

import json
import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_JSON = ROOT / "data" / "data.json"
INCOMING = ROOT / "data" / "incoming"
CANDIDATES_JSON = ROOT / "data" / "candidates.json"
REJECTED_JSON = ROOT / "data" / "rejected.json"
STATE_JSON = ROOT / "data" / "writeup_state.json"


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def bot_prs():
    r = subprocess.run(["gh", "pr", "list", "--state", "all", "--limit", "200", "--search", "head:bot/",
                        "--json", "number,state,headRefName,mergedAt,url"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        print(f"Could not list pull requests: {r.stderr.strip()[-200:]}")
        return []
    return json.loads(r.stdout or "[]")


def fold_incoming(data, incoming_dir=INCOMING):
    """Move merged new routes from data/incoming/ into data.json. Returns the slugs moved."""
    moved = []
    known = {r.get("slug") for r in data}
    for path in sorted(incoming_dir.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("slug") not in known:
            data.append(record)
            known.add(record.get("slug"))
        path.unlink()
        moved.append(record.get("slug"))
    return moved


def apply_decisions(prs, candidates, rejected, state, today):
    notes = []
    for pr in prs:
        parts = pr.get("headRefName", "").split("/")
        if len(parts) != 3 or parts[0] != "bot":
            continue
        kind, slug = parts[1], parts[2]
        merged = bool(pr.get("mergedAt"))
        closed = pr.get("state") == "CLOSED" and not merged
        if kind == "new-route":
            for c in candidates:
                if c.get("slug") != slug or c.get("status") in ("approved", "rejected"):
                    continue
                if merged:
                    c["status"], c["resolved"] = "approved", today
                    notes.append(f"approved new route {slug}")
                elif closed:
                    c["status"], c["resolved"] = "rejected", today
                    rejected.append({"name": c["name"], "link": c.get("link", ""), "date": today,
                                     "reason": f"pull request closed without merging ({pr.get('url', '')})"})
                    notes.append(f"rejected new route {slug}")
        elif kind == "page-update" and slug in state and state[slug].get("status") == "open":
            if merged:
                state[slug].update(status="merged", resolved=today)
                notes.append(f"published page update for {slug}")
            elif closed:
                state[slug].update(status="rejected", resolved=today)
                notes.append(f"rejected page update for {slug}")
    return notes


def main():
    today = date.today().isoformat()
    candidates, rejected, state = load(CANDIDATES_JSON, []), load(REJECTED_JSON, []), load(STATE_JSON, {})
    notes = apply_decisions(bot_prs(), candidates, rejected, state, today)
    data = load(DATA_JSON, [])
    moved = fold_incoming(data)
    if moved:
        DATA_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        notes.append("moved into data.json: " + ", ".join(moved))
    save(CANDIDATES_JSON, candidates)
    save(REJECTED_JSON, rejected)
    save(STATE_JSON, state)
    print("\n".join(notes) or "No decisions to record.")


if __name__ == "__main__":
    main()
