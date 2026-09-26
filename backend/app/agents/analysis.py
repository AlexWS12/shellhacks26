from datetime import date

from app import config
from app.clients import gemini
from app.core.analysis import (cost_block, fact_sheet, shared_resources, template_insight, unsupported_numbers)
from app.core.models import ReferenceResult
from app.core.overlap import Filters, distance_mi, find_overlaps, time_gap_days
from app.runtime.agent import Agent, AgentSpec, Ctx

TOP_ANALYSES = 6


class OverlapEngine(Agent):
    spec = AgentSpec("overlap", "Overlaps", "Center-to-center miles and day gaps. Plain code, no AI",
                     ["code"], depends_on=["geocoder"], kind="tool", engine="Math")

    async def run(self, ctx: Ctx) -> str:
        b = ctx.board
        projects = list(b.projects.values())
        async with ctx.tool("find_overlaps", {"cutoff_mi": 25, "center": "midpoint of located endpoints",
                                              "distance": "haversine, R=3958.8 mi", "ga_owners": "GPC, SAV"}) as out:
            f = Filters(today=config.TODAY)
            b.overlaps = find_overlaps(projects, f, b.sample_pairs())
            n_desc = sum(1 for p in projects if p.utility == "DESC" and p.lat is not None)
            n_ga = sum(1 for p in projects if p.utility == "GA" and p.lat is not None and p.sponsor in ("GPC", "SAV"))
            out["summary"] = f"{n_desc} x {n_ga} pairs compared, {len(b.overlaps)} under 25 mi"
        ctx.log("Overlaps: comparing every Dominion project with every Georgia project, center to center.")
        for o in b.overlaps:
            ctx.emit("overlap.found", overlap=o.model_dump())
            await ctx.pace(0.3)  # slow enough to watch each connection form
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
            r = ReferenceResult(overlap_id=so.overlap_id, a=so.a, b=so.b, a_project=pa.id if pa else None,
                                b_project=pb.id if pb else None, expected_mi=so.distance_mi, got_mi=got_mi,
                                expected_days=so.time_gap_days, got_days=got_days,
                                passed=got_mi is not None and abs(got_mi - so.distance_mi) < 0.01 and got_days == so.time_gap_days)
            b.reference.append(r)
            ctx.emit("reference.result", result=r.model_dump())
            await ctx.pace(0.15)
        passed = sum(r.passed for r in b.reference)
        ctx.log(f"Scorer: {passed} of {len(b.reference)} known overlaps reproduced exactly, "
                "using dates from our own extraction.")
        return f"{passed}/{len(b.reference)} exact"


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
        for o in b.overlaps[:TOP_ANALYSES]:
            a, g = b.projects[o.project_a], b.projects[o.project_b]
            shared = b.costs[o.id]["shared"]
            facts = fact_sheet(a, g, o, shared)
            text, actor = "", "gemini"
            try:
                ctx.think(f"\n\n#{o.rank} {a.name} × {g.name}\n")
                async for chunk in gemini.stream_text(SYSTEM, f"Facts (JSON):\n{facts}", ctx.run.pace):
                    text += chunk
                    ctx.think(chunk)
            except Exception as e:
                ctx.think(f"[Gemini unavailable: {type(e).__name__}. Using the template.]")
                ctx.log(f"Analyst: Gemini failed on #{o.rank} ({type(e).__name__}), wrote it from the template.")
                text, actor = template_insight(a, g, o, shared), "template"
            bad = unsupported_numbers(text, facts) if actor == "gemini" else set()
            b.analyses[o.id] = {"text": text.strip(), "actor": actor, "unsupported_numbers": sorted(bad)}
            ctx.emit("analysis.ready", overlap_id=o.id, **b.analyses[o.id])
            written += 1
            await ctx.pace(0.3)
        return f"{written} write-ups"
