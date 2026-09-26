# All numbers here come from code. LLM write-ups can only restate them.

import re
from datetime import date
from typing import Any

from app import config
from app.core.models import Overlap, Project

FIELD = {"new_line", "rebuild", "substation_construction"}


def shared_resources(a: Project, b: Project, o: Overlap) -> dict[str, Any]:
    ta, tb = a.project_type or "other", b.project_type or "other"
    lines = {"new_line", "rebuild"}
    if o.windows_overlap is None:
        timing = "unknown"
    else:
        timing = "concurrent" if o.windows_overlap else "sequential"
    if timing == "concurrent" and ta in lines and tb in lines:
        items, level = ["crews", "equipment and cranes", "staging/laydown yards", "outage coordination"], "high"
    elif timing == "concurrent" and ta in FIELD and tb in FIELD:
        items, level = ["contractors", "staging/laydown yards", "deliveries", "outage coordination"], "high"
    elif timing == "concurrent":
        items, level = ["outage coordination", "shared deliveries"], "medium"
    elif ta in lines or tb in lines:
        items, level = ["surveys", "right-of-way records", "environmental studies", "design of the later project"], "medium"
    else:
        items, level = ["information sharing (surveys, permits, design)"], "low"
    if "in_substation_equipment" in (ta, tb) and level == "high":
        level = "medium"
    return {"timing": timing, "items": items, "level": level, "types": [ta, tb]}


def cost_block(a: Project, b: Project, o: Overlap) -> dict[str, Any]:
    # No savings number unless a cited share is configured.
    block: dict[str, Any] = {"desc_cost": a.cost_total, "ga_cost": None, "desc_miles": a.miles,
                             "desc_cost_per_mile": round(a.cost_total / a.miles) if a.cost_total and a.miles else None,
                             "savings": None, "source": None}
    if config.COST_MOBILIZATION_SHARE > 0 and config.COST_SOURCE and o.windows_overlap and a.cost_total:
        block["savings"] = round(a.cost_total * config.COST_MOBILIZATION_SHARE)
        block["source"] = config.COST_SOURCE
        block["statement"] = (f"If one mobilization is avoided, about {config.COST_MOBILIZATION_SHARE:.0%} of Dominion's "
                              "public project cost, per the cited source. Dominion's side only; Georgia's cost is redacted.")
    elif o.windows_overlap:
        block["statement"] = ("Both are under construction at the same time, so one mobilization and staging setup could "
                              "serve both. No dollar figure is shown until a cited mobilization share is configured.")
    else:
        block["statement"] = "The build windows don't overlap (or one is unknown), so no crew or staging savings are claimed."
    return block


def fact_sheet(a: Project, b: Project, o: Overlap, shared: dict[str, Any]) -> dict[str, Any]:
    def side(p: Project) -> dict[str, Any]:
        return {"utility": "Dominion Energy South Carolina" if p.utility == "DESC" else f"Georgia ({p.sponsor})",
                "name": p.name, "type": p.project_type, "status": p.status, "in_service": p.in_service_date,
                "build_start": p.build_start, "description": p.description[:700], "need": p.need_text[:300],
                "cost": p.cost_total, "source": f"{p.source_file} p.{p.source_page} ({p.source_ref})"}
    return {"distance_mi": o.distance_mi, "time_gap_days": o.time_gap_days, "windows_overlap": o.windows_overlap,
            "location_confidence": o.pair_confidence, "shared": shared, "dominion": side(a), "georgia": side(b)}


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def unsupported_numbers(text: str, facts: dict[str, Any]) -> set[str]:
    # Numbers in the LLM text that weren't in the facts we gave it.
    allowed = numbers_in(str(facts))
    return {n for n in numbers_in(text) if n not in allowed and len(n) > 1}


def template_insight(a: Project, b: Project, o: Overlap, shared: dict[str, Any]) -> str:
    years = o.time_gap_days / 365
    if shared["timing"] == "concurrent":
        return (f"Both projects are under construction at the same time, {o.distance_mi:.1f} miles apart. "
                f"They could share {', '.join(shared['items'])}.")
    if years < 2:
        return (f"Their in-service dates are {o.time_gap_days} days apart. A modest schedule shift could put both "
                "crews in the area at once.")
    return (f"Their in-service dates are about {round(years)} years apart, so crews won't overlap. The value is "
            "shared information: surveys, right-of-way records, and designing the later project around the earlier one.")


def today() -> date:
    return date.fromisoformat(config.TODAY)
