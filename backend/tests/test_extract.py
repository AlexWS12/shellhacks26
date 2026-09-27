import json

import pytest

from app.config import DATA_DIR, DESC_PDF, GA_PDF
from app.core import extract_desc, extract_ga
from app.core.endpoints import clean_endpoint, looks_awkward, split_endpoints
from app.core.normalize import parse_money
from app.core.pdftext import read_pdf
from app.core.places import OsmIndex

REF = DATA_DIR / "reference"


def load_ref(name: str):
    path = REF / name
    if not path.exists():
        pytest.skip("reference fixtures not present")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def desc_pages():
    pdf = read_pdf(DESC_PDF)
    return [p for i, t in enumerate(pdf.pages) if (p := extract_desc.parse_page(t, i + 1))]


@pytest.fixture(scope="module")
def ga():
    pdf = read_pdf(GA_PDF)
    return extract_ga.parse_table(pdf.pages), extract_ga.parse_details(pdf.pages)


def test_desc_matches_fixture(desc_pages):
    ref = load_ref("desc.json")
    assert len(desc_pages) == len(ref) == 44
    for p, r in zip(desc_pages, ref):
        assert (p.page, p.name, p.pid, p.description, p.need, p.status, p.isd_raw) == (
            r["page"], r["name"], r["pid"], r["desc"], r["need"], r["status"], r["isd"])
        assert p.cost_cells == r["costs_raw"].split()


def test_ga_matches_fixture(ga):
    rows, details = ga
    ref = load_ref("gpc.json")
    assert len(rows) == len(ref["rows"]) == 208
    for r in rows:
        x = ref["rows"][r.teams]
        assert (r.zone, r.year, r.name, r.need, r.sponsor) == (x["zone"], x["year"], x["tname"], x["need"], x["sponsor"])
    for teams, x in ref["det"].items():
        d = details[teams]
        assert (d.page, d.title, d.need, d.start or "", d.description) == (x["page"], x["title"], x["need"],
                                                                           x["start"] or "", x["desc"])


def test_known_values(desc_pages, ga):
    by_id = {extract_desc.project_id(p.pid): p for p in desc_pages}
    jasper, _ = extract_desc.to_project(by_id["DESC-06367DG"], DESC_PDF.name)
    assert jasper.in_service_date == "2025-12-31"
    dawson = next(p for p in desc_pages if p.name.startswith("Dawson"))
    project, checks = extract_desc.to_project(dawson, DESC_PDF.name)
    assert project.in_service_date == "2026-10-01"  # final phase of a phased date
    assert any(c.rule == "phased_date" for c in checks)
    rows, details = ga
    row = next(r for r in rows if r.teams == "20277")
    p, _ = extract_ga.to_project(row, details["20277"], GA_PDF.name)
    assert (p.build_start, p.in_service_date, p.sponsor) == ("2024-01-01", "2026-06-01", "SAV")


def test_malformed_money_is_caught(desc_pages):
    riverport = next(p for p in desc_pages if p.name.startswith("Riverport"))
    assert "$19,00,181" in riverport.cost_cells
    project, checks = extract_desc.to_project(riverport, DESC_PDF.name)
    assert project.cost_by_year["2024"] is None  # never guessed
    assert project.cost_total == 34877427
    assert any(c.rule == "malformed_money" for c in checks)


def test_money_parser():
    assert parse_money("$1,234,567") == 1234567
    assert parse_money("$0") == 0
    assert parse_money("$19,00,181") is None


@pytest.mark.parametrize("title, parts", [
    ("Union Pier 115-13.8 kV Sub: Tap", ["Union Pier"]),
    ("SAV: CC - Hyundai Motors Savannah Aka. Project Ea", ["Hyundai Motors Savannah"]),
    ("Grady 230/115kV Relay Modernization", ["Grady"]),
    ("CC - Cass Pine 230/25 New Sub - Qcells - CC Improvements", ["Cass Pine", "Qcells"]),
    ("GTC: Morning Hornet 2nd 230/115 kV Bank & Thumbs Up 115kV Tl", ["Morning Hornet"]),
    ("GTC: Robins Spring Capacitor Bank Installation", ["Robins Spring"]),
    ("Plant Yates Breaker And Half Station", ["Plant Yates"]),
    ("GTC: Talbot #2 - Tazewell 500kV Line", ["Talbot", "Tazewell"]),
    ("Bowen #10 500/230kV Autobank Replacement", ["Bowen"]),
    ("GTC: East Moultrie - Highway 112 230 kV Line", ["East Moultrie", "Highway 112"]),  # 112 is not a voltage
    ("Jasper – Okatie 230 kV #2: Construct", ["Jasper", "Okatie"]),
])
def test_split_endpoints(title, parts):
    assert split_endpoints(title) == parts


def test_clean_endpoint_drops_fragments():
    # What Gemini returned for 'Union Pier 115-13.8 kV Sub: Tap'
    assert [clean_endpoint(n) for n in ["Union Pier 115", "13."]] == ["Union Pier", ""]


def test_short_acronyms_go_to_gemini():
    title = "MEAG: Alcovy Road - Skc 115 kV Reconductor"
    assert split_endpoints(title) == ["Alcovy Road", "Skc"]
    assert looks_awkward(title, split_endpoints(title))
    assert not looks_awkward("Jasper – Okatie 230 kV #2", ["Jasper", "Okatie"])


def test_osm_index_alt_names_plants_and_name_parts():
    idx = OsmIndex([
        {"osm_id": "way/1", "name": "Plant McDonough-Atkinson", "operator": "Georgia Power", "power": "plant",
         "alt_names": [], "ref": "", "lat": 33.8, "lon": -84.5},
        {"osm_id": "way/2", "name": "Goat Rock Dam Substation", "operator": "", "power": "substation",
         "alt_names": ["Goat Rock Switchyard"], "ref": "", "lat": 32.6, "lon": -85.1},
        {"osm_id": "node/3", "name": "Pactiv Substation", "operator": "", "lat": 33.7, "lon": -84.0},  # old cache
    ])
    assert [(f["osm_id"], via) for _, f, via in idx.candidates("atkinson")] == [("way/1", "name_part")]
    assert [(s, f["osm_id"], via) for s, f, via in idx.candidates("goat rock")] == [(1.0, "way/2", "name")]
    assert [f["osm_id"] for _, f, _ in idx.candidates("pactiv")] == ["node/3"]
