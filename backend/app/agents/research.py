# The research team: one scout per category of other utility, plus a code-only agent that flags
# other utilities' projects near each Dominion-Georgia opportunity.
#
# Scouts read data/research/other_utilities.json (built by a team of research agents; every record cites
# its sources and was checked by independent fact-checkers). With RESEARCH_LIVE=true and Gemini available,
# they also run a Google-grounded search and keep only claims tied to a returned page.
# Locations come from the same sources as the filings: overrides, OpenStreetMap + judge, GeoNames towns,
# Census county centers, Nominatim. Distances and day gaps are code (core/research.py).

import asyncio
import json
from functools import lru_cache
from typing import Any

from app import config
from app.clients import models, nominatim, overpass
from app.clients.gemini import cite
from app.core.models import Endpoint, ResearchPlace, ResearchProject
from app.core.normalize import norm_key
from app.core.owners import book
from app.core.places import OsmIndex, Place, States, Towns, load_overrides, variants
from app.core.research import load_file, nearby, place_center, prepare, rollup_confidence
from app.runtime.agent import Agent, AgentSpec, Ctx

ACCEPT = 0.5
LABEL = {"electric": "Electric", "gas": "Gas", "roads_water": "Roads & water"}
WHAT = {"electric": "electric transmission, substation, generation and storage",
        "gas": "natural gas pipeline, compressor, LNG and gas distribution",
        "roads_water": "road, bridge, water, sewer, port and waterway"}
AREA = ("the Augusta-Aiken area (Aiken, Edgefield, McCormick, Barnwell counties SC; Richmond, Columbia, McDuffie, "
        "Burke counties GA) and the Savannah-Jasper-Beaufort area (Jasper, Beaufort, Hampton counties SC; Chatham, "
        "Effingham, Bryan counties GA)")
POINT_KINDS = ("substation", "power_plant", "facility")

LIVE_SCHEMA = {
    "type": "object",
    "properties": {"projects": {"type": "array", "items": {"type": "object", "properties": {
        "utility": {"type": "string"}, "utility_kind": {"type": "string"}, "name": {"type": "string"},
        "description": {"type": "string"},
        "status": {"type": "string", "enum": ["planned", "under_construction", "completed", "unknown"]},
        "start": {"type": ["string", "null"]}, "in_service": {"type": ["string", "null"]},
        "date_quote": {"type": ["string", "null"]},
        "places": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"},
            "kind": {"type": "string", "enum": ["substation", "power_plant", "town", "county", "road", "facility",
                                                 "water_body", "other"]},
            "state": {"type": "string", "enum": ["SC", "GA"]},
            "role": {"type": "string", "enum": ["endpoint", "site", "along"]}},
            "required": ["name", "kind", "state", "role"]}},
        "source_numbers": {"type": "array", "items": {"type": "integer"}}},
        "required": ["utility", "name", "description", "status", "start", "in_service", "date_quote", "places",
                     "source_numbers"]}}},
    "required": ["projects"],
}
LIVE_SEARCH_SYSTEM = ("You research public construction plans. Name specific projects with their owner, the towns or "
                      "counties they are in, and their start and completion dates exactly as the sources state them.")
LIVE_EXTRACT_SYSTEM = (
    "Turn the text into project records. Use ONLY facts in the text. Each record must list the [n] source numbers "
    "that appear next to its facts. Dates exactly as precise as the text ('2028', '2027-06', '2026-10-01'), null if "
    "not given. Never add places, dates or numbers that the text does not state.")


@lru_cache(maxsize=1)
def _resources() -> dict[str, Any]:
    # Loaded once per process and shared by the three scouts.
    states = States()
    counties = json.loads((config.GEO_DIR / "county_centroids_sc_ga.json").read_text(encoding="utf-8"))
    return {"states": states, "osm": OsmIndex(overpass.load() or [], states), "towns": Towns(),
            "counties": {(norm_key(n), s): Place(la, lo, f"{n} County, {s}", {}) for n, s, la, lo in counties["rows"]},
            "county_source": counties["source"]}


def _in_box(lat: float, lon: float) -> bool:
    # Inside the search area: the union of the active sources' states (or OSM_BBOX).
    s, w, n, e = book().bbox()
    return s <= lat <= n and w <= lon <= e


