# Cost research, then the savings calculator. Code does the arithmetic (core/costs.py); Gemini finds published
# costs on the web; Jev checks each found cost against its quote, and whether each pair can really share work.

import asyncio
from typing import Any

from app import config
from app.clients import models
from app.clients.gemini import cite
from app.core.analysis import shared_resources
from app.core.costs import TIER_WORDS, benchmark_estimate, benchmark_table, estimate_for, filed_estimate, savings_block
from app.core.models import Overlap, Project
from app.core.owners import owner_name
from app.runtime.agent import Agent, AgentSpec, Ctx

SEARCH_SYSTEM = ("You look up the published cost of one planned electric transmission project. Search news, utility, "
                 "regulator and grid-operator pages for its estimated cost. Report only a dollar figure a page states for "
                 "this specific project, and quote the sentence that states it. If no page states one, say so.")
EXTRACT_SYSTEM = ("From the search answer, pull out the one cost figure stated for the named project. Use only what the "
                  "text says. amount_usd is the full figure in dollars (\"$45 million\" is 45000000). quote is the "
                  "sentence as written. source is the number of the page it came from. found is false if no figure is "
                  "stated for this project.")
EXTRACT_SCHEMA = {"type": "object", "properties": {
    "found": {"type": "boolean"}, "amount_usd": {"type": ["number", "null"]}, "quote": {"type": ["string", "null"]},
    "source": {"type": ["integer", "null"]}}, "required": ["found"]}


def _side(p: Project) -> dict[str, Any]:
    # construction_start is often unknown; under_way_since is when work is known to be happening (Dominion's
    # "Previous" spending), which is what the build windows use when there is no start date.
    return {"name": p.name, "owner": owner_name(p), "type": p.project_type, "description": p.description[:400],
            "construction_start": p.build_start, "under_way_since": p.build_active_from,
            "in_service": p.in_service_date, "already_in_service": p.in_service_date < config.TODAY, "miles": p.miles}


def sharing_question(a: Project, g: Project, o: Overlap, shared: dict[str, Any]) -> tuple[str, dict[str, Any], str, str]:
    # (question, state, true, false) for Jev. The distance is the closest points between the two projects and the
    # tier says what that distance lets them share (the challenge's rules). Asks whether ANY listed item is worth
    # raising between the two owners: all of them at once is a much higher bar than the savings range assumes.
    items = ", ".join(shared["items"])
    together = shared["timing"] == "concurrent" and not o.finished
    state = {"today": config.TODAY, "closest_distance_mi": o.distance_mi, "tier": TIER_WORDS.get(o.tier, o.tier),
             "build_windows_overlap": o.windows_overlap, "a": _side(a), "b": _side(g)}
    if together:
        return (f"Two different utilities are building these projects {TIER_WORDS[shared['tier']]} at the same time. Is "
                f"it worth the two utilities discussing sharing at least one of these: {items}?", state,
                "Close enough in place, timing and kind of work that coordinating on at least one of them is worth raising.",
                "Too different in timing or kind of work for any of them to be worth raising.")
    # land, structures, yards and records don't need both crews on site at once, so timing isn't part of this one
    return (f"Two different utilities are building these projects {TIER_WORDS[shared['tier']]}, at different times. Is "
            f"it worth the two utilities discussing sharing at least one of these: {items}?", state,
            "Close enough in place and kind of work that sharing at least one of them would help.",
            "Too different in kind of work for any of them to help.")


