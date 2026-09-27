# Nominatim (OpenStreetMap search) for endpoint names that OSM substations and GeoNames towns miss.
# Usage policy: at most 1 request per second, a real User-Agent, and cache everything.
# https://operations.osmfoundation.org/policies/nominatim/

import asyncio
import logging
import time
from typing import Any

import httpx

from app import config
from app.clients import cache

log = logging.getLogger("nominatim")
URL = "https://nominatim.openstreetmap.org/search"
CACHE_VERSION = "v1"
VIEWBOX = "-85.7,35.3,-78.4,30.3"  # left, top, right, bottom: all of GA and SC
MIN_GAP_S = 1.1
MAX_FAILURES = 3  # then stop trying for the rest of the process (offline, blocked)

# What a named endpoint can plausibly be. Roads, shops and churches that share the name are dropped.
PLACE_TYPES = {"city", "town", "village", "hamlet", "locality", "suburb", "neighbourhood", "isolated_dwelling"}

_lock = asyncio.Lock()
_last = 0.0
_failures = 0
_deadline = float("inf")


def start_budget(seconds: float) -> None:
    # Live lookups for one run stop after this many seconds so the geocoder stays under its timeout.
    # Whatever was skipped is looked up on a later run; everything fetched is cached.
    global _deadline
    _deadline = time.monotonic() + seconds


def _keep(r: dict[str, Any]) -> dict[str, Any] | None:
    cls, typ = r.get("category") or r.get("class"), r.get("type")
    if not ((cls == "power" and typ == "substation") or (cls == "place" and typ in PLACE_TYPES)):
        return None
    addr = r.get("address") or {}
    return {"osm_id": f"{r.get('osm_type')}/{r.get('osm_id')}", "name": r.get("name") or r.get("display_name", ""),
            "display_name": r.get("display_name", ""), "kind": f"{cls}={typ}", "lat": float(r["lat"]),
            "lon": float(r["lon"]), "county": addr.get("county", ""), "state_name": addr.get("state", "")}


async def search(name: str) -> tuple[list[dict[str, Any]], str]:
    # Returns (candidates inside SC + GA, "cache" | "live" | "off" | "budget" | "error").
    global _last, _failures
    payload = {"q": name, "viewbox": VIEWBOX, "bounded": 1, "countrycodes": "us", "format": "jsonv2",
               "addressdetails": 1, "limit": 5}
    if (hit := cache.get("nominatim", CACHE_VERSION, payload)) is not None:
        return [k for r in hit if (k := _keep(r))], "cache"
    if not config.OSM_LIVE or _failures >= MAX_FAILURES:
        return [], "off"
    if time.monotonic() > _deadline:
        return [], "budget"
    async with _lock:
        if time.monotonic() > _deadline:
            return [], "budget"
        wait = MIN_GAP_S - (time.monotonic() - _last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(URL, params=payload, headers={"User-Agent": config.NOMINATIM_USER_AGENT})
            r.raise_for_status()
            raw = r.json()
            _failures = 0
        except Exception as e:  # offline or throttled: the endpoint just stays unlocated
            _failures += 1
            log.warning("Nominatim failed for %r: %s", name, e)
            return [], "error"
        finally:
            _last = time.monotonic()
    cache.put("nominatim", CACHE_VERSION, payload, raw)
    return [k for r in raw if (k := _keep(r))], "live"
