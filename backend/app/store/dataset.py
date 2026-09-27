import json
from dataclasses import dataclass, field
from typing import Any

from app.config import SNAPSHOT_PATH, TODAY
from app.core.analysis import shared_resources
from app.core.costs import benchmark_table, estimate_for, savings_block
from app.core.models import (RESEARCH_CATEGORIES, Check, Confidence, Overlap, Project, ReferenceResult,
                             ResearchProject, ThirdParty)
from app.core.overlap import CONF_ORDER, Filters, find_overlaps, make_overlap
from app.core.research import nearby


@dataclass
class Dataset:
    run_id: str | None = None
    projects: dict[str, Project] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    reference: list[ReferenceResult] = field(default_factory=list)
    sample_pairs: set[tuple[str, str]] = field(default_factory=set)
    analyses: dict[str, Any] = field(default_factory=dict)
    briefs: dict[str, Any] = field(default_factory=dict)
    estimates: dict[str, Any] = field(default_factory=dict)  # project id -> cost estimate from the run
    costs: dict[str, Any] = field(default_factory=dict)  # overlap id -> savings block, with Jev's check
    benchmarks: dict[str, Any] = field(default_factory=dict)  # costs for pairs the run didn't cost (other filters)
    research: list[ResearchProject] = field(default_factory=list)
    research_selected: list[str] = field(default_factory=lambda: list(RESEARCH_CATEGORIES))
    report: dict[str, Any] | None = None
    agents: list[dict[str, Any]] = field(default_factory=list)  # the graph of the run that made this dataset
    sources: list[dict[str, Any]] = field(default_factory=list)


CURRENT = Dataset()


def load_snapshot(snap: dict[str, Any]) -> None:
    global CURRENT
    CURRENT = Dataset(
        run_id=snap.get("run_id"),
        projects={p["id"]: Project(**p) for p in snap["projects"]},
        checks=[Check(**c) for c in snap["checks"]],
        reference=[ReferenceResult(**r) for r in snap["reference"]],
        sample_pairs={tuple(x) for x in snap.get("sample_pairs", [])},  # type: ignore[misc]
        analyses=snap.get("analyses", {}), briefs=snap.get("briefs", {}),
        estimates=snap.get("estimates", {}), costs=snap.get("costs", {}),
        research=[ResearchProject(**r) for r in snap.get("research", [])],
        research_selected=snap.get("research_selected", list(RESEARCH_CATEGORIES)),
        report=snap.get("report"),
        agents=snap.get("agents") or [], sources=snap.get("sources") or [],
    )
    CURRENT.benchmarks = benchmark_table(list(CURRENT.projects.values()))


def load_from_disk() -> bool:
    if SNAPSHOT_PATH.exists():
        load_snapshot(json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8")))
        return True
    return False


def overlaps(f: Filters) -> list[Overlap]:
    return find_overlaps(list(CURRENT.projects.values()), f, CURRENT.sample_pairs)


def others(ovs: list[Overlap], min_conf: Confidence = "town") -> list[ThirdParty]:
    # Recomputed for whatever overlaps the filters produce, so the flags always match the list on screen.
    # min_conf: the "Approx. locations" filter also drops links that rest on a town- or county-level location.
    return [t for t in nearby(ovs, CURRENT.projects, CURRENT.research, CURRENT.research_selected)
            if CONF_ORDER.index(t.confidence) <= CONF_ORDER.index(min_conf)]


def pair_detail(a_id: str, b_id: str) -> dict[str, Any] | None:
    a, b = CURRENT.projects.get(a_id), CURRENT.projects.get(b_id)
    if not a or not b or a.lat is None or b.lat is None:
        return None
    o = make_overlap(a, b, TODAY, CURRENT.sample_pairs)
    shared = shared_resources(a, b, o)
    cost = CURRENT.costs.get(o.id)
    if not cost or "savings_high" not in cost:  # a pair the run didn't cost: code's estimates, no Jev check
        cost = savings_block(a, b, o, shared, estimate_for(a, CURRENT.estimates, CURRENT.benchmarks),
                             estimate_for(b, CURRENT.estimates, CURRENT.benchmarks))
    return {"overlap": o.model_dump(), "a": a.model_dump(), "b": b.model_dump(), "shared": shared,
            "cost": {k: v for k, v in cost.items() if k != "shared"}, "analysis": CURRENT.analyses.get(o.id), "brief": CURRENT.briefs.get(o.id),
            "others": [t.model_dump() for t in others([o])]}