class ResearchScout(Agent):
    def __init__(self, category: str) -> None:
        self.category = category
        live = config.RESEARCH_LIVE
        self.spec = AgentSpec(
            f"research_{category}", f"Scout · {LABEL[category]}",
            f"Finds other utilities' {WHAT[category]} projects near the river and places them",
            ["research_file", "osm", "jev"] + (["gemini"] if live else []),
            engine="Research + Gemini" if live else "Research + Jev", team="research",
            roles=["research_search", "research_extract", "research_confirm"])

    async def run(self, ctx: Ctx) -> str:
        if self.category not in ctx.run.research:
            ctx.log(f"{self.spec.name}: not selected for this run.")
            return "not selected for this run"
        try:
            return await self.scout(ctx)
        except Exception as e:  # the research team is extra: its failure must not stop the core results
            ctx.log(f"{self.spec.name} stopped: {type(e).__name__}: {e}")
            return f"stopped: {type(e).__name__}"

    async def scout(self, ctx: Ctx) -> str:
        b = ctx.board
        async with ctx.tool("read_research_file", {"file": config.RESEARCH_FILE.name, "category": self.category},
                            actor="research_file") as out:
            records, meta = load_file(config.RESEARCH_FILE)
            records = [r for r in records if r.category == self.category]
            out["summary"] = (f"{len(records)} cited records (researched {meta.get('generated', '?')}, "
                              f"each checked by independent fact-checkers"
                              + (f"; {meta['skipped']} malformed skipped" if meta.get("skipped") else "") + ")"
                              if meta else "no research file yet")
        if config.RESEARCH_LIVE:
            records += await self.live_search(ctx, records)
        res = _resources()
        sem = asyncio.Semaphore(6)
        placed = 0

        async def one(r: ResearchProject) -> None:
            nonlocal placed
            ctx.emit("research.found", record=r.model_dump(),
                     **({"model": r.verification.get("model")} if r.found_by == "gemini_search" else {}))
            async with sem:
                await self.locate(ctx, r, res)
            b.research[r.id] = r  # each scout writes only its own category's records
            if r.lat is not None:
                placed += 1
                ctx.emit("research.placed", research_id=r.id, lat=r.lat, lon=r.lon, confidence=r.location_confidence,
                         endpoints=[e.model_dump() for e in r.endpoints])
            else:
                ctx.emit("research.unlocated", research_id=r.id, endpoints=[e.model_dump() for e in r.endpoints],
                         reason=" | ".join(f"{e.name}: {e.evidence.get('reason', '')}" for e in r.endpoints)
                         or "the sources name no place")
            ctx.progress(placed, len(records), "placed")
            await ctx.pace(0.15)

        await asyncio.gather(*(one(r) for r in records))
        ctx.log(f"{self.spec.name}: {len(records)} projects by other owners, {placed} placed on the map.")
        return f"{len(records)} projects, {placed} placed"

    async def live_search(self, ctx: Ctx, known: list[ResearchProject]) -> list[ResearchProject]:
        if not models.available("research_search"):
            ctx.log(f"{self.spec.name}: live search is on but no model is configured for it; using the research file only.")
            return []
        prompt = (f"List specific {WHAT[self.category]} construction projects in {AREA} that are under construction or "
                  "planned between 2024 and 2034, by owners other than Dominion Energy South Carolina's electric "
                  "business and Georgia Power's electric business. Give owner, project name, towns or counties, and "
                  "start and completion dates.")
        async with ctx.tool("gemini_google_search", {"category": self.category}, actor="gemini") as out:
            try:
                g = await models.call("research_search", prompt, system=LIVE_SEARCH_SYSTEM)
            except models.RoleExhausted:
                g = None
            out["summary"] = f"{len(g.value['sources'])} pages" if g else "search unavailable"
            out["model"] = g.model if g else None
        if not g or not g.value["sources"]:
            return []
        found = g.value
        numbered = "\n".join(f"[{i + 1}] {s['title']} {s['url']}" for i, s in enumerate(found["sources"]))
        async with ctx.tool("gemini_extract_records", {"sources": len(found["sources"])}, actor="gemini") as out:
            try:
                x = await models.call("research_extract", f"Text:\n{cite(found['text'], found['supports'])}\n\n"
                                      f"Sources:\n{numbered}", LIVE_SCHEMA, system=LIVE_EXTRACT_SYSTEM)
            except models.RoleExhausted:
                x = None
            out["summary"] = f"{len(x.value.get('projects', []))} records" if x else "extraction failed"
            out["model"] = x.model if x else None
        records = live_records(x.value.get("projects", []) if x else [], found["sources"], self.category, known)
        for r in records:
            r.verification["model"] = x.model if x else None  # which model turned the search into this record
        return records

    async def locate(self, ctx: Ctx, r: ResearchProject, res: dict[str, Any]) -> None:
        stated = [c for c in r.stated_coordinates if _in_box(float(c.get("lat", 0)), float(c.get("lon", 0)))]
        if stated:  # a source printed coordinates: use them as given
            c = stated[0]
            r.endpoints = [Endpoint(name=r.places[0].name if r.places else r.name, lat=float(c["lat"]), lon=float(c["lon"]),
                                    method="source", confidence="verified", evidence={"url": c.get("url"), "quote": c.get("quote")})]
            r.places = r.places[:1] or [ResearchPlace(name=r.name, role="site")]
        else:
            r.endpoints = [await self.locate_place(ctx, r, pl, res) for pl in r.places]
        c = place_center(r)
        r.lat, r.lon = c if c else (None, None)
        r.location_confidence = rollup_confidence(r)

    async def locate_place(self, ctx: Ctx, r: ResearchProject, pl: ResearchPlace, res: dict[str, Any]) -> Endpoint:
        state, key = pl.state, norm_key(pl.name)
        keys, tried = variants(key), []
        for k in keys:
            if state and (o := load_overrides().get(f"{k}|{state}")) is not None:
                return _ep(pl.name, o, "override", o.extra["confidence"], o.extra)

        if pl.kind in POINT_KINDS:
            cands = [c for c in res["osm"].candidates(key, state) if c[1].get("state") == state][:2]
            if not cands:
                tried.append(f"OpenStreetMap: no power feature by that name in {state}")
            for score, f, via in cands:
                v = await ctx.ask_noul(
                    "Is this OpenStreetMap feature the place the research record names?", f"{pl.name} → {f['name']}",
                    {"place": pl.name, "kind": pl.kind, "state": state, "project": r.name, "owner": r.utility,
                     "description": r.description[:300],
                     "candidate": {k2: f.get(k2) for k2 in ("name", "operator", "voltage", "power", "state")}},
                    true="Same facility: name, owner or operator and area fit the project.",
                    false="A different facility that only shares part of the name, or is in another area.",
                    heuristic=lambda s=score, via=via: 0.8 if s == 1.0 and via in ("name", "alt_name") else 0.3,
                    role="research_confirm")
                if v.value >= ACCEPT:
                    return _ep(pl.name, Place(f["lat"], f["lon"], f["name"], {}), "overpass", "confirmed_osm",
                               {"osm_id": f["osm_id"], "osm_name": f["name"], "judge": v.actor, "p_match": round(float(v.value), 3)})
                tried.append(f"OpenStreetMap {f['name']}: rejected by {v.actor}")

        county_key = norm_key(pl.name.replace(" County", "").replace(" county", ""))
        if pl.kind == "county" or pl.name.lower().endswith(" county"):
            if (c := res["counties"].get((county_key, state))) is not None:
                return _ep(pl.name, c, "county_centroid", "town", {"county": c.label, "source": res["county_source"]})
            tried.append(f"Census: no {pl.name} in {state}")
            return Endpoint(name=pl.name, evidence={"tried": tried, "reason": "; ".join(tried)})

        if pl.kind == "road":
            tried.append("roads are not placed as points; the project is placed by its towns or counties")
            return Endpoint(name=pl.name, evidence={"tried": tried, "reason": "; ".join(tried)})

        town = next((t for k in keys if state and (t := res["towns"].find(k, state))), None)
        if town is None:
            tried.append(f"GeoNames: no town by that name in {state}")
        else:
            v = await ctx.ask_noul(
                "Is this town the place the research record names?", f"{pl.name} → {town.label}",
                {"place": pl.name, "kind": pl.kind, "project": r.name, "owner": r.utility,
                 "description": r.description[:300], "candidate_town": town.label, "county": town.extra.get("county")},
                true="The record means this town (or a site in it).",
                false="The name only coincides with the town.",
                heuristic=lambda: 0.8 if pl.kind == "town" else 0.55, role="research_confirm")
            if v.value >= ACCEPT:
                return _ep(pl.name, town, "geonames_town", "town",
                           {"town": town.label, "judge": v.actor, "p_match": round(float(v.value), 3)})
            tried.append(f"GeoNames {town.label}: rejected by {v.actor}")

        results, source = await nominatim.search(key)
        if source in ("off", "error", "budget"):
            tried.append(f"Nominatim: {source}")
        named = [x for x in results if norm_key(x["name"]) in keys and res["states"].state_of(x["lat"], x["lon"]) == state]
        if source not in ("off", "error", "budget") and not named:
            tried.append(f"Nominatim: nothing by that name in {state}")
        for x in named[:1]:
            v = await ctx.ask_noul(
                "Is this OpenStreetMap place the place the research record names?", f"{pl.name} → {x['display_name'][:60]}",
                {"place": pl.name, "kind": pl.kind, "project": r.name, "owner": r.utility,
                 "description": r.description[:300], "candidate": {"name": x["name"], "kind": x["kind"], "county": x["county"]}},
                true="The record means this place.", false="The name only coincides.",
                heuristic=lambda: 0.7, role="research_confirm")
            if v.value >= ACCEPT:
                sub = x["kind"] == "power=substation"
                return _ep(pl.name, Place(x["lat"], x["lon"], x["display_name"], {}), "nominatim",
                           "confirmed_osm" if sub else "town",
                           {"osm_id": x["osm_id"], "kind": x["kind"], "judge": v.actor, "p_match": round(float(v.value), 3)})
            tried.append(f"Nominatim {x['name']}: rejected by {v.actor}")
        return Endpoint(name=pl.name, evidence={"tried": tried, "reason": "; ".join(tried)})


