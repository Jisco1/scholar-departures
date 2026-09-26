#!/usr/bin/env python3
"""
DegreeStep — turn drafts/*/meta.json into pull requests (one per proposal).

For each proposal: branch bot/<kind>/<slug> from origin/main, write its files,
run build.py (a draft that breaks the site is dropped), commit, push, and open a
pull request labelled page-update or new-route. The owner merges to publish or
closes to reject; updater/sync_decisions.py records the decision next month.

Needs git and the GitHub CLI (gh), with GH_TOKEN set — both are present on
GitHub's runners. The repository must allow GitHub Actions to create pull
requests (Settings → Actions → General → Workflow permissions).
"""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRAFTS = ROOT / "drafts"
STATE_JSON = ROOT / "data" / "writeup_state.json"
CANDIDATES_JSON = ROOT / "data" / "candidates.json"
LABELS = {"page-update": ("1d76db", "Proposed update to a route page"),
          "new-route": ("0e8a16", "Proposed new route for the board")}


def run(*cmd, check=True):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(r.stderr or r.stdout)[-400:]}")
    return r


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main():
    metas = sorted(DRAFTS.glob("*/meta.json"))
    if not metas:
        print("No proposals this month.")
        return
    for name, (color, desc) in LABELS.items():
        run("gh", "label", "create", name, "--color", color, "--description", desc, "--force", check=False)
    run("git", "fetch", "origin", "main")
    start = run("git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    state, candidates = load(STATE_JSON, {}), load(CANDIDATES_JSON, [])
    opened, summary = 0, []

    for meta_path in metas:
        m = json.loads(meta_path.read_text(encoding="utf-8"))
        branch = m["branch"]
        existing = run("gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number", check=False)
        if existing.returncode == 0 and json.loads(existing.stdout or "[]"):
            summary.append(f"- {m['title']} — already open, left as it is")
            continue
        try:
            run("git", "checkout", "-B", branch, "origin/main")
            for rel, content in m["files"].items():
                target = ROOT / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8", newline="\n")
            build = run(sys.executable, "build.py", check=False)
            if build.returncode != 0:
                summary.append(f"- {m['title']} — dropped: the site would not build with it")
                print(build.stdout[-800:], build.stderr[-800:])
                continue
            run("git", "add", *m["files"].keys())
            run("git", "commit", "-m", m["title"], "-m", "Drafted by the monthly refresh; merge to publish, close to reject.")
            run("git", "push", "--force", "origin", branch)
            body = DRAFTS / "body.md"
            body.write_text(m["body"], encoding="utf-8")
            pr = run("gh", "pr", "create", "--base", "main", "--head", branch, "--title", m["title"],
                     "--body-file", str(body), "--label", m["kind"], check=False)
            if pr.returncode != 0:
                summary.append(f"- {m['title']} — branch pushed, but the pull request could not be opened "
                               f"({pr.stderr.strip()[-160:]}). Check that Actions may create pull requests.")
                continue
            url = pr.stdout.strip().splitlines()[-1]
            summary.append(f"- [{m['title']}]({url})")
            opened += 1
            if m["kind"] == "page-update":
                state[m["slug"]] = {"status": "open", "branch": branch, "page_hash": m.get("page_hash"),
                                    "opened": date.today().isoformat(), "pr": url}
            else:
                for c in candidates:
                    if c.get("slug") == m["slug"] and c.get("status") == "pending":
                        c["status"] = "in-review"
                        c["pr"] = url
        except RuntimeError as e:
            summary.append(f"- {m['title']} — failed: {e}")
        finally:
            run("git", "checkout", "--force", start, check=False)

    save(STATE_JSON, state)
    save(CANDIDATES_JSON, candidates)
    print(f"## {opened} proposal(s) waiting for your review\n")
    print("\n".join(summary))


if __name__ == "__main__":
    main()
