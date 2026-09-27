# Phase 4: endpoints and geocoding.
import asyncio

from app import config
from app.agents.geocoder import MAX_SPAN_MI
from app.clients import nominatim, overpass
from app.core.overlap import distance_mi
from app.core.places import OsmIndex, States, variants


def test_abbreviations_are_tried_both_ways():
    assert variants("st george") == ["st george", "saint george"]
    assert "fort johnson" in variants("ft johnson")
    assert "st matthews" in variants("saint matthews")


def test_state_of_points():
    s = States()
    assert s.state_of(33.186, -80.576) == "SC"  # St. George, SC
    assert s.state_of(32.08, -81.09) == "GA"  # Savannah
    assert s.state_of(40.0, -80.0) is None


def test_osm_candidates_prefer_the_filing_state():
    feats = overpass.load()
    if feats is None:
        return  # no OSM cache on this machine
    top = OsmIndex(feats, States()).candidates("st george", "SC")[0][1]
    assert top["name"] == "Saint George Switching Station" and top["state"] == "SC"


def test_st_george_is_placed_in_south_carolina(finished_run):
    # Used to match Saint George Substation in south Georgia and drag the center onto Savannah.
    p = finished_run.board.projects["DESC-6809M"]
    st = next(e for e in p.endpoints if e.name == "St George")
    assert st.lat is not None and States().state_of(st.lat, st.lon) == "SC"


def test_no_project_spans_more_than_the_limit(finished_run):
    for p in finished_run.board.projects.values():
        pts = [e for e in p.endpoints if e.lat is not None]
        if len(pts) == 2 and not all(e.method in ("sponsor_file", "override") for e in pts):
            assert distance_mi((pts[0].lat, pts[0].lon), (pts[1].lat, pts[1].lon)) <= MAX_SPAN_MI, p.name


def test_every_endpoint_has_method_and_evidence(finished_run):
    for p in finished_run.board.projects.values():
        for e in p.endpoints:
            if e.lat is None:
                assert e.method == "none" and e.evidence.get("tried") and e.evidence.get("reason"), (p.id, e)
            else:
                assert e.method in ("sponsor_file", "override", "overpass", "nominatim", "geonames_town"), (p.id, e)
                assert e.evidence, (p.id, e)


def test_every_unlocated_project_has_a_reason(finished_run):
    un = {e["project_id"]: e["reason"] for e in finished_run.events if e["type"] == "project.unlocated"}
    assert un and all(un.values())
    assert len(un) == sum(1 for p in finished_run.board.projects.values() if p.lat is None)


def test_nominatim_keeps_places_and_substations_only():
    road = {"category": "highway", "type": "residential", "name": "Hooks Rd", "lat": "33", "lon": "-81"}
    hamlet = {"category": "place", "type": "hamlet", "name": "Purrysburg", "lat": "32.3", "lon": "-81.1",
              "osm_type": "node", "osm_id": 1, "display_name": "Purrysburg, Jasper County, South Carolina",
              "address": {"county": "Jasper County", "state": "South Carolina"}}
    assert nominatim._keep(road) is None
    kept = nominatim._keep(hamlet)
    assert kept and kept["kind"] == "place=hamlet" and kept["county"] == "Jasper County"


def test_nominatim_offline_uses_cache_only(monkeypatch):
    monkeypatch.setattr(config, "OSM_LIVE", False)
    monkeypatch.setattr(nominatim.cache, "get", lambda *a: None)
    assert asyncio.run(nominatim.search("zz no such place")) == ([], "off")


def test_nominatim_stops_when_the_run_budget_is_used(monkeypatch):
    monkeypatch.setattr(config, "OSM_LIVE", True)
    monkeypatch.setattr(nominatim.cache, "get", lambda *a: None)
    nominatim.start_budget(-1)
    try:
        assert asyncio.run(nominatim.search("zz no such place")) == ([], "budget")
    finally:
        nominatim.start_budget(float("inf"))
