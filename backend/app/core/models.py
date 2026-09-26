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


class Project(BaseModel):
    id: str  # 'DESC-0139MN' or 'GA-20277'
    utility: Utility
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
