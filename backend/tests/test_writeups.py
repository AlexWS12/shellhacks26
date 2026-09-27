# Which pairs get written up, and what the model is told about them. Dominion-Georgia prompts must not change: offline
# reruns replay their cached answers.

import pytest

from app import config
from app.agents.analysis import written_pairs
from app.core.analysis import fact_sheet, shared_resources, sides
from app.core.models import Overlap, Project
from app.store import sources


def proj(pid: str, utility: str, sponsor: str, source_id: str | None = None) -> Project:
    return Project(id=pid, utility=utility, sponsor=sponsor, source_id=source_id, name=f"{pid} line",
                   in_service_date="2028-01-01", source_file="f.pdf", source_page=1, source_ref="p.1")


def pair(a: Project, b: Project, rank: int) -> Overlap:
    return Overlap(id=f"{a.id}|{b.id}", project_a=a.id, project_b=b.id, distance_mi=float(rank), time_gap_days=10,
                   pair_confidence="town", rank=rank)


@pytest.fixture
def owners(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SOURCES_DB", tmp_path / "sources.db")  # DESC and GPC, seeded on first use
    filing = sources.add("SCPSA", "Santee Cooper", ["SC"], "ai", status="active", display={"origin": "upload"})
    plan = sources.add("PLAN", "A Plan", ["SC"], "sheet", status="active", display={"origin": sources.ORIGIN})
    return filing, plan


def test_every_two_utilities_get_their_own_top_pairs(owners):
    filing, plan = owners
    d = [proj(f"DESC-{i}", "DESC", "DESC") for i in range(3)]
    g = [proj(f"GA-{i}", "GA", "GPC") for i in range(3)]
    s = [proj(f"SCPSA-{i}", filing.utility_key, "SCPSA", filing.id) for i in range(3)]
    x = proj("PLAN-0", plan.utility_key, "PLAN", plan.id)
    projects = {p.id: p for p in [*d, *g, *s, x]}
    overlaps = [pair(d[0], g[0], 1), pair(d[1], g[1], 2), pair(d[2], g[2], 3), pair(d[0], s[0], 4), pair(d[1], s[1], 5),
                pair(d[2], s[2], 6), pair(g[0], s[0], 7), pair(d[0], x, 8)]
    got = [o.id for o in written_pairs(overlaps, projects, 2)]
    # two per pair of utilities, in rank order; the spreadsheet plan never gets written sides
    assert got == ["DESC-0|GA-0", "DESC-1|GA-1", "DESC-0|SCPSA-0", "DESC-1|SCPSA-1", "GA-0|SCPSA-0"]


def test_the_dominion_georgia_fact_sheet_is_unchanged(owners):
    filing, _ = owners
    a, b = proj("DESC-1", "DESC", "DESC"), proj("GA-1", "GA", "GPC")
    o = pair(a, b, 1)
    f = fact_sheet(a, b, o, shared_resources(a, b, o))
    assert list(f) == ["distance_mi", "time_gap_days", "windows_overlap", "location_confidence", "shared",
                       "dominion", "georgia"]
    assert f["dominion"]["utility"] == "Dominion Energy South Carolina" and f["georgia"]["utility"] == "Georgia (GPC)"
    # any other pair names both utilities and doesn't call either one Dominion or Georgia
    c = proj("SCPSA-1", filing.utility_key, "SCPSA", filing.id)
    o2 = pair(b, c, 2)
    f2 = fact_sheet(b, c, o2, shared_resources(b, c, o2))
    assert "dominion" not in f2 and "georgia" not in f2
    first, second = sides(f2)
    assert (first["utility"], second["utility"]) == ("Georgia (GPC)", "Santee Cooper")
