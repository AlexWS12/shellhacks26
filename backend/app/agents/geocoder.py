# Source order: override > benchmark points > OSM substation > GeoNames town > Nominatim > unlocated.
# Fuzzy matches get confirmed by the judge. Code rejects a match that puts one project's two ends
# farther apart than span_limit, or an out-of-state end more than TIE_CROSS_MI from the other one,
# then looks again near the end it trusts more.

import asyncio

from app import config
from app.clients import gemini, nominatim, overpass
from app.core.endpoints import clean_endpoint, looks_awkward, split_endpoints
from app.core.models import Check, Endpoint, Project
from app.core.normalize import norm_key
from app.core.overlap import MAX_SPAN_MI, TIE_CROSS_MI, center, distance_mi, span_limit
from app.core.places import OsmIndex, Place, States, Towns, load_overrides, sponsor_points, variants
from app.core.sample import match_projects
from app.runtime.agent import Agent, AgentSpec, Ctx

ACCEPT = 0.5
ACCEPT_OUT_OF_STATE = 0.65  # a candidate outside the filing's state needs a clearer yes
STRENGTH = {"override": 0, "sponsor_file": 0, "overpass": 1, "nominatim": 2, "geonames_town": 2}
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
        nominatim.start_budget(config.NOMINATIM_BUDGET_S)
        self.states = States()
        self.osm = OsmIndex(features or [], self.states)
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
        why = {"no endpoint names in the title": 0, "a candidate was found but rejected": 0, "no source had the name": 0}
        for p in un:
            if not p.endpoints:
                why["no endpoint names in the title"] += 1
            elif any("rejected" in t for e in p.endpoints for t in e.evidence.get("tried", [])):
                why["a candidate was found but rejected"] += 1
            else:
                why["no source had the name"] += 1
        unlocated_check = Check(
            id="geo:unlocated", level="warn", rule="unlocated", title="Projects with no location yet",
            detail=f"{len(un)} projects have endpoints that no source could place: "
                   + ", ".join(f"{n} {k}" for k, n in why.items() if n)
                   + ". They stay off the map and out of the overlap math. Each one lists what was tried.",
            source="Geocoder output")
        b.checks.append(unlocated_check)  # the validator runs in parallel, so report it here
        ctx.emit("check.found", check=unlocated_check.model_dump())
        ctx.log(f"Geocoder: {conf('verified')} at surveyed points, {conf('confirmed_osm')} confirmed in OSM, "
                f"{conf('town') + conf('partial')} at town level, {len(un)} unlocated. "
                "Method: OpenStreetMap via Overpass, each match checked against the filing.")
        return f"{placed} placed, {len(un)} unlocated"

    @staticmethod
    def reason(p: Project) -> str:
        if not p.endpoints:
            return "no endpoint names found in the project title"
        return " | ".join(f"{e.name}: {e.evidence.get('reason', 'not tried')}" for e in p.endpoints if e.lat is None)

    async def locate_project(self, ctx: Ctx, p: Project) -> None:
        state = "SC" if p.utility == "DESC" else "GA"
        ref = self.by_sample.get(p.id)
        if ref and ctx.board.sample:
            sp = ctx.board.sample.projects[ref]  # use the sponsor's own coordinates for this row
            p.sponsor_ref_id = ref
            p.endpoints = [Endpoint(name=pt.name, lat=pt.lat, lon=pt.lon,
                                    method="sponsor_file" if pt.lat is not None else "none",
                                    confidence="verified" if pt.lat is not None else "unlocated",
                                    evidence={"ref_id": ref, "file": "Projects_Overlaps.xlsx"} | ({} if pt.lat is not None else {
                                        "tried": ["benchmark file: no coordinates for this endpoint"],
                                        "reason": "no coordinates in the benchmark file; left unlocated so the center "
                                                  "matches the benchmark's own"}))
                           for pt in (sp.a, sp.b) if pt.name]
        else:
            names = split_endpoints(p.name)
            if looks_awkward(p.name, names) and gemini.enabled():
                names = await self.gemini_split(ctx, p, names)
            eps = [await self.locate_endpoint(ctx, p, n, state) for n in names]
            p.endpoints = await self.check_span(ctx, p, eps, state)
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

    def _outside(self, e: Endpoint, state: str) -> bool:
        # A searched match (not a surveyed point) that landed outside the filing's state.
        return STRENGTH.get(e.method, 3) > 0 and self.states.state_of(e.lat, e.lon) != state  # type: ignore[arg-type]

    def _bad_span(self, a: Endpoint, b: Endpoint, state: str,
                  limit: float = MAX_SPAN_MI) -> tuple[Endpoint, Endpoint, str] | None:
        # (weak end, kept end, why) when the pair can't be one project, else None.
        d = distance_mi((a.lat, a.lon), (b.lat, b.lon))  # type: ignore[arg-type]
        outside = [e for e in (a, b) if self._outside(e, state)] if d > TIE_CROSS_MI else []
        if len(outside) == 1:
            weak = outside[0]
            why = f"outside {state} and {d:.0f} mi from the other end (limit {TIE_CROSS_MI:.0f} for a border tie)"
        elif d > limit:
            # drop the weaker match; between equals, the one outside the filing's state
            rank = lambda e: (STRENGTH.get(e.method, 3), self.states.state_of(e.lat, e.lon) != state)  # noqa: E731
            weak = a if rank(a) > rank(b) else b
            why = f"{d:.0f} mi from the other end (limit {limit:.0f})"
        else:
            return None
        if STRENGTH.get(weak.method, 3) == 0:
            return None  # both from verified sources: leave it for a human
        return weak, (b if weak is a else a), why

    async def check_span(self, ctx: Ctx, p: Project, eps: list[Endpoint], state: str) -> list[Endpoint]:
        located = [e for e in eps if e.lat is not None]
        if len(located) != 2:
            return eps
        bad = self._bad_span(*located, state, span_limit(p.miles))
        if bad is None:
            return eps
        weak, keep, why = bad
        ctx.emit("endpoint.rejected", project_id=p.id, endpoint=weak.name, candidate=weak.evidence.get("matched", ""),
                 actor="code")
        ctx.log(f"Geocoder: {weak.name} -> {weak.evidence.get('matched')} is {why}, "
                f"too far for one project. Looking again near {keep.name}.")
        note = f"{weak.evidence.get('matched')} rejected by code: {why}"
        again = await self.locate_endpoint(ctx, p, weak.name, state, near=(keep.lat, keep.lon), prior=[note])  # type: ignore[arg-type]
        if again.lat is not None and (bad := self._bad_span(again, keep, state, span_limit(p.miles))) is not None and bad[0] is again:
            # the second look found something just as implausible: leave the end unlocated
            tried = [note, f"{again.evidence.get('matched')} rejected by code: {bad[2]}"]
            again = Endpoint(name=weak.name, evidence={"tried": tried, "reason": "; ".join(tried)})
        return [again if e is weak else e for e in eps]

    @staticmethod
    def _far(p: Project, lat: float, lon: float, near: tuple[float, float] | None) -> bool:
        return near is not None and distance_mi((lat, lon), near) > span_limit(p.miles)

    async def locate_endpoint(self, ctx: Ctx, p: Project, name: str, state: str,
                              near: tuple[float, float] | None = None, prior: list[str] | None = None) -> Endpoint:
        tried: list[str] = list(prior or [])
        key = norm_key(name)
        keys, exact = variants(key), variants(key, strip=False)
        for k in keys:
            if (o := self.overrides.get(f"{k}|{state}")) is not None:
                return self._ep(name, o, "override", "verified", o.extra)
            if (s := self.points.get(k)) is not None and not self._far(p, s.lat, s.lon, near):
                return self._ep(name, s, "sponsor_file", "verified", s.extra)

        cands = [c for c in self.osm.candidates(key, state) if not self._far(p, c[1]["lat"], c[1]["lon"], near)]
        if not cands:
            tried.append("OpenStreetMap: no substation by that name" + (" nearby" if near else ""))
        for score, f, via in cands:
            same = f.get("state") == state
            candidate = {**{k2: f[k2] for k2 in ("name", "operator", "voltage", "lat", "lon")},
                         "state": f.get("state") or "outside SC/GA"}
            if f.get("power", "substation") != "substation":
                candidate["power"] = f["power"]
            if via != "name":
                candidate["matched_on"] = {"alt_name": f.get("alt_names"), "ref": f.get("ref"), "name_part": f["name"],
                                           "operator+name": f"{f['operator']} {f['name']}"}.get(via)
            exact = score == 1.0 and via in ("name", "alt_name")
            v = await ctx.ask_noul(
                "Is this OpenStreetMap substation the endpoint the filing names?", f"{name} → {f['name']}",
                {"endpoint": name, "utility": p.sponsor, "filing_state": state, "project": p.name,
                 "description": p.description[:400], "zone": p.zone, "candidate": candidate},
                true="Same facility: the name matches and the operator, voltage and state are consistent with the project.",
                false="A different facility, e.g. a similarly named substation of another utility or in another state "
                      "or region than the project describes.",
                heuristic=lambda e=exact, m=same: (0.85 if m else 0.45) if e else (0.4 if m else 0.25),
                project_id=p.id)
            if v.value >= (ACCEPT if same else ACCEPT_OUT_OF_STATE):
                return self._ep(name, Place(f["lat"], f["lon"], f["name"], {}), "overpass", "confirmed_osm",
                                {"osm_id": f["osm_id"], "osm_name": f["name"], "operator": f["operator"],
                                 "osm_match": via, "candidate_state": f.get("state"), "judge": v.actor,
                                 "p_match": round(float(v.value), 3)})
            tried.append(f"OpenStreetMap {f['name']} ({f.get('state') or 'outside SC/GA'}): rejected by {v.actor}")

        towns = [(k, t) for k in keys if k and (t := self.towns.find(k, state)) and not self._far(p, t.lat, t.lon, near)]
        if not towns:
            tried.append(f"GeoNames: no town by that name in {state}")
        for k, town in towns[:1]:
            v = await ctx.ask_noul(
                "Is this town a fair approximate location for the endpoint?", f"{name} → {town.label}",
                {"endpoint": name, "project": p.name, "description": p.description[:400],
                 "candidate_town": town.label, "county": town.extra.get("county")},
                true="The endpoint is named after this town (e.g. 'Bluffton' substation near Bluffton, SC).",
                false="The name only shares a word with the town or clearly refers to something elsewhere "
                      "(e.g. 'Union Pier' is not Union, SC).",
                heuristic=lambda k=k: 0.75 if k in exact else 0.55, project_id=p.id)
            if v.value >= ACCEPT:
                return self._ep(name, town, "geonames_town", "town",
                                {"town": town.label, "county": town.extra.get("county"), "judge": v.actor,
                                 "p_match": round(float(v.value), 3)})
            ctx.emit("endpoint.rejected", project_id=p.id, endpoint=name, candidate=town.label, actor=v.actor)
            tried.append(f"GeoNames {town.label}: rejected by {v.actor}")

        found = await self.nominatim(ctx, p, name, key, keys, state, near, tried)
        if found is not None:
            return found
        return Endpoint(name=name, evidence={"tried": tried, "reason": "; ".join(tried)})

    async def nominatim(self, ctx: Ctx, p: Project, name: str, key: str, keys: list[str], state: str,
                        near: tuple[float, float] | None, tried: list[str]) -> Endpoint | None:
        async with ctx.tool("nominatim_search", {"q": key}, actor="osm") as out:
            results, source = await nominatim.search(key)
            out["summary"] = f"{len(results)} places or substations ({source})"
        if source in ("off", "error", "budget"):
            tried.append("Nominatim: " + {"off": "not queried (offline)", "error": "request failed",
                                          "budget": "skipped, this run's lookup time was used up"}[source])
            return None
        for r in results:
            r["state"] = self.states.state_of(r["lat"], r["lon"])
        named = [r for r in results if norm_key(r["name"]) in keys and not self._far(p, r["lat"], r["lon"], near)]
        named.sort(key=lambda r: (r["state"] != state, r["kind"] != "power=substation"))
        if not named:
            tried.append("Nominatim: no place or substation by that name" + (" nearby" if near else ""))
        for r in named[:2]:
            same = r["state"] == state
            sub = r["kind"] == "power=substation"
            v = await ctx.ask_noul(
                "Is this OpenStreetMap place a fair approximate location for the endpoint?",
                f"{name} → {r['display_name'][:60]}",
                {"endpoint": name, "filing_state": state, "project": p.name, "description": p.description[:400],
                 "zone": p.zone, "candidate": {"name": r["name"], "kind": r["kind"], "county": r["county"],
                                               "state": r["state"] or "outside SC/GA", "lat": r["lat"], "lon": r["lon"]}},
                true="The endpoint is this substation, or is named after this community and the project area fits.",
                false="The name only coincides; the project describes a different area or state.",
                heuristic=lambda m=same, s=sub: (0.8 if s else 0.7) if m else 0.45, project_id=p.id)
            if v.value >= (ACCEPT if same else ACCEPT_OUT_OF_STATE):
                return self._ep(name, Place(r["lat"], r["lon"], r["display_name"], {}), "nominatim",
                                "confirmed_osm" if sub else "town",
                                {"osm_id": r["osm_id"], "kind": r["kind"], "county": r["county"],
                                 "candidate_state": r["state"], "judge": v.actor, "p_match": round(float(v.value), 3)})
            ctx.emit("endpoint.rejected", project_id=p.id, endpoint=name, candidate=r["display_name"][:80], actor=v.actor)
            tried.append(f"Nominatim {r['name']} ({r['state'] or 'outside SC/GA'}): rejected by {v.actor}")
        return None

    @staticmethod
    def _ep(name: str, place: Place, method: str, confidence: str, evidence: dict) -> Endpoint:
        return Endpoint(name=name, lat=place.lat, lon=place.lon, method=method, confidence=confidence,  # type: ignore[arg-type]
                        evidence={"matched": place.label, **evidence})
