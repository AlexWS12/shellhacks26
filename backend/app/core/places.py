import csv
import difflib
import json
import re
from dataclasses import dataclass
from typing import Any

from app.config import GEO_DIR, OVERRIDES_CSV
from app.core.normalize import norm_key
from app.core.sample import Sample

GENERIC = re.compile(r"\b(primary|pri|dam|energy|reservoir|transmission|relay|modernization|upgrades?|new|build|low|"
                     r"side|breaker|equipment|replacement|psa|county|common|\d+)\b")


def strip_generic(key: str) -> str:
    return re.sub(r"\s+", " ", GENERIC.sub("", key)).strip()


@dataclass
class Place:
    lat: float
    lon: float
    label: str
    extra: dict[str, Any]


def sponsor_points(sample: Sample | None) -> dict[str, Place]:
    out: dict[str, Place] = {}
    if not sample:
        return out
    for sp in sample.projects.values():
        for pt in (sp.a, sp.b):
            if pt.name and pt.lat is not None and pt.lon is not None:
                out.setdefault(norm_key(pt.name), Place(pt.lat, pt.lon, pt.name, {"ref_id": sp.ref_id}))
    return out


def load_overrides() -> dict[str, Place]:
    # Columns: endpoint,state,lat,lon,source_note (the note is required).
    out: dict[str, Place] = {}
    if not OVERRIDES_CSV.exists():
        return out
    with OVERRIDES_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("source_note", "").strip() and row.get("lat") and row.get("lon"):
                out[f"{norm_key(row['endpoint'])}|{row['state'].strip().upper()}"] = Place(
                    float(row["lat"]), float(row["lon"]), row["endpoint"], {"note": row["source_note"]})
    return out


class Towns:
    # GeoNames towns. Exact name match in the same state only.
    def __init__(self) -> None:
        data = json.loads((GEO_DIR / "towns_sc_ga.json").read_text(encoding="utf-8"))
        self.source = data["source"]
        self.index: dict[tuple[str, str], Place] = {}
        for name, state, lat, lon, county in data["rows"]:
            self.index.setdefault((norm_key(name), state), Place(lat, lon, f"{name}, {state}", {"county": county}))

    def find(self, key: str, state: str) -> Place | None:
        return self.index.get((key, state))


class OsmIndex:
    # Scores by name only. The judge confirms.
    def __init__(self, features: list[dict[str, Any]]) -> None:
        self.features = features
        self.by_key: dict[str, list[dict[str, Any]]] = {}
        for f in features:
            self.by_key.setdefault(norm_key(f["name"]), []).append(f)

    def candidates(self, key: str, limit: int = 3) -> list[tuple[float, dict[str, Any]]]:
        if not key:
            return []
        scored: list[tuple[float, dict[str, Any]]] = [(1.0, f) for f in self.by_key.get(key, [])]
        if len(scored) < limit:
            for k in difflib.get_close_matches(key, list(self.by_key), n=limit, cutoff=0.82):
                if k != key:
                    ratio = difflib.SequenceMatcher(None, key, k).ratio()
                    scored.extend((ratio, f) for f in self.by_key[k])
        return sorted(scored, key=lambda s: -s[0])[:limit]
