from datetime import date

from app import config
from app.clients import models
from app.core.analysis import (cost_block, fact_sheet, shared_resources, template_insight, unsupported_numbers)
from app.core.models import Overlap, Project, ReferenceResult
from app.core.overlap import Filters, distance_mi, find_overlaps, time_gap_days
from app.core.owners import book
from app.runtime.agent import Agent, AgentSpec, Ctx
from app.store import sources

TOP_ANALYSES = 6  # per two utilities
BLIND_TOLERANCE_MI = 1.0  # our own geocoding vs the benchmark distance


def written_pairs(overlaps: list[Overlap], projects: dict[str, Project], top: int) -> list[Overlap]:
    # The pairs that get written up: the top `top` of every two utilities, in rank order. Both sides must come from a
    # filing (built in, or added and reviewed in the Sources menu). Plans from the spreadsheet / link form get the
    # facts, the cost block and the report, not the written sides.
    owners = book()

    def filing(p: Project) -> bool:
        s = owners.of(p)
        return s is not None and s.display.get("origin") != sources.ORIGIN

    seen: dict[tuple[str, str], int] = {}
    out = []
    for o in overlaps:
        a, b = projects[o.project_a], projects[o.project_b]
        if not (filing(a) and filing(b)):
            continue
        key = (owners.group(a), owners.group(b))
        if seen.get(key, 0) < top:
            seen[key] = seen.get(key, 0) + 1
            out.append(o)
    return out


class OverlapEngine(Agent):
    spec = AgentSpec("overlap", "Overlaps", "Center-to-center miles and day gaps. Plain code, no AI",
                     ["code"], depends_on=["geocoder"], kind="tool", engine="Math")

    async def run(self, ctx: Ctx) -> str:
        b = ctx.board
        projects = list(b.projects.values())
        async with ctx.tool("find_overlaps", {"cutoff_mi": 25, "distance": "closest points, lines as straight segments",
                                              "tiers_mi": {"touching": 0.1, "row": 1, "site": 5, "crew": 25},
                                              "ga_owners": "GPC, SAV"}) as out:
            f = Filters(today=config.TODAY)
            b.overlaps = find_overlaps(projects, f, b.sample_pairs())
            owners = {p.utility for p in projects if p.lat is not None}
            out["summary"] = f"{len(owners)} owners' plans compared pair by pair, {len(b.overlaps)} pairs under 25 mi"
        ctx.log("Overlaps: comparing every project with every project of a different owner, center to center.")
        step = min(0.3, 20 / max(1, len(b.overlaps)))  # watchable, but never more than ~20 s in total
        for o in b.overlaps:
            ctx.emit("overlap.found", overlap=o.model_dump())
            await ctx.pace(step)
        ctx.log(f"Overlaps: {len(b.overlaps)} pairs under 25 miles.")
        return f"{len(b.overlaps)} pairs under 25 miles"


