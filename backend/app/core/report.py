# The final report. Code gathers every number and name; the Writer agent only adds two short pieces of prose
# (summary, next steps) on top, and those are checked for numbers that are not in these facts.

import io
from collections import Counter
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app import config
from app.core.costs import ASSUMPTIONS
from app.core.models import Project
from app.core.overlap import OVERLAP_CUTOFF_MI
from app.core.owners import owner_name
from app.runtime.board import Board

TOP_REPORT = 10
TIER_LABEL = {"touching": "touching", "row": "under 1 mi", "site": "under 5 mi", "crew": "under 25 mi", "none": ""}
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
            "rank": o.rank, "overlap_id": o.id, "distance_mi": o.distance_mi, "center_mi": o.center_mi, "tier": o.tier,
            "time_gap_days": o.time_gap_days,
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
        "tiers": {t: sum(1 for o in board.overlaps if o.tier == t) for t in ("touching", "row", "site", "crew")},
        "research_categories": [CATEGORY[c] for c in board.research_selected],
        "top": top,
        "issues": [{"level": c.level, "title": c.title, "source": c.source} for c in board.checks if c.level != "info"],
        "method": [
            "Distance: miles between the closest points of the two projects. A line is the straight segment between "
            "its two located ends (the filings give no routes); anything else is a point. An opportunity is a pair "
            "under 25 miles (40 km) apart.",
            "Tiers: touching or crossing must coordinate; under 1 mile can share right-of-way, access roads and "
            "permits; under 5 miles, laydown yards and deliveries; under 25 miles, crews and equipment. Crews, "
            "equipment, deliveries and outage timing count only while both are under construction.",
            "Benchmark: the known overlaps are measured center to center (midpoint of the located ends), as in "
            "Sperry's sample.",
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
        "top": [{k: t[k] for k in ("rank", "distance_mi", "tier", "time_gap_days", "built_at_same_time", "shared_level",
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
    llm = {"gemini": "Gemini", "claude": "Claude", "openai": "OpenAI"}.get(w["actor"])
    who = f"Written by {llm} from the facts in this report." if llm else "Template from the computed facts."
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
          f"| By closest distance | {r['tiers']['touching']} touching, {r['tiers']['row']} under 1 mi, "
          f"{r['tiers']['site']} under 5 mi, {r['tiers']['crew']} under 25 mi |",
          f"| Benchmark | {c['benchmark_passed']} of {c['benchmark_total']} known overlaps exact |",
          f"| Data issues | {c['issues_error']} errors, {c['issues_warn']} warnings, {c['issues_info']} notes |"]
    if r["research_categories"]:
        md.append(f"| Other utilities ({', '.join(r['research_categories'])}) | {c['other_utility_projects']} projects, "
                  f"near {c['opportunities_with_other_utilities']} opportunities |")
    md += ["", "## Top opportunities", "",
           "| # | Project A | Project B | Miles (closest) | Miles (centers) | Tier | Days apart | Same time | Other owners nearby |",
           "|---|---|---|---|---|---|---|---|---|"]
    for t in r["top"]:
        md.append(f"| {t['rank']} | {t['a']['name']} ({t['a']['owner']}) | {t['b']['name']} ({t['b']['owner']}) | {t['distance_mi']} | "
                  f"{t['center_mi']} | {TIER_LABEL[t['tier']]} | {_days(t)} | {_yes(t['built_at_same_time'])} | {len(t['other_utilities']) or ''} |")
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


def _pdf_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    dark = colors.HexColor("#1a2b3c")
    return {
        "title": ParagraphStyle("title", parent=base["Heading1"], fontSize=20, spaceAfter=4, textColor=dark),
        "asof": ParagraphStyle("asof", parent=base["Normal"], fontSize=9.5, textColor=colors.HexColor("#666666"),
                               spaceAfter=16),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=14, spaceBefore=16, spaceAfter=6, textColor=dark),
        "h3": ParagraphStyle("h3", parent=base["Heading3"], fontSize=11.5, spaceBefore=10, spaceAfter=4, textColor=dark),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=10, leading=14, spaceAfter=6),
        "byline": ParagraphStyle("byline", parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=8.5,
                                 leading=11, textColor=colors.HexColor("#777777"), spaceAfter=10),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontSize=8, leading=10.5),
        "cell_h": ParagraphStyle("cell_h", parent=base["Normal"], fontSize=8, leading=10.5, textColor=colors.white,
                                 fontName="Helvetica-Bold"),
    }


def _pdf_table(rows: list[tuple], widths: list[float], styles: dict[str, ParagraphStyle]) -> Table:
    data = [[Paragraph(xml_escape(str(cell)), styles["cell_h"]) for cell in rows[0]]]
    data += [[Paragraph(xml_escape(str(cell)), styles["cell"]) for cell in row] for row in rows[1:]]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a2b3c")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f8")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _pdf_bullets(items: list[str], styles: dict[str, ParagraphStyle]) -> ListFlowable:
    return ListFlowable([ListItem(Paragraph(xml_escape(i), styles["body"]), leftIndent=8) for i in items],
                        bulletType="bullet", start="circle")


