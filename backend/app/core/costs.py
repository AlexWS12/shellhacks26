# Project cost estimates and the savings range. All numbers here come from code.
#
# Each project's cost comes from, in order:
#   filed      the owner's own filing states it (Dominion; submitted plans that give a cost)
#   published  the cost researcher found it on the web with a quote, and Jev confirmed the quote backs it
#   benchmark  the median filed Dominion cost for the same kind of work (per mile for lines)
# Savings are a labeled assumption range, not a sourced figure.

from statistics import median
from typing import Any

from app.core.models import Overlap, Project
from app.core.owners import owner_name

LINES = {"new_line", "rebuild"}
ROUND_TO = 100_000

# Share of the smaller project's cost that coordination might save, by (timing, shared level).
# ASSUMPTIONS: team estimates, not figures from a cited source. Edit here; the UI and report say so.
# Reasoning, from typical transmission cost breakdowns:
#   Mobilization and staging run a few percent of construction; sharing avoids much of one set, not all of it
#   (crews still move between sites), so 2-5%.
#   Outage planning and deliveries are a small slice of cost: 0.5-2%.
#   Engineering, surveys and environmental work are about a tenth of a line project; reusing the other project's
#   records saves a fraction of that, so 0.5-1.5%. Substation equipment work gains little from them: 0-0.5%.
ASSUMPTIONS: dict[tuple[str, str], tuple[float, float]] = {
    ("concurrent", "high"): (0.02, 0.05),  # one mobilization, staging yard and outage window instead of two
    ("concurrent", "medium"): (0.005, 0.02),  # shared outage coordination and deliveries
    ("sequential", "medium"): (0.005, 0.015),  # reused surveys, right-of-way records and studies
    ("sequential", "low"): (0.0, 0.005),  # information sharing only
}
ASSUMPTION_LABEL = "Assumption range set by the team, not a sourced figure"


def _round(n: float) -> int:
    return int(round(n / ROUND_TO) * ROUND_TO) or ROUND_TO


def _sig(n: float, digits: int = 2) -> int:
    # Savings are rough; two significant figures ($38,000, $1,200,000) say so without hiding small ones.
    return int(float(f"{n:.{digits - 1}e}")) if n else 0


def benchmark_table(projects: list[Project]) -> dict[str, dict[str, Any]]:
    # Median filed Dominion cost per kind of work, and per mile for lines.
    filed = [p for p in projects if p.utility == "DESC" and p.cost_total]
    table: dict[str, dict[str, Any]] = {}
    for t in sorted({p.project_type or "other" for p in filed}):
        same = [p for p in filed if (p.project_type or "other") == t]
        per_mile = [p.cost_total / p.miles for p in same if p.miles and p.cost_total]  # type: ignore[operator]
        table[t] = {"per_project": median(p.cost_total for p in same), "n": len(same),  # type: ignore[type-var]
                    "floor": min(p.cost_total for p in same),  # type: ignore[type-var]
                    "per_mile": median(per_mile) if t in LINES and per_mile else None, "n_per_mile": len(per_mile)}
    if filed:
        table["_all"] = {"per_project": median(p.cost_total for p in filed), "n": len(filed),  # type: ignore[type-var]
                         "per_mile": None, "n_per_mile": 0}
    return table


def filed_estimate(p: Project) -> dict[str, Any] | None:
    if not p.cost_total:
        return None
    return {"project_id": p.id, "amount": p.cost_total, "basis": "filed",
            "source": f"{p.source_file} p.{p.source_page} ({p.source_ref})",
            "method": f"Stated in {owner_name(p)}'s filing.", "check": None}


def benchmark_estimate(p: Project, table: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    t = p.project_type or "other"
    row = table.get(t)
    if row and row["per_mile"] and p.miles:
        amount = row["per_mile"] * p.miles
        method = (f"{p.miles:g} mi at ${row['per_mile'] / 1e6:.1f}M per mile, the median of "
                  f"{row['n_per_mile']} filed Dominion {t.replace('_', ' ')} projects.")
        if amount < row["floor"]:  # too short to scale by the mile: no filed project of this kind costs less
            amount = row["floor"]
            method += f" Raised to ${amount / 1e6:.1f}M, the cheapest of {row['n']} filed projects of this kind."
    elif row:
        amount = row["per_project"]
        method = f"Median of {row['n']} filed Dominion {t.replace('_', ' ')} projects."
    elif "_all" in table:
        row = table["_all"]
        amount = row["per_project"]
        method = f"Median of all {row['n']} filed Dominion projects (no filed project of this kind)."
    else:
        return None
    return {"project_id": p.id, "amount": _round(amount), "basis": "benchmark",
            "source": "Dominion Energy SC filed project costs", "method": method, "check": None}


def estimate_for(p: Project, estimates: dict[str, dict[str, Any]], table: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    # The researched estimate if the run made one, else what code can say on its own.
    return estimates.get(p.id) or filed_estimate(p) or benchmark_estimate(p, table)


def savings_basis(o: Overlap, shared: dict[str, Any]) -> dict[str, Any]:
    # What the savings are for. Once either project is in service, crews and staging can't be shared, only its
    # records and design, whatever the build windows say. (shared_resources is left alone: other agents use it.)
    if not o.finished or shared["timing"] == "sequential":
        return shared
    if LINES & set(shared["types"]):
        items, level = ["surveys", "right-of-way records", "environmental studies", "design of the later project"], "medium"
    else:
        items, level = ["information sharing (surveys, permits, design)"], "low"
    return {**shared, "timing": "sequential", "items": items, "level": level}


def savings_block(a: Project, b: Project, o: Overlap, shared: dict[str, Any], ea: dict[str, Any] | None,
                  eb: dict[str, Any] | None, check: dict[str, Any] | None = None) -> dict[str, Any]:
    # check: Jev's verdict on whether sharing what savings_basis lists is worth raising; None if not asked.
    shared = savings_basis(o, shared)
    block: dict[str, Any] = {"a": ea, "b": eb, "savings_low": None, "savings_high": None, "share": None,
                             "applies_to": None, "for": shared["items"], "assumption": ASSUMPTION_LABEL,
                             "check": check, "statement": ""}
    share = ASSUMPTIONS.get((shared["timing"], shared["level"]))
    if share is None:
        block["statement"] = "One build window is unknown, so no savings range is given."
        return block
    if not ea or not eb:
        block["statement"] = "One project has no cost estimate, so no savings range is given."
        return block
    if check and check["p"] < 0.5:
        block["statement"] = (f"{check['actor'].capitalize()} judged that sharing {', '.join(shared['items'])} isn't "
                              "worth raising for these two projects, so no savings are claimed.")
        return block
    smaller = min((ea, a), (eb, b), key=lambda x: x[0]["amount"])
    base = smaller[0]["amount"]
    block.update(savings_low=_sig(base * share[0]), savings_high=_sig(base * share[1]),
                 share=list(share), applies_to=smaller[1].id)
    modeled = [owner_name(p) for e, p in ((ea, a), (eb, b)) if e["basis"] == "benchmark"]
    block["statement"] = (
        f"{share[0]:.1%} to {share[1]:.1%} of the smaller project's cost ({owner_name(smaller[1])}), for sharing "
        f"{', '.join(shared['items'])}. {ASSUMPTION_LABEL}."
        + (" One project is already in service, so only its records and design count." if o.finished else "")
        + (f" {' and '.join(modeled)} cost is modeled from Dominion's filed costs." if modeled else ""))
    return block
