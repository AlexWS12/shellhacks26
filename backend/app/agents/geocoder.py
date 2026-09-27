# Source order: override > benchmark points > OSM substation > GeoNames town > Nominatim > unlocated.
# When no title end is found, the places the description names stand in (role 'context', town-level).
# Fuzzy matches get confirmed by the judge. Code rejects a match that puts one project's two ends
# farther apart than span_limit, or an out-of-state end more than TIE_CROSS_MI from the other one,
# then looks again near the end it trusts more.

import asyncio
import re

from app import config
from app.clients import models, nominatim, overpass
from app.core.endpoints import clean_endpoint, description_names, looks_awkward, split_endpoints
from app.core.models import Check, Endpoint, Project
from app.core.normalize import norm_key
from app.core.overlap import MAX_SPAN_MI, TIE_CROSS_MI, center, distance_mi, span_limit
from app.core.owners import Book
from app.core.owners import book as owners_book
from app.core.places import OsmIndex, Place, States, Towns, load_overrides, sponsor_points, variants
from app.core.sample import match_projects
from app.runtime.agent import Agent, AgentSpec, Ctx

ACCEPT = 0.5
ACCEPT_OUT_OF_STATE = 0.65  # a candidate outside the filing's state needs a clearer yes
# The only substation in the filing's state with exactly the endpoint's name is accepted on a weaker yes.
# In the 2026-09-26 run every such candidate Jev put between 0.3 and 0.5 was the right place (Harleyville,
# O'Hara, Faber Place, Dresden, Eatonton); the wrong ones were other states or other names, far lower.
EXACT_ACCEPT = 0.3
# A title that names the other utility ('Lower River - Webb (APC)') expects one end in its territory.
NEIGHBOR_RE = re.compile(r"\((APC|FPL)\)|\b(DEP)\b", re.I)
NEIGHBORS = {"APC": "Alabama Power", "FPL": "Florida Power & Light", "DEP": "Duke Energy Progress"}
CONTEXT_SPREAD_MI = 25.0  # description places farther than this from the first one found are left out
STRENGTH = {"override": 0, "sponsor_file": 0, "overpass": 1, "nominatim": 2, "geonames_town": 2}
SPLIT_SCHEMA = {"type": "object", "properties": {"endpoints": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
                                                 "note": {"type": "string"}}, "required": ["endpoints"]}


