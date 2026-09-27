import asyncio
import re

from app.core.models import Project
from app.runtime.agent import Agent, AgentSpec, Ctx

TYPES = {
    "new_line": "Builds a new transmission line on new right-of-way.",
    "rebuild": "Rebuilds, reconductors or upgrades an existing line along its existing corridor.",
    "substation_construction": "Builds a new substation or expands one (new yard, banks, ring bus, fold-ins).",
    "in_substation_equipment": "Replaces or adds equipment inside an existing substation (breakers, relays, "
                               "transformers, reactors, switch houses) with little field work outside the fence.",
    "other": "Anything else.",
}


def type_heuristic(p: Project) -> str:
    # Used when neither Jev nor Gemini is available.
    name, text = p.name.lower(), f"{p.name} {p.description}".lower()
    if re.search(r"reactor|breaker|relay|autobank|\bbank\b|capacitor|sw house|switch house|transformer", name):
        return "in_substation_equipment"
    if re.search(r"rebuild|reconductor|upgrade", name):
        return "rebuild"
    if re.search(r"construct (a |an |two )?(new )?[\w/.\- ]{0,20}line|new [\w/.\- ]{0,20}kv line|build .* line", text):
        return "new_line"
    if re.search(r"construct (a )?new .*sub|new .*substation|expand|fold", text):
        return "substation_construction"
    if re.search(r"rebuild|reconductor|re-conductor", text):
        return "rebuild"
    if re.search(r"breaker|relay|transformer|reactor|switch house|bank|capacitor", text):
        return "in_substation_equipment"
    return "other"


class Classifier(Agent):
    spec = AgentSpec("classifier", "Classifier", "Tags each project by kind of work", ["jev"],
                     depends_on=["extract_desc", "extract_ga"], engine="Jev")

    async def run(self, ctx: Ctx) -> str:
        projects = list(ctx.board.projects.values())
        sem = asyncio.Semaphore(8)
        done = 0

        async def one(p: Project) -> None:
            nonlocal done
            async with sem:
                v = await ctx.ask_choice("What kind of work is this project?", p.name,
                                         {"title": p.name, "description": p.description[:700]}, TYPES,
                                         heuristic=lambda: type_heuristic(p), project_id=p.id, role="classify_type")
            p.project_type, p.project_type_actor = str(v.value), v.actor
            done += 1
            ctx.emit("project.classified", project_id=p.id, project_type=p.project_type, actor=v.actor,
                     model=v.model, confidence=round(v.confidence, 3))
            ctx.progress(done, len(projects), "classified")
            await ctx.pace(0.008)

        await asyncio.gather(*(one(p) for p in projects))
        counts: dict[str, int] = {}
        for p in projects:
            counts[p.project_type or "other"] = counts.get(p.project_type or "other", 0) + 1
        return "; ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
