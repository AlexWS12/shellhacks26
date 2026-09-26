# Pulls every named substation in SC + GA once and saves it to data/cache/osm.
# Run scripts/fetch_osm.py on a machine with internet.

import json
import logging
from typing import Any

import httpx

from app.config import CACHE_DIR, NOMINATIM_USER_AGENT

log = logging.getLogger("overpass")
URL = "https://overpass-api.de/api/interpreter"
BBOX = (30.3, -85.7, 35.3, -78.4)  # south, west, north, east: all of GA and SC
OSM_FILE = CACHE_DIR / "osm" / "substations_sc_ga.json"

QUERY = f"""[out:json][timeout:120];
(
  nwr["power"="substation"]["name"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
);
out center tags;"""


def load() -> list[dict[str, Any]] | None:
    if OSM_FILE.exists():
        return json.loads(OSM_FILE.read_text(encoding="utf-8"))["features"]
    return None


def _feature(el: dict[str, Any]) -> dict[str, Any] | None:
    tags = el.get("tags", {})
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lon = el.get("lon") or (el.get("center") or {}).get("lon")
    if lat is None or lon is None or not tags.get("name"):
        return None
    return {"osm_id": f"{el['type']}/{el['id']}", "name": tags["name"], "operator": tags.get("operator", ""),
            "voltage": tags.get("voltage", ""), "substation": tags.get("substation", ""), "lat": lat, "lon": lon}


def fetch() -> list[dict[str, Any]]:
    r = httpx.post(URL, data={"data": QUERY}, headers={"User-Agent": NOMINATIM_USER_AGENT}, timeout=180)
    r.raise_for_status()
    features = [f for el in r.json().get("elements", []) if (f := _feature(el))]
    OSM_FILE.parent.mkdir(parents=True, exist_ok=True)
    OSM_FILE.write_text(json.dumps({"query": QUERY, "source": URL, "license": "ODbL, (c) OpenStreetMap contributors",
                                    "features": features}, indent=0), encoding="utf-8")
    log.info("Overpass: %d named substations saved to %s", len(features), OSM_FILE)
    return features
