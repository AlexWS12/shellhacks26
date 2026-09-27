# DESC PDF: one project per page.

import re
from dataclasses import dataclass, field

from app.core.models import Check, Project
from app.core.normalize import DATE_RE, parse_date, parse_money

SOURCE = "DESC project list"
YEARS = ["prev", "2024", "2025", "2026", "2027", "2028"]
HEADINGS = ["Project ID", "Project Description", "Project Need", "Project Status",
            "Planned In-Service Date", "Estimated Project Cost"]


@dataclass
class DescPage:
    page: int
    name: str = ""
    pid: str = ""
    description: str = ""
    need: str = ""
    status: str = ""
    isd_raw: str = ""
    cost_cells: list[str] = field(default_factory=list)


def _section(lines: list[str], start: str, end: str | None) -> str:
    try:
        i = next(k for k, ln in enumerate(lines) if ln.strip() == start)
    except StopIteration:
        return ""
    j = len(lines)
    if end:
        j = next((k for k in range(i + 1, len(lines)) if lines[k].strip() == end), len(lines))
    return " ".join(ln.strip() for ln in lines[i + 1 : j] if ln.strip())


def parse_page(text: str, page: int) -> DescPage | None:
    if "Project ID" not in text or "5 Year Budget" not in text:
        return None
    lines = text.splitlines()
    budget = next(k for k, ln in enumerate(lines) if "5 Year Budget" in ln)
    pid_at = next(k for k, ln in enumerate(lines) if ln.strip() == "Project ID")
    p = DescPage(page=page)
    p.name = " ".join(ln.strip() for ln in lines[budget + 1 : pid_at] if ln.strip())
    p.pid = _section(lines, "Project ID", "Project Description")
    p.description = _section(lines, "Project Description", "Project Need")
    p.need = _section(lines, "Project Need", "Project Status")
    p.status = _section(lines, "Project Status", "Planned In-Service Date")
    p.isd_raw = _section(lines, "Planned In-Service Date", "Estimated Project Cost")
    cost_at = next((k for k, ln in enumerate(lines) if ln.strip() == "Estimated Project Cost"), None)
    if cost_at is not None:
        row = next((ln for ln in lines[cost_at + 1 :] if ln.strip().startswith("$")), "")
        p.cost_cells = row.split()
    return p


def project_id(pid: str) -> str:
    return "DESC-" + re.sub(r"[^A-Za-z0-9]", "", pid)


def to_project(p: DescPage, source_file: str) -> tuple[Project, list[Check]]:
    checks: list[Check] = []
    src = f"DESC PDF p.{p.page}"
    pid = project_id(p.pid)

    values = [parse_money(c) for c in p.cost_cells]
    by_year: dict[str, int | None] = dict(zip(YEARS, values[:6]))
    total = values[6] if len(values) > 6 else None
    bad = [c for c, v in zip(p.cost_cells, values) if v is None]
    if bad:
        checks.append(Check(id=f"money:{pid}", level="error", rule="malformed_money", title="Malformed cost value",
                            detail=f"{p.name}: a cost cell reads '{bad[0]}', which isn't a valid dollar amount. "
                                   "That year is stored as unknown; the total is kept.",
                            source=src, project_id=pid))
    elif total is not None and sum(v or 0 for v in values[:6]) != total:
        s = sum(v or 0 for v in values[:6])
        checks.append(Check(id=f"sum:{pid}", level="warn", rule="cost_sum", title="Yearly costs don't add up to the total",
                            detail=f"{p.name}: years sum to ${s:,}, total says ${total:,}. The gap may be spending after 2028.",
                            source=src, project_id=pid))

    dates = DATE_RE.findall(p.isd_raw)
    isd = parse_date(dates[-1]) if dates else None
    if isd is None:
        raise ValueError(f"no in-service date on page {p.page}: {p.isd_raw!r}")
    if len(dates) > 1:
        checks.append(Check(id=f"phased:{pid}", level="info", rule="phased_date", title="Two in-service dates",
                            detail=f"{p.name} is phased ({p.isd_raw}). Used the final phase.", source=src, project_id=pid))

    late = [y for y in YEARS[1:] if (by_year.get(y) or 0) > 0 and int(y) > isd.year]
    if late:
        amount = sum(by_year[y] or 0 for y in late)
        checks.append(Check(id=f"late:{pid}", level="warn", rule="spend_after_isd", title="Spending after the in-service date",
                            detail=f"{p.name}: in service {isd:%b %Y}, but ${amount:,} is budgeted in {', '.join(late)}.",
                            source=src, project_id=pid))

    spend_years = [int(y) for y in YEARS[1:] if (by_year.get(y) or 0) > 0]
    build_start = None
    if not by_year.get("prev"):
        build_start = f"{min(spend_years) if spend_years else isd.year}-01-01"
    # "Previous" spending = started before 2024, exact date unknown.
    active_from = build_start or "2024-01-01"

    miles = re.search(r"(\d+(?:\.\d+)?)[\s-]*miles?\b", f"{p.name} {p.description}", re.I)
    project = Project(
        id=pid, utility="DESC", sponsor="DESC", name=p.name, description=p.description, need_text=p.need,
        status=p.status, in_service_date=isd.isoformat(), in_service_raw=p.isd_raw, build_start=build_start,
        build_active_from=active_from,
        cost_total=total, cost_by_year=by_year, miles=float(miles.group(1)) if miles else None,
        source_file=source_file, source_page=p.page, source_ref=f"ID {p.pid}",
    )
    return project, checks
