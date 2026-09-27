import asyncio
import re
from collections import Counter

from app import config
from app.core.models import Check, Project
from app.runtime.agent import LLM_ACTORS, Agent, AgentSpec, Ctx

# equipment word in the need -> words that show the description is about that equipment
EQUIPMENT: dict[str, list[str]] = {
    "transformer": ["transformer", "autobank", "bank", "mva"],
    "line": ["line", "structure", "conductor", "circuit", "section", "rebuild", "tie", "kv"],
    "structure": ["structure", "pole", "tower", "h-frame"],
    "switch house": ["switch house", "sw house"],
    "breaker": ["breaker"],
    "relay": ["relay"],
    "capacitor": ["capacitor"],
    "reactor": ["reactor"],
}


def need_mismatch_heuristic(p: Project) -> float:
    # e.g. the need says 'these transformers' but the project is about switch houses.
    need, body = p.need_text.lower(), f"{p.name} {p.description}".lower()
    for word, evidence in EQUIPMENT.items():
        if re.search(rf"\b(these|this|the)\s+{word}s?\b", need) and not any(e in body for e in evidence):
            return 0.8
    return 0.08


class Validator(Agent):
    spec = AgentSpec("validator", "Validator", "Checks dates, costs, duplicates and whether each filing agrees with itself",
                     ["code", "jev"], depends_on=["sample", "extract_desc", "extract_ga"],
                     engine="Rules + Jev")

    async def report(self, ctx: Ctx, check: Check) -> None:
        ctx.board.checks.append(check)
        ctx.emit("check.found", check=check.model_dump(), **({"model": check.model} if check.actor in LLM_ACTORS else {}))
        await ctx.pace(0.22)

    async def run(self, ctx: Ctx) -> str:
        b = ctx.board
        projects = list(b.projects.values())
        desc = [p for p in projects if p.utility == "DESC"]
        ga = [p for p in projects if p.utility == "GA"]
        found = list(b.pending_checks)  # extractors and sample reader are done by now
        for c in found:
            await self.report(ctx, c)
        b.pending_checks = [c for c in b.pending_checks if c not in found]

        past = [p for p in desc if p.in_service_date < config.TODAY]
        await self.report(ctx, Check(id="desc:past", level="warn", rule="past_isd", title="Dominion dates already in the past",
                                     detail=f"{len(past)} of {len(desc)} Dominion projects have in-service dates before "
                                            f"{config.TODAY}. They're kept; the Hide finished filter removes them.",
                                     source="DESC project list"))
        await self.report(ctx, Check(id="ga:redacted", level="info", rule="redacted_cost", title="Georgia costs are redacted",
                                     detail=f"All {len(ga)} Georgia project costs read 'REDACTED'. They're stored as unknown, "
                                            "never zero. Cost estimates use Dominion's public figures only.",
                                     source="GA IRP Vol. 3, Ten-Year Plan table"))
        if b.ga_ceii_pages:
            await self.report(ctx, Check(id="ga:ceii", level="info", rule="ceii_banner", title="CEII banner on public pages",
                                         detail=f"{b.ga_ceii_pages} of {b.ga_page_count} pages carry a CEII notice, but this "
                                                "is the public-disclosure version with sensitive fields redacted. Only visible "
                                                "text is used.", source="GA IRP Vol. 3"))
        sponsors = Counter(p.sponsor for p in ga)
        await self.report(ctx, Check(id="ga:sponsors", level="info", rule="mixed_sponsors",
                                     title="Not every Georgia project is Georgia Power's",
                                     detail="Owners in the Georgia plan: " + ", ".join(f"{k} {v}" for k, v in sponsors.most_common())
                                            + ". GPC and SAV (Georgia Power Savannah) are shown by default.", source="GA IRP Vol. 3, project list"))
        for label, group in [*self._duplicates(desc).items(), *self._duplicates(ga).items()]:
            first = group[0]
            entries = "; ".join(f"{p.source_ref} (in service {p.in_service_date}"
                                + (f", ${p.cost_total:,}" if p.cost_total is not None else "") + ")" for p in group)
            await self.report(ctx, Check(id=f"dup:{first.id}", level="info", rule="near_duplicate",
                                         title="Near-duplicate project entries",
                                         detail=f"'{first.name}' and {len(group) - 1} more with almost the same title: "
                                                f"{entries}. The pipeline kept them as separate projects; they may be "
                                                "phases of one job.",
                                         source="DESC project list" if first.utility == "DESC" else "GA IRP Vol. 3",
                                         project_id=first.id))

        flagged = 0
        sem = asyncio.Semaphore(8)

        async def semantic(p: Project) -> None:
            nonlocal flagged
            async with sem:
                v = await ctx.ask_noul(
                    "Does the stated project need contradict the project description?", p.name,
                    {"project": p.name, "description": p.description, "need": p.need_text},
                    true="The need talks about equipment or work that the description and title never mention "
                         "(likely copied from another project).",
                    false="The need is generic (load growth, reliability, end of life) or matches the described work.",
                    heuristic=lambda: need_mismatch_heuristic(p), project_id=p.id, role="validate_semantic")
            if v.value >= 0.6:
                flagged += 1
                await self.report(ctx, Check(
                    id=f"need:{p.id}", level="warn", rule="need_mismatch", actor=v.actor, model=v.model,
                    title="Stated need doesn't match the description",
                    detail=f"{p.name}: the need says \"{p.need_text}\" but the description is about: "
                           f"\"{p.description[:160]}\". Probably copied from another project.",
                    source=f"DESC PDF p.{p.source_page}", project_id=p.id))

        await asyncio.gather(*(semantic(p) for p in desc))
        return f"{len(b.checks)} checks ({flagged} semantic flags)"

    @staticmethod
    def _duplicates(projects: list[Project]) -> dict[str, list[Project]]:
        # Same title once work verbs, spacing and punctuation are ignored ("... 46kV Rebuilds" vs "... 46kV").
        # Circuit numbers stay, so "#5" and "#6" are different projects.
        groups: dict[str, list[Project]] = {}
        for p in projects:
            key = re.sub(r"\b(rebuilds?|construct(ion)?|upgrades?|replace(ment)?|reconductor)\b", "", p.name.lower())
            groups.setdefault(re.sub(r"[^a-z0-9#]", "", key), []).append(p)
        return {k: v for k, v in groups.items() if len(v) > 1}
