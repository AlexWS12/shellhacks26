# Cost research and the savings calculator: filed costs first, benchmarks from Dominion's filed costs, and a
# savings range by distance tier, labeled as an assumption and dropped when Jev says sharing isn't worth raising.
from statistics import median

from app.core.analysis import shared_resources
from app.core.costs import ASSUMPTION_LABEL, ASSUMPTIONS, _sig, benchmark_table, savings_block
from app.core.models import Overlap, Project


def proj(pid: str, utility: str, cost: int | None, kind: str = "rebuild", miles: float | None = None) -> Project:
    return Project(id=pid, utility=utility, sponsor=utility, name=pid, in_service_date="2028-01-01", cost_total=cost,
                   project_type=kind, miles=miles, source_file="f.pdf", source_page=1, source_ref="r")


def test_every_paired_project_has_an_estimate(finished_run):
    b = finished_run.board
    ids = {pid for o in b.overlaps for pid in (o.project_a, o.project_b)}
    assert ids and ids <= set(b.estimates)
    for pid in ids:
        e, p = b.estimates[pid], b.projects[pid]
        if p.cost_total:
            assert e["basis"] == "filed" and e["amount"] == p.cost_total
        else:  # offline: no web search, so Georgia's redacted costs are modeled
            assert e["basis"] == "benchmark" and e["amount"] > 0 and "Dominion" in e["method"]


def test_benchmark_is_the_median_of_filed_dominion_costs(finished_run):
    projects = list(finished_run.board.projects.values())
    t = benchmark_table(projects)
    rebuilds = [p for p in projects if p.utility == "DESC" and p.cost_total and p.project_type == "rebuild"]
    assert t["rebuild"]["n"] == len(rebuilds)
    assert t["rebuild"]["per_mile"] == median(p.cost_total / p.miles for p in rebuilds if p.miles)
    assert t["in_substation_equipment"]["per_mile"] is None  # only lines are costed per mile


def test_savings_blocks_for_every_pair(finished_run):
    b = finished_run.board
    assert set(b.costs) == {o.id for o in b.overlaps}
    ranged = [c for c in b.costs.values() if c["savings_high"]]
    assert ranged, "expected at least one pair with a savings range"
    for c in ranged:
        assert 0 <= c["savings_low"] < c["savings_high"]
        assert c["applies_to"] in (c["a"]["project_id"], c["b"]["project_id"])
        assert ASSUMPTION_LABEL in c["statement"] and c["check"]
    for o in b.overlaps:  # the savings follow the pair's distance tier and what it can share
        c = b.costs[o.id]
        assert c["tier"] == o.tier and c["for"] == c["shared"]["items"]


def pair(finished: bool = False, windows: bool | None = True, tier: str = "site") -> tuple[Project, Project, Overlap]:
    a, g = proj("A", "DESC", 10_000_000), proj("G", "GA", None)
    o = Overlap(id="A|G", project_a="A", project_b="G", distance_mi=3, tier=tier, time_gap_days=10,
                windows_overlap=windows, pair_confidence="verified", finished=finished)
    return a, g, o


def test_savings_range_uses_the_smaller_cost():
    a, g, o = pair()
    shared = shared_resources(a, g, o)
    ea = {"project_id": "A", "amount": 10_000_000, "basis": "filed"}
    eg = {"project_id": "G", "amount": 4_000_000, "basis": "benchmark"}
    lo, hi = ASSUMPTIONS[("site", True)]
    c = savings_block(a, g, o, shared, ea, eg, {"actor": "jev", "p": 0.9})
    assert (c["savings_low"], c["savings_high"], c["applies_to"]) == (round(4e6 * lo), round(4e6 * hi), "G")
    assert "under 5 miles" in c["statement"] and "modeled" in c["statement"]


def test_closer_tiers_save_more():
    for together in (True, False):
        lows = [ASSUMPTIONS[(t, together)][0] for t in ("crew", "site", "row", "touching")]
        highs = [ASSUMPTIONS[(t, together)][1] for t in ("crew", "site", "row", "touching")]
        assert lows == sorted(lows) and highs == sorted(highs)
    for t in ("crew", "site", "row", "touching"):  # crews on site together always add something
        assert ASSUMPTIONS[(t, True)][1] > ASSUMPTIONS[(t, False)][1]


