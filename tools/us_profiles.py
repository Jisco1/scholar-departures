"""Facts that belong to one US university, for its profile page, from IPEDS (U.S. Department of Education, public domain).

HD2024 (institutional directory, 2024-25): city and state, public or private, setting, size band, research
classification, HBCU / tribal / land-grant status, and the institution's own admissions, financial aid, application and
net price calculator pages. C2023_A (completions, 2022-23): degrees awarded at each level, and the fields with the most
graduates. Schools are matched to the directory exactly as tools/us_programs.py matches them (name and website).

usage: python -I tools/us_profiles.py <folder with HD2024.zip and C2023_A.zip> <website repo>
writes <website repo>/data/us_profiles.json, keyed by the school's website host
"""
import collections, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from us_programs_lib import read_csv, host, base, norm, FIELDS, CIP_TO_FIELD  # noqa: E402

src, repo = Path(sys.argv[1]), Path(sys.argv[2])

CONTROL = {"1": "public", "2": "private nonprofit", "3": "private for-profit"}
LOCALE = {"11": "a large city", "12": "a midsize city", "13": "a small city", "21": "the suburbs of a large city",
          "22": "the suburbs of a midsize city", "23": "the suburbs of a small city", "31": "a town on the edge of a city",
          "32": "a town", "33": "a remote town", "41": "a rural area near a city", "42": "a rural area", "43": "a remote rural area"}
SIZE = {"1": "under 1,000", "2": "1,000 to 4,999", "3": "5,000 to 9,999", "4": "10,000 to 19,999", "5": "20,000 or more"}
CARNEGIE = {"15": "a doctoral university with very high research activity (R1)",
            "16": "a doctoral university with high research activity (R2)",
            "17": "a doctoral and professional university",
            "18": "a master's university with large programmes", "19": "a master's university with medium programmes",
            "20": "a master's university with small programmes",
            "21": "a liberal arts college (arts and sciences focus)", "22": "a baccalaureate college across diverse fields"}
LEVEL = {"3": "a", "5": "b", "7": "m", "17": "d", "18": "p"}  # associate's, bachelor's, master's, research and professional doctorates


def url(u):
    u = (u or "").strip()
    if not u or " " in u or "<" in u or '"' in u:
        return None
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    u = re.sub(r"^http://", "https://", u, flags=re.I)
    return u if re.match(r"^https://[a-z0-9.-]+\.[a-z]{2,}(/[^\s]*)?$", u, re.I) else None


hd = {r["UNITID"]: r for r in read_csv(src / "HD2024.zip", r"hd2024\.csv")}
degrees = collections.defaultdict(lambda: collections.Counter())
fields = collections.defaultdict(lambda: collections.Counter())
for r in read_csv(src / "C2023_A.zip", r"c2023_a\.csv"):
    if r["MAJORNUM"].strip() != "1":
        continue
    lvl = LEVEL.get(r["AWLEVEL"].strip())
    if not lvl:
        continue
    n = int(r["CTOTALT"] or 0)
    cip = r["CIPCODE"].strip()
    if cip == "99":
        degrees[r["UNITID"]][lvl] += n
    elif re.fullmatch(r"[0-9]{2}[.][0-9]{4}", cip) and lvl in ("b", "m", "d"):  # six-digit programmes only
        f = CIP_TO_FIELD.get(cip[:2])
        if f is not None:
            fields[r["UNITID"]][f] += n

by_host, by_name = collections.defaultdict(list), collections.defaultdict(list)
for uid, r in hd.items():
    if host(r["WEBADDR"]):
        by_host[base(host(r["WEBADDR"]))].append(uid)
    by_name[norm(r["INSTNM"])].append(uid)

world = json.loads((repo / "data" / "world_universities.json").read_text(encoding="utf-8"))
curated = json.loads((repo / "data" / "schools.json").read_text(encoding="utf-8"))
ours = [(s["name"], s["url"]) for s in world if s["country"] == "United States"]
ours += [(s["name"], s["link"]) for s in curated if s["country"] == "United States"]

out, how = {}, collections.Counter()
for name, site in ours:
    h = host(site)
    uids = [u for u in by_name.get(norm(name), []) if base(host(hd[u]["WEBADDR"])) == base(h)]
    if not uids:
        named = [u for u in by_host.get(base(h), []) if norm(hd[u]["INSTNM"]) == norm(name)]
        uids = named or ([] if len(by_host.get(base(h), [])) != 1 else by_host[base(h)])
    if not uids:
        same = by_name.get(norm(name), [])
        uids = same if len(same) == 1 else []
    if len(uids) != 1:  # a profile describes one institution; several campuses under one site stay out
        how["skipped" if uids else "unmatched"] += 1
        continue
    how["matched"] += 1
    u = uids[0]
    r = hd[u]
    p = {"city": r["CITY"].strip().title() if r["CITY"].isupper() else r["CITY"].strip(), "state": r["STABBR"].strip()}
    for key, table, col in (("control", CONTROL, "CONTROL"), ("setting", LOCALE, "LOCALE"), ("size", SIZE, "INSTSIZE"),
                            ("kind", CARNEGIE, "C21BASIC")):
        v = table.get(r[col].strip())
        if v:
            p[key] = v
    flags = [label for col, label in (("HBCU", "a historically Black college or university"),
                                      ("TRIBAL", "a tribal college"), ("LANDGRNT", "a land-grant university"))
             if r[col].strip() == "1"]
    if flags:
        p["flags"] = flags
    links = {k: url(r[col]) for k, col in (("admissions", "ADMINURL"), ("aid", "FAIDURL"), ("apply", "APPLURL"),
                                            ("netprice", "NPRICURL"))}
    links = {k: v for k, v in links.items() if v}
    if links:
        p["links"] = links
    d = {k: v for k, v in degrees[u].items() if v > 0}
    if d:
        p["degrees"] = d
    top = [FIELDS[f][0] for f, n in fields[u].most_common(3) if n > 0]
    if top:
        p["top_fields"] = top
    out[h] = p

doc = {"source": "U.S. Department of Education, IPEDS: institutional directory 2024-25 and completions 2022-23",
       "year": "2022-23", "profiles": dict(sorted(out.items()))}
(repo / "data" / "us_profiles.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n",
                                                encoding="utf-8", newline="\n")
print("US schools in directory:", len(ours), "|", dict(how))
for probe in ["mit.edu", "bowdoin.edu", "aamu.edu", "berea.edu"]:
    print(probe, json.dumps(out.get(probe), ensure_ascii=False))