class Geocoder(Agent):
    spec = AgentSpec("geocoder", "Geocoder", "Finds coordinates: surveyed points, OpenStreetMap, then towns",
                     ["code", "osm", "jev", "gemini"], depends_on=["sample", "extract_desc", "extract_ga"], engine="OSM + Jev")

    def __init__(self, only: set[str] | None = None, tag: str = "") -> None:
        self.only = only  # geocode only these projects (a source being activated); None = every project
        self.tag = tag  # that source's id, so its checks don't replace the full run's

    async def run(self, ctx: Ctx) -> str:
        b = ctx.board
        todo = [p for p in b.projects.values() if self.only is None or p.id in self.only]
        self._book = owners_book()  # sources: states, OSM operator names, the search area
        async with ctx.tool("match_sample", {"sample_projects": len(b.sample.projects) if b.sample else 0}) as out:
            built_in = [p for p in b.projects.values() if self.book.builtin(p)]  # the benchmark covers the built-ins
            b.sample_map = match_projects(b.sample, built_in) if b.sample else {}
            out["summary"] = f"matched {len(b.sample_map)} of {len(b.sample.projects) if b.sample else 0} benchmark projects"
        ctx.emit("sample.matched", mapping=b.sample_map)

        features = overpass.load()
        source = "cache" if features is not None else "live"
        bbox = self.book.bbox()
        async with ctx.tool("overpass_substations", {"bbox": bbox, "source": source}, actor="osm") as out:
            if features is None and config.OSM_LIVE:
                try:
                    features = await asyncio.wait_for(asyncio.to_thread(overpass.fetch, bbox), timeout=150)
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
            ctx.progress(placed, len(todo), "placed")
            await ctx.pace(0.02)

        await asyncio.gather(*(one(p) for p in todo))

        # Blind pass: the benchmark projects again, as if the file had no coordinates, so the
        # scorer can test our geocoding and not only the distance math.
        async def blind(pid: str) -> None:
            p = b.projects[pid].model_copy(deep=True)
            p.endpoints = []  # start from the title, not the file's endpoint names
            async with sem:
                await self.locate_project(ctx, p, blind=True)
            b.blind[pid] = {"lat": p.lat, "lon": p.lon, "confidence": p.location_confidence,
                            "endpoints": [e.model_dump() for e in p.endpoints]}

        async with ctx.tool("blind_benchmark", {"projects": len(b.sample_map) if self.only is None else 0}) as out:
            if self.only is None:  # placing one new source: the benchmark projects aren't being placed again
                await asyncio.gather(*(blind(pid) for pid in b.sample_map.values()))
            out["summary"] = (f"{sum(1 for v in b.blind.values() if v['lat'] is not None)} of {len(b.blind)} "
                              "benchmark projects placed without the file's coordinates")
        un = [p for p in todo if p.lat is None]
        conf = lambda c: sum(1 for p in todo if p.location_confidence == c)  # noqa: E731
        why = {"no endpoint names in the title": 0, "a candidate was found but rejected": 0, "no source had the name": 0}
        for p in un:
            if not p.endpoints:
                why["no endpoint names in the title"] += 1
            elif any("rejected" in t for e in p.endpoints for t in e.evidence.get("tried", [])):
                why["a candidate was found but rejected"] += 1
            else:
                why["no source had the name"] += 1
        unlocated_check = Check(
            id=f"geo:unlocated{':' + self.tag if self.tag else ''}", level="warn", rule="unlocated",
            title="Projects with no location yet",
            detail=f"{len(un)} projects have endpoints that no source could place: "
                   + ", ".join(f"{n} {k}" for k, n in why.items() if n)
                   + ". They stay off the map and out of the overlap math. Each one lists what was tried.",
            source="Geocoder output")
        b.checks.append(unlocated_check)  # the validator runs in parallel, so report it here
        ctx.emit("check.found", check=unlocated_check.model_dump())
        ctx.log(f"Geocoder: {conf('verified')} at surveyed points, {conf('confirmed_osm')} confirmed in OSM, "
                f"{conf('partial')} placed from one end or mixed sources, {conf('town')} at town level, "
                f"{len(un)} unlocated. "
                "Method: OpenStreetMap via Overpass, each match checked against the filing.")
        return f"{placed} placed, {len(un)} unlocated"

    @property
    def book(self) -> Book:
        if not hasattr(self, "_book"):
            self._book = owners_book()
        return self._book

    @staticmethod
    def reason(p: Project) -> str:
        if not p.endpoints:
            return "no endpoint names found in the project title"
        return " | ".join(f"{e.name}: {e.evidence.get('reason', 'not tried')}" for e in p.endpoints if e.lat is None)

    async def locate_project(self, ctx: Ctx, p: Project, blind: bool = False) -> None:
        # blind: ignore the benchmark file's coordinates, as if this project weren't in it.
        state = self.book.state_of(p)
        ref = None if blind else self.by_sample.get(p.id)
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
        elif any(e.method == "submitted" for e in p.endpoints):
            pass  # coordinates from the submitted file are used as given
        else:
            if p.endpoints:  # endpoint names from a submitted plan's columns
                names = [e.name for e in p.endpoints]
            else:
                names = split_endpoints(p.name)
                if looks_awkward(p.name, names) and models.available("endpoint_split"):
                    names = await self.gemini_split(ctx, p, names)
            eps = [await self.locate_endpoint(ctx, p, n, state, blind=blind) for n in names]
            p.endpoints = await self.check_span(ctx, p, eps, state, blind)
            if not any(e.lat is not None for e in p.endpoints):
                p.endpoints += await self.description_places(ctx, p, state, blind)
        c = center([(e.lat, e.lon) for e in p.endpoints])
        p.lat, p.lon = (c if c else (None, None))
        located = [e for e in p.endpoints if e.lat is not None]
        kinds = {e.confidence for e in located}
        if not located:
            p.location_confidence = "unlocated"
        elif all(e.role == "context" for e in located):
            p.location_confidence = "town"  # near the places the description names, not on the work itself
        elif kinds <= {"verified"}:
            p.location_confidence = "verified"
        elif kinds <= {"verified", "confirmed_osm"}:
            p.location_confidence = "confirmed_osm"
        elif kinds & {"verified", "confirmed_osm"}:
            p.location_confidence = "partial"
        else:
            p.location_confidence = "town"
        if p.location_confidence in ("verified", "confirmed_osm") and any(e.lat is None for e in p.endpoints):
            # One end is unknown, so the center is only as good as a guess at where the line runs.
            p.location_confidence = "partial"

    async def description_places(self, ctx: Ctx, p: Project, state: str, blind: bool = False) -> list[Endpoint]:
        # No title end was found: place the project near the existing substations or lines its
        # description names ('loop it into the Cartersville - Pinson 230kV line'). Only places
        # close together count; the first one found anchors the rest.
        failed = {norm_key(e.name) for e in p.endpoints}
        found: list[Endpoint] = []
        for name in description_names(p.description):
            if norm_key(name) in failed:
                continue
            e = await self.locate_endpoint(ctx, p, name, state, blind=blind)
            if e.lat is None or (found and distance_mi((found[0].lat, found[0].lon), (e.lat, e.lon)) > CONTEXT_SPREAD_MI):  # type: ignore[arg-type]
                continue
            e.role = "context"
            e.evidence["from"] = "description"
            found.append(e)
        if found:
            ctx.log(f"Geocoder: {p.name}: title ends not found, placed near "
                    f"{', '.join(e.name for e in found)} from the description.")
        return found

    async def gemini_split(self, ctx: Ctx, p: Project, fallback: list[str]) -> list[str]:
        try:
            res = await models.call(
                "endpoint_split", f"Title: {p.name}\nDescription: {p.description[:500]}", SPLIT_SCHEMA,
                system="Split a transmission project title into its named endpoints (substations or places). "
                       "Return at most two names, without voltages, numbers or words like Rebuild/Line/Substation.")
        except models.RoleExhausted:
            return fallback
        ctx.tokens += res.tokens
        # The model sometimes keeps voltages or fragments ('Union Pier 115', '13.'): same cleanup as the regex split.
        names = [c for n in (res.value.get("endpoints") or []) if (c := clean_endpoint(n))][:2]
        if names:
            ctx.emit("endpoints.split", project_id=p.id, actor=res.provider, model=res.model, endpoints=names,
                     deterministic=fallback)
            return names
        return fallback

    def _outside(self, e: Endpoint, state: str) -> bool:
        # A searched match (not a surveyed point) that landed outside the filing's state.
        return STRENGTH.get(e.method, 3) > 0 and self.states.state_of(e.lat, e.lon) != state  # type: ignore[arg-type]

    def _bad_span(self, a: Endpoint, b: Endpoint, state: str,
                  limit: float = MAX_SPAN_MI) -> tuple[Endpoint, Endpoint, str, bool] | None:
        # (weak end, kept end, why, tied) when the pair can't be one project, else None.
        # tied: nothing says which end is wrong, so the caller should try dropping either.
        d = distance_mi((a.lat, a.lon), (b.lat, b.lon))  # type: ignore[arg-type]
        outside = [e for e in (a, b) if self._outside(e, state)] if d > TIE_CROSS_MI else []
        tied = False
        if len(outside) == 1:
            weak = outside[0]
            why = f"outside {state} and {d:.0f} mi from the other end (limit {TIE_CROSS_MI:.0f} for a border tie)"
        elif d > limit:
            # drop the weaker match; between equals, the one outside the filing's state
            rank = lambda e: (STRENGTH.get(e.method, 3), self.states.state_of(e.lat, e.lon) != state)  # noqa: E731
            weak = a if rank(a) > rank(b) else b
            tied = rank(a) == rank(b)
            why = f"{d:.0f} mi from the other end (limit {limit:.0f})"
        else:
            return None
        if STRENGTH.get(weak.method, 3) == 0:
            return None  # both from verified sources: leave it for a human
        return weak, (b if weak is a else a), why, tied

    async def check_span(self, ctx: Ctx, p: Project, eps: list[Endpoint], state: str,
                         blind: bool = False) -> list[Endpoint]:
        located = [e for e in eps if e.lat is not None]
        if len(located) != 2:
            return eps
        bad = self._bad_span(*located, state, span_limit(p.miles))
        if bad is None:
            return eps
        weak, keep, why, tied = bad
        # Two equally strong ends ('Goshen' near Augusta vs McIntosh near Savannah): try dropping
        # each one and keep whichever look-again gives a plausible pair.
        tries = [(weak, keep), (keep, weak)] if tied else [(weak, keep)]
        first: list[Endpoint] | None = None
        for weak, keep in tries:
            ctx.emit("endpoint.rejected", project_id=p.id, endpoint=weak.name,
                     candidate=weak.evidence.get("matched", ""), actor="code")
            ctx.log(f"Geocoder: {weak.name} -> {weak.evidence.get('matched')} is {why}, "
                    f"too far for one project. Looking again near {keep.name}.")
            note = f"{weak.evidence.get('matched')} rejected by code: {why}"
            again = await self.locate_endpoint(ctx, p, weak.name, state, near=(keep.lat, keep.lon), prior=[note],  # type: ignore[arg-type]
                                               blind=blind)
            if again.lat is not None and (bad := self._bad_span(again, keep, state, span_limit(p.miles))) is not None and bad[0] is again:
                # the second look found something just as implausible: leave the end unlocated
                tried = [note, f"{again.evidence.get('matched')} rejected by code: {bad[2]}"]
                again = Endpoint(name=weak.name, evidence={"tried": tried, "reason": "; ".join(tried)})
            result = [again if e is weak else e for e in eps]
            if again.lat is not None:
                return result
            first = first or result
        return first  # type: ignore[return-value]

    @staticmethod
    def _far(p: Project, lat: float, lon: float, near: tuple[float, float] | None) -> bool:
        return near is not None and distance_mi((lat, lon), near) > span_limit(p.miles)

    async def locate_endpoint(self, ctx: Ctx, p: Project, name: str, state: str,
                              near: tuple[float, float] | None = None, prior: list[str] | None = None,
                              blind: bool = False) -> Endpoint:
        tried: list[str] = list(prior or [])
        key = norm_key(name)
        keys, exact = variants(key), variants(key, strip=False)
        for k in keys:
            if (o := self.overrides.get(f"{k}|{state}")) is not None:
                return self._ep(name, o, "override", "verified", o.extra)
            if not blind and (s := self.points.get(k)) is not None and not self._far(p, s.lat, s.lon, near):
                return self._ep(name, s, "sponsor_file", "verified", s.extra)

        cands = [c for c in self.osm.candidates(key, state, operators=self.book.operators(p, state))
                 if not self._far(p, c[1]["lat"], c[1]["lon"], near)]
        if not cands:
            tried.append("OpenStreetMap: no substation by that name" + (" nearby" if near else ""))
        cue = NEIGHBOR_RE.search(p.name)
        neighbor = NEIGHBORS[(cue[1] or cue[2]).upper()] if cue else None
        # in the filing's state, or run by the neighbor utility the title names
        home = lambda f: f.get("state") == state or bool(neighbor and neighbor.lower() in f["operator"].lower())  # noqa: E731
        exact_home = [f for s, f, via in cands if s == 1.0 and via in ("name", "alt_name") and home(f)]
        for score, f, via in cands:
            same = home(f)
            candidate = {**{k2: f[k2] for k2 in ("name", "operator", "voltage", "lat", "lon")},
                         "state": f.get("state") or "outside SC/GA"}
            if f.get("power", "substation") != "substation":
                candidate["power"] = f["power"]
            if via != "name":
                candidate["matched_on"] = {"alt_name": f.get("alt_names"), "ref": f.get("ref"), "name_part": f["name"],
                                           "operator+name": f"{f['operator']} {f['name']}"}.get(via)
            exact_name = score == 1.0 and via in ("name", "alt_name")
            v = await ctx.ask_noul(
                "Is this OpenStreetMap substation the endpoint the filing names?", f"{name} → {f['name']}",
                {"endpoint": name, "utility": p.sponsor, "filing_state": state, "project": p.name,
                 "description": p.description[:400], "zone": p.zone, "candidate": candidate}
                | ({"neighbor_utility": neighbor} if neighbor else {}),
                true="Same facility: the name matches and the operator, voltage and state are consistent with the project.",
                false="A different facility, e.g. a similarly named substation of another utility or in another state "
                      "or region than the project describes.",
                heuristic=lambda e=exact_name, m=same: (0.85 if m else 0.45) if e else (0.4 if m else 0.25),
                project_id=p.id, role="confirm_osm")
            by_rule = exact_name and same and len(exact_home) == 1 and v.value >= EXACT_ACCEPT
            if v.value >= (ACCEPT if same else ACCEPT_OUT_OF_STATE) or by_rule:
                return self._ep(name, Place(f["lat"], f["lon"], f["name"], {}), "overpass", "confirmed_osm",
                                {"osm_id": f["osm_id"], "osm_name": f["name"], "operator": f["operator"],
                                 "osm_match": via, "candidate_state": f.get("state"), "judge": v.actor, "judge_model": v.model,
                                 "p_match": round(float(v.value), 3)}
                                | ({"accepted_by": "exact_name_rule"} if v.value < ACCEPT else {})
                                | ({"neighbor_utility": neighbor} if neighbor and f.get("state") != state else {}))
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
                heuristic=lambda k=k: 0.75 if k in exact else 0.55, project_id=p.id, role="confirm_town")
            if v.value >= ACCEPT:
                return self._ep(name, town, "geonames_town", "town",
                                {"town": town.label, "county": town.extra.get("county"), "judge": v.actor,
                                 "p_match": round(float(v.value), 3)})
            ctx.emit("endpoint.rejected", project_id=p.id, endpoint=name, candidate=town.label, actor=v.actor,
                     model=v.model)
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
                heuristic=lambda m=same, s=sub: (0.8 if s else 0.7) if m else 0.45, project_id=p.id,
                role="confirm_place")
            if v.value >= (ACCEPT if same else ACCEPT_OUT_OF_STATE):
                return self._ep(name, Place(r["lat"], r["lon"], r["display_name"], {}), "nominatim",
                                "confirmed_osm" if sub else "town",
                                {"osm_id": r["osm_id"], "kind": r["kind"], "county": r["county"],
                                 "candidate_state": r["state"], "judge": v.actor, "p_match": round(float(v.value), 3)})
            ctx.emit("endpoint.rejected", project_id=p.id, endpoint=name, candidate=r["display_name"][:80], actor=v.actor,
                     model=v.model)
            tried.append(f"Nominatim {r['name']} ({r['state'] or 'outside SC/GA'}): rejected by {v.actor}")
        return None

    @staticmethod
    def _ep(name: str, place: Place, method: str, confidence: str, evidence: dict) -> Endpoint:
        return Endpoint(name=name, lat=place.lat, lon=place.lon, method=method, confidence=confidence,  # type: ignore[arg-type]
                        evidence={"matched": place.label, **evidence})
