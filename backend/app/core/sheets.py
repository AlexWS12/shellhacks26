# Submitted spreadsheets (CSV / XLSX): read the table, suggest a column mapping, turn rows into projects.
# Plain code: nothing is guessed. A row without a name or a readable in-service date is skipped with a reason.

import csv
import io
import re
from datetime import date, datetime
from pathlib import Path
from collections.abc import Iterator
from typing import Any

from app.core.models import Endpoint, Project
from app.core.normalize import excel_date, parse_date
from app.core.research import normalize_date

FIELDS: dict[str, tuple[str, bool]] = {  # field -> (label shown in the menu, required)
    "name": ("Project name", True),
    "in_service": ("In-service or completion date", True),
    "id": ("Project ID", False),
    "endpoint_a": ("From (endpoint A)", False),
    "endpoint_b": ("To (endpoint B)", False),
    "lat": ("Latitude", False),
    "lon": ("Longitude", False),
    "state": ("State (SC or GA)", False),
    "start": ("Construction start", False),
    "cost": ("Cost (USD)", False),
    "status": ("Status", False),
    "description": ("Description", False),
}
HINTS: dict[str, str] = {
    "in_service": r"in.?service|isd|need.?date|complet|energi[sz]|operation|\bcod\b|finish",
    "start": r"start|begin|construction.?(date|start)",
    "id": r"^(project.?)?(id|no|number|#|ref)$|teams|project.?id",
    "endpoint_a": r"^from|endpoint.?a|terminal.?a|origin|substation.?1",
    "endpoint_b": r"^to$|^to\b|endpoint.?b|terminal.?b|destination|substation.?2",
    "lat": r"^lat(itude)?$|latitude",
    "lon": r"^(lon|lng|long)(gitude)?$|longitude",
    "state": r"^state$|^st$",
    "cost": r"cost|budget|amount|\$",
    "status": r"status|phase",
    "description": r"descr|scope|summary|detail",
    "name": r"project.?name|^name$|title|^project$|facility|description of project",
}
REGION = (24.0, -92.0, 37.5, -75.0)  # south, west, north, east: submitted coordinates must fall in the Southeast
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


MAX_ROWS = 2000  # projects per submitted plan; the rest are reported, not read
MAX_COLS = 60
MAX_XLSX_UNZIPPED = 200 * 1024 * 1024  # refuse spreadsheets that expand to more than this (zip bombs)


def _xlsx_rows(path: Path) -> Iterator[list[Any]]:
    import zipfile

    import openpyxl

    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_XLSX_UNZIPPED:
            raise ValueError("That spreadsheet expands to more than 200 MB. Save the project list on its own and try again.")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for r in wb.worksheets[0].iter_rows(values_only=True, max_col=MAX_COLS):
            yield list(r)
    finally:
        wb.close()


def _csv_rows(path: Path) -> Iterator[list[Any]]:
    data = path.read_bytes()
    text = next((data.decode(enc) for enc in ("utf-8-sig", "cp1252") if _decodes(data, enc)), data.decode("latin-1"))
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    for r in csv.reader(io.StringIO(text), dialect):
        yield r[:MAX_COLS]


def read_table(path: Path, limit: int = MAX_ROWS) -> tuple[list[str], list[tuple[int, list[Any]]]]:
    # (header, [(row number as people see it in the file, cells)]). The header is the first row with two filled
    # cells in the first 20. Blank rows are skipped but keep counting, so row numbers match the file.
    rows_in = _xlsx_rows(path) if path.suffix.lower() == ".xlsx" else _csv_rows(path)
    header: list[str] | None = None
    rows: list[tuple[int, list[Any]]] = []
    for n, r in enumerate(rows_in, start=1):
        while r and (r[-1] is None or str(r[-1]).strip() == ""):
            r = r[:-1]  # XLSX rows come padded to MAX_COLS
        filled = sum(1 for c in r if str(c if c is not None else "").strip())
        if header is None:
            if filled >= 2:
                header = [str(c or "").strip() or f"Column {i + 1}" for i, c in enumerate(r)]
            elif n >= 20:
                break
            continue
        if filled:
            rows.append((n, (list(r) + [None] * len(header))[:len(header)]))
            if len(rows) > limit:
                break
    if header is None:
        raise ValueError("No table found: the file needs a header row and at least one project row.")
    return header, rows


def _decodes(data: bytes, enc: str) -> bool:
    try:
        data.decode(enc)
        return True
    except UnicodeDecodeError:
        return False


