# Behind the Sources menu: add a utility's filing (upload, describe), read it with the AI reader, review what it
# found, activate it, and take it out again. Built-in sources can't be changed here.
#
# Review rules: every row is accepted or rejected before activation. A row with no in-service date can't be compared
# (no day gap) and a row with no endpoints can't be placed; both are labeled. Accepted rows with a date become the
# source's projects; undated ones stay in the review until someone adds a date. An edit is stored with the value it
# replaced as human_override in that field's provenance.

import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from app import config
from app.core.endpoints import clean_endpoint
from app.core.models import Endpoint, Project
from app.core.normalize import parse_money
from app.core.pdftext import read_pdf
from app.core.sheets import parse_when
from app.store import sources, submissions
from app.store.sources import Source

CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,15}$")
EDITABLE = {"name", "description", "status", "in_service_date", "start_date", "cost_total", "voltage_kv", "endpoints"}
MAX_OPERATORS = 20


class AdminError(Exception):
    def __init__(self, message: str, status: int = 400, **details: Any) -> None:
        super().__init__(message)
        self.message, self.status, self.details = message, status, details


def _states() -> set[str]:
    p = config.GEO_DIR / "state_bounds.json"
    return set(json.loads(p.read_text(encoding="utf-8"))["states"]) if p.exists() else {"SC", "GA"}


def meta() -> dict[str, Any]:
    return {"states": sorted(_states()), "max_upload_mb": config.UPLOAD_MAX_MB, "page_images": bool(shutil.which("pdftoppm"))}