def _ep(name: str, place: Place, method: str, confidence: str, evidence: dict) -> Endpoint:
    return Endpoint(name=name, lat=place.lat, lon=place.lon, method=method, confidence=confidence,  # type: ignore[arg-type]
                    evidence={"matched": place.label, **evidence})


def live_records(raw: list[dict[str, Any]], sources: list[dict[str, str]], category: str,
                 known: list[ResearchProject]) -> list[ResearchProject]:
    # Keep only records tied to a returned page; skip ones the research file already has.
    have = {norm_key(r.name) for r in known}
    out: list[ResearchProject] = []
    for rec in raw:
        nums = sorted({n for n in rec.get("source_numbers") or [] if isinstance(n, int) and 1 <= n <= len(sources)})
        if not nums or norm_key(rec.get("name", "")) in have:
            continue
        srcs = [{"url": sources[n - 1]["url"], "title": sources[n - 1]["title"], "publisher": sources[n - 1]["title"],
                 "quote": ""} for n in nums]
        r = prepare({**rec, "category": category, "sources": srcs,
                     "verification": {"note": "live web search via Gemini; not checked by the fact-checkers"}},
                    prefix="LIVE", found_by="gemini_search")
        if r:
            have.add(norm_key(r.name))
            out.append(r)
    return out


class OtherUtilities(Agent):
    spec = AgentSpec("third_party", "Other utilities", "Flags other utilities' projects under 25 mi from both sides "
                     "of an opportunity. Plain code", ["code"],
                     depends_on=["overlap", "research_electric", "research_gas", "research_roads_water"],
                     kind="tool", engine="Math", team="research")

    async def run(self, ctx: Ctx) -> str:
        b = ctx.board
        b.research_selected = list(ctx.run.research)
        async with ctx.tool("find_nearby_others", {"cutoff_mi": 25, "rule": "under 25 mi from BOTH project centers",
                                                   "categories": b.research_selected}) as out:
            b.third_party = nearby(b.overlaps, b.projects, list(b.research.values()), b.research_selected)
            out["summary"] = f"{len(b.third_party)} links on {len({t.overlap_id for t in b.third_party})} opportunities"
        for t in b.third_party:
            ctx.emit("third_party.found", link=t.model_dump())
            await ctx.pace(0.1)
        ctx.log(f"Other utilities: {len(b.third_party)} other-owner projects sit under 25 mi from both sides of an "
                f"opportunity.")
        return f"{len(b.third_party)} links on {len({t.overlap_id for t in b.third_party})} opportunities"
