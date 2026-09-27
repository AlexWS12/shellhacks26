# One real positive case from the files for every validator rule (Phase 5).
import re

import pytest
from fastapi.testclient import TestClient


def checks(run, rule):
    found = [c for c in run.board.checks if c.rule == rule]
    assert found, f"no {rule} check"
    return found


def test_malformed_money_riverport(finished_run):
    c = checks(finished_run, "malformed_money")[0]
    assert c.level == "error" and "Riverport Tap" in c.detail and "$19,00,181" in c.detail and c.source == "DESC PDF p.22"


def test_cost_sum_vcs2_ward(finished_run):
    assert any("VCS2-Ward" in c.detail and "$20,000,000" in c.detail and "$30,000,000" in c.detail
               for c in checks(finished_run, "cost_sum"))


def test_spend_after_isd_stevens_creek(finished_run):
    assert any("Stevens Creek - Hooks" in c.detail and "$7,550,000" in c.detail and "2026" in c.detail
               for c in checks(finished_run, "spend_after_isd"))


def test_phased_date_dawson(finished_run):
    c = checks(finished_run, "phased_date")[0]
    assert c.level == "info" and "Dawson" in c.detail and "phase 2" in c.detail


def test_past_isd_count(finished_run):
    c = checks(finished_run, "past_isd")[0]
    assert c.level == "warn" and re.search(r"\d+ of 44 Dominion projects", c.detail)


def test_georgia_costs_redacted(finished_run):
    assert "All 208 Georgia project costs" in checks(finished_run, "redacted_cost")[0].detail


def test_ceii_banner(finished_run):
    assert re.search(r"\d+ of \d+ pages carry a CEII notice", checks(finished_run, "ceii_banner")[0].detail)


def test_mixed_sponsors(finished_run):
    d = checks(finished_run, "mixed_sponsors")[0].detail
    assert all(s in d for s in ("GPC", "GTC", "SAV", "MEAG", "DU"))


def test_same_substation_two_coordinates(finished_run):
    c = checks(finished_run, "same_name_two_coords")[0]
    assert "Mcintosh" in c.detail and "GPC_2" in c.detail and "GPC_3" in c.detail


def test_mixed_date_types(finished_run):
    c = checks(finished_run, "mixed_date_types")[0]
    assert "DESC_5" in c.detail and "GPC_4" in c.detail


def test_benchmark_endpoints_without_coordinates(finished_run):
    d = checks(finished_run, "sample_missing_coords")[0].detail
    assert "Hooks Sub" in d and "PURRYSBURG" in d


def test_unlocated_reports_count_and_reasons(finished_run):
    c = checks(finished_run, "unlocated")[0]
    un = [p for p in finished_run.board.projects.values() if p.lat is None]
    assert c.level == "warn" and c.detail.startswith(f"{len(un)} projects")
    assert re.search(r"\d+ (no endpoint names in the title|a candidate was found but rejected|no source had the name)", c.detail)


def test_need_contradicts_description_williams_st(finished_run):
    c = next(c for c in checks(finished_run, "need_mismatch") if "Williams St" in c.detail)
    assert c.level == "warn" and "transformers" in c.detail and c.project_id


def test_near_duplicate_6809_e_and_g(finished_run):
    c = next(c for c in checks(finished_run, "near_duplicate") if "ID 6809 E" in c.detail)
    assert "ID 6809 G" in c.detail and "Stevens Creek - Hooks" in c.detail
    # different circuits are not duplicates
    assert not any("#5" in c.detail and "#6" in c.detail for c in finished_run.board.checks if c.rule == "near_duplicate")


def test_every_check_has_source_and_plain_tone(finished_run):
    for c in finished_run.board.checks:
        assert c.source and c.title and c.detail
        assert not re.search(r"\byour (file|data)\b|\bwrong\b", f"{c.title} {c.detail}", re.I), c


@pytest.fixture
def api(finished_run):
    from app.main import app
    from app.store import dataset

    dataset.load_snapshot(finished_run.board.to_snapshot())
    return TestClient(app)  # no context manager: skips the startup Gemini check


def test_checks_endpoint_returns_every_rule(api, finished_run):
    r = api.get("/api/checks")
    assert r.status_code == 200
    assert {c["rule"] for c in r.json()} == {c.rule for c in finished_run.board.checks}
    assert {"near_duplicate", "unlocated", "need_mismatch", "ceii_banner", "mixed_sponsors"} <= {c["rule"] for c in r.json()}
