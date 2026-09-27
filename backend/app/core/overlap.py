# The challenge's rules. Distance = closest points between two projects: a line is the straight segment between its
# two located ends (the filings give no routes), anything else is a point. Overlap = under 25 mi (40 km), ranked in
# tiers by that distance. Time gap = days between in-service dates.
# Center = midpoint of the located endpoints, used to place a project on the map and by the benchmark, whose expected
# distances (Sperry's sample) are center to center. No LLM touches anything in this file.

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from app.core.models import Confidence, Overlap, Project
from app.core.owners import Book, book

EARTH_RADIUS_MI = 3958.8
OVERLAP_CUTOFF_MI = 25.0  # 40 km: how far a crew drives from one staging yard
# Closer overlaps can share more (the challenge's tiers). Each tier also gets everything the farther ones share.
TIERS: list[tuple[str, float]] = [
    ("touching", 0.1),  # touching or crossing, within a substation's footprint: must coordinate
    ("row", 1.0),  # 1.6 km: can share the land itself (right-of-way, access roads, permits)
    ("site", 5.0),  # 8 km: can share site logistics (laydown yards, deliveries)
    ("crew", OVERLAP_CUTOFF_MI),  # 40 km: can share crews and equipment
]
MAX_SPAN_MI = 60.0  # when the filing gives no length: longer than any such line in either plan
TIE_CROSS_MI = 30.0  # an end outside the filing's state must be this close to the other end (border ties are short)
CONF_ORDER: list[Confidence] = ["verified", "confirmed_osm", "partial", "town", "unlocated"]


def center(points: list[tuple[float | None, float | None]]) -> tuple[float, float] | None:
    located = [(la, lo) for la, lo in points if la is not None and lo is not None]
    if not located:
        return None
    return (sum(p[0] for p in located) / len(located), sum(p[1] for p in located) / len(located))


def distance_mi(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(h))


Point = tuple[float, float]


def geometry(p: Project) -> list[Point]:
    # The project's shape for closest-point distance: its located title ends in order (a line through them), or its
    # center when fewer than two are located or the ends are implausibly far apart.
    ends = [(e.lat, e.lon) for e in p.endpoints if e.role == "endpoint" and e.lat is not None and e.lon is not None]
    if len(ends) >= 2 and span_ok(p):
        return ends  # type: ignore[return-value]
    return [(p.lat, p.lon)] if p.lat is not None and p.lon is not None else []


