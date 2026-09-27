# The Writer runs last and turns the finished run into a report.
# Code builds every number and name (core/report.py). Gemini writes only the summary paragraph and the next
# steps; any number it adds that is not in the facts is flagged, and a template takes over when Gemini fails.

import json
from typing import Any

from app.clients import models
from app.core.analysis import unsupported_numbers
from app.core.report import TOP_REPORT, build, facts_for_prose, template_next_steps, template_summary, to_markdown
from app.runtime.agent import Agent, AgentSpec, Ctx

SUMMARY = ("You write the opening paragraph of a coordination report for transmission planners at neighboring "
           "utilities. Use ONLY the facts. Do not introduce any number that is not in the facts. 3 or 4 plain "
           "sentences: how many nearby pairs were found and which ones stand out, and why. No headings, no bullets.")
NEXT_STEPS = ("You write the recommended next steps of a coordination report for neighboring utilities. Use ONLY the "
              "facts. Write 3 to 5 short bullets that start with '- ', each one a concrete action that names the "
              "projects involved. Do not introduce any number that is not in the facts. No headings.")


class Writer(Agent):
    spec = AgentSpec("writer", "Writer", "Writes the final report that sums up the opportunities",
                     ["code", "gemini"], depends_on=["mediator", "third_party"], engine="Gemini", roles=["writer"])

    async def run(self, ctx: Ctx) -> str:
        b = ctx.board
        async with ctx.tool("assemble_report_facts", {"top": TOP_REPORT}) as out:
            r = build(b)
            c = r["counts"]
            out["summary"] = (f"{len(r['top'])} top opportunities of {c['opportunities']}, {len(r['issues'])} issues, "
                              f"benchmark {c['benchmark_passed']}/{c['benchmark_total']}")
        facts = facts_for_prose(r)
        r["summary"] = await self.write(ctx, SUMMARY, facts, template_summary(r), "Summary")
        r["next_steps"] = await self.write(ctx, NEXT_STEPS, facts, template_next_steps(r), "Next steps")
        r["markdown"] = to_markdown(r)
        b.report = r
        ctx.emit("report.ready", report=r, model=r["summary"]["model"])  # each part also names its own model
        ctx.log(f"Writer: report ready, {len(r['top'])} opportunities summarized.")
        return f"report on {len(r['top'])} opportunities (summary: {r['summary']['actor']})"

    async def write(self, ctx: Ctx, system: str, facts: dict[str, Any], fallback: str, label: str) -> dict[str, Any]:
        text, actor, model = "", "gemini", None
        ctx.think(f"\n\n{label}\n")
        stream = models.stream("writer", f"Facts (JSON):\n{json.dumps(facts, default=str)}", system=system,
                               pace=ctx.run.pace)
        try:
            async for chunk in stream:
                text += chunk
                ctx.think(chunk)
            actor, model = stream.provider or "gemini", stream.model
        except Exception as e:
            if not ctx.run.templates:
                raise RuntimeError(f"No model could write the {label.lower()} and template fallback is off: "
                                   f"{models.describe(e)}") from e
            ctx.think(f"[No model available: {models.describe(e)}. Using the template.]")
            ctx.log(f"Writer: no model could write the {label.lower()} ({models.describe(e)}), used the template.")
            text, actor = fallback, "template"
        text = text.replace(" \u2014 ", ", ").replace("\u2014", ", ")  # house style: no em dashes
        bad = sorted(unsupported_numbers(text, facts)) if actor != "template" else []
        return {"text": text.strip(), "actor": actor, "model": model, "unsupported_numbers": bad}
