"""Helpers shared by tools/us_programs.py and tools/us_profiles.py: reading IPEDS files, matching schools by
website and name, and our subject names for CIP families."""
import csv, io, re, zipfile

LEVELS = {"5": "b", "7": "m", "17": "d"}  # bachelor's, master's, research doctorate (PhD)
FIELDS = [  # our subject names, each with the 2-digit CIP families it covers
    ("Agriculture and environment", ["01", "03"]),
    ("Architecture and planning", ["04"]),
    ("Arts and design", ["50"]),
    ("Biology and life sciences", ["26"]),
    ("Business and management", ["52"]),
    ("Communication and media", ["09", "10"]),
    ("Computer science", ["11"]),
    ("Education", ["13"]),
    ("Engineering", ["14"]),
    ("Engineering technology", ["15"]),  # drafting, CAD, mechatronics: IPEDS keeps these apart
    ("Health and medicine", ["51", "60", "61"]),
    ("Humanities and languages", ["05", "16", "23", "38", "39", "54"]),
    ("Law", ["22"]),
    ("Mathematics and statistics", ["27"]),
    ("Physical sciences", ["40"]),
    ("Psychology", ["42"]),
    ("Public policy and social work", ["44"]),
    ("Social sciences", ["45"]),
]
CIP_TO_FIELD = {cip: i for i, (_, cips) in enumerate(FIELDS) for cip in cips}


def read_csv(zip_path, member_re):
    z = zipfile.ZipFile(zip_path)
    name = next(n for n in z.namelist() if re.fullmatch(member_re, n, re.I))
    raw = z.read(name)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    return csv.DictReader(io.StringIO(text))


def host(url):
    h = re.sub(r"^[a-z]+://", "", (url or "").strip().lower()).split("/")[0].split("?")[0]
    return h[4:] if h.startswith("www.") else h


def base(h):
    """The registrable part of a US host: web.mit.edu and mit.edu are the same school."""
    parts = h.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


def norm(name):
    return re.sub(r"[^a-z0-9]+", " ", name.lower().replace("&", " and ")).strip()
