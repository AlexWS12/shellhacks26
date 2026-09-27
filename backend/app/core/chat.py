# The facts a person's question is answered from. Code gathers them from the finished run; the model only reads
# them. Nothing here calls a model. Whole-run facts plus every research record, capped so the prompt stays small.

from typing import Any

from app import config
from app.core.overlap import Filters
from app.store import dataset

MAX_CONTEXT_CHARS = 26000  # ~6.5k tokens: room for every research record plus the top opportunities and the method
MAX_RESEARCH = 150  # research records put in the prompt; the rest are counted
MAX_TOP = 10
MAX_OTHERS_PER_OPPORTUNITY = 4  # other owners named under one opportunity; the rest are counted
MAX_LINE = 240

SYSTEM = (
    "You answer a planner's questions about one finished pipeline run, using ONLY the facts below. "
    "The facts cover the projects and coordination opportunities the pipeline found and the research team's "
    "records about other owners' nearby projects. If a question is not covered by the facts, say so plainly "
    "instead of guessing. Cite research records by owner and project name. Never invent numbers, dates, "
    "distances or sources. Be concise and concrete."
)

CATEGORY = {"electric": "electric", "gas": "gas", "roads_water": "roads and water"}


def _filters() -> Filters:
    # The API's default view: everything at town level or better, closest first.
    return Filters(all_sponsors=False, min_confidence="town", hide_finished=False, today=config.TODAY, sort="distance")


def _counts(cur: Any) -> dict[str, Any]:
    report = cur.report or {}
    if report.get("counts"):
        return report["counts"]
    projects = list(cur.projects.values())
    overlaps = cur.overlaps(_filters()) if projects else []
    return {
        "projects": len(projects),
        "placed": sum(1 for p in projects if p.lat is not None),
        "unlocated": sum(1 for p in projects if p.lat is None),
        "opportunities": len(overlaps),
        "other_utility_projects": len(cur.research),
        "other_utility_placed": sum(1 for r in cur.research if r.lat is not None),
    }


def _top_lines(cur: Any) -> list[str]:
    report = cur.report or {}
    out: list[str] = []
    if report.get("top"):
        for t in report["top"][:MAX_TOP]:
            a, b = t.get("a", {}), t.get("b", {})
            nearby = [f"{o['owner']}: {o['name']}" for o in t.get("other_utilities", [])]
            others = nearby[:MAX_OTHERS_PER_OPPORTUNITY]
            line = (f"#{t['rank']} {a.get('name')} ({a.get('owner')}) <-> {b.get('name')} ({b.get('owner')}): "
                    f"{t['distance_mi']} mi closest, {t.get('time_gap_days')} days apart")
            if t.get("tier"):
                line += f", tier {t['tier']}"
            if others:
                line += "; also nearby: " + "; ".join(others)
                if len(nearby) > len(others):
                    line += f" (+{len(nearby) - len(others)} more)"
            out.append(line[:MAX_LINE])
        return out
    for o in cur.overlaps(_filters())[:MAX_TOP]:
        a, b = cur.projects.get(o.project_a), cur.projects.get(o.project_b)
        if a and b:
            out.append(f"#{o.rank} {a.name} <-> {b.name}: {o.distance_mi} mi closest, {o.time_gap_days} days apart, "
                       f"tier {o.tier}")
    return out


def _research_lines(cur: Any) -> tuple[list[str], int]:
    by_cat: dict[str, list] = {}
    for r in cur.research:
        by_cat.setdefault(r.category, []).append(r)
    lines: list[str] = []
    shown = 0
    for cat in ("electric", "gas", "roads_water"):
        recs = by_cat.get(cat, [])
        if not recs:
            continue
        lines.append(f"{CATEGORY.get(cat, cat).title()} ({len(recs)}):")
        for r in recs:
            if shown >= MAX_RESEARCH:
                break
            urls = ", ".join(s.url for s in r.sources[:2])
            # Some records put a sentence in the date field; keep only a short, single-line value.
            date = " ".join(str(r.in_service or r.start or r.date_quote or "no date").split())[:60]
            lines.append(f"- {r.utility} | {r.name} | status {r.status} | in service {date} | {urls}"[:MAX_LINE])
            shown += 1
    return lines, shown


def build_context() -> str:
    # The finished run's facts as plain text, bounded to MAX_CONTEXT_CHARS.
    cur = dataset.CURRENT
    report = cur.report or {}
    parts: list[str] = [f"Run: {cur.run_id or 'unknown'} (as of {config.TODAY})."]
    parts.append("Counts: " + ", ".join(f"{k}={v}" for k, v in _counts(cur).items()))
    if report.get("tiers"):
        parts.append("Distance tiers: " + ", ".join(f"{k}={v}" for k, v in report["tiers"].items()))
    if report.get("owners"):
        parts.append("Projects by owner: " + ", ".join(f"{k}={v}" for k, v in report["owners"].items()))
    if report.get("research_categories"):
        parts.append("Research categories this run: " + ", ".join(report["research_categories"]))
    # Research first: questions are usually about the other owners, and this must survive the length cap.
    research, shown = _research_lines(cur)
    if research:
        parts.append(f"\nOther utilities' projects (research team; showing {shown} of {len(cur.research)}):\n"
                     + "\n".join(research))
    elif cur.research:
        parts.append(f"\nOther utilities' projects: {len(cur.research)} on file, none shown this run.")
    top = _top_lines(cur)
    if top:
        parts.append("\nOpportunities (closest first):\n" + "\n".join(top))
    issues = [c for c in cur.checks if c.level in ("error", "warn")]
    if issues:
        parts.append("\nData issues: " + "; ".join(f"{i.title} ({i.source})" for i in issues[:20]))
    if report.get("method"):
        parts.append("\nMethod:\n" + "\n".join(f"- {m}" for m in report["method"]))
    text = "\n".join(parts)
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS] + "\n[...facts truncated...]"
    return text


def build_prompt(context: str, question: str, history: list[dict[str, str]]) -> str:
    turns = [f"{'Question' if t.get('role') == 'user' else 'Answer'}: {t.get('text', '')}" for t in history]
    convo = ("\n\nEarlier in this conversation:\n" + "\n".join(turns)) if turns else ""
    return f"{context}{convo}\n\nQuestion: {question}\nAnswer:"
