# Source order: override > sponsor sample > OSM substation > town.
# Fuzzy matches (OSM, towns) get confirmed by the judge.

import asyncio

from app import config
from app.clients import gemini, overpass
from app.core.endpoints import clean_endpoint, looks_awkward, split_endpoints
from app.core.models import Check, Endpoint, Project
from app.core.normalize import norm_key
from app.core.overlap import center
from app.core.places import OsmIndex, Place, Towns, load_overrides, sponsor_points, strip_generic
from app.core.sample import match_projects
from app.runtime.agent import Agent, AgentSpec, Ctx

ACCEPT = 0.5
SPLIT_SCHEMA = {"type": "object", "properties": {"endpoints": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
                                                 "note": {"type": "string"}}, "required": ["endpoints"]}


class Geocoder(Agent):
    spec = AgentSpec("geocoder", "Geocoder", "Finds coordinates: surveyed points, OpenStreetMap, then towns",
                     ["code", "osm", "jev", "gemini"], depends_on=["sample", "extract_desc", "extract_ga"], engine="OSM + Jev")

    async def run(self, ctx: Ctx) -> str:
        b = ctx.board
        async with ctx.tool("match_sample", {"sample_projects": len(b.sample.projects) if b.sample else 0}) as out:
            b.sample_map = match_projects(b.sample, list(b.projects.values())) if b.sample else {}
            out["summary"] = f"matched {len(b.sample_map)} of {len(b.sample.projects) if b.sample else 0} benchmark projects"
        ctx.emit("sample.matched", mapping=b.sample_map)

        features = overpass.load()
        source = "cache" if features is not None else "live"
        async with ctx.tool("overpass_substations", {"bbox": overpass.BBOX, "source": source}, actor="osm") as out:
            if features is None and config.OSM_LIVE:
                try:
                    features = await asyncio.wait_for(asyncio.to_thread(overpass.fetch), timeout=150)
                except Exception as e:  # offline or rate limited: keep going with towns
                    out["summary"] = f"OpenStreetMap unreachable ({type(e).__name__}), using towns"
            if features is not None:
                out["summary"] = f"{len(features)} named substations in SC + GA ({source})"
            elif not out["summary"]:
                out["summary"] = "no OpenStreetMap data (OSM_LIVE is off)"
        self.osm = OsmIndex(features or [])
        self.towns, self.overrides, self.points = Towns(), load_overrides(), sponsor_points(b.sample)
        self.by_sample = {pid: ref for ref, pid in b.sample_map.items()}

        sem = asyncio.Semaphore(8)
        placed = 0

        async def one(p: Project) -> None:
            nonlocal placed
            async with sem:
                await self.locate_project(ctx, p)
            if p.lat is not None:
                placed += 1
                ctx.emit("project.placed", project_id=p.id, lat=p.lat, lon=p.lon, confidence=p.location_confidence,
                         endpoints=[e.model_dump() for e in p.endpoints])
            else:
                ctx.emit("project.unlocated", project_id=p.id, endpoints=[e.model_dump() for e in p.endpoints],
                         reason=self.reason(p))
            ctx.progress(placed, len(b.projects), "placed")
            await ctx.pace(0.02)

        await asyncio.gather(*(one(p) for p in list(b.projects.values())))
        un = [p for p in b.projects.values() if p.lat is None]
        conf = lambda c: sum(1 for p in b.projects.values() if p.location_confidence == c)  # noqa: E731
        unlocated_check = Check(
            id="geo:unlocated", level="warn", rule="unlocated", title="Projects with no location yet",
            detail=f"{len(un)} projects have endpoints that no source could place. "
                   + "They stay off the map and out of the overlap math.",
            source="Geocoder output")
        b.checks.append(unlocated_check)  # the validator runs in parallel, so report it here
        ctx.emit("check.found", check=unlocated_check.model_dump())
        ctx.log(f"Geocoder: {conf('verified')} at surveyed points, {conf('confirmed_osm')} confirmed in OSM, "
                f"{conf('town') + conf('partial')} at town level, {len(un)} unlocated.")
        return f"{placed} placed, {len(un)} unlocated"

    @staticmethod
    def reason(p: Project) -> str:
        if not p.endpoints:
            return "no endpoint names found in the project title"
        return "no source matched: " + ", ".join(e.name for e in p.endpoints)

    async def locate_project(self, ctx: Ctx, p: Project) -> None:
        state = "SC" if p.utility == "DESC" else "GA"
        ref = self.by_sample.get(p.id)
        if ref and ctx.board.sample:
            sp = ctx.board.sample.projects[ref]  # use the sponsor's own coordinates for this row
            p.sponsor_ref_id = ref
            p.endpoints = [Endpoint(name=pt.name, lat=pt.lat, lon=pt.lon,
                                    method="sponsor_file" if pt.lat is not None else "none",
                                    confidence="verified" if pt.lat is not None else "unlocated",
                                    evidence={"ref_id": ref, "file": "Projects_Overlaps.xlsx"})
                           for pt in (sp.a, sp.b) if pt.name]
        else:
            names = split_endpoints(p.name)
            if looks_awkward(p.name, names) and gemini.enabled():
                names = await self.gemini_split(ctx, p, names)
            p.endpoints = [await self.locate_endpoint(ctx, p, n, state) for n in names]
        c = center([(e.lat, e.lon) for e in p.endpoints])
        p.lat, p.lon = (c if c else (None, None))
        located = {e.confidence for e in p.endpoints if e.lat is not None}
        if not located:
            p.location_confidence = "unlocated"
        elif located <= {"verified"}:
            p.location_confidence = "verified"
        elif located <= {"verified", "confirmed_osm"}:
            p.location_confidence = "confirmed_osm"
        elif located & {"verified", "confirmed_osm"}:
            p.location_confidence = "partial"
        else:
            p.location_confidence = "town"

    async def gemini_split(self, ctx: Ctx, p: Project, fallback: list[str]) -> list[str]:
        res = await gemini.generate_json(
            "Split a transmission project title into its named endpoints (substations or places). "
            "Return at most two names, without voltages, numbers or words like Rebuild/Line/Substation.",
            f"Title: {p.name}\nDescription: {p.description[:500]}", SPLIT_SCHEMA)
        # Gemini sometimes keeps voltages or fragments ('Union Pier 115', '13.'): same cleanup as the regex split.
        names = [c for n in (res["data"].get("endpoints") or []) if (c := clean_endpoint(n))][:2] if res else []
        if names:
            ctx.emit("endpoints.split", project_id=p.id, actor="gemini", endpoints=names, deterministic=fallback)
            return names
        return fallback

    async def locate_endpoint(self, ctx: Ctx, p: Project, name: str, state: str) -> Endpoint:
        key = norm_key(name)
        for k in dict.fromkeys([key, strip_generic(key)]):
            if not k:
                continue
            if (o := self.overrides.get(f"{k}|{state}")) is not None:
                return self._ep(name, o, "override", "verified", o.extra)
            if (s := self.points.get(k)) is not None:
                return self._ep(name, s, "sponsor_file", "verified", s.extra)
        seen: set[str] = set()
        for score, f, via in [c for k in dict.fromkeys([key, strip_generic(key)]) for c in self.osm.candidates(k)]:
            if f["osm_id"] in seen:
                continue
            seen.add(f["osm_id"])
            candidate = {k2: f[k2] for k2 in ("name", "operator", "voltage", "lat", "lon")}
            # Extra fields only for plants/switches and non-name matches, so plain substation judgments stay cached.
            if f.get("power", "substation") != "substation":
                candidate["power"] = f["power"]
            if via != "name":
                candidate["matched_on"] = {"alt_name": f.get("alt_names"), "ref": f.get("ref"), "name_part": f["name"],
                                           "operator+name": f"{f['operator']} {f['name']}"}.get(via)
            v = await ctx.ask_noul(
                "Is this OpenStreetMap substation the endpoint the filing names?", f"{name} → {f['name']}",
                {"endpoint": name, "utility": p.sponsor, "state": state, "project": p.name,
                 "description": p.description[:400], "zone": p.zone, "candidate": candidate},
                true="Same facility: the name matches and the operator, voltage and area are consistent with the project.",
                false="A different facility, e.g. a similarly named substation of another utility or in the wrong area.",
                heuristic=lambda s=score, via=via: 0.85 if s == 1.0 and via in ("name", "alt_name") else 0.4,
                project_id=p.id)
            if v.value >= ACCEPT:
                return self._ep(name, Place(f["lat"], f["lon"], f["name"], {}), "overpass", "confirmed_osm",
                                {"osm_id": f["osm_id"], "osm_name": f["name"], "operator": f["operator"],
                                 "osm_match": via, "judge": v.actor, "p_match": round(float(v.value), 3)})
        for k in dict.fromkeys([key, strip_generic(key)]):
            town = self.towns.find(k, state) if k else None
            if town is None:
                continue
            v = await ctx.ask_noul(
                "Is this town a fair approximate location for the endpoint?", f"{name} → {town.label}",
                {"endpoint": name, "project": p.name, "description": p.description[:400],
                 "candidate_town": town.label, "county": town.extra.get("county")},
                true="The endpoint is named after this town (e.g. 'Bluffton' substation near Bluffton, SC).",
                false="The name only shares a word with the town or clearly refers to something elsewhere "
                      "(e.g. 'Union Pier' is not Union, SC).",
                heuristic=lambda: 0.75 if k == key else 0.55, project_id=p.id)
            if v.value >= ACCEPT:
                return self._ep(name, town, "geonames_town", "town",
                                {"town": town.label, "county": town.extra.get("county"), "judge": v.actor,
                                 "p_match": round(float(v.value), 3)})
            ctx.emit("endpoint.rejected", project_id=p.id, endpoint=name, candidate=town.label, actor=v.actor)
        return Endpoint(name=name)

    @staticmethod
    def _ep(name: str, place: Place, method: str, confidence: str, evidence: dict) -> Endpoint:
        return Endpoint(name=name, lat=place.lat, lon=place.lon, method=method, confidence=confidence,  # type: ignore[arg-type]
                        evidence={"matched": place.label, **evidence})