def _seg_dist(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    # Planar distance from p to segment ab.
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _cross(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _crosses(a1: tuple[float, float], a2: tuple[float, float], b1: tuple[float, float], b2: tuple[float, float]) -> bool:
    d1, d2, d3, d4 = _cross(b1, b2, a1), _cross(b1, b2, a2), _cross(a1, a2, b1), _cross(a1, a2, b2)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0) and 0 not in (d1, d2, d3, d4)


def closest_mi(ga: list[Point], gb: list[Point]) -> float:
    # Closest points between two shapes (points or lines through points), in miles. Projected flat around their mean
    # latitude: within 25 mi the error is far below the precision of the locations.
    lat0 = math.radians(sum(p[0] for p in ga + gb) / len(ga + gb))
    flat = lambda g: [(math.radians(lo) * math.cos(lat0) * EARTH_RADIUS_MI, math.radians(la) * EARTH_RADIUS_MI)  # noqa: E731
                      for la, lo in g]
    sa, sb = flat(ga), flat(gb)
    segs_a = list(zip(sa, sa[1:])) or [(sa[0], sa[0])]
    segs_b = list(zip(sb, sb[1:])) or [(sb[0], sb[0])]
    best = math.inf
    for a1, a2 in segs_a:
        for b1, b2 in segs_b:
            if _crosses(a1, a2, b1, b2):
                return 0.0
            best = min(best, _seg_dist(a1, b1, b2), _seg_dist(a2, b1, b2), _seg_dist(b1, a1, a2), _seg_dist(b2, a1, a2))
    return best


def tier_of(miles: float) -> str:
    return next((name for name, limit in TIERS if miles < limit), "none")


def span_mi(p: Project) -> float | None:
    # Straight-line miles between the two located endpoints, None unless both are located.
    pts = [(e.lat, e.lon) for e in p.endpoints if e.lat is not None and e.lon is not None and e.role == "endpoint"]
    return distance_mi(pts[0], pts[1]) if len(pts) == 2 else None


def span_limit(miles: float | None) -> float:
    # The ends of a line can't be farther apart than the line is long ('Farley - Tazewell 500kV', 120 mi).
    return max(MAX_SPAN_MI, miles * 1.1 + 5) if miles else MAX_SPAN_MI


def span_ok(p: Project) -> bool:
    # A longer span means one end is the wrong place, so the center (and every distance from it) is too.
    s = span_mi(p)
    return s is None or s <= span_limit(p.miles)


def center_slack_mi(p: Project) -> float:
    # A project placed from one end of two, or from places its description names: the real
    # midpoint is up to half the line away. With no stated length, half the longest plausible span.
    title = [e for e in p.endpoints if e.role == "endpoint"]
    if not any(e.lat is not None for e in p.endpoints) or all(e.lat is not None for e in title):
        return 0.0
    return (p.miles or MAX_SPAN_MI) / 2


def time_gap_days(a: date, b: date) -> int:
    return abs((a - b).days)


def is_overlap(distance: float) -> bool:
    return distance < OVERLAP_CUTOFF_MI


def windows_overlap(a: Project, b: Project) -> bool | None:
    # None when we can't tell (a start date is missing).
    fa, fb = a.build_start or a.build_active_from, b.build_start or b.build_active_from
    if not fa or not fb:
        return None
    ia, ib = date.fromisoformat(a.in_service_date), date.fromisoformat(b.in_service_date)
    start, end = max(date.fromisoformat(fa), date.fromisoformat(fb)), min(ia, ib)
    if end > start:
        return True
    if (a.build_start is None and ib <= date.fromisoformat(fa)) or (b.build_start is None and ia <= date.fromisoformat(fb)):
        return None
    return False


def weaker(a: Confidence, b: Confidence) -> Confidence:
    return a if CONF_ORDER.index(a) >= CONF_ORDER.index(b) else b


@dataclass
class Filters:
    all_sponsors: bool = False
    min_confidence: Confidence = "town"  # include everything at least this good
    hide_finished: bool = False
    today: str = "2026-09-26"
    sort: str = "distance"  # distance | gap


def visible(p: Project, f: Filters, owners: Book | None = None) -> bool:
    if p.lat is None or p.lon is None:
        return False
    # Owners inside a filing that aren't shown by default (Georgia's GTC, MEAG, DU) come from the sources table.
    if not f.all_sponsors and (owners or book()).hidden_by_default(p):
        return False
    if CONF_ORDER.index(p.location_confidence) > CONF_ORDER.index(f.min_confidence):
        return False
    if f.hide_finished and p.in_service_date < f.today:
        return False
    return True


CELL_DEG = 0.5  # index cell: 34.5 mi of latitude


def _cells(g: list[Point], pad_mi: float) -> set[tuple[int, int]]:
    # Index cells covering the shape's bounding box, widened by pad_mi. Distances are still computed exactly.
    lats, lons = [p[0] for p in g], [p[1] for p in g]
    dlat = pad_mi / 69.0
    dlon = pad_mi / (69.0 * max(0.2, math.cos(math.radians(sum(lats) / len(lats)))))
    return {(y, x) for y in range(math.floor((min(lats) - dlat) / CELL_DEG), math.floor((max(lats) + dlat) / CELL_DEG) + 1)
            for x in range(math.floor((min(lons) - dlon) / CELL_DEG), math.floor((max(lons) + dlon) / CELL_DEG) + 1)}


def make_overlap(a: Project, b: Project, today: str, sample_pairs: set[tuple[str, str]] | None = None,
                 ga: list[Point] | None = None, gb: list[Point] | None = None) -> Overlap:
    # One pair, whatever its distance. find_overlaps keeps those under the cutoff; the pair API shows any pair.
    d = closest_mi(ga or geometry(a), gb or geometry(b))
    return Overlap(
        id=f"{a.id}|{b.id}", project_a=a.id, project_b=b.id, distance_mi=round(d, 2), tier=tier_of(d),
        center_mi=round(distance_mi((a.lat, a.lon), (b.lat, b.lon)), 2),  # type: ignore[arg-type]
        time_gap_days=time_gap_days(date.fromisoformat(a.in_service_date), date.fromisoformat(b.in_service_date)),
        windows_overlap=windows_overlap(a, b), pair_confidence=weaker(a.location_confidence, b.location_confidence),
        in_sponsor_sample=(a.id, b.id) in (sample_pairs or set()), finished=min(a.in_service_date, b.in_service_date) < today,
        distance_slack_mi=round(center_slack_mi(a) + center_slack_mi(b), 1))


def find_overlaps(projects: list[Project], f: Filters, sample_pairs: set[tuple[str, str]] | None = None) -> list[Overlap]:
    # The only place overlaps get computed: every pair of projects from two different active sources (owner codes)
    # whose closest points are under the cutoff. Owners are ordered as the sources table ranks them (Dominion, Georgia,
    # then the rest), so Dominion-Georgia pairs keep 'DESC-...|GA-...'. A project whose two ends are implausibly far
    # apart is left out (span_ok): its shape can't be trusted.
    book_ = book()
    groups: dict[str, list[Project]] = {}
    for p in projects:
        if book_.active(p) and visible(p, f, book_) and span_ok(p):
            groups.setdefault(book_.group(p), []).append(p)
    owners = sorted(groups, key=book_.rank)
    shapes = {p.id: geometry(p) for g in groups.values() for p in g}
    found: list[Overlap] = []
    for i, ua in enumerate(owners):
        for ub in owners[i + 1:]:
            cells: dict[tuple[int, int], list[Project]] = {}
            for b in groups[ub]:
                for c in _cells(shapes[b.id], 0):
                    cells.setdefault(c, []).append(b)
            for a in groups[ua]:
                near = {b.id: b for c in _cells(shapes[a.id], OVERLAP_CUTOFF_MI) for b in cells.get(c, [])}
                for b in near.values():
                    o = make_overlap(a, b, f.today, sample_pairs, shapes[a.id], shapes[b.id])
                    if o.tier != "none":  # under the cutoff, before rounding
                        found.append(o)
    # Active pairs first: a finished project can only share records, not crews.
    if f.sort == "gap":
        found.sort(key=lambda o: (o.finished, o.time_gap_days, o.distance_mi, o.project_a, o.project_b))
    else:
        found.sort(key=lambda o: (o.finished, o.distance_mi, o.time_gap_days, o.project_a, o.project_b))
    for i, o in enumerate(found, start=1):
        o.rank = i
    return found
