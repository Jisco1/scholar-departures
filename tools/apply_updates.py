"""Apply researched corrections to data/data.json.

usage: python tools/apply_updates.py updates.json

updates.json maps a route slug to the fields to set, e.g.
  {"rwth-aachen-university": {"deadline": "...", "deadlines": [{"m": 3, "d": 1}], "approx": true}}
Every route touched gets last_verified = today and needs_review = false.
"""
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
path = ROOT / "data" / "data.json"
data = json.loads(path.read_text(encoding="utf-8"))
updates = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
by_slug = {r["slug"]: r for r in data}
today = dt.date.today().isoformat()
for slug, fields in updates.items():
    if slug not in by_slug:
        sys.exit(f"unknown slug: {slug}")
    r = by_slug[slug]
    for k, v in fields.items():
        if v is None:
            r.pop(k, None)
        else:
            r[k] = v
    r["last_verified"] = today
    r["needs_review"] = False
    print(f"updated {slug}: {', '.join(fields)}")
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
