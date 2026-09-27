import json

import pytest

from app.config import DATA_DIR, DESC_PDF, GA_PDF
from app.core import extract_desc, extract_ga
from app.core.normalize import parse_money
from app.core.pdftext import read_pdf

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
