# The Writer: last agent, report built from code facts, prose checked, served by the API.
import re

from fastapi.testclient import TestClient

from app.core.analysis import unsupported_numbers
from app.core.report import facts_for_prose, to_pdf


def report_of(run):
    ev = [e for e in run.events if e["type"] == "report.ready"]
    assert len(ev) == 1, "expected exactly one report.ready event"
    return ev[0]["report"]


def test_writer_runs_last(finished_run):
    done = [e["agent_id"] for e in finished_run.events if e["type"] == "agent.done"]
    assert done[-1] == "writer", done[-3:]
    started = next(e for e in finished_run.events if e["type"] == "run.started")
    writer = next(a for a in started["agents"] if a["id"] == "writer")
    assert set(writer["depends_on"]) == {"mediator", "third_party"}


def test_report_numbers_come_from_the_board(finished_run):
    r = report_of(finished_run)
    b = finished_run.board
    assert r["counts"]["opportunities"] == len(b.overlaps)
    assert r["counts"]["dominion_projects"] == 44 and r["counts"]["georgia_projects"] == 208
    assert r["counts"]["benchmark_passed"] == 6 and r["counts"]["benchmark_total"] == 6
    assert [t["overlap_id"] for t in r["top"]] == [o.id for o in b.overlaps[:len(r["top"])]]
    for t, o in zip(r["top"], b.overlaps):
        assert (t["distance_mi"], t["time_gap_days"], t["rank"]) == (o.distance_mi, o.time_gap_days, o.rank)


def test_template_prose_when_gemini_is_off(finished_run):
    r = report_of(finished_run)
    for part in ("summary", "next_steps"):
        assert r[part]["actor"] == "template" and r[part]["text"]
        assert unsupported_numbers(r[part]["text"], facts_for_prose(r)) == set()


def test_markdown_has_every_top_pair_and_house_wording(finished_run):
    md = report_of(finished_run)["markdown"]
    for t in report_of(finished_run)["top"]:
        assert t["a"]["name"] in md and t["b"]["name"] in md
    for heading in ("## Summary", "## At a glance", "## Top opportunities", "## Recommended next steps", "## Method"):
        assert heading in md
    assert not re.search(r"sponsor", md, re.I)
    assert "—" not in md  # no em dashes


def test_pdf_tolerates_a_report_built_by_an_older_pipeline(finished_run):
    # data/snapshot.json can be older than this code (e.g. it predates the "tiers" field / a pair's
    # "center_mi"/"tier"). to_pdf() must degrade gracefully on that, not KeyError.
    r = dict(report_of(finished_run))
    del r["tiers"]
    r["top"] = [{k: v for k, v in t.items() if k not in ("center_mi", "tier")} for t in r["top"]]
    assert to_pdf(r)[:5] == b"%PDF-"


def test_report_endpoints(finished_run):
    from app.main import app
    from app.store import dataset

    dataset.load_snapshot(finished_run.board.to_snapshot())
    c = TestClient(app)
    assert c.get("/api/report").json()["title"].startswith("Coordination opportunities")
    md = c.get("/api/report.md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert "attachment" in md.headers["content-disposition"]
    pdf = c.get("/api/report.pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"].startswith("application/pdf")
    assert "attachment" in pdf.headers["content-disposition"]
    assert pdf.content[:5] == b"%PDF-"
    dataset.load_snapshot({**finished_run.board.to_snapshot(), "report": None})
    assert c.get("/api/report").status_code == 404
