# Phase 4: endpoints and geocoding.
import asyncio

from app import config
from app.agents.geocoder import Geocoder
from app.clients import nominatim, overpass
from app.core.models import Endpoint, Project
from app.core.overlap import Filters, distance_mi, find_overlaps, span_limit, span_mi, span_ok
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
        pts = [e for e in p.endpoints if e.lat is not None and e.role == "endpoint"]
        if len(pts) == 2 and not all(e.method in ("sponsor_file", "override") for e in pts):
            assert distance_mi((pts[0].lat, pts[0].lon), (pts[1].lat, pts[1].lon)) <= span_limit(p.miles), p.name


def _osm(name: str, lat: float, lon: float) -> Endpoint:
    return Endpoint(name=name, lat=lat, lon=lon, method="overpass", confidence="confirmed_osm", evidence={"matched": name})


def test_out_of_state_end_far_from_the_other_is_rejected():
    g = Geocoder.__new__(Geocoder)
    g.states = States()
    sumter, st_george_ga = _osm("Sumter", 33.898, -80.324), _osm("St George", 30.523, -82.026)
    weak, keep, _, tied = g._bad_span(sumter, st_george_ga, "SC")
    assert weak is st_george_ga and keep is sumter and not tied
    # a real border tie: South Bainbridge (GA) - Sinai (FL), 27 mi
    assert g._bad_span(_osm("South Bainbridge", 30.84, -84.489), _osm("Sinai", 30.664, -84.901), "GA") is None
    # both ends surveyed: left for a human
    a, b = (e.model_copy(update={"method": "sponsor_file"}) for e in (sumter, st_george_ga))
    assert g._bad_span(a, b, "SC") is None


def test_a_project_with_an_implausible_span_makes_no_overlaps():
    def proj(pid: str, utility: str, eps: list[Endpoint]) -> Project:
        return Project(id=pid, utility=utility, sponsor="DESC" if utility == "DESC" else "SAV", name=pid,  # type: ignore[arg-type]
                       in_service_date="2026-05-31", endpoints=eps, lat=32.21, lon=-81.175,
                       location_confidence="verified", source_file="x", source_page=1, source_ref="x")
    bogus = proj("DESC-X", "DESC", [_osm("St George", 30.523, -82.026), _osm("Sumter", 33.898, -80.324)])
    savannah = proj("GA-Y", "GA", [])
    assert not span_ok(bogus)
    assert find_overlaps([bogus, savannah], Filters()) == []


def test_span_limit_follows_the_filed_line_length():
    assert span_limit(None) == span_limit(2.0) == 60.0
    assert span_limit(120.0) > 120.0  # Farley - Tazewell 500kV


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
    # a town mapped only as its limits is still a town; a county boundary is not
    limits = {"category": "boundary", "type": "administrative", "addresstype": "town", "name": "Eastover",
              "lat": "33.877", "lon": "-80.692", "osm_type": "relation", "osm_id": 2}
    assert (nominatim._keep(limits) or {}).get("kind") == "place=town"
    assert nominatim._keep({**limits, "addresstype": "county", "name": "Richland County"}) is None


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


def test_one_ended_center_slack():
    from app.core.overlap import MAX_SPAN_MI, center_slack_mi
    ends = [Endpoint(name="A", lat=33.0, lon=-81.0, method="overpass", confidence="confirmed_osm"), Endpoint(name="B")]
    base = dict(id="X", utility="DESC", sponsor="DESC", name="A - B", in_service_date="2027-01-01",
                source_file="x", source_page=1, source_ref="x")
    assert center_slack_mi(Project(**base, endpoints=ends, miles=9.5)) == 4.75
    assert center_slack_mi(Project(**base, endpoints=ends)) == MAX_SPAN_MI / 2  # no stated length
    assert center_slack_mi(Project(**base, endpoints=ends[:1])) == 0  # the only named end is located


