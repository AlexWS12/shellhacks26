# The final report. Code gathers every number and name; the Writer agent only adds two short pieces of prose
# (summary, next steps) on top, and those are checked for numbers that are not in these facts.

from collections import Counter
from typing import Any

from app import config
from app.core.costs import ASSUMPTIONS
from app.core.models import Project
from app.core.overlap import OVERLAP_CUTOFF_MI
from app.core.owners import owner_name
from app.runtime.board import Board

TOP_REPORT = 10
CATEGORY = {"electric": "electric", "gas": "gas", "roads_water": "roads and water"}


def _other(board: Board, oid: str) -> list[dict[str, Any]]:
    out = []
    for t in board.third_party:
        if t.overlap_id != oid or t.research_id not in board.research:
            continue
        r = board.research[t.research_id]
        out.append({"owner": r.utility, "name": r.name, "category": CATEGORY[r.category], "miles_to_a": t.dist_a_mi,
                    "miles_to_b": t.dist_b_mi, "in_service": r.in_service, "sources": len(r.sources)})
    return out


def _cost(e: dict[str, Any] | None) -> dict[str, Any] | None:
    return {"amount": e["amount"], "basis": e["basis"], "source": e["source"]} if e else None


def _side(p: Project) -> dict[str, Any]:
    built_in = p.utility in ("DESC", "GA")
    return {"id": p.id, "name": p.name, "owner": owner_name(p), "in_service": p.in_service_date, "status": p.status,
            "date_precision": p.date_precision or "day",
            "source": f"{p.source_file} p.{p.source_page}" if built_in else f"{p.source_file}, {p.source_ref}"}


def build(board: Board) -> dict[str, Any]:
    projects = list(board.projects.values())
    desc = [p for p in projects if p.utility == "DESC"]
    ga = [p for p in projects if p.utility == "GA"]
    levels = Counter(c.level for c in board.checks)
    top = []
    for o in board.overlaps[:TOP_REPORT]:
        a, g = board.projects[o.project_a], board.projects[o.project_b]
        cost = board.costs.get(o.id, {})
        shared = cost.get("shared") or {}
        top.append({
            "rank": o.rank, "overlap_id": o.id, "distance_mi": o.distance_mi, "time_gap_days": o.time_gap_days,
            "built_at_same_time": o.windows_overlap, "location": o.pair_confidence, "benchmark_pair": o.in_sponsor_sample,
            "a": _side(a), "b": _side(g),
            "shared_level": shared.get("level"), "shared_items": shared.get("items", []), "timing": shared.get("timing"),
            "a_cost": _cost(cost.get("a")), "b_cost": _cost(cost.get("b")),
            "savings_low": cost.get("savings_low"), "savings_high": cost.get("savings_high"),
            "analysis": board.analyses.get(o.id), "joint_agenda": (board.briefs.get(o.id) or {}).get("mediator"),
            "other_utilities": _other(board, o.id),
        })
    research = list(board.research.values())
    owners = Counter("Georgia Power and partners" if p.utility == "GA" else owner_name(p) for p in projects)
    return {
        "title": "Coordination opportunities across utility plans",
        "owners": dict(owners),
        "as_of": config.TODAY,
        "counts": {
            "projects": len(projects), "owners_count": len({p.utility for p in projects}),
            "dominion_projects": len(desc), "georgia_projects": len(ga),
            "placed": sum(1 for p in projects if p.lat is not None),
            "unlocated": sum(1 for p in projects if p.lat is None),
            "opportunities": len(board.overlaps),
            "built_at_same_time": sum(1 for o in board.overlaps if o.windows_overlap),
            "benchmark_passed": sum(r.passed for r in board.reference), "benchmark_total": len(board.reference),
            "issues_error": levels.get("error", 0), "issues_warn": levels.get("warn", 0), "issues_info": levels.get("info", 0),
            "other_utility_projects": len(research),
            "other_utility_placed": sum(1 for r in research if r.lat is not None),
            "opportunities_with_other_utilities": len({t.overlap_id for t in board.third_party}),
        },
        "research_categories": [CATEGORY[c] for c in board.research_selected],
        "top": top,
        "issues": [{"level": c.level, "title": c.title, "source": c.source} for c in board.checks if c.level != "info"],
        "method": [
            "Project center: midpoint of the two located endpoints, or the one located endpoint.",
            "Distance: straight-line (haversine) miles between centers. An opportunity is a pair under 25 miles apart.",
            "Day gap: days between the two in-service dates (Dominion's planned in-service date, Georgia's need date, "
            "and the in-service column of each submitted plan).",
            "Default view: Georgia Power and Georgia Power (Savannah) projects; approximate locations included.",
            "Other utilities: projects found by the research team, each with cited sources, listed when under 25 miles "
            "from both sides of an opportunity.",
            "Plans that give only a year or month use the last day of that period; their day gaps are marked 'about'.",
            "Project cost: the owner's filing when it states one; else a cost published on the web whose quote Jev "
            "confirmed; else the median filed Dominion cost for the same kind of work (per mile for lines).",
            f"Savings: {min(lo for lo, _ in ASSUMPTIONS.values()):.0%} to {max(hi for _, hi in ASSUMPTIONS.values()):.0%} "
            "of the smaller project's cost, depending on timing and what the two could share. These percentages are the team's assumptions, not sourced figures; Jev rules out pairs unlikely "
            "to share work.",
        ],
    }


