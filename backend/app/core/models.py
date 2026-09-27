from typing import Literal

from pydantic import BaseModel, Field

Utility = Literal["DESC", "GA"]
Confidence = Literal["verified", "confirmed_osm", "partial", "town", "unlocated"]
Actor = Literal["code", "gemini", "jev", "osm", "sponsor_file", "override", "heuristic", "template"]


class Endpoint(BaseModel):
    name: str
    lat: float | None = None
    lon: float | None = None
    method: str = "none"  # sponsor_file | override | overpass | nominatim | geonames_town | none
    confidence: Confidence = "unlocated"
    evidence: dict = Field(default_factory=dict)
    # endpoint: named in the title. context: a place the description names, used only to place
    # a project whose title ends weren't found. Never drawn as a line or used for the span.
    role: Literal["endpoint", "context"] = "endpoint"


class Project(BaseModel):
    id: str  # 'DESC-0139MN', 'GA-20277', or '<submission>-<row>' for submitted plans
    utility: str  # 'DESC' | 'GA' | a submitted owner's key
    sponsor: str  # DESC | GPC | SAV | GTC | MEAG | DU
    name: str
    description: str = ""
    need_text: str = ""
    status: str = ""
    in_service_date: str  # ISO date
    in_service_raw: str = ""
    build_start: str | None = None  # known construction start
    build_active_from: str | None = None  # when work is known to be underway (DESC with "Previous" spend: 2024-01-01)
    cost_total: int | None = None
    cost_by_year: dict[str, int | None] | None = None
    miles: float | None = None
    zone: str | None = None
    project_type: str | None = None
    project_type_actor: str | None = None
    endpoints: list[Endpoint] = Field(default_factory=list)
    lat: float | None = None
    lon: float | None = None
    location_confidence: Confidence = "unlocated"
    source_file: str
    source_page: int
    source_ref: str  # 'ID 0139 M,N' or 'TEAMS 20277, zone 219'
    extracted_by: str = "code"
    sponsor_ref_id: str | None = None  # 'DESC_3' when the project is in Sperry's sample
    state: str | None = None  # SC | GA; set for submitted plans (Dominion = SC, Georgia = GA otherwise)
    date_precision: str | None = None  # day | month | year; submitted rows that give only a year or month


class Check(BaseModel):
    id: str
    level: Literal["error", "warn", "info"]
    rule: str
    title: str
    detail: str
    source: str
    project_id: str | None = None
    actor: str = "code"


class Overlap(BaseModel):
    id: str
    project_a: str  # DESC project id
    project_b: str  # GA project id
    distance_mi: float
    time_gap_days: int
    windows_overlap: bool | None = None
    pair_confidence: Confidence
    in_sponsor_sample: bool = False
    finished: bool = False  # either project is in service before today; ranked after the active pairs
    distance_slack_mi: float = 0.0  # how far the distance could move if the unlocated ends were found
    rank: int = 0


class ReferenceResult(BaseModel):
    overlap_id: str
    a: str
    b: str
    a_project: str | None
    b_project: str | None
    expected_mi: float
    got_mi: float | None
    expected_days: int
    got_days: int | None
    passed: bool
    # Same pair with our own geocoding: the benchmark projects located as if the file had no coordinates.
    blind_mi: float | None = None
    blind_passed: bool | None = None  # within BLIND_TOLERANCE_MI of the expected distance


# Other utilities' projects, found by the research team (data/research/other_utilities.json or a live search).
ResearchCategory = Literal["electric", "gas", "roads_water"]
RESEARCH_CATEGORIES: tuple[str, ...] = ("electric", "gas", "roads_water")


class Source(BaseModel):
    url: str
    title: str = ""
    publisher: str = ""
    quote: str = ""  # verbatim from the page
    accessed: str | None = None


class ResearchPlace(BaseModel):
    name: str
    kind: str = "other"  # substation | power_plant | town | county | road | facility | water_body | other
    state: str | None = None  # SC | GA
    role: str = "site"  # endpoint | site | along


class ResearchProject(BaseModel):
    id: str  # 'OU-santee-cooper-...' (research file) or 'LIVE-...' (live search)
    category: ResearchCategory
    utility: str
    utility_kind: str = ""
    name: str
    description: str = ""
    status: str = "unknown"
    start: str | None = None  # as precise as the source: 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD'
    in_service: str | None = None
    date_quote: str | None = None
    start_date: str | None = None  # ISO, set by code: first day of the stated period
    in_service_date: str | None = None  # ISO, set by code: last day of the stated period
    date_precision: str | None = None  # day | month | year, for in_service
    places: list[ResearchPlace] = Field(default_factory=list)
    stated_coordinates: list[dict] = Field(default_factory=list)  # only when a source prints them
    endpoints: list[Endpoint] = Field(default_factory=list)  # one per place, located or not
    lat: float | None = None
    lon: float | None = None
    location_confidence: Confidence = "unlocated"
    miles: float | None = None
    cost_usd: float | None = None
    cost_quote: str | None = None
    sources: list[Source] = Field(default_factory=list)
    found_by: str = "research_file"  # research_file | gemini_search
    verification: dict = Field(default_factory=dict)


class ThirdParty(BaseModel):
    # Another utility's project under 25 mi from BOTH projects of a Dominion-Georgia opportunity.
    overlap_id: str
    research_id: str
    category: ResearchCategory
    dist_a_mi: float
    dist_b_mi: float
    gap_a_days: int | None  # |in-service date - Dominion in-service date|, None if the source gives no date
    gap_b_days: int | None
    approx_date: bool = False  # the source gives only a year or month
    confidence: Confidence
