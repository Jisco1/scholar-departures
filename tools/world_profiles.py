"""Facts for universities outside the US, from Wikidata (CC0): founding year, student numbers (only with the year
they were counted), the town, and what kind of institution it is.

Raw input: one SPARQL result per country (higher-education institutions with an official website), fetched with the
user's agreement on 13 Oct 2026. A directory school is matched to a Wikidata item by its official website host; when
several items share a host (a university and its medical school), the item whose name matches ours wins, and if none
does clearly, the school is left out. Conflicting founding years are left out rather than guessed.

usage: python -I tools/world_profiles.py <folder of <CC>.json SPARQL results> <website repo>
writes <website repo>/data/world_profiles.json, keyed by the school's website host
"""
import collections, difflib, json, re, sys, unicodedata
from pathlib import Path

src, repo = Path(sys.argv[1]), Path(sys.argv[2])

ADMIN = re.compile(r"\b(region|district|province|county|state|municipality|prefecture|oblast|governorate|department|"
                   r"canton|voivodeship|metropolitan|division|territory|krai|okrug|raion|commune|arrondissement|emirate|parish|borough|shire)\b", re.I)
REGIONS = {"Hokkaido", "Queensland", "New South Wales", "Western Australia", "South Australia", "Tasmania", "Ontario",
           "Quebec", "British Columbia", "Alberta", "Manitoba", "Saskatchewan", "Nova Scotia", "North Rhine-Westphalia",
           "Bavaria", "Baden-Württemberg", "Lower Saxony", "Hesse", "Saxony", "Saxony-Anhalt", "Thuringia",
           "Rhineland-Palatinate", "Schleswig-Holstein", "Brandenburg", "Mecklenburg-Vorpommern", "Bashkortostan",
           "Tatarstan", "Catalonia", "Andalusia", "Lombardy", "Île-de-France", "Punjab", "Sindh", "Kerala", "Tamil Nadu",
           "Maharashtra", "Karnataka", "Gujarat", "Rajasthan", "Uttar Pradesh", "West Bengal", "Ashanti", "Lagos State"}
SKIP_TYPES = re.compile(r"^(higher education institution|educational institution|educational organization|organization|"
                        r"open-access publisher|business|legal person|nonprofit organization|foundation|charitable organization|"
                        r"faculty|academic department|department|campus|website|publisher|library|research institute)$", re.I)
SCHOOLISH = re.compile(r"university|college|institute|academy|polytechnic|conservatory|school|seminary|universit", re.I)
STOP = {"university", "universidad", "universidade", "universite", "universita", "universitat", "universiteit",
        "universitet", "uniwersytet", "of", "the", "de", "del", "la", "le", "di", "da", "do", "and", "et", "y", "e",
        "college", "institute", "institut", "instituto", "national", "state", "school", "higher", "for", "in", "at"}


def host(url):
    h = re.sub(r"^[a-z]+://", "", (url or "").strip().lower()).split("/")[0].split("?")[0].split(":")[0]
    return h[4:] if h.startswith("www.") else h


FOLD = str.maketrans({"ł": "l", "ø": "o", "æ": "ae", "œ": "oe", "đ": "d", "ð": "d", "ı": "i", "ß": "ss", "þ": "th"})


def words(name):
    t = unicodedata.normalize("NFKD", name.lower().translate(FOLD))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return {w for w in re.findall(r"[a-z0-9]+", t) if w not in STOP and len(w) > 1}


def spelled_alike(a, b):
    """The same name spelled another way: "Ubonratchathani" and "Ubon Ratchathani", "Jigjiga" and "Jijiga"."""
    ja, jb = "".join(sorted(words(a))), "".join(sorted(words(b)))
    return bool(ja and jb) and difflib.SequenceMatcher(None, ja, jb).ratio() >= 0.8


def score(a, b):
    wa, wb = words(a), words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def year(v):
    m = re.match(r"^(-?\d{1,4})-", v or "")
    return int(m.group(1)) if m else None


