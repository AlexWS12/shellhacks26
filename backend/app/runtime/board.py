# Shared state. Any agent can read it, but each field has one writer.

from dataclasses import dataclass, field
from typing import Any

from app.core.models import Check, Overlap, Project, ReferenceResult, ResearchProject, ThirdParty
from app.core.sample import Sample


@dataclass
class Board:
    projects: dict[str, Project] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    sample: Sample | None = None
    sample_map: dict[str, str] = field(default_factory=dict)  # 'DESC_3' -> 'DESC-06367D-G'
    overlaps: list[Overlap] = field(default_factory=list)
    reference: list[ReferenceResult] = field(default_factory=list)
    analyses: dict[str, dict[str, Any]] = field(default_factory=dict)  # overlap id -> {text, actor}
    costs: dict[str, dict[str, Any]] = field(default_factory=dict)
    briefs: dict[str, dict[str, Any]] = field(default_factory=dict)  # overlap id -> {desc, ga, mediator}
    pending_checks: list[Check] = field(default_factory=list)  # the validator reports these
    research: dict[str, ResearchProject] = field(default_factory=dict)  # each scout writes its own category
    research_selected: list[str] = field(default_factory=list)  # third_party
    third_party: list[ThirdParty] = field(default_factory=list)  # third_party
    ga_page_count: int = 0
    ga_ceii_pages: int = 0

    def sample_pairs(self) -> set[tuple[str, str]]:
        if not self.sample:
            return set()
        m = self.sample_map
        return {(m[o.a], m[o.b]) for o in self.sample.overlaps if o.a in m and o.b in m}

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "projects": [p.model_dump() for p in self.projects.values()],
            "checks": [c.model_dump() for c in self.checks],
            "sample_map": self.sample_map,
            "sample_pairs": sorted(self.sample_pairs()),
            "overlaps": [o.model_dump() for o in self.overlaps],
            "reference": [r.model_dump() for r in self.reference],
            "analyses": self.analyses,
            "costs": self.costs,
            "briefs": self.briefs,
            "research": [r.model_dump() for r in self.research.values()],
            "research_selected": self.research_selected,
            "third_party": [t.model_dump() for t in self.third_party],
        }
