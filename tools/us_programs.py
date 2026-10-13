"""Which levels and subjects each US university in the DegreeStep directory awards degrees in, from IPEDS.

IPEDS (U.S. Department of Education, public domain): HD2024 gives each institution's website; C2023_A counts the
degrees awarded in 2022-23 by programme (CIP code) and award level. A school "offers" a level in a subject when it
awarded at least one first-major degree at that level in that CIP family that year.

usage: python -I tools/us_programs.py <folder with HD2024.zip and C2023_A.zip from nces.ed.gov/ipeds/datacenter/data/> .
writes <website repo>/data/us_programs.json
"""
import json, sys, collections
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

src, repo = Path(sys.argv[1]), Path(sys.argv[2])

from us_programs_lib import LEVELS, FIELDS, CIP_TO_FIELD, read_csv, host, base, norm  # noqa: E402


hd = {r["UNITID"]: r for r in read_csv(src / "HD2024.zip", r"hd2024\.csv")}
offers = collections.defaultdict(lambda: collections.defaultdict(set))
for r in read_csv(src / "C2023_A.zip", r"c2023_a\.csv"):
    lvl = LEVELS.get(r["AWLEVEL"].strip())
    if not lvl or r["MAJORNUM"].strip() != "1":
        continue
    cip = r["CIPCODE"].strip()
    field = CIP_TO_FIELD.get(cip[:2])
    if field is None or int(r["CTOTALT"] or 0) <= 0:
        continue
    offers[r["UNITID"]][lvl].add(field)

by_host, by_name = collections.defaultdict(list), collections.defaultdict(list)
for uid, r in hd.items():
    if uid in offers:
        if host(r["WEBADDR"]):
            by_host[base(host(r["WEBADDR"]))].append(uid)
        by_name[norm(r["INSTNM"])].append(uid)

world = json.loads((repo / "data" / "world_universities.json").read_text(encoding="utf-8"))
curated = json.loads((repo / "data" / "schools.json").read_text(encoding="utf-8"))
ours = [(s["name"], s["url"]) for s in world if s["country"] == "United States"]
ours += [(s["name"], s["link"]) for s in curated if s["country"] == "United States"]

out, how = {}, collections.Counter()
for name, url in ours:
    h = host(url)
    uids = [u for u in by_name.get(norm(name), []) if base(host(hd[u]["WEBADDR"])) == base(h)]  # same name and website
    if uids:
        how["name+site"] += 1
    else:
        uids = by_host.get(base(h), [])
        named = [u for u in uids if norm(hd[u]["INSTNM"]) == norm(name)]
        if named:
            uids = named
        if uids:
            how["site"] += 1
        else:
            uids = by_name.get(norm(name), [])
            if len(uids) == 1:
                how["name"] += 1
            else:
                how["unmatched"] += 1
                continue
    merged = collections.defaultdict(set)
    for u in uids:
        for lvl, fs in offers[u].items():
            merged[lvl] |= fs
    if merged:
        out[h] = {lvl: sorted(fs) for lvl, fs in sorted(merged.items())}

doc = {"source": "U.S. Department of Education, IPEDS: institutional directory 2024-25 and completions 2022-23",
       "levels": {"b": "Bachelor's", "m": "Master's", "d": "PhD"},
       "fields": [f for f, _ in FIELDS], "schools": dict(sorted(out.items()))}
(repo / "data" / "us_programs.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n",
                                                encoding="utf-8", newline="\n")
print("US schools in directory:", len(ours), "| matched:", dict(how), "| with offers:", len(out))
lv = collections.Counter(l for o in out.values() for l in o)
print("schools per level:", dict(lv))
fc = collections.Counter(FIELDS[f][0] for o in out.values() for fs in o.values() for f in fs)
print("top fields:", fc.most_common(6))
for probe in ["mit.edu", "harvard.edu", "stanford.edu", "bowdoin.edu"]:
    print(probe, out.get(probe))