# one record per Wikidata item, merging the rows the grouping still splits (several towns, students counts)
items = {}
for path in sorted(src.glob("*.json")):
    for r in json.loads(path.read_text(encoding="utf-8")):
        qid = r["item"]["value"].rsplit("/", 1)[-1]
        it = items.setdefault(qid, {"qid": qid, "cc": path.stem, "name": r.get("itemLabel", {}).get("value", ""), "hosts": set(),
                                    "years": set(), "students": {}, "towns": collections.Counter(), "types": set()})
        it["hosts"].add(host(r["site"]["value"]))
        if "inception" in r and year(r["inception"]["value"]):
            it["years"].add(year(r["inception"]["value"]))
        if "students" in r and "studentsDate" in r:
            n, y = r["students"]["value"], year(r["studentsDate"]["value"])
            if re.fullmatch(r"\d+(\.0+)?", n) and y:
                it["students"][y] = int(float(n))
        if "cityLabel" in r:
            it["towns"][r["cityLabel"]["value"]] += 1
        for t in (r.get("types", {}).get("value") or "").split("|"):
            if t:
                it["types"].add(t)

by_host = collections.defaultdict(list)
for it in items.values():
    if not any(SCHOOLISH.search(t) and not SKIP_TYPES.match(t) for t in it["types"]):
        continue  # a faculty, department or campus record, not the institution
    for h in it["hosts"]:
        if h:
            by_host[h].append(it)


def describe(it):
    p = {"qid": it["qid"]}
    if len(it["years"]) == 1:
        y = next(iter(it["years"]))
        if 800 <= y <= 2026:
            p["founded"] = y
    if it["students"]:
        y = max(it["students"])
        if 2000 <= y <= 2026 and it["students"][y] > 0:
            p["students"], p["students_year"] = it["students"][y], y
    towns = [t for t, n in sorted(it["towns"].items(), key=lambda x: (-x[1], x[0])) if not ADMIN.search(t) and t not in REGIONS and not t.endswith("shire")]
    if towns and len(towns[0]) <= 60:
        town = towns[0]
        if it["cc"] == "GB":  # UK council areas: "Aberdeen City", "City of Edinburgh"
            town = re.sub(r"^City of |\s+City$", "", town)
        p["city"] = town
    types = sorted((t for t in it["types"] if SCHOOLISH.search(t) and not SKIP_TYPES.match(t)),
                   key=lambda t: (0 if re.search(r"public|private", t) else 1, len(t)))
    if types:
        p["type"] = types[0]
    return p if len(p) > 1 else None


world = json.loads((repo / "data" / "world_universities.json").read_text(encoding="utf-8"))
curated = json.loads((repo / "data" / "schools.json").read_text(encoding="utf-8"))
ours_c = [(u["name"], u["url"], u["country"]) for u in world if u["country"] != "United States"]
ours_c += [(s["name"], s["link"], s["country"]) for s in curated if s["country"] != "United States"]
ours = [(n, u) for n, u, _ in ours_c]

out, how = {}, collections.Counter()
for name, url in ours:
    h = host(url)
    cands = by_host.get(h, [])
    if not cands:
        how["no item with this website"] += 1
        continue
    ranked = sorted(cands, key=lambda it: -score(name, it["name"]))
    best = ranked[0]
    s = score(name, best["name"])
    clear = s >= 0.34 and (len(ranked) == 1 or s > score(name, ranked[1]["name"]))
    if not clear and len(cands) > 1:
        how["several items, none clearly ours"] += 1
        continue
    if len(cands) == 1 and s == 0 and not spelled_alike(name, best["name"]):
        how["one item, but another name"] += 1  # renamed, or a different body on the same website: left out
        continue
    p = describe(best)
    if not p:
        how["matched, nothing to show"] += 1
        continue
    how["matched"] += 1
    out[h] = p

# "one of the oldest universities in <country>": within the oldest few of that country's universities here
country_of = {host(url): c for _, url, c in ours_c}
by_country = collections.defaultdict(list)
for h, p in out.items():
    if p.get("founded"):
        by_country[country_of.get(h)].append((p["founded"], h))
for c, xs in by_country.items():
    if len(xs) >= 8:
        xs.sort()
        cut = max(3, -(-len(xs) * 5 // 100))
        for y, h in xs[:cut]:
            out[h]["among_oldest"] = True

doc = {"source": "Wikidata (CC0), fetched 13 October 2026", "profiles": dict(sorted(out.items()))}
(repo / "data" / "world_profiles.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n",
                                                   encoding="utf-8", newline="\n")
print("schools outside the US:", len(ours), "|", dict(how))
fields = collections.Counter(k for p in out.values() for k in p if k != "qid")
print("facts:", dict(fields))
for probe in ["knust.edu.gh", "ug.edu.gh", "uni-hamburg.de", "afghanuniversity.edu.af"]:
    print(probe, out.get(probe))
