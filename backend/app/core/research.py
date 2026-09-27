# Other utilities' projects: loading the research file, dates, centers and the three-way check.
# Everything here is plain code. The research team finds and cites; code decides distances and day gaps.

import calendar
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.models import RESEARCH_CATEGORIES, Confidence, Overlap, Project, ResearchProject, ThirdParty
from app.core.overlap import OVERLAP_CUTOFF_MI, center, distance_mi, weaker

DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")


def normalize_date(text: str | None, end: bool) -> tuple[str | None, str | None]:
    # '2028' -> ('2028-12-31', 'year') for an end date, ('2028-01-01', 'year') for a start. Unparseable -> (None, None).
    m = DATE_RE.match((text or "").strip())
    if not m:
        return None, None
    y, mo, d = int(m[1]), m[2], m[3]
    try:
        if d:
            return date(y, int(mo), int(d)).isoformat(), "day"
        if mo:
            last = calendar.monthrange(y, int(mo))[1]
            return date(y, int(mo), last if end else 1).isoformat(), "month"
        return date(y, 12, 31).isoformat() if end else date(y, 1, 1).isoformat(), "year"
    except ValueError:
        return None, None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def prepare(raw: dict[str, Any], prefix: str = "OU", found_by: str = "research_file") -> ResearchProject | None:
    try:
        return _prepare(raw, prefix, found_by)
    except (ValidationError, TypeError, ValueError, AttributeError):
        return None  # a malformed record is skipped, never guessed at


def _prepare(raw: dict[str, Any], prefix: str, found_by: str) -> ResearchProject | None:
    # Research record (file or live search) -> ResearchProject with code-normalized dates. None if unusable.
    if raw.get("category") not in RESEARCH_CATEGORIES or not raw.get("name") or not raw.get("utility"):
        return None
    sources = [s for s in raw.get("sources") or [] if str(s.get("url", "")).startswith("http")]
    if not sources:
        return None  # nothing is shown without a source
    start_date, _ = normalize_date(raw.get("start"), end=False)
    in_service_date, precision = normalize_date(raw.get("in_service"), end=True)
    return ResearchProject(
        id=raw.get("id") or f"{prefix}-{slug(raw['utility'])[:20]}-{slug(raw['name'])}",
        category=raw["category"], utility=raw["utility"], utility_kind=raw.get("utility_kind") or "",
        name=raw["name"], description=raw.get("description") or "", status=raw.get("status") or "unknown",
        start=raw.get("start"), in_service=raw.get("in_service"), date_quote=raw.get("date_quote"),
        start_date=start_date, in_service_date=in_service_date, date_precision=precision,
        places=[p for p in raw.get("places") or [] if p.get("name")],
        stated_coordinates=raw.get("coordinates") or raw.get("stated_coordinates") or [],
        miles=raw.get("miles"), cost_usd=raw.get("cost_usd"), cost_quote=raw.get("cost_quote"),
        sources=[{**s, "accessed": s.get("accessed") or raw.get("accessed")} for s in sources],
        found_by=found_by, verification=raw.get("verification") or {},
    )


def load_file(path: Path) -> tuple[list[ResearchProject], dict[str, Any]]:
    # Returns (records, file metadata). A missing file is not an error: the team just has nothing to report.
    if not path.exists():
        return [], {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[ResearchProject] = []
    seen: set[str] = set()
    skipped = 0
    for raw in data.get("records", []):
        r = prepare(raw)
        if r and r.id not in seen:
            seen.add(r.id)
            out.append(r)
        elif not r:
            skipped += 1
    return out, {**{k: v for k, v in data.items() if k not in ("records", "dropped")}, "skipped": skipped}


def place_center(r: ResearchProject) -> tuple[float, float] | None:
    # Same rule as the filings: midpoint of the two located ends of a line or pipeline;
    # otherwise the located site(s), otherwise every located place (e.g. the counties a pipeline crosses).
    # A county's center is only used when nothing finer is known for that role: a town or substation in the
    # county says more than the middle of the county.
    located = [(e, p) for e, p in zip(r.endpoints, r.places) if e.lat is not None]

    def finest(pts: list) -> list:
        fine = [x for x in pts if x[0].method != "county_centroid"]
        return fine or pts

    ends = [x for x in located if x[1].role == "endpoint"]
    if len(ends) >= 2:
        chosen = finest(ends)
        return center([(e.lat, e.lon) for e, _ in (chosen if len(chosen) >= 2 else ends)[:2]])
    sites = [x for x in located if x[1].role == "site"]
    if sites:
        return center([(e.lat, e.lon) for e, _ in finest(sites)])
    return center([(e.lat, e.lon) for e, _ in located])


def rollup_confidence(r: ResearchProject) -> Confidence:
    got = {e.confidence for e in r.endpoints if e.lat is not None}
    if not got:
        return "unlocated"
    if got <= {"verified"}:
        return "verified"
    if got <= {"verified", "confirmed_osm"}:
        return "confirmed_osm"
    if got & {"verified", "confirmed_osm"}:
        return "partial"
    return "town"


def _gap(a: str | None, b: str) -> int | None:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) if a else None


def nearby(overlaps: list[Overlap], projects: dict[str, Project], research: list[ResearchProject],
           categories: list[str] | tuple[str, ...] = RESEARCH_CATEGORIES) -> list[ThirdParty]:
    # A third project counts when its center is under 25 mi from BOTH project centers of the pair.
    out: list[ThirdParty] = []
    located = [r for r in research if r.lat is not None and r.lon is not None and r.category in categories]
    for o in overlaps:
        a, b = projects.get(o.project_a), projects.get(o.project_b)
        if not a or not b or a.lat is None or b.lat is None:
            continue
        for r in located:
            da = distance_mi((r.lat, r.lon), (a.lat, a.lon))  # type: ignore[arg-type]
            db = distance_mi((r.lat, r.lon), (b.lat, b.lon))  # type: ignore[arg-type]
            if da < OVERLAP_CUTOFF_MI and db < OVERLAP_CUTOFF_MI:
                out.append(ThirdParty(
                    overlap_id=o.id, research_id=r.id, category=r.category, dist_a_mi=round(da, 2), dist_b_mi=round(db, 2),
                    gap_a_days=_gap(r.in_service_date, a.in_service_date), gap_b_days=_gap(r.in_service_date, b.in_service_date),
                    approx_date=r.date_precision in ("year", "month"),
                    confidence=weaker(r.location_confidence, o.pair_confidence)))
    return out
