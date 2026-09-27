# Advocates only read the fact sheet. They can't change distances or dates.

import asyncio

from app.agents.analysis import written_pairs
from app.clients import models
from app.core.analysis import fact_sheet, is_core, sides, utility_label
from app.runtime.agent import Agent, AgentSpec, Ctx

TOP_BRIEFS = 3  # per two utilities

ADVOCATE = ("You prepare {utility}'s side for a SERTP coordination meeting about one nearby project of the other "
            "utility. Use ONLY the facts. Write 3 short bullets: what {utility} likely wants, what constrains it, and "
            "one concrete ask of the other utility. Never state a number that is not in the facts.")
MEDIATOR = ("You are a neutral mediator preparing a joint agenda item for a SERTP meeting. Given the facts and each "
            "utility's prep notes, write 3 short bullets: where they align, where they conflict, and a proposed next "
            "step. Be balanced: neither side should give up much more than the other. No numbers beyond the facts.")


async def _write(ctx: Ctx, role: str, system: str, prompt: str, fallback: str) -> tuple[str, str, str | None]:
    # (text, actor, model)
    text = ""
    stream = models.stream(role, prompt, system=system, pace=ctx.run.pace)
    try:
        async for chunk in stream:
            text += chunk
            ctx.think(chunk)
        return text.strip(), stream.provider or "gemini", stream.model
    except Exception as e:
        if not ctx.run.templates:
            raise RuntimeError(f"No model could write it and template fallback is off: {models.describe(e)}") from e
        ctx.think("[No model available: template from the facts.]")
        ctx.log(f"{ctx.spec.name}: no model could write it ({models.describe(e)}), wrote it from the template.")
        return fallback, "template", None


def _template_side(f: dict, me: str) -> str:
    first, second = sides(f)
    mine, other = (first, second) if me == "dominion" else (second, first)
    return (f"- Keep {mine['name']} on schedule for {mine['in_service']}.\n"
            f"- Constraint: status '{mine['status']}', work type {mine['type']}.\n"
            f"- Ask: share survey and outage plans for {other['name']}.")


class Advocate(Agent):
    # One advocate per side of a pair. The side keys stay "dominion" (project_a's side) and "georgia" (project_b's
    # side) for every pair, so earlier runs still replay: in a pair without Dominion, "dominion" is the first utility.
    def __init__(self, side: str) -> None:
        self.side = side
        label = "DESC" if side == "dominion" else "GA"
        other = "the first utility" if side == "dominion" else "the second utility"
        self.spec = AgentSpec("advocate_desc" if side == "dominion" else "advocate_ga", f"Advocate · {label}",
                              f"Prepares {label}'s interests (or {other}'s, in a pair without them) for the "
                              "coordination meeting", ["gemini"], depends_on=["analyst"], engine="Gemini",
                              roles=["advocate"])

    async def run(self, ctx: Ctx) -> str:
        b = ctx.board
        pairs = written_pairs(b.overlaps, b.projects, TOP_BRIEFS)
        for o in pairs:
            a, g = b.projects[o.project_a], b.projects[o.project_b]
            if is_core(o):  # the original wording, so cached answers still match
                utility = "Dominion Energy South Carolina" if self.side == "dominion" else "Georgia Power"
            else:
                utility = utility_label(a if self.side == "dominion" else g)
            f = fact_sheet(a, g, o, b.costs[o.id]["shared"])
            ctx.think(f"\n\n#{o.rank} {a.name} × {g.name}\n")
            text, actor, model = await _write(ctx, "advocate", ADVOCATE.format(utility=utility), f"Facts:\n{f}",
                                              _template_side(f, self.side))
            b.briefs.setdefault(o.id, {})[self.side] = {"text": text, "actor": actor, "model": model}
            ctx.emit("brief.side", overlap_id=o.id, side=self.side, text=text, actor=actor, model=model)
            await ctx.pace(0.2)
        return f"{len(pairs)} prep notes"


class Mediator(Agent):
    spec = AgentSpec("mediator", "Mediator", "Neutral joint agenda: where the two sides align and conflict",
                     ["gemini"], depends_on=["advocate_desc", "advocate_ga"], engine="Gemini", roles=["mediator"])

    async def run(self, ctx: Ctx) -> str:
        b = ctx.board
        pairs = written_pairs(b.overlaps, b.projects, TOP_BRIEFS)
        for o in pairs:
            a, g = b.projects[o.project_a], b.projects[o.project_b]
            f = fact_sheet(a, g, o, b.costs[o.id]["shared"])
            notes = b.briefs.get(o.id, {})
            first, second = ("Dominion", "Georgia") if is_core(o) else (utility_label(a), utility_label(g))
            prompt = (f"Facts:\n{f}\n\n{first} prep:\n{notes.get('dominion', {}).get('text', '')}\n\n"
                      f"{second} prep:\n{notes.get('georgia', {}).get('text', '')}")
            fallback = (f"- Align: both have work {o.distance_mi:.1f} mi apart.\n"
                        f"- Conflict: in-service dates are {o.time_gap_days} days apart.\n"
                        f"- Next step: exchange schedules and share: {', '.join(f['shared']['items'])}.")
            ctx.think(f"\n\n#{o.rank}\n")
            text, actor, model = await _write(ctx, "mediator", MEDIATOR, prompt, fallback)
            b.briefs.setdefault(o.id, {})["mediator"] = {"text": text, "actor": actor, "model": model}
            ctx.emit("brief.ready", overlap_id=o.id, brief=b.briefs[o.id], model=model)
            await asyncio.sleep(0)
        return f"{len(pairs)} joint agenda items"
