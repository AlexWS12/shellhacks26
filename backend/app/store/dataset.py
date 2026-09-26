import json
from dataclasses import dataclass, field
from typing import Any

from app.config import SNAPSHOT_PATH
from app.core.analysis import cost_block, shared_resources
from app.core.models import Check, Overlap, Project, ReferenceResult
from app.core.overlap import Filters, distance_mi, find_overlaps, time_gap_days, weaker, windows_overlap


@dataclass
class Dataset:
    run_id: str | None = None
    projects: dict[str, Project] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    reference: list[ReferenceResult] = field(default_factory=list)
    sample_pairs: set[tuple[str, str]] = field(default_factory=set)
    analyses: dict[str, Any] = field(default_factory=dict)
    briefs: dict[str, Any] = field(default_factory=dict)


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
    )


def load_from_disk() -> bool:
    if SNAPSHOT_PATH.exists():
        load_snapshot(json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8")))
        return True
    return False


def overlaps(f: Filters) -> list[Overlap]:
    return find_overlaps(list(CURRENT.projects.values()), f, CURRENT.sample_pairs)


def pair_detail(a_id: str, b_id: str) -> dict[str, Any] | None:
    a, b = CURRENT.projects.get(a_id), CURRENT.projects.get(b_id)
    if not a or not b or a.lat is None or b.lat is None:
        return None
    from datetime import date

    o = Overlap(id=f"{a.id}|{b.id}", project_a=a.id, project_b=b.id,
                distance_mi=round(distance_mi((a.lat, a.lon), (b.lat, b.lon)), 2),  # type: ignore[arg-type]
                time_gap_days=time_gap_days(date.fromisoformat(a.in_service_date), date.fromisoformat(b.in_service_date)),
                windows_overlap=windows_overlap(a, b), pair_confidence=weaker(a.location_confidence, b.location_confidence),
                in_sponsor_sample=(a.id, b.id) in CURRENT.sample_pairs)
    shared = shared_resources(a, b, o)
    return {"overlap": o.model_dump(), "a": a.model_dump(), "b": b.model_dump(), "shared": shared,
            "cost": cost_block(a, b, o), "analysis": CURRENT.analyses.get(o.id), "brief": CURRENT.briefs.get(o.id)}