def suggest(columns: list[str]) -> dict[str, str]:
    # Best-effort mapping by header name; the person confirms or changes it in the menu.
    out: dict[str, str] = {}
    used: set[str] = set()
    for field, pattern in HINTS.items():
        for col in columns:
            if col not in used and re.search(pattern, col.strip().lower()):
                out[field] = col
                used.add(col)
                break
    return out


def validate_mapping(mapping: dict[str, str], columns: list[str]) -> dict[str, str]:
    clean = {f: c for f, c in mapping.items() if f in FIELDS and c}
    unknown = [c for c in clean.values() if c not in columns]
    if unknown:
        raise ValueError(f"Not a column in this file: {', '.join(unknown)}")
    missing = [FIELDS[f][0] for f, (_, req) in FIELDS.items() if req and f not in clean]
    if missing:
        raise ValueError(f"Choose a column for: {', '.join(missing)}")
    return clean


def parse_when(v: Any, end: bool = True) -> tuple[str | None, str | None]:
    # (ISO date, precision). Real dates, Excel serials, 12/31/2027, 2027-12-31, 2027-06, 'June 2027', 2027.
    if v is None or v == "":
        return None, None
    if isinstance(v, (datetime, date)):
        return (v.date() if isinstance(v, datetime) else v).isoformat(), "day"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 1900 <= v <= 2100 and float(v).is_integer():
            return normalize_date(str(int(v)), end)
        if 20000 <= v <= 80000:
            d = excel_date(v)
            return (d.isoformat(), "day") if d else (None, None)
        return None, None
    s = str(v).strip()
    if (d := parse_date(s)) is not None:
        return d.isoformat(), "day"
    iso, precision = normalize_date(s[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", s) else s, end)
    if iso:
        return iso, precision
    m = re.match(r"^([A-Za-z]{3})[a-z]*\.?\s+(\d{4})$", s)
    if m and m[1].lower() in MONTHS:
        return normalize_date(f"{m[2]}-{MONTHS[m[1].lower()]:02d}", end)
    return None, None


def _money(v: Any) -> int | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    s = re.sub(r"[,$\s]", "", str(v or ""))
    return int(float(s)) if re.fullmatch(r"\d+(\.\d+)?", s) else None


def _coord(v: Any, lo: float, hi: float) -> float | None:
    try:
        x = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return x if lo <= x <= hi else None


def cell(row: list[Any], header: list[str], mapping: dict[str, str], field: str) -> Any:
    col = mapping.get(field)
    if not col or col not in header:
        return None
    v = row[header.index(col)]
    return v.strip() if isinstance(v, str) else v


def to_project(sub: Any, mapping: dict[str, str], header: list[str], row: list[Any], rownum: int) -> tuple[Project | None, str]:
    # rownum is the row number people see in the file (header = 1).
    get = lambda f: cell(row, header, mapping, f)  # noqa: E731
    name = str(get("name") or "").strip()
    if not name:
        return None, "no project name"
    raw_date = get("in_service")
    isd, precision = parse_when(raw_date, end=True)
    if not isd:
        return None, f"in-service date '{raw_date}' is not a date" if raw_date not in (None, "") else "no in-service date"
    start, _ = parse_when(get("start"), end=False)
    ref = str(get("id") or "").strip()
    state = str(get("state") or "").strip().upper()
    state = state if state in ("SC", "GA") else sub.state
    lat, lon = _coord(get("lat"), -90, 90), _coord(get("lon"), -180, 180)
    if lat is not None and lon is not None and not (REGION[0] <= lat <= REGION[2] and REGION[1] <= lon <= REGION[3]):
        lat = lon = None  # outside the Southeast (often a missing minus sign): ignored, the names are used instead
    a, b = str(get("endpoint_a") or "").strip(), str(get("endpoint_b") or "").strip()
    if lat is not None and lon is not None:
        endpoints = [Endpoint(name=a or name, lat=lat, lon=lon, method="submitted", confidence="verified",
                              evidence={"file": sub.filename, "row": rownum, "note": "coordinates from the submitted file"})]
    else:
        endpoints = [Endpoint(name=n) for n in (a, b) if n]
    pid = f"{sub.id}-{re.sub(r'[^A-Za-z0-9]+', '', ref)[:20] or f'r{rownum}'}"
    return Project(
        id=pid, utility=sub.owner_key, sponsor=sub.owner, name=name[:200],
        description=str(get("description") or "")[:1500], status=str(get("status") or "")[:60],
        in_service_date=isd, in_service_raw=str(raw_date), date_precision=precision, build_start=start,
        cost_total=_money(get("cost")), endpoints=endpoints, state=state,
        source_file=getattr(sub, "url", None) or sub.filename, source_page=rownum, source_ref=f"row {rownum}" + (f", ID {ref}" if ref else ""),
        extracted_by="code",
    ), ""
