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


# Filings abbreviate, OSM and GeoNames spell out ("St George" vs "Saint George Switching Station").
ABBREV = {"st": "saint", "ft": "fort", "mt": "mount", "pt": "point"}
SPELLED = {v: k for k, v in ABBREV.items()}


def variants(key: str, strip: bool = True) -> list[str]:
    # The exact key first, then the generic-stripped key, then each with abbreviations flipped.
    out: list[str] = []
    for k in (key, strip_generic(key)) if strip else (key,):
        words = k.split()
        for table in (None, ABBREV, SPELLED):
            v = " ".join(table.get(w, w) for w in words) if table else k
            if v and v not in out:
                out.append(v)
    return out


class States:
    # SC and GA outlines (Census, simplified). Used to rank candidates, never to compute a distance.
    def __init__(self) -> None:
        data = json.loads((GEO_DIR / "states_sc_ga.json").read_text(encoding="utf-8"))
        self.rings: list[tuple[str, list[list[float]]]] = []
        for f in data["features"]:
            g = f["geometry"]
            polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
            self.rings += [(f["properties"]["state"], poly[0]) for poly in polys]

    def state_of(self, lat: float, lon: float) -> str | None:
        for state, ring in self.rings:
            inside = False
            for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
                if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                    inside = not inside
            if inside:
                return state
        return None


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


OPERATORS = {"SC": ("dominion", "sce&g", "south carolina electric", "santee cooper"),
             "GA": ("georgia power", "southern company", "georgia transmission", "meag")}


class OsmIndex:
    # Ranks by name similarity, then state and operator. The judge confirms.
    def __init__(self, features: list[dict[str, Any]], states: States | None = None) -> None:
        self.features = features
        self.by_key: dict[str, list[tuple[dict[str, Any], str]]] = {}
        for f in features:
            if states is not None and "state" not in f:
                f["state"] = states.state_of(f["lat"], f["lon"])
            for k, via in osm_keys(f):
                if not any(g is f for g, _ in self.by_key.get(k, [])):
                    self.by_key.setdefault(k, []).append((f, via))

    def candidates(self, key: str, state: str | None = None, limit: int = 3) -> list[tuple[float, dict[str, Any], str]]:
        # (name similarity, feature, which tag matched), one entry per feature, best first.
        if not key:
            return []
        names: dict[str, float] = {}
        for k in variants(key):
            if k in self.by_key:
                names[k] = 1.0
            for close in difflib.get_close_matches(k, list(self.by_key), n=limit, cutoff=0.82):
                names.setdefault(close, round(difflib.SequenceMatcher(None, k, close).ratio(), 3))
        best: dict[str, tuple[float, float, dict[str, Any], str]] = {}
        for k, sim in names.items():
            for f, via in self.by_key[k]:
                bonus = 0.0
                if state and f.get("state") == state:
                    bonus += 0.1
                if state and any(o in f.get("operator", "").lower() for o in OPERATORS.get(state, ())):
                    bonus += 0.05
                if via in ("name", "alt_name"):
                    bonus += 0.01  # a real name beats a name part or operator match at the same similarity
                prev = best.get(f["osm_id"])
                if prev is None or sim + bonus > prev[0]:
                    best[f["osm_id"]] = (sim + bonus, sim, f, via)
        ranked = sorted(best.values(), key=lambda c: -c[0])
        return [(sim, f, via) for _, sim, f, via in ranked[:limit]]
