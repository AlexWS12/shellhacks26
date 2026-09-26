import io

import openpyxl
import pytest

from app import config
from app.core.sample import OVERLAP_HEADERS, PROJECT_HEADERS
from app.runtime.executor import execute
from app.runtime.run import new_run


@pytest.fixture(scope="module")
def finished_run(tmp_path_factory):
    import asyncio

    from app.pipeline import SOURCES, build_agents

    config.GEMINI_API_KEY = ""
    config.JEV_PROVIDER = ""
    config.OSM_LIVE = False
    run = new_run("live")
    run.pace = 0
    asyncio.run(execute(run, build_agents(), SOURCES))
    return run


def test_run_completes(finished_run):
    last = finished_run.events[-1]
    assert last["type"] == "run.done" and last["ok"], last
    assert last["stats"]["projects"] == 252


def test_reference_six_of_six_from_own_extraction(finished_run):
    ref = finished_run.board.reference
    assert len(ref) == 6 and all(r.passed for r in ref), [r for r in ref if not r.passed]


def test_overlaps_ranked_and_under_cutoff(finished_run):
    ov = finished_run.board.overlaps
    assert ov, "expected overlaps"
    assert all(o.distance_mi < 25 for o in ov)
    assert [o.rank for o in ov] == list(range(1, len(ov) + 1))
    assert [o.distance_mi for o in ov] == sorted(o.distance_mi for o in ov)
    headline = next(o for o in ov if o.project_a == "DESC-06367DG" and o.project_b == "GA-20277")
    assert (headline.distance_mi, headline.time_gap_days, headline.in_sponsor_sample) == (5.65, 152, True)


def test_validator_findings(finished_run):
    rules = {c.rule for c in finished_run.board.checks}
    assert {"malformed_money", "spend_after_isd", "cost_sum", "phased_date", "same_name_two_coords",
            "mixed_date_types", "redacted_cost", "past_isd"} <= rules
    need = [c for c in finished_run.board.checks if c.rule == "need_mismatch"]
    assert any("Williams St" in c.detail for c in need)


def test_every_event_has_contract_fields(finished_run):
    for i, e in enumerate(finished_run.events):
        assert {"type", "run_id", "seq", "ts"} <= e.keys()
        assert e["seq"] == i


def test_export_headers(finished_run):
    from app.export import build_xlsx
    from app.store import dataset

    snap = finished_run.board.to_snapshot()
    dataset.load_snapshot(snap)
    wb = openpyxl.load_workbook(io.BytesIO(build_xlsx(finished_run.board.overlaps)))
    assert [c.value for c in wb["projects"][1]][: len(PROJECT_HEADERS)] == PROJECT_HEADERS
    assert [c.value for c in wb["overlaps"][1]][: len(OVERLAP_HEADERS)] == OVERLAP_HEADERS
    assert wb["overlaps"].max_row == len(finished_run.board.overlaps) + 1