def test_finished_pairs_rank_after_active():
    def proj(pid: str, utility: str, lat: float, isd: str) -> Project:
        return Project(id=pid, utility=utility, sponsor="DESC" if utility == "DESC" else "GPC", name=pid,
                       in_service_date=isd, lat=lat, lon=-81.0, location_confidence="verified",
                       source_file="x", source_page=1, source_ref="x")
    done = proj("DESC-done", "DESC", 33.0, "2024-12-31")
    ahead = proj("DESC-ahead", "DESC", 33.2, "2028-12-31")
    ga = proj("GA-1", "GA", 33.0, "2029-06-01")
    ov = find_overlaps([done, ahead, ga], Filters(today="2026-09-26"))
    assert [(o.project_a, o.finished, o.rank) for o in ov] == [("DESC-ahead", False, 1), ("DESC-done", True, 2)]


def test_equal_ends_too_far_apart_are_a_tie():
    g = Geocoder.__new__(Geocoder)
    g.states = States()
    goshen_augusta, mcintosh = _osm("Goshen", 33.3198, -81.9953), _osm("Mcintosh", 32.3521, -81.1751)
    *_, tied = g._bad_span(goshen_augusta, mcintosh, "GA")
    assert tied


def test_blind_geocode_picks_the_goshen_near_mcintosh(finished_run):
    # Two Goshen substations in Georgia. The one near Augusta is ranked first; the span check must
    # drop it rather than McIntosh.
    b = finished_run.board
    blind = b.blind[b.sample_map["GPC_3"]]
    goshen = next(e for e in blind["endpoints"] if e["name"] == "Goshen")
    assert goshen["lat"] is not None and distance_mi((goshen["lat"], goshen["lon"]), (32.2487, -81.2095)) < 1


def test_fuzzy_match_keeps_the_direction():
    feats = overpass.load()
    if feats is None:
        return
    names = [f["name"] for _, f, _ in OsmIndex(feats, States()).candidates("north tifton", "GA")]
    assert "South Tifton Substation" not in names


def test_description_names_skip_new_places():
    from app.core.endpoints import description_names
    text = ("GPC will build a 230/25kV station named Two Run Ranch and loop it into the Cartersville - Pinson "
            "230kV line to serve new load in the area.")
    assert description_names(text) == ["Cartersville", "Pinson"]
    assert description_names("Build a new 7.58 mile line between new Cass Pine and Hill View 230kV substations.") == []
    assert description_names("Installation of Smart Valve devices inside the Eatonton Primary substation.") == ["Eatonton Primary"]


def test_description_places_stand_in_when_no_title_end_is_found(finished_run):
    p = finished_run.board.projects["DESC-06367ACH"]  # Riverport Tap: Riverport is new; the line runs from Okatie
    ctx = [e for e in p.endpoints if e.role == "context"]
    assert p.lat is not None and p.location_confidence == "town"
    assert [e.name for e in ctx] == ["Okatie"]
    assert span_mi(p) is None  # a description place is never one end of the line


def test_reference_scores_our_own_geocoding_too(finished_run):
    ref = finished_run.board.reference
    assert all(r.passed for r in ref)
    assert all(r.blind_passed is not None for r in ref)


class _Judge:
    # Stands in for Ctx: answers every question with one probability.
    def __init__(self, p: float) -> None:
        self.p = p

    async def ask_noul(self, *a, **k):
        from app.clients.judge import Verdict
        return Verdict(self.p, 0.5, "jev")

    def emit(self, *a, **k) -> None:
        pass

    def log(self, *a) -> None:
        pass

    def tool(self, *a, **k):
        import contextlib

        @contextlib.asynccontextmanager
        async def cm():
            yield {"summary": ""}
        return cm()


def _geocoder(features: list[dict]) -> Geocoder:
    from app.core.places import Towns
    g = Geocoder.__new__(Geocoder)
    g.states = States()
    g.osm, g.towns, g.overrides, g.points = OsmIndex(features, g.states), Towns(), {}, {}
    return g


