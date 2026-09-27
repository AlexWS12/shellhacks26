# The challenge's distance rule: closest points between two projects, not their centers, ranked in tiers.
from app.core.analysis import shared_resources
from app.core.models import Endpoint, Project
from app.core.overlap import TIERS, closest_mi, geometry, make_overlap, tier_of

MI_LAT = 1 / 69.05  # one mile of latitude, in degrees


def line(pid: str, utility: str, a: tuple[float, float], b: tuple[float, float], **kw) -> Project:
    ends = [Endpoint(name=f"{pid}-a", lat=a[0], lon=a[1], confidence="verified"),
            Endpoint(name=f"{pid}-b", lat=b[0], lon=b[1], confidence="verified")]
    return Project(id=pid, utility=utility, sponsor=utility, name=pid, in_service_date=kw.get("isd", "2029-12-31"),
                   endpoints=ends, lat=(a[0] + b[0]) / 2, lon=(a[1] + b[1]) / 2, location_confidence="verified",
                   source_file="f", source_page=1, source_ref="r", build_start=kw.get("start"), miles=kw.get("miles"))


def point(pid: str, utility: str, at: tuple[float, float], **kw) -> Project:
    return Project(id=pid, utility=utility, sponsor=utility, name=pid, in_service_date=kw.get("isd", "2029-12-31"),
                   endpoints=[Endpoint(name=pid, lat=at[0], lon=at[1], confidence="verified")], lat=at[0], lon=at[1],
                   location_confidence="verified", source_file="f", source_page=1, source_ref="r", build_start=kw.get("start"))


def test_a_long_line_passing_a_substation_is_close_even_when_its_center_is_not():
    # the challenge's own example: a 60 km (37 mi) line that passes near the other utility's substation
    ln = line("DESC-1", "DESC", (33.0, -81.0), (33.0 + 37 * MI_LAT, -81.0), miles=37)
    sub = point("GA-1", "GA", (33.0 + 2 * MI_LAT, -81.0 + 0.05))
    o = make_overlap(ln, sub, "2026-09-26")
    assert o.center_mi > 15 and 2.8 < o.distance_mi < 3.0 and o.tier == "site"


def test_crossing_lines_touch():
    a = line("DESC-1", "DESC", (33.0, -81.1), (33.0, -80.9))
    b = line("GA-1", "GA", (32.9, -81.0), (33.1, -81.0))
    assert closest_mi(geometry(a), geometry(b)) == 0.0
    assert make_overlap(a, b, "2026-09-26").tier == "touching"


def test_lines_ending_at_the_same_substation_touch():
    a = line("DESC-1", "DESC", (33.0, -81.0), (33.1, -81.1))
    b = line("GA-1", "GA", (33.0, -81.0), (32.9, -80.9))
    assert make_overlap(a, b, "2026-09-26").tier == "touching"


def test_tier_boundaries():
    assert [tier_of(m) for m in (0.05, 0.5, 3, 20, 30)] == ["touching", "row", "site", "crew", "none"]
    assert [t for t, _ in TIERS] == ["touching", "row", "site", "crew"]


def test_closer_tiers_share_more_and_crews_need_both_building():
    a = line("DESC-1", "DESC", (33.0, -81.1), (33.0, -80.9), start="2026-01-01", isd="2028-01-01")
    b = line("GA-1", "GA", (32.9, -81.0), (33.1, -81.0), start="2026-06-01", isd="2028-06-01")
    s = shared_resources(a, b, make_overlap(a, b, "2026-09-26"))
    assert s["must_coordinate"] and s["level"] == "high"
    assert {"outage timing", "right-of-way", "laydown yards", "crews"} <= set(s["items"])
    # the same crossing after one project is in service: land and structures, no crews or outage timing
    done = line("GA-1", "GA", (32.9, -81.0), (33.1, -81.0), start="2024-01-01", isd="2025-06-01")
    s = shared_resources(a, done, make_overlap(a, done, "2026-09-26"))
    assert "crossing structures" in s["items"] and "crews" not in s["items"] and "outage timing" not in s["items"]


def test_far_apart_in_time_and_place_still_shares_records():
    a = point("DESC-1", "DESC", (33.0, -81.0), start="2024-01-01", isd="2025-01-01")
    b = point("GA-1", "GA", (33.0 + 20 * MI_LAT, -81.0), start="2028-01-01", isd="2029-01-01")
    s = shared_resources(a, b, make_overlap(a, b, "2026-09-26"))
    assert s["tier"] == "crew" and s["items"] == ["survey and design records"] and s["level"] == "low"
