# Who owns a project and which state it is in, read from the sources table (store/sources.py), not from code.
# A project finds its source by source_id, or by its utility key for projects made before sources existed
# ('DESC' -> Dominion, 'GA' -> Georgia Power). A project with no known source is its own owner.

import json
import math
from typing import Any

from app import config
from app.core.models import Project

BBOX_PAD_DEG = 0.05  # margin around the states, then rounded outward to 0.1 deg (SC + GA -> 30.3,-85.7,35.3,-78.4)
_bounds: dict[str, list[float]] | None = None


def _state_bounds() -> dict[str, list[float]]:
    global _bounds
    if _bounds is None:
        path = config.GEO_DIR / "state_bounds.json"
        _bounds = json.loads(path.read_text(encoding="utf-8"))["states"] if path.exists() else {}
    return _bounds


class Book:
    # The sources at one moment, with the questions the pipeline asks about a project's owner.
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.by_id = {s.id: s for s in rows}
        self.by_key: dict[str, Any] = {}
        for s in rows:
            self.by_key.setdefault(s.utility_key, s)

    def of(self, p: Project) -> Any | None:
        return self.by_id.get(p.source_id or "") or self.by_key.get(p.utility)

    def group(self, p: Project) -> str:
        # Overlaps pair projects of different groups: one group per owner code.
        s = self.of(p)
        return s.code if s else p.utility

    def rank(self, group: str) -> tuple:
        # Built-ins first (Dominion, then Georgia), so Dominion-Georgia pairs keep their 'DESC-...|GA-...' ids.
        s = next((r for r in self.rows if r.code == group), None)
        return (s.position, s.code) if s else (10_000, group)

    def active(self, p: Project) -> bool:
        s = self.of(p)
        return s is None or s.status == "active"

    def hidden_by_default(self, p: Project) -> bool:
        # An owner inside a filing that isn't shown by default (Georgia's GTC, MEAG, DU).
        s = self.of(p)
        return bool(s and s.sponsors) and p.sponsor not in s.default_sponsors

    def state_of(self, p: Project) -> str:
        if p.state:
            return p.state
        s = self.of(p)
        if s and s.states:
            return s.states[0]
        return "SC" if p.utility == "DESC" else "GA"

    def owner_name(self, p: Project) -> str:
        s = self.of(p)
        names = {x["code"]: x["name"] for x in (s.sponsors if s else [])}
        return names.get(p.sponsor, p.sponsor)

    def display_name(self, p: Project) -> str:
        s = self.of(p)
        return s.display_name if s else p.sponsor

    def code(self, p: Project) -> str:
        s = self.of(p)
        return s.code if s else p.sponsor

    def builtin(self, p: Project) -> bool:
        s = self.of(p)
        return bool(s and s.builtin)

    def operators_for_state(self, state: str | None) -> tuple[str, ...]:
        # OSM operator patterns of every active source in that state (for places with no source of their own).
        out: list[str] = []
        for s in self.rows:
            if s.status == "active" and state in s.states:
                out += [o for o in s.osm_operator_patterns if o not in out]
        return tuple(out)

    def operators(self, p: Project, state: str | None) -> tuple[str, ...]:
        s = self.of(p)
        return tuple(s.osm_operator_patterns) if s and s.osm_operator_patterns else self.operators_for_state(state)

    def states(self) -> list[str]:
        return sorted({st for s in self.rows if s.status == "active" for st in s.states})

    def bbox(self) -> tuple[float, float, float, float]:
        # south, west, north, east. OSM_BBOX overrides; otherwise the union of the active sources' states.
        if config.OSM_BBOX:
            s, w, n, e = (float(x) for x in config.OSM_BBOX.split(","))
            return (s, w, n, e)
        boxes = [b for st in self.states() if (b := _state_bounds().get(st))]
        if not boxes:
            return (30.3, -85.7, 35.3, -78.4)  # SC + GA
        up = lambda v: math.ceil(round(v * 10, 6)) / 10  # noqa: E731
        down = lambda v: math.floor(round(v * 10, 6)) / 10  # noqa: E731
        return (down(min(b[0] for b in boxes) - BBOX_PAD_DEG), down(min(b[1] for b in boxes) - BBOX_PAD_DEG),
                up(max(b[2] for b in boxes) + BBOX_PAD_DEG), up(max(b[3] for b in boxes) + BBOX_PAD_DEG))


def book() -> Book:
    from app.store import sources  # the table lives in store/; this module only reads it

    return Book(sources.all_sources())


def owner_name(p: Project) -> str:
    return book().owner_name(p)


def state_of(p: Project) -> str:
    return book().state_of(p)