def _upload_path(sha: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
        raise AdminError("That upload isn't known. Upload the file again.")
    return Path(config.UPLOADS_DIR) / f"{sha}.pdf"


def _same_file(sha: str) -> Source | None:
    return next((s for s in sources.all_sources() if s.file_sha256 == sha), None)


def upload(filename: str, data: bytes) -> dict[str, Any]:
    limit = int(config.UPLOAD_MAX_MB * 1024 * 1024)
    if len(data) > limit:
        raise AdminError(f"Files up to {config.UPLOAD_MAX_MB:g} MB.", 413)
    if not data.startswith(b"%PDF-"):
        raise AdminError("That isn't a PDF. Filings are added as PDFs; spreadsheets and links have their own form.")
    sha = hashlib.sha256(data).hexdigest()
    if (same := _same_file(sha)) is not None:
        raise AdminError(f"This filing is already a source: {same.display_name} ({same.code}).", 409, source_id=same.id)
    path = _upload_path(sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)
    try:
        doc = read_pdf(path, 120, 300)
    except Exception as e:
        path.unlink(missing_ok=True)
        raise AdminError(f"The PDF can't be read ({type(e).__name__}).") from None
    chars = sum(len(t.strip()) for t in doc.pages)
    return {"upload_id": sha, "filename": Path(filename).name[:120] or "filing.pdf", "size": len(data),
            "pages": len(doc.pages), "has_text": chars > 200 * max(1, len(doc.pages)) // 10}


def create(upload_id: str, filename: str, display_name: str, code: str, states: list[str], operators: list[str],
           pages: str | None) -> Source:
    path = _upload_path(upload_id)
    if not path.exists():
        raise AdminError("That upload isn't here any more. Upload the file again.")
    if (same := _same_file(upload_id)) is not None:
        raise AdminError(f"This filing is already a source: {same.display_name} ({same.code}).", 409, source_id=same.id)
    name = " ".join((display_name or "").split())
    if not 2 <= len(name) <= 80:
        raise AdminError("Give the utility's name (2 to 80 characters).")
    code = sources.owner_code(code or "")
    if not CODE_RE.match(code):
        raise AdminError("The short code is 2 to 16 letters, digits or dashes, like SCPSA or DUKE-EP.")
    if (taken := next((s for s in sources.all_sources() if s.code == code), None)) is not None:
        raise AdminError(f"The code {code} is already used by {taken.display_name}. Pick another.")
    picked = [s.upper() for s in states or []]
    if not picked or any(s not in _states() for s in picked):
        raise AdminError("Pick the state or states the filing covers.")
    ops = [" ".join(o.lower().split()) for o in operators or [] if o and o.strip()]
    if len(ops) > MAX_OPERATORS or any(len(o) > 60 for o in ops):
        raise AdminError(f"Up to {MAX_OPERATORS} operator names, 60 characters each.")
    if pages:
        from app.readers.ai_reader import parse_pages

        try:
            parse_pages(pages, len(read_pdf(path, 120, 300).pages))
        except ValueError as e:
            raise AdminError(f"Page range: {e}") from None
    return sources.add(code, name, picked, "ai", status="draft", osm_operator_patterns=list(dict.fromkeys(ops)),
                       file_path=sources._rel(path),  # relative to the data directory when it's inside it
                       display={"origin": "upload", "page_range": pages or None, "original_filename": filename,
                                "card_detail": f"Uploaded filing: {filename}", "cite": "page"})


def _uploaded(source_id: str) -> Source:
    s = sources.get(source_id)
    if s is None:
        raise AdminError("No source with that id.", 404)
    if s.builtin:
        raise AdminError("Built-in sources can't be changed here.", 403)
    return s


# ---- review

def _flags(p: dict[str, Any]) -> list[str]:
    return [f for f, missing in (("in_service_date", not p.get("in_service_date")), ("endpoints", not p.get("endpoints")))
            if missing]


def review(source_id: str) -> dict[str, Any]:
    s = _uploaded(source_id)
    d = sources.load_draft(source_id)
    if d is None:
        raise AdminError("Nothing has been read yet. Run the extraction first.", 404)
    rows = d.get("review", {})
    for p in d["projects"]:
        r = rows.setdefault(p["id"], {"status": "pending"})
        r["incomplete"] = _flags(p)
    counts = {k: sum(1 for r in rows.values() if r["status"] == k) for k in ("pending", "accepted", "rejected")}
    return {**d, "source": sources.public(s), "review": rows, "counts": counts,
            "ready": counts["pending"] == 0 and counts["accepted"] > 0}


def _save(source_id: str, d: dict[str, Any]) -> None:
    for k in ("source", "counts", "ready"):
        d.pop(k, None)
    sources.save_draft(source_id, d)


def edit(source_id: str, project_id: str, field: str, value: Any) -> dict[str, Any]:
    if field not in EDITABLE:
        raise AdminError(f"{field} can't be edited here.")
    d = review(source_id)
    p = next((x for x in d["projects"] if x["id"] == project_id), None)
    if p is None:
        raise AdminError("No such row.", 404)
    text = " ".join(str(value if value is not None else "").split())
    before: Any
    if field in ("name", "description", "status"):
        if field == "name" and not text:
            raise AdminError("A project needs a name.")
        before, p[field] = p.get(field), text[:2000]
    elif field == "in_service_date":
        iso, precision = parse_when(text, end=True)
        if not iso:
            raise AdminError("That's not a date code can read. Try 12/31/2027, June 2028 or 2029.")
        before = p.get("in_service_raw") or p.get("in_service_date")
        p.update(in_service_date=iso, date_precision=precision, in_service_raw=text)
    elif field == "start_date":
        iso, _ = parse_when(text, end=False) if text else (None, None)
        if text and not iso:
            raise AdminError("That's not a date code can read.")
        before, p["build_start"] = p.get("build_start"), iso
    elif field == "cost_total":
        amount = parse_money(text) if text else None
        if text and amount is None:
            raise AdminError("That's not a dollar amount code can read, like $1,250,000.")
        before, p["cost_total"] = p.get("cost_total"), amount
    elif field == "voltage_kv":
        m = re.search(r"\d+(?:\.\d+)?", text)
        before = (p.get("provenance") or {}).get("voltage_kv_parsed")
        p.setdefault("provenance", {})["voltage_kv_parsed"] = float(m[0]) if m else None
    else:  # endpoints: "A, B" or a list
        names = value if isinstance(value, list) else re.split(r"\s*[,;]\s*|\s+[-–]\s+", text)
        ends = [e for e in (clean_endpoint(str(n)) for n in names) if e][:2]
        before, p["endpoints"] = [e["name"] for e in p.get("endpoints", [])], [Endpoint(name=e).model_dump() for e in ends]
    prov = p.setdefault("provenance", {})
    key = {"start_date": "start_date"}.get(field, field)
    old = prov.get(key) if isinstance(prov.get(key), dict) else {}
    prov[key] = {
        **(old or {}), "human_override": {"value": value, "previous": before, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}}
    d["review"][project_id]["edited"] = True
    Project(**p)  # still a valid project
    _save(source_id, d)
    return review(source_id)


def decide(source_id: str, project_id: str | None, status: str) -> dict[str, Any]:
    # One row (accepted / rejected / pending), or every pending row with project_id None ("accept all").
    if status not in ("accepted", "rejected", "pending"):
        raise AdminError("Unknown decision.")
    d = review(source_id)
    rows = d["review"]
    if project_id is None:
        for r in rows.values():
            if r["status"] == "pending":
                r["status"] = status
    elif project_id in rows:
        rows[project_id]["status"] = status
    else:
        raise AdminError("No such row.", 404)
    _save(source_id, d)
    return review(source_id)


def publishable(d: dict[str, Any]) -> list[dict[str, Any]]:
    # Accepted rows that can be compared: they have an in-service date.
    return [p for p in d["projects"] if d["review"][p["id"]]["status"] == "accepted" and p.get("in_service_date")]


def activate(source_id: str) -> list[dict[str, Any]]:
    s = _uploaded(source_id)
    if s.reader != "ai":
        raise AdminError("Only filings read by the AI reader are activated here.")
    d = review(source_id)
    if d["counts"]["pending"]:
        raise AdminError(f"{d['counts']['pending']} rows still need a decision: accept them all, or reject the ones you don't want.")
    rows = publishable(d)
    if not rows:
        raise AdminError("No accepted row has an in-service date, so nothing could be compared. Add a date or accept a row.")
    sources.save_published(source_id, rows)
    sources.set_status(source_id, "active")
    return rows


def deactivate(source_id: str) -> int:
    from app.pipeline import remove_from_results

    s = _uploaded(source_id)
    if s.reader != "ai" or s.display.get("origin") != "upload":
        raise AdminError("Plans added as a spreadsheet or link are removed, not deactivated.")
    sources.set_status(source_id, "review" if sources.load_draft(source_id) else "draft")
    return remove_from_results(s)


def delete(source_id: str) -> int:
    from app.pipeline import remove_from_results

    s = _uploaded(source_id)
    removed = remove_from_results(s)
    if s.display.get("origin") == sources.ORIGIN:  # a plan from the spreadsheet / link form
        submissions.delete(source_id)
        sources.all_sources()  # the sync drops its row
        return removed
    sources.remove(source_id)
    shutil.rmtree(sources.draft_path(source_id).parent, ignore_errors=True)
    if s.file_sha256 and not _same_file(s.file_sha256):
        _upload_path(s.file_sha256).unlink(missing_ok=True)
        for png in (Path(config.UPLOADS_DIR) / "pages").glob(f"{s.file_sha256[:16]}-p*.png"):  # its page pictures
            png.unlink(missing_ok=True)
    return removed


# ---- pages, for the review's thumbnail and highlighted text

def _file(s: Source) -> Path:
    return config.DATA_DIR / (s.file_path or "")


def page_text(source_id: str, n: int) -> str:
    s = _uploaded(source_id)
    pages = read_pdf(_file(s), 120, 300).pages
    if not 1 <= n <= len(pages):
        raise AdminError("No such page.", 404)
    return pages[n - 1]


def page_image(source_id: str, n: int) -> Path:
    s = _uploaded(source_id)
    if not shutil.which("pdftoppm"):
        raise AdminError("Page images need poppler's pdftoppm on the server.", 404)
    out = Path(config.UPLOADS_DIR) / "pages" / f"{(s.file_sha256 or s.id)[:16]}-p{n}"
    png = out.with_suffix(".png")
    if not png.exists():
        png.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["pdftoppm", "-f", str(n), "-l", str(n), "-png", "-scale-to", "560", "-singlefile",
                            str(_file(s)), str(out)], capture_output=True, timeout=60)
        if r.returncode or not png.exists():
            raise AdminError("That page can't be drawn.", 404)
    return png