def facts_for_prose(r: dict[str, Any]) -> dict[str, Any]:
    # What the Writer may mention. Every number in its text must appear here.
    return {
        "cutoff_mi": int(OVERLAP_CUTOFF_MI),
        "counts": r["counts"],
        "top": [{k: t[k] for k in ("rank", "distance_mi", "time_gap_days", "built_at_same_time", "shared_level",
                                   "shared_items")} | {"a": f"{t['a']['name']} ({t['a']['owner']})",
                                                       "b": f"{t['b']['name']} ({t['b']['owner']})",
                                                       "a_in_service": t["a"]["in_service"], "b_in_service": t["b"]["in_service"],
                                                       "other_utilities": [f"{o['owner']}: {o['name']}" for o in t["other_utilities"]]}
                for t in r["top"][:5]],
    }


def template_summary(r: dict[str, Any]) -> str:
    c = r["counts"]
    if not r["top"]:
        return (f"The pipeline read {c['projects']} projects from {c['owners_count']} plans and found no pairs from "
                "different owners under 25 miles apart in the default view.")
    t = r["top"][0]
    same = f" {c['built_at_same_time']} of them are built at the same time." if c["built_at_same_time"] else ""
    return (f"The pipeline read {c['projects']} projects from {c['owners_count']} plans, placed {c['placed']} on the map, "
            f"and found {c['opportunities']} pairs from different owners under 25 miles apart.{same} The closest is "
            f"{t['a']['name']} ({t['a']['owner']}) and {t['b']['name']} ({t['b']['owner']}), {t['distance_mi']} miles apart.")


def template_next_steps(r: dict[str, Any]) -> str:
    if not r["top"]:
        return "- Recheck the unlocated projects; a better location could create new pairs."
    lines = []
    for t in r["top"][:3]:
        share = ", ".join(t["shared_items"]) if t["shared_items"] else "schedules and survey data"
        lines.append(f"- #{t['rank']} {t['a']['name']} and {t['b']['name']}: {t['a']['owner']} and {t['b']['owner']} "
                     f"exchange schedules and share {share}.")
    if r["counts"]["unlocated"]:
        lines.append("- Place the unlocated projects; each one could add pairs to this list.")
    return "\n".join(lines)