class ReferenceChecker(Agent):
    spec = AgentSpec("reference", "Scorer", "Scores our results against the 6 known overlaps",
                     ["code"], depends_on=["overlap", "sample"], kind="tool", engine="Math")

    async def run(self, ctx: Ctx) -> str:
        b = ctx.board
        if not b.sample:
            return "no sample loaded"
        for so in b.sample.overlaps:
            pa, pb = b.projects.get(b.sample_map.get(so.a, "")), b.projects.get(b.sample_map.get(so.b, ""))
            got_mi = got_days = None
            if pa and pb and pa.lat is not None and pb.lat is not None:
                got_mi = round(distance_mi((pa.lat, pa.lon), (pb.lat, pb.lon)), 2)  # type: ignore[arg-type]
                got_days = time_gap_days(date.fromisoformat(pa.in_service_date), date.fromisoformat(pb.in_service_date))
            # the same pair from our own geocoding, without the file's coordinates
            ba, bb = b.blind.get(pa.id if pa else ""), b.blind.get(pb.id if pb else "")
            blind_mi = (round(distance_mi((ba["lat"], ba["lon"]), (bb["lat"], bb["lon"])), 2)
                        if ba and bb and ba["lat"] is not None and bb["lat"] is not None else None)
            r = ReferenceResult(overlap_id=so.overlap_id, a=so.a, b=so.b, a_project=pa.id if pa else None,
                                b_project=pb.id if pb else None, expected_mi=so.distance_mi, got_mi=got_mi,
                                expected_days=so.time_gap_days, got_days=got_days,
                                passed=got_mi is not None and abs(got_mi - so.distance_mi) < 0.01 and got_days == so.time_gap_days,
                                blind_mi=blind_mi,
                                blind_passed=blind_mi is not None and abs(blind_mi - so.distance_mi) <= BLIND_TOLERANCE_MI)
            b.reference.append(r)
            ctx.emit("reference.result", result=r.model_dump())
            await ctx.pace(0.15)
        passed = sum(r.passed for r in b.reference)
        blind = sum(bool(r.blind_passed) for r in b.reference)
        ctx.log(f"Scorer: {passed} of {len(b.reference)} known overlaps reproduced exactly, "
                "using dates from our own extraction. With our own geocoding instead of the file's coordinates, "
                f"{blind} of {len(b.reference)} land within {BLIND_TOLERANCE_MI:g} mi.")
        return f"{passed}/{len(b.reference)} exact, {blind}/{len(b.reference)} blind"


class CostEstimator(Agent):
    spec = AgentSpec("cost", "Cost model", "Public Dominion costs; savings only with a cited source",
                     ["code"], depends_on=["overlap", "classifier"], kind="tool", engine="Math")

    async def run(self, ctx: Ctx) -> str:
        b = ctx.board
        for o in b.overlaps:
            a, g = b.projects[o.project_a], b.projects[o.project_b]
            b.costs[o.id] = {**cost_block(a, g, o), "shared": shared_resources(a, g, o)}
            ctx.emit("cost.ready", overlap_id=o.id, cost=b.costs[o.id])
        concurrent = sum(1 for o in b.overlaps if o.windows_overlap)
        return f"{len(b.overlaps)} cost blocks, {concurrent} with overlapping build windows"


SYSTEM = ("You write one short paragraph for a transmission planner about two nearby planned projects from different "
          "utilities. Use ONLY the facts given. Do not introduce any number that is not in the facts. 3 to 5 sentences, "
          "plain language, no headings, no bullet points.")


class Analyst(Agent):
    spec = AgentSpec("analyst", "Analyst", "Writes each top opportunity up from the filing text",
                     ["gemini"], depends_on=["cost", "validator", "reference"], engine="Gemini")

    async def run(self, ctx: Ctx) -> str:
        b = ctx.board
        written = 0
        for o in written_pairs(b.overlaps, b.projects, TOP_ANALYSES):
            a, g = b.projects[o.project_a], b.projects[o.project_b]
            shared = b.costs[o.id]["shared"]
            facts = fact_sheet(a, g, o, shared)
            text, actor, model = "", "gemini", None
            ctx.think(f"\n\n#{o.rank} {a.name} × {g.name}\n")
            stream = models.stream("analyst", f"Facts (JSON):\n{facts}", system=SYSTEM, pace=ctx.run.pace)
            try:
                async for chunk in stream:
                    text += chunk
                    ctx.think(chunk)
                actor, model = stream.provider or "gemini", stream.model
            except Exception as e:
                if not ctx.run.templates:
                    raise RuntimeError(f"No model could write #{o.rank} and template fallback is off: "
                                       f"{models.describe(e)}") from e
                ctx.think(f"[No model available: {models.describe(e)}. Using the template.]")
                ctx.log(f"Analyst: no model could write #{o.rank} ({models.describe(e)}), wrote it from the template.")
                text, actor = template_insight(a, g, o, shared), "template"
            bad = unsupported_numbers(text, facts) if actor != "template" else set()
            b.analyses[o.id] = {"text": text.strip(), "actor": actor, "model": model, "unsupported_numbers": sorted(bad)}
            ctx.emit("analysis.ready", overlap_id=o.id, **b.analyses[o.id])
            written += 1
            await ctx.pace(0.3)
        return f"{written} write-ups"