def _feature(name: str, lat: float, lon: float, operator: str = "", osm_id: str = "way/1") -> dict:
    return {"osm_id": osm_id, "name": name, "operator": operator, "voltage": "", "lat": lat, "lon": lon,
            "power": "substation", "alt_names": [], "ref": ""}


def _project(name: str, utility: str = "DESC") -> Project:
    return Project(id="X", utility=utility, sponsor="DESC" if utility == "DESC" else "GPC", name=name,
                   in_service_date="2027-01-01", source_file="x", source_page=1, source_ref="x")


def test_unique_exact_name_in_state_accepts_a_weak_yes(monkeypatch):
    monkeypatch.setattr(config, "OSM_LIVE", False)  # no Nominatim calls from a unit test
    g = _geocoder([_feature("Harleyville Substation", 33.21, -80.451)])
    e = asyncio.run(g.locate_endpoint(_Judge(0.37), _project("Harleyville 115KV Tap"), "Harleyville", "SC"))
    assert e.method == "overpass" and e.evidence["accepted_by"] == "exact_name_rule"
    # below the floor, or two same-named substations in the state: the judge's no stands
    e = asyncio.run(g.locate_endpoint(_Judge(0.2), _project("Harleyville 115KV Tap"), "Harleyville", "SC"))
    assert e.method != "overpass"
    two = _geocoder([_feature("Goshen Substation", 33.32, -81.995), _feature("Goshen Substation", 32.249, -81.209, osm_id="way/2")])
    e = asyncio.run(two.locate_endpoint(_Judge(0.4), _project("Goshen - Kraft", "GA"), "Goshen", "GA"))
    assert e.method != "overpass"


def test_named_neighbor_utility_counts_as_home(monkeypatch):
    monkeypatch.setattr(config, "OSM_LIVE", False)
    webb = [_feature("Webb Substation", 31.251, -85.289, "Alabama Power"),
            _feature("Webb Substation", 31.255, -85.313, "Wiregrass Electric Cooperative", osm_id="way/2")]
    g = _geocoder(webb)
    e = asyncio.run(g.locate_endpoint(_Judge(0.32), _project("Lower River - Webb (Apc) 115kV", "GA"), "Webb", "GA"))
    assert e.method == "overpass" and e.evidence["operator"] == "Alabama Power"
    # without the cue it's an out-of-state candidate and needs a clear yes
    e = asyncio.run(g.locate_endpoint(_Judge(0.32), _project("Lower River - Webb 115kV", "GA"), "Webb", "GA"))
    assert e.method != "overpass"


def test_voltages_in_osm_names_are_ignored():
    from app.core.normalize import norm_key
    assert norm_key("Mitchell Substation (230kV)") == norm_key("Mitchell") == "mitchell"
    assert norm_key("North Tifton Substation (500kV)") == "north tifton"
    assert norm_key("Wrens 46/12 kV Substation") == "wrens"


def test_live_only_jev_calls_are_not_cached(monkeypatch):
    # The watchdog asks with use_cache=False every few seconds; storing those answers only churned the cache.
    from app.clients import jev

    class Resp:
        status_code = 200
        headers: dict = {}

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"answers": {"q": {"noul": 0.5}}, "usage": {"input_tokens": 1}}

    class Client:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a) -> None:
            pass

        async def post(self, *a, **k):
            return Resp()

    puts: list = []
    monkeypatch.setattr(config, "JEV_PROVIDER", "typesafe")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(jev.httpx, "AsyncClient", Client)
    monkeypatch.setattr(jev.cache, "put", lambda *a: puts.append(a))
    monkeypatch.setattr(jev.cache, "get", lambda *a: None)
    assert asyncio.run(jev.evaluate("s", {"q": {}}, use_cache=False))["answers"]
    assert puts == []
    asyncio.run(jev.evaluate("s", {"q": {}}))
    assert len(puts) == 1