def to_pdf(r: dict[str, Any]) -> bytes:
    # Same facts and wording as to_markdown(); laid out as a document instead of a text dump.
    # .get()/TIER_LABEL.get() guard the few fields a report built by an older pipeline version might lack
    # (e.g. "tiers" and a pair's "center_mi"/"tier"), so this never breaks on report data older than this code.
    s = _pdf_styles()

    def p(text: str, style: str = "body") -> Paragraph:
        return Paragraph(xml_escape(str(text)), s[style])

    story: list[Any] = [p(r["title"], "title"),
                        p(f"As of {r['as_of']}. Every number below comes from the pipeline's code.", "asof"),
                        p("Summary", "h2"), p(r["summary"]["text"]), p(_byline(r["summary"]).strip("_"), "byline")]

    c = r["counts"]
    glance = [("Measure", "Value"),
              ("Projects read", ", ".join(f"{n} {o}" for o, n in r["owners"].items())),
              ("On the map", f"{c['placed']} ({c['unlocated']} without a location)"),
              ("Pairs under 25 miles", f"{c['opportunities']} ({c['built_at_same_time']} built at the same time)")]
    tiers = r.get("tiers")
    if tiers:
        glance.append(("By closest distance", f"{tiers['touching']} touching, {tiers['row']} under 1 mi, "
                       f"{tiers['site']} under 5 mi, {tiers['crew']} under 25 mi"))
    glance += [("Benchmark", f"{c['benchmark_passed']} of {c['benchmark_total']} known overlaps exact"),
              ("Data issues", f"{c['issues_error']} errors, {c['issues_warn']} warnings, {c['issues_info']} notes")]
    if r["research_categories"]:
        glance.append((f"Other utilities ({', '.join(r['research_categories'])})",
                       f"{c['other_utility_projects']} projects, near {c['opportunities_with_other_utilities']} opportunities"))
    story += [p("At a glance", "h2"), _pdf_table(glance, [2.1 * inch, 4.9 * inch], s)]

    if r["top"]:
        rows = [("#", "Project A", "Project B", "Mi (closest)", "Mi (centers)", "Tier", "Days apart", "Same time",
                 "Other owners")]
        for t in r["top"]:
            rows.append((t["rank"], f"{t['a']['name']} ({t['a']['owner']})", f"{t['b']['name']} ({t['b']['owner']})",
                        t["distance_mi"], t.get("center_mi", ""), TIER_LABEL.get(t.get("tier"), ""), _days(t),
                        _yes(t["built_at_same_time"]), len(t["other_utilities"]) or ""))
        story += [p("Top opportunities", "h2"),
                 _pdf_table(rows, [0.3 * inch, 1.55 * inch, 1.55 * inch, 0.6 * inch, 0.6 * inch, 0.65 * inch,
                                   0.6 * inch, 0.55 * inch, 0.6 * inch], s)]
    else:
        story += [p("Top opportunities", "h2"),
                 p("No pairs from different owners under 25 mi in the default view.")]

    for t in r["top"][:3]:
        story.append(Spacer(1, 6))
        story.append(p(f"#{t['rank']} {t['a']['name']} and {t['b']['name']}", "h3"))
        story.append(p(f"{t['a']['owner']} and {t['b']['owner']}: {t['distance_mi']} miles apart, {_days(t)} days "
                       f"between in-service dates ({t['a']['in_service']} and {t['b']['in_service']})."))
        story.append(p(f"Sources: {t['a']['source']}; {t['b']['source']}."))
        if t["shared_items"]:
            story.append(p(f"Could share: {', '.join(t['shared_items'])}."))
        if t.get("savings_high"):
            story.append(p(f"Estimated savings: {_money(t['savings_low'])} to {_money(t['savings_high'])} "
                           "(assumption range, see Method)."))
        if t["analysis"]:
            story.append(p(t["analysis"]["text"]))
        if t["joint_agenda"]:
            story.append(p("Joint agenda", "h3"))
            story.append(p(t["joint_agenda"]["text"]))
        if t["other_utilities"]:
            story.append(_pdf_bullets([f"Also nearby: {o['owner']}, {o['name']} ({o['category']}), {o['miles_to_a']} "
                                       f"and {o['miles_to_b']} miles from the two projects." for o in t["other_utilities"]], s))

    lines = [ln[2:] if ln.startswith("- ") else ln for ln in r["next_steps"]["text"].splitlines() if ln.strip()]
    story += [p("Recommended next steps", "h2"), _pdf_bullets(lines, s), p(_byline(r["next_steps"]).strip("_"), "byline")]

    if r["issues"]:
        story += [p("Data issues the pipeline caught", "h2"),
                 _pdf_bullets([f"{i['title']} ({i['source']})" for i in r["issues"]], s)]

    story += [p("Method", "h2"), _pdf_bullets(r["method"], s)]

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#888888"))
        canvas.drawString(0.75 * inch, 0.5 * inch, "UtiliTies · coordination report")
        canvas.drawRightString(LETTER[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, topMargin=0.75 * inch, bottomMargin=0.75 * inch,
                            leftMargin=0.75 * inch, rightMargin=0.75 * inch, title=r["title"])
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