class CostResearcher(Agent):
    spec = AgentSpec("cost_research", "Cost research", "Finds each project's cost: filed, published on the web, "
                     "or a Dominion benchmark", ["code", "gemini", "jev"], depends_on=["overlap", "classifier"],
                     engine="Gemini", roles=["cost_search", "cost_extract", "cost_quote_check"])

    async def run(self, ctx: Ctx) -> str:
        b = ctx.board
        table = benchmark_table(list(b.projects.values()))
        ids = list(dict.fromkeys(pid for o in b.overlaps for pid in (o.project_a, o.project_b)))
        sem = asyncio.Semaphore(4)
        done = 0

        async def one(p: Project) -> None:
            nonlocal done
            async with sem:
                e = filed_estimate(p) or await self.search(ctx, p) or benchmark_estimate(p, table)
            if e:
                b.estimates[p.id] = e
                ctx.emit("cost.estimate", project_id=p.id, estimate=e)
            done += 1
            ctx.progress(done, len(ids), "costed")

        await asyncio.gather(*(one(b.projects[i]) for i in ids))
        counts = {k: sum(1 for e in b.estimates.values() if e["basis"] == k) for k in ("filed", "published", "benchmark")}
        ctx.log(f"Cost research: {counts['filed']} costs from the filings, {counts['published']} published on the web "
                f"and confirmed, {counts['benchmark']} modeled from Dominion's filed costs.")
        return ", ".join(f"{n} {k}" for k, n in counts.items() if n) or "no projects to cost"

    async def search(self, ctx: Ctx, p: Project) -> dict[str, Any] | None:
        if not models.available("cost_search"):
            return None
        prompt = (f"Project: {p.name}\nOwner: {owner_name(p)}\nIn service: {p.in_service_date}\n"
                  f"Scope: {p.description[:400]}\n\nWhat is its published estimated cost?")
        async with ctx.tool("gemini_search_cost", {"project": p.name}, actor="gemini") as out:
            try:
                g = await models.call("cost_search", prompt, system=SEARCH_SYSTEM)
            except models.RoleExhausted:
                g = None
            out["summary"] = f"{len(g.value['sources'])} pages" if g else "search unavailable"
            out["model"] = g.model if g else None
        if not g or not g.value["sources"]:
            return None
        found = g.value
        numbered = "\n".join(f"[{i + 1}] {s['title']} {s['url']}" for i, s in enumerate(found["sources"]))
        try:
            x = await models.call("cost_extract", f"Project: {p.name}\n\nAnswer:\n{cite(found['text'], found['supports'])}"
                                  f"\n\nSources:\n{numbered}", EXTRACT_SCHEMA, system=EXTRACT_SYSTEM)
        except models.RoleExhausted:
            return None
        d = x.value or {}
        amount, quote, n = d.get("amount_usd"), d.get("quote"), d.get("source")
        if not d.get("found") or not isinstance(amount, (int, float)) or amount <= 0 or not quote                 or not isinstance(n, int) or not 1 <= n <= len(found["sources"]):
            return None
        words = [w for w in p.name.lower().replace("-", " ").split() if len(w) > 4]
        v = await ctx.ask_noul(
            "Does the quote state the cost of this specific project, and does the amount match the quote?", p.name,
            {"project": _side(p), "quote": quote, "amount_usd": amount},
            "The quote gives this project's own cost and the amount matches it.",
            "The quote is about another project or a program total, or the amount doesn't match.",
            heuristic=lambda: 0.6 if any(w in quote.lower() for w in words) else 0.3, project_id=p.id,
            role="cost_quote_check")
        check = {"actor": v.actor, "p": round(float(v.value), 3)}
        if v.value < 0.5:
            ctx.log(f"Cost research: {v.actor} rejected ${amount:,.0f} for {p.name}; using the benchmark instead.")
            return None
        src = found["sources"][n - 1]
        return {"project_id": p.id, "amount": int(amount), "basis": "published", "source": src["url"],
                "source_title": src["title"], "quote": quote, "method": "Published on the web; the quote states it.",
                "check": check}


class SavingsCalculator(Agent):
    spec = AgentSpec("cost", "Savings", "Savings range for each pair; Jev checks the two can really share work",
                     ["code", "jev"], depends_on=["cost_research"], engine="Jev", roles=["savings_check"])

    async def run(self, ctx: Ctx) -> str:
        b = ctx.board
        table = benchmark_table(list(b.projects.values()))
        sem = asyncio.Semaphore(8)
        done = 0

        async def one(o: Overlap) -> None:
            nonlocal done
            a, g = b.projects[o.project_a], b.projects[o.project_b]
            shared = shared_resources(a, g, o)
            ea, eg = estimate_for(a, b.estimates, table), estimate_for(g, b.estimates, table)
            check = None
            if ea and eg:
                question, state, true, false = sharing_question(a, g, o, shared)
                async with sem:
                    v = await ctx.ask_noul(question, f"{a.name} × {g.name}", state, true, false,
                                           heuristic=lambda: {"high": 0.8, "medium": 0.65}.get(shared["level"], 0.55),
                                           role="savings_check")
                check = {"actor": v.actor, "p": round(float(v.value), 3)}
            b.costs[o.id] = {**savings_block(a, g, o, shared, ea, eg, check), "shared": shared}
            ctx.emit("cost.ready", overlap_id=o.id, cost=b.costs[o.id])
            done += 1
            ctx.progress(done, len(b.overlaps), "pairs")

        await asyncio.gather(*(one(o) for o in b.overlaps))
        ranged = sum(1 for c in b.costs.values() if c["savings_high"])
        ruled_out = sum(1 for c in b.costs.values() if c["check"] and c["check"]["p"] < 0.5)
        ctx.log(f"Savings: a range for {ranged} of {len(b.overlaps)} pairs; {ruled_out} judged not worth raising. "
                "The percentages are the team's assumptions, not sourced figures.")
        return f"{ranged} savings ranges, {ruled_out} ruled out"
