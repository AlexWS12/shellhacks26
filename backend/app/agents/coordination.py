# Advocates only read the fact sheet. They can't change distances or dates.

import asyncio

from app.clients import gemini
from app.core.analysis import fact_sheet
from app.runtime.agent import Agent, AgentSpec, Ctx

TOP_BRIEFS = 3

ADVOCATE = ("You prepare {utility}'s side for a SERTP coordination meeting about one nearby project of the other "
            "utility. Use ONLY the facts. Write 3 short bullets: what {utility} likely wants, what constrains it, and "
            "one concrete ask of the other utility. Never state a number that is not in the facts.")
MEDIATOR = ("You are a neutral mediator preparing a joint agenda item for a SERTP meeting. Given the facts and each "
            "utility's prep notes, write 3 short bullets: where they align, where they conflict, and a proposed next "
            "step. Be balanced: neither side should give up much more than the other. No numbers beyond the facts.")


async def _write(ctx: Ctx, system: str, prompt: str, fallback: str) -> tuple[str, str]:
    text = ""
    try:
        async for chunk in gemini.stream_text(system, prompt, ctx.run.pace):
            text += chunk
            ctx.think(chunk)
        return text.strip(), "gemini"
    except Exception as e:
        if not ctx.run.templates:
            raise RuntimeError(f"Gemini failed and template fallback is off: {gemini.describe(e)}") from e
        ctx.think("[Gemini unavailable: template from the facts.]")
        ctx.log(f"{ctx.spec.name}: Gemini failed ({gemini.describe(e)}), wrote it from the template.")
        return fallback, "template"


def _template_side(f: dict, me: str) -> str:
    mine = f["dominion"] if me == "dominion" else f["georgia"]
    other = f["georgia"] if me == "dominion" else f["dominion"]
    return (f"- Keep {mine['name']} on schedule for {mine['in_service']}.\n"
            f"- Constraint: status '{mine['status']}', work type {mine['type']}.\n"
            f"- Ask: share survey and outage plans for {other['name']}.")


class Advocate(Agent):
    def __init__(self, side: str) -> None:
        self.side = side
        label = "DESC" if side == "dominion" else "GA"
        self.spec = AgentSpec("advocate_desc" if side == "dominion" else "advocate_ga", f"Advocate · {label}",
                              f"Prepares {label}'s interests for the coordination meeting", ["gemini"],
                              depends_on=["analyst"], engine="Gemini")

    async def run(self, ctx: Ctx) -> str:
        b = ctx.board
        utility = "Dominion Energy South Carolina" if self.side == "dominion" else "Georgia Power"
        for o in b.overlaps[:TOP_BRIEFS]:
            a, g = b.projects[o.project_a], b.projects[o.project_b]
            f = fact_sheet(a, g, o, b.costs[o.id]["shared"])
            ctx.think(f"\n\n#{o.rank} {a.name} × {g.name}\n")
            text, actor = await _write(ctx, ADVOCATE.format(utility=utility), f"Facts:\n{f}", _template_side(f, self.side))
            b.briefs.setdefault(o.id, {})[self.side] = {"text": text, "actor": actor}
            ctx.emit("brief.side", overlap_id=o.id, side=self.side, text=text, actor=actor)
            await ctx.pace(0.2)
        return f"{min(TOP_BRIEFS, len(b.overlaps))} prep notes"


class Mediator(Agent):
    spec = AgentSpec("mediator", "Mediator", "Neutral joint agenda: where the two sides align and conflict",
                     ["gemini"], depends_on=["advocate_desc", "advocate_ga"], engine="Gemini")

    async def run(self, ctx: Ctx) -> str:
        b = ctx.board
        for o in b.overlaps[:TOP_BRIEFS]:
            a, g = b.projects[o.project_a], b.projects[o.project_b]
            f = fact_sheet(a, g, o, b.costs[o.id]["shared"])
            sides = b.briefs.get(o.id, {})
            prompt = (f"Facts:\n{f}\n\nDominion prep:\n{sides.get('dominion', {}).get('text', '')}\n\n"
                      f"Georgia prep:\n{sides.get('georgia', {}).get('text', '')}")
            fallback = (f"- Align: both have work {o.distance_mi:.1f} mi apart.\n"
                        f"- Conflict: in-service dates are {o.time_gap_days} days apart.\n"
                        f"- Next step: exchange schedules and share: {', '.join(f['shared']['items'])}.")
            ctx.think(f"\n\n#{o.rank}\n")
            text, actor = await _write(ctx, MEDIATOR, prompt, fallback)
            b.briefs.setdefault(o.id, {})["mediator"] = {"text": text, "actor": actor}
            ctx.emit("brief.ready", overlap_id=o.id, brief=b.briefs[o.id])
            await asyncio.sleep(0)
        return f"{min(TOP_BRIEFS, len(b.overlaps))} joint agenda items"