def test_jev_can_rule_a_pair_out():
    a, g, o = pair()
    e = {"project_id": "A", "amount": 10_000_000, "basis": "filed"}
    c = savings_block(a, g, o, shared_resources(a, g, o), e, {**e, "project_id": "G"}, {"actor": "jev", "p": 0.2})
    assert c["savings_high"] is None and "worth raising" in c["statement"]


def test_finished_pairs_save_only_what_lasts():
    # One project already in service: no crews, deliveries or outage timing, whatever the build windows said.
    from app.agents.costs import sharing_question

    a, g, o = pair(finished=True, tier="row")
    shared = shared_resources(a, g, o)
    e = {"project_id": "A", "amount": 10_000_000, "basis": "filed"}
    c = savings_block(a, g, o, shared, e, {**e, "project_id": "G"}, {"actor": "jev", "p": 0.9})
    lo, hi = ASSUMPTIONS[("row", False)]
    assert not c["together"] and "crews" not in c["for"] and "right-of-way" in c["for"]
    assert (c["savings_low"], c["savings_high"]) == (_sig(1e7 * lo), _sig(1e7 * hi))  # two significant figures
    q, state, _, _ = sharing_question(a, g, o, shared)
    assert "different times" in q and "today" in state and "already_in_service" in state["a"]


def test_unknown_timing_still_shares_the_land():
    a, g, o = pair(windows=None, tier="row")
    e = {"project_id": "A", "amount": 10_000_000, "basis": "filed"}
    c = savings_block(a, g, o, shared_resources(a, g, o), e, {**e, "project_id": "G"})
    assert c["savings_high"] and ASSUMPTIONS[("row", False)] == tuple(c["share"])


def test_jev_sees_the_closest_distance_and_tier(finished_run):
    from app.agents.costs import sharing_question

    b = finished_run.board
    o = next(o for o in b.overlaps if o.tier == "touching")
    a, g = b.projects[o.project_a], b.projects[o.project_b]
    q, state, _, _ = sharing_question(a, g, o, shared_resources(a, g, o))
    assert "touching or crossing" in q and "at least one of these:" in q
    assert state["closest_distance_mi"] == o.distance_mi and state["tier"] == "touching or crossing"
    assert state["a"]["construction_start"] or state["a"]["under_way_since"]


def test_pair_api_costs_pairs_outside_the_run(finished_run):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.store import dataset

    snap = finished_run.board.to_snapshot()
    dataset.load_snapshot(snap)
    b = finished_run.board
    o = b.overlaps[0]
    d = TestClient(app).get(f"/api/pair/{o.project_a}/{o.project_b}").json()
    assert d["cost"]["check"] == b.costs[o.id]["check"]  # the run's block, with Jev's verdict
    # a Dominion-Georgia pair the run never compared still gets code's estimates, without a check
    seen = {x.id for x in b.overlaps}
    pa = next(p for p in b.projects.values() if p.utility == "DESC" and p.lat is not None)
    pb = next(p for p in b.projects.values() if p.utility == "GA" and p.lat is not None and f"{pa.id}|{p.id}" not in seen)
    d = TestClient(app).get(f"/api/pair/{pa.id}/{pb.id}").json()
    assert d["cost"]["a"]["basis"] == "filed" and d["cost"]["b"]["basis"] == "benchmark" and d["cost"]["check"] is None


def test_short_lines_are_not_scaled_below_the_cheapest_comparable():
    from app.core.costs import benchmark_estimate

    dom = [proj("D1", "DESC", 2_000_000, miles=2), proj("D2", "DESC", 6_000_000, miles=4)]
    t = benchmark_table(dom)
    assert benchmark_estimate(proj("G", "GA", None, miles=0.1), t)["amount"] == 2_000_000
    assert benchmark_estimate(proj("G", "GA", None, miles=10), t)["amount"] == 12_500_000  # 10 mi at $1.25M
