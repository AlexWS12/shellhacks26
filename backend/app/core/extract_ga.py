# Georgia IRP Vol. 3: the ten-year plan table plus one detail page per project,
# joined on the TEAMS number.

import re
from dataclasses import dataclass

from app.core.models import Check, Project
from app.core.normalize import parse_date, title_case

ROW_RE = re.compile(r"^\s{1,6}(\d{3})\s+(\d{4})\s+(\d{5})\s+(.+?)\s{2,}(\d{1,2}/\d{1,2}/\d{4})\s+([A-Z]{2,5})\s+REDACTED")
CONT_RE = re.compile(r"^\s{20,}(\S.*?)\s*$")
TEAMS_RE = re.compile(r"Teams #\s*(\d{5})")
DATES_RE = re.compile(r"Need Date\s+(\d{1,2}/\d{1,2}/\d{4})(?:\s+Start Date\s+(\d{1,2}/\d{1,2}/\d{4}))?")


@dataclass
class TableRow:
    zone: str
    year: str
    teams: str
    name: str
    need: str
    sponsor: str
    page: int


@dataclass
class DetailPage:
    teams: str
    page: int
    title: str
    need: str
    start: str | None
    description: str


def parse_table(pages: list[str]) -> list[TableRow]:
    rows: list[TableRow] = []
    for i, text in enumerate(pages):
        if "Ten-Year Plan (2025-2034)" not in text or "REDACTED" not in text:
            continue
        current: TableRow | None = None
        for line in text.splitlines():
            m = ROW_RE.match(line)
            if m:
                current = TableRow(zone=m[1], year=m[2], teams=m[3], name=m[4].strip(), need=m[5], sponsor=m[6], page=i + 1)
                rows.append(current)
                continue
            c = CONT_RE.match(line)
            if current and c and not line.lstrip().startswith(("Total", "2024 GA ITS")) and len(c[1]) < 45:
                current.name = f"{current.name} {c[1]}"
            elif line.strip().startswith("Total") or not line.strip():
                current = None if line.strip().startswith("Total") else current
    return rows


def parse_details(pages: list[str]) -> dict[str, DetailPage]:
    found: dict[str, DetailPage] = {}
    for i, text in enumerate(pages):
        m = TEAMS_RE.search(text)
        if not m or "Description" not in text:
            continue
        lines = text.splitlines()
        at = next(k for k, ln in enumerate(lines) if TEAMS_RE.search(ln))
        title_lines: list[str] = []
        for ln in reversed(lines[:at]):
            s = ln.strip()
            if not s or s.endswith("employees.") or "CRITICAL ENERGY" in s or s == "PUBLIC DISCLOSURE":
                if title_lines:
                    break
                continue
            title_lines.insert(0, s)
        d = DATES_RE.search(text)
        desc_at = next((k for k, ln in enumerate(lines) if ln.strip() == "Description"), None)
        end_at = next((k for k, ln in enumerate(lines) if ln.strip() == "Supporting Statement"), len(lines))
        description = " ".join(ln.strip() for ln in lines[(desc_at or at) + 1 : end_at] if ln.strip())
        found[m[1]] = DetailPage(teams=m[1], page=i + 1, title=" ".join(title_lines), need=d[1] if d else "",
                                 start=d[2] if d and d[2] else None, description=description)
    return found


def to_project(row: TableRow, det: DetailPage | None, source_file: str) -> tuple[Project, list[Check]]:
    checks: list[Check] = []
    pid = f"GA-{row.teams}"
    need = parse_date(det.need) if det and det.need else None
    table_need = parse_date(row.need)
    isd = need or table_need
    if isd is None:
        raise ValueError(f"TEAMS {row.teams}: no need date")
    start = parse_date(det.start) if det and det.start else None
    name = det.title if det and len(det.title) >= len(row.name) - 3 else row.name
    miles = re.search(r"(\d+(?:\.\d+)?)\s*miles", det.description if det else "", re.I)
    if det is None:
        checks.append(Check(id=f"nodetail:{pid}", level="warn", rule="missing_detail", title="No project page",
                            detail=f"TEAMS {row.teams} is in the table but has no detail page.",
                            source=f"GA IRP Vol. 3 p.{row.page}", project_id=pid))
    project = Project(
        id=pid, utility="GA", sponsor=row.sponsor, name=title_case(name), description=det.description if det else "",
        status="Planned", in_service_date=isd.isoformat(), in_service_raw=det.need if det else row.need,
        build_start=start.isoformat() if start else None,
        build_active_from=start.isoformat() if start else None, cost_total=None, cost_by_year=None,
        miles=float(miles.group(1)) if miles else None, zone=row.zone,
        source_file=source_file, source_page=det.page if det else row.page,
        source_ref=f"TEAMS {row.teams}, zone {row.zone}",
    )
    return project, checks
