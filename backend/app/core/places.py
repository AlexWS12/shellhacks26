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


POWER_WORDS = re.compile(r"\b(plant|power|steam|generating|station|switchyard|hydro|hydroelectric|electric|"
                         r"combined cycle|energy center)\b")


def osm_keys(f: dict[str, Any]) -> list[tuple[str, str]]:
    # (key, which tag it came from). 'Plant Yates' and 'Yates Switchyard' both index as 'yates' too.
    out: list[tuple[str, str]] = []
    names = [(f["name"], "name")] + [(n, "alt_name") for n in f.get("alt_names", [])]
    if f.get("operator"):
        names.append((f"{f['operator']} {f['name']}", "operator+name"))
    if re.search(r"[a-z]{3}", f.get("ref", ""), re.I):  # skip numeric refs like '12'
        names.append((f["ref"], "ref"))
    parts = re.split(r"\s*[-/]\s*", f["name"])
    if len(parts) > 1:  # 'Plant McDonough-Atkinson' -> 'McDonough', 'Atkinson'
        names.extend((part, "name_part") for part in parts)
    for text, via in names:
        key = norm_key(text)
        for k in (key, re.sub(r"\s+", " ", POWER_WORDS.sub("", strip_generic(key))).strip()):
            if k and (k, via) not in out:
                out.append((k, via))
    return out


class OsmIndex:
    # Scores by name only. The judge confirms.
    def __init__(self, features: list[dict[str, Any]]) -> None:
        self.features = features
        self.by_key: dict[str, list[tuple[dict[str, Any], str]]] = {}
        for f in features:
            for k, via in osm_keys(f):
                if not any(g is f for g, _ in self.by_key.get(k, [])):
                    self.by_key.setdefault(k, []).append((f, via))

    def candidates(self, key: str, limit: int = 3) -> list[tuple[float, dict[str, Any], str]]:
        if not key:
            return []
        scored: list[tuple[float, dict[str, Any], str]] = [(1.0, f, via) for f, via in self.by_key.get(key, [])]
        if len(scored) < limit:
            for k in difflib.get_close_matches(key, list(self.by_key), n=limit, cutoff=0.82):
                if k != key:
                    ratio = difflib.SequenceMatcher(None, key, k).ratio()
                    scored.extend((ratio, f, via) for f, via in self.by_key[k])
        best: dict[str, tuple[float, dict[str, Any], str]] = {}
        for c in sorted(scored, key=lambda s: -s[0]):
            best.setdefault(c[1]["osm_id"], c)  # one entry per feature, its best-scoring key
        return list(best.values())[:limit]
