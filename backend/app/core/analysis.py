# All numbers here come from code. LLM write-ups can only restate them.

import re
from datetime import date
from typing import Any

from app import config
from app.core.models import Overlap, Project
from app.core.owners import book

# What each distance tier lets two projects share (the challenge's tiers, core/overlap.py TIERS). A closer tier also
# gets everything the farther ones do. Items marked together need both projects under construction at once.
TIER_ITEMS: dict[str, list[tuple[str, bool]]] = {
    "touching": [("outage timing", True), ("crossing structures", False)],
    "row": [("right-of-way", False), ("access roads", False), ("permits", False)],
    "site": [("laydown yards", False), ("deliveries", True)],
    "crew": [("crews", True), ("equipment", True)],
}
TIER_ORDER = ["touching", "row", "site", "crew"]


def shared_resources(a: Project, b: Project, o: Overlap) -> dict[str, Any]:
    ta, tb = a.project_type or "other", b.project_type or "other"
    if o.windows_overlap is None:
        timing = "unknown"
    else:
        timing = "concurrent" if o.windows_overlap else "sequential"
    # a project already in service has no crews left to share, whatever its build window said
    together = timing == "concurrent" and not o.finished
    tier = o.tier if o.tier in TIER_ORDER else "crew"
    items = [name for t in TIER_ORDER[TIER_ORDER.index(tier):] for name, needs_both in TIER_ITEMS[t]
             if together or not needs_both]
    if not items:  # crews apart in time: the earlier project's records can still help the later one
        items = ["survey and design records"]
    if tier in ("touching", "row"):
        level = "high"
    elif tier == "site":
        level = "high" if together else "medium"
    else:
        level = "medium" if together else "low"
    return {"timing": timing, "tier": tier, "items": items, "level": level, "types": [ta, tb],
            "must_coordinate": tier == "touching"}


def is_core(o: Overlap) -> bool:
    # A Dominion-Georgia pair: the pair the write-ups were built around.
    return o.project_a.startswith("DESC-") and o.project_b.startswith("GA-")


def side_keys(o: Overlap) -> tuple[str, str]:
    # The fact sheet's keys for the two projects. Dominion-Georgia pairs keep their original keys, so their prompts
    # (and so offline reruns from the model cache) are unchanged; any other pair says "project_a"/"project_b".
    return ("dominion", "georgia") if is_core(o) else ("project_a", "project_b")


def utility_label(p: Project) -> str:
    if p.id.startswith("DESC-"):
        return "Dominion Energy South Carolina"
    if p.id.startswith("GA-"):
        return f"Georgia ({p.sponsor})"
    b = book()
    s = b.of(p)
    return b.owner_name(p) if s and s.sponsors else b.display_name(p)  # "Santee Cooper", not its code


def fact_sheet(a: Project, b: Project, o: Overlap, shared: dict[str, Any]) -> dict[str, Any]:
    def side(p: Project) -> dict[str, Any]:
        return {"utility": utility_label(p),
                "name": p.name, "type": p.project_type, "status": p.status, "in_service": p.in_service_date,
                "build_start": p.build_start, "description": p.description[:700], "need": p.need_text[:300],
                "cost": p.cost_total, "source": f"{p.source_file} p.{p.source_page} ({p.source_ref})"}
    ka, kb = side_keys(o)
    return {"distance_mi": o.distance_mi, "distance_rule": "closest points", "tier": o.tier,
            "time_gap_days": o.time_gap_days, "windows_overlap": o.windows_overlap,
            "location_confidence": o.pair_confidence, "shared": shared, ka: side(a), kb: side(b)}


def sides(facts: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    # (project_a's side, project_b's side) of a fact sheet, whichever keys it uses.
    if "dominion" in facts:
        return facts["dominion"], facts["georgia"]
    return facts["project_a"], facts["project_b"]


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def unsupported_numbers(text: str, facts: dict[str, Any]) -> set[str]:
    # Numbers in the LLM text that weren't in the facts we gave it.
    allowed = numbers_in(str(facts))
    return {n for n in numbers_in(text) if n not in allowed and len(n) > 1}


def template_insight(a: Project, b: Project, o: Overlap, shared: dict[str, Any]) -> str:
    years = o.time_gap_days / 365
    if shared["tier"] == "touching":
        return (f"The two projects touch or cross, so they must coordinate. They could share "
                f"{', '.join(shared['items'])}.")
    if shared["timing"] == "concurrent" and not o.finished:
        return (f"Both projects are under construction at the same time, {o.distance_mi:.1f} miles apart at their "
                f"closest points. They could share {', '.join(shared['items'])}.")
    if years < 2:
        return (f"Their in-service dates are {o.time_gap_days} days apart. A modest schedule shift could put both "
                "crews in the area at once.")
    return (f"Their in-service dates are about {round(years)} years apart, so crews won't overlap. The value is "
            "shared information: surveys, right-of-way records, and designing the later project around the earlier one.")


def today() -> date:
    return date.fromisoformat(config.TODAY)