def _byline(w: dict[str, Any]) -> str:
    who = "Written by Gemini from the facts in this report." if w["actor"] == "gemini" else "Template from the computed facts."
    if w.get("unsupported_numbers"):
        who += f" Check these numbers, they are not in the facts: {', '.join(w['unsupported_numbers'])}."
    return f"_{who}_"


def _days(t: dict[str, Any]) -> str:
    # About, when either plan gives only a year or month (its date is the period's last day).
    approx = any(t[side].get("date_precision", "day") != "day" for side in ("a", "b"))
    return f"{'about ' if approx else ''}{t['time_gap_days']:,}"


def _money(n: int) -> str:
    return f"${n / 1e6:.1f}M" if n >= 1e6 else f"${n:,}"


def _yes(v: bool | None) -> str:
    return "yes" if v else "unknown" if v is None else "no"


def to_markdown(r: dict[str, Any]) -> str:
    c = r["counts"]
    md = [f"# {r['title']}", "", f"As of {r['as_of']}. Every number below comes from the pipeline's code.", "",
          "## Summary", "", r["summary"]["text"], "", _byline(r["summary"]), "", "## At a glance", "",
          "| Measure | Value |", "|---|---|",
          "| Projects read | " + ", ".join(f"{n} {o}" for o, n in r["owners"].items()) + " |",
          f"| On the map | {c['placed']} ({c['unlocated']} without a location) |",
          f"| Pairs under 25 miles | {c['opportunities']} ({c['built_at_same_time']} built at the same time) |",
          f"| Benchmark | {c['benchmark_passed']} of {c['benchmark_total']} known overlaps exact |",
          f"| Data issues | {c['issues_error']} errors, {c['issues_warn']} warnings, {c['issues_info']} notes |"]
    if r["research_categories"]:
        md.append(f"| Other utilities ({', '.join(r['research_categories'])}) | {c['other_utility_projects']} projects, "
                  f"near {c['opportunities_with_other_utilities']} opportunities |")
    md += ["", "## Top opportunities", "",
           "| # | Project A | Project B | Miles | Days apart | Same time | Other owners nearby |",
           "|---|---|---|---|---|---|---|"]
    for t in r["top"]:
        md.append(f"| {t['rank']} | {t['a']['name']} ({t['a']['owner']}) | {t['b']['name']} ({t['b']['owner']}) | {t['distance_mi']} | "
                  f"{_days(t)} | {_yes(t['built_at_same_time'])} | {len(t['other_utilities']) or ''} |")
    for t in r["top"][:3]:
        md += ["", f"### #{t['rank']} {t['a']['name']} and {t['b']['name']}", "",
               f"{t['a']['owner']} and {t['b']['owner']}: {t['distance_mi']} miles apart, {_days(t)} days "
               f"between in-service dates ({t['a']['in_service']} and {t['b']['in_service']}).",
               f"Sources: {t['a']['source']}; {t['b']['source']}."]
        if t["shared_items"]:
            md.append(f"Could share: {', '.join(t['shared_items'])}.")
        if t.get("savings_high"):
            md.append(f"Estimated savings: {_money(t['savings_low'])} to {_money(t['savings_high'])} "
                      "(assumption range, see Method).")
        if t["analysis"]:
            md += ["", t["analysis"]["text"]]
        if t["joint_agenda"]:
            md += ["", "Joint agenda:", "", t["joint_agenda"]["text"]]
        for o in t["other_utilities"]:
            md.append(f"- Also nearby: {o['owner']}, {o['name']} ({o['category']}), {o['miles_to_a']} and "
                      f"{o['miles_to_b']} miles from the two projects.")
    md += ["", "## Recommended next steps", "", r["next_steps"]["text"], "", _byline(r["next_steps"]), ""]
    if r["issues"]:
        md += ["## Data issues the pipeline caught", ""] + [f"- {i['title']} ({i['source']})" for i in r["issues"]] + [""]
    md += ["## Method", ""] + [f"- {m}" for m in r["method"]] + [""]
    return "\n".join(md)
