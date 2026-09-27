# Rebuilds data/geo/towns_sc_ga.json: the GeoNames rows already there, plus every SC and GA place in the
# Census Gazetteer (incorporated towns and census-designated places, public domain) that GeoNames lacks.
# GeoNames cities1000 stops at population 1,000, so small towns like Eastover and Harleyville (SC) were missing.
#   uv run python scripts/build_towns.py
# The Gazetteer zip is cached in data/cache/census/ and only downloaded when it isn't there.

import csv
import io
import json
import sys
import zipfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CACHE_DIR, GEO_DIR, REPO_DIR  # noqa: E402
from app.core.normalize import norm_key  # noqa: E402

URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_place_national.zip"
ZIP = CACHE_DIR / "census" / "2023_Gaz_place_national.zip"
COUNTIES = REPO_DIR / "frontend" / "public" / "geo" / "counties_sc_ga.json"
TOWNS = GEO_DIR / "towns_sc_ga.json"
# 'Eastover town', 'Okatie CDP' -> 'Eastover', 'Okatie'
SUFFIXES = (" city", " town", " village", " CDP", " consolidated government (balance)", " unified government (balance)")


def inside(lat: float, lon: float, ring: list[list[float]]) -> bool:
    hit = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def county_of(lat: float, lon: float, counties: list[tuple[str, list[list[float]]]]) -> str:
    return next((name for name, ring in counties if inside(lat, lon, ring)), "")


def main() -> None:
    if not ZIP.exists():
        ZIP.parent.mkdir(parents=True, exist_ok=True)
        r = httpx.get(URL, timeout=60, follow_redirects=True)
        r.raise_for_status()
        ZIP.write_bytes(r.content)
    with zipfile.ZipFile(ZIP) as z:
        text = z.read(z.namelist()[0]).decode("latin-1")
    counties = []
    for f in json.loads(COUNTIES.read_text(encoding="utf-8"))["features"]:
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        counties += [(f"{f['properties']['name']} County", p[0]) for p in polys]

    data = json.loads(TOWNS.read_text(encoding="utf-8"))
    geonames = [r for r in data["rows"] if len(r) < 6 or r[5] == "geonames"]
    rows = [r[:5] + ["geonames"] for r in geonames]
    have = {(norm_key(r[0]), r[1]) for r in rows}
    added = 0
    for rec in csv.DictReader(io.StringIO(text), delimiter="\t"):
        rec = {k.strip(): v.strip() for k, v in rec.items()}
        if rec["USPS"] not in ("SC", "GA"):
            continue
        name = rec["NAME"]
        for s in SUFFIXES:
            if name.endswith(s):
                name = name[: -len(s)]
                break
        lat, lon = float(rec["INTPTLAT"]), float(rec["INTPTLONG"])
        if (norm_key(name), rec["USPS"]) in have:
            continue
        have.add((norm_key(name), rec["USPS"]))
        rows.append([name, rec["USPS"], round(lat, 5), round(lon, 5), county_of(lat, lon, counties), "census"])
        added += 1
    TOWNS.write_text(json.dumps({
        "source": "GeoNames cities1000 (CC BY 4.0) via reverse_geocoder 1.5.1, population >= 1000; plus US Census "
                  "Gazetteer 2023 places (public domain) missing from GeoNames, by scripts/build_towns.py",
        "columns": ["name", "state", "lat", "lon", "county", "source"],
        "rows": rows}, indent=0), encoding="utf-8")
    print(f"{len(geonames)} GeoNames + {added} Census places -> {TOWNS}")


if __name__ == "__main__":
    main()
