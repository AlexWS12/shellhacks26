# Sperry's rules. Center = midpoint of the located endpoints, distance = haversine miles
# (R = 3958.8), overlap = under 25 mi, time gap = days between in-service dates.
# No LLM touches anything in this file.

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from app.core.models import Confidence, Overlap, Project
from app.core.owners import Book, book

EARTH_RADIUS_MI = 3958.8
OVERLAP_CUTOFF_MI = 25.0
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


CELL_DEG = 0.5  # bucket size: 0.5 deg of latitude is 34.5 mi, so neighbours within 25 mi are at most 1 cell away
# (2 cells in longitude, which covers 25 mi up to 68 deg north). Distances are still computed exactly.


def _cell(p: Project) -> tuple[int, int]:
    return math.floor(p.lat / CELL_DEG), math.floor(p.lon / CELL_DEG)  # type: ignore[operator]


def find_overlaps(projects: list[Project], f: Filters, sample_pairs: set[tuple[str, str]] | None = None) -> list[Overlap]:
    # The only place overlaps get computed: every pair of projects from two different active sources (owner codes).
    # Owners are ordered as the sources table ranks them (Dominion, Georgia, then the rest), so Dominion-Georgia
    # pairs keep 'DESC-...|GA-...'. A project whose two ends are implausibly far apart is left out (span_ok): its
    # center can't be trusted.
    book_ = book()
    groups: dict[str, list[Project]] = {}
    for p in projects:
        if book_.active(p) and visible(p, f, book_) and span_ok(p):
            groups.setdefault(book_.group(p), []).append(p)
    owners = sorted(groups, key=book_.rank)
    sample_pairs = sample_pairs or set()
    found: list[Overlap] = []
    for i, ua in enumerate(owners):
        for ub in owners[i + 1:]:
            cells: dict[tuple[int, int], list[Project]] = {}
            for b in groups[ub]:
                cells.setdefault(_cell(b), []).append(b)
            for a in groups[ua]:
                ca = _cell(a)
                near = [b for dy in (-1, 0, 1) for dx in (-2, -1, 0, 1, 2) for b in cells.get((ca[0] + dy, ca[1] + dx), [])]
                for b in near:
                    d = distance_mi((a.lat, a.lon), (b.lat, b.lon))  # type: ignore[arg-type]
                    if not is_overlap(d):
                        continue
                    gap = time_gap_days(date.fromisoformat(a.in_service_date), date.fromisoformat(b.in_service_date))
                    found.append(Overlap(
                        id="", project_a=a.id, project_b=b.id, distance_mi=round(d, 2), time_gap_days=gap,
                        windows_overlap=windows_overlap(a, b),
                        pair_confidence=weaker(a.location_confidence, b.location_confidence),
                        in_sponsor_sample=(a.id, b.id) in sample_pairs,
                        finished=min(a.in_service_date, b.in_service_date) < f.today,
                        distance_slack_mi=round(center_slack_mi(a) + center_slack_mi(b), 1),
                    ))
    # Active pairs first: a finished project can only share records, not crews.
    if f.sort == "gap":
        found.sort(key=lambda o: (o.finished, o.time_gap_days, o.distance_mi, o.project_a, o.project_b))
    else:
        found.sort(key=lambda o: (o.finished, o.distance_mi, o.time_gap_days, o.project_a, o.project_b))
    for i, o in enumerate(found, start=1):
        o.rank = i
        o.id = f"{o.project_a}|{o.project_b}"
    return found
