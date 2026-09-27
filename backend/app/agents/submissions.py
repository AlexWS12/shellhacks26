# One Reader per submitted plan. Spreadsheets are read by code with the column mapping the person confirmed.
# PDFs and web pages are read page by page by Gemini, which may only copy what the page says; code parses the
# dates, and every project keeps its file and page. A bad submission never stops the rest of the run.

import asyncio
import re
import time
from typing import Any

from app.clients import gemini
from app.core.htmltext import html_to_text, pages
from app.core.models import Check, Endpoint, Project
from app.core.pdftext import read_pdf
from app.core.sheets import MAX_ROWS, parse_when, read_table, to_project
from app.runtime.agent import Agent, AgentSpec, Ctx
from app.store.submissions import Submission, file_of

MAX_PAGES = 40  # Gemini pages per document per run
MAX_DOC_PAGES = 150  # pages pdftotext reads from a submitted PDF
READER_BUDGET_S = 150.0  # stop reading (keeping what was read) well before the 240 s agent timeout
YEAR = re.compile(r"\b20[2-4]\d\b")
FREEFORM_SCHEMA = {
    "type": "object",
    "properties": {"projects": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "project_id": {"type": ["string", "null"]},
        "in_service": {"type": ["string", "null"], "description": "completion / in-service date exactly as written"},
        "start": {"type": ["string", "null"]}, "endpoint_a": {"type": ["string", "null"]},
        "endpoint_b": {"type": ["string", "null"]}, "description": {"type": ["string", "null"]},
        "status": {"type": ["string", "null"]}},
        "required": ["name", "in_service"]}}},
    "required": ["projects"],
}
def _n(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


FREEFORM_SYSTEM = ("Extract every specific planned construction project listed on this page of a utility's plan. Copy "
                   "names, IDs and dates exactly as written; never invent or estimate. Put the completion or in-service "
                   "date as written (for example '12/31/2027', 'June 2028' or '2029'). If a project runs between two "
                   "named places, give them as endpoint_a and endpoint_b. Return an empty list if the page lists none.")


class SubmissionReader(Agent):
    def __init__(self, sub: Submission) -> None:
        self.sub = sub
        sheet = sub.kind == "spreadsheet"
        what = "spreadsheet" if sheet else ("web page" if sub.stored.endswith(".html") else "PDF")
        self.spec = AgentSpec(f"extract_{sub.id}", f"Reader · {sub.label}",
                              f"Reads {sub.owner}'s submitted {what}", ["code"] if sheet else ["gemini"],
                              engine="Parser" if sheet else "Gemini")

    def source(self) -> dict[str, Any]:
        return {"id": self.sub.id, "label": self.sub.owner, "detail": f"Submitted {self.sub.kind}: {self.sub.url or self.sub.filename}",
                "file": self.sub.filename, "total": 0, "owner_key": self.sub.owner_key}

    async def run(self, ctx: Ctx) -> str:
        try:
            if self.sub.kind == "spreadsheet":
                return await self.read_sheet(ctx)
            return await self.read_text(ctx)
        except Exception as e:  # a broken upload must not stop the Dominion-Georgia results
            ctx.log(f"{self.spec.name} stopped: {type(e).__name__}: {e}")
            self._check(ctx, "warn", "submission_unreadable", f"Couldn't read {self.sub.filename}",
                        f"{type(e).__name__}: {e}. The rest of the run continues without this plan.")
            return f"stopped: {type(e).__name__}"

    def _check(self, ctx: Ctx, level: str, rule: str, title: str, detail: str) -> None:
        ctx.board.pending_checks.append(Check(id=f"{rule}:{self.sub.id}", level=level, rule=rule, title=title,  # type: ignore[arg-type]
                                              detail=detail, source=f"Submitted: {self.sub.filename}"))

    def _add(self, ctx: Ctx, p: Project, read: int, total: int, current: str) -> None:
        ctx.board.projects[p.id] = p
        ctx.emit("project.extracted", source_id=self.sub.id, project=p.model_dump(), actor=p.extracted_by)
        ctx.emit("source.progress", source_id=self.sub.id, read=read, total=total, current=current)

    async def read_sheet(self, ctx: Ctx) -> str:
        s = self.sub
        async with ctx.tool("read_table", {"file": s.filename, "columns": s.mapping}) as out:
            header, rows = await asyncio.to_thread(read_table, file_of(s))
            out["summary"] = f"{len(rows)} rows, {len(header)} columns"
        ctx.emit("source.opened", source_id=s.id, pages=len(rows), method="spreadsheet")
        done, skipped, approx = 0, [], 0
        if len(rows) > MAX_ROWS:
            self._check(ctx, "warn", "submitted_rows_capped", f"Only the first {MAX_ROWS} rows of {s.filename} were read",
                        f"Plans are read up to {MAX_ROWS} projects so every run stays within its time limit.")
            rows = rows[:MAX_ROWS]
        for i, row in rows:
            p, why = to_project(s, s.mapping, header, row, i)
            if p is None:
                skipped.append(f"row {i}: {why}")
                continue
            if p.id in ctx.board.projects:  # same ID twice in the file: keep both, make the second unique
                p.id = f"{p.id}-r{i}"
            approx += p.date_precision in ("year", "month")
            done += 1
            self._add(ctx, p, done, len(rows), f"row {i}: {p.name}")
            await ctx.pace(0.03)
        if skipped:
            self._check(ctx, "warn", "submitted_rows_skipped", f"{_n(len(skipped), 'row')} skipped in {s.filename}",
                        "The pipeline caught rows it couldn't use: " + "; ".join(skipped[:6]) + ("; ..." if len(skipped) > 6 else ""))
        if approx:
            self._check(ctx, "info", "submitted_dates_approx", f"Year or month only in {s.filename}",
                        f"{approx} projects give only a year or month; day gaps use the period's last day.")
        ctx.log(f"{self.spec.name}: {_n(done, 'project')} from {s.owner}'s spreadsheet"
                + (f", {_n(len(skipped), 'row')} skipped." if skipped else "."))
        return f"{_n(done, s.owner + ' project')}" + (f", {_n(len(skipped), 'row')} skipped" if skipped else "")

    async def read_text(self, ctx: Ctx) -> str:
        s = self.sub
        path = file_of(s)
        async with ctx.tool("read_document", {"file": s.filename}) as out:
            if path.suffix == ".pdf":
                doc = await asyncio.to_thread(read_pdf, path, 60, MAX_DOC_PAGES)
                texts, method = doc.pages, doc.method
            else:
                texts, method = pages(html_to_text(path.read_text(encoding="utf-8", errors="replace"))), "html"
            out["summary"] = f"{len(texts)} pages via {method}"
        ctx.emit("source.opened", source_id=s.id, pages=len(texts), method=method)
        if not gemini.enabled():
            self._check(ctx, "warn", "submission_needs_gemini", f"{s.filename} was not read",
                        "PDFs and web pages are read by Gemini, which is not configured. Spreadsheets work without it.")
            return "not read: Gemini is not configured"
        candidates = [(n, t) for n, t in enumerate(texts, start=1) if YEAR.search(t)]
        if len(candidates) > MAX_PAGES:
            ctx.log(f"{self.spec.name}: reading the first {MAX_PAGES} of {len(candidates)} pages that mention a year.")
            candidates = candidates[:MAX_PAGES]
        done, failures, skipped = 0, 0, 0
        started = time.monotonic()
        for n, text in candidates:
            if time.monotonic() - started > READER_BUDGET_S:
                self._check(ctx, "warn", "submission_partial", f"{s.filename} was read up to page {n - 1}",
                            f"Reading stopped after {READER_BUDGET_S:.0f} s so the run stays within its time limit; "
                            "later pages are read on a later run from the cache.")
                break
            async with ctx.tool("gemini_extract_page", {"page": n}, actor="gemini") as out:
                left = READER_BUDGET_S - (time.monotonic() - started)
                try:  # one call with all its retries must still fit in what is left of the budget
                    res = await asyncio.wait_for(gemini.generate_json(FREEFORM_SYSTEM, text[:12000], FREEFORM_SCHEMA),
                                                 timeout=max(5.0, left))
                except TimeoutError:
                    res = None
                out["summary"] = f"{len(res['data'].get('projects', []))} projects" if res else "Gemini call failed"
            if not res:
                failures += 1
                if failures >= 3 and done == 0:
                    self._check(ctx, "warn", "submission_needs_gemini", f"{s.filename} was not read",
                                "Gemini failed three times in a row (quota or outage). Try again later.")
                    return "not read: Gemini unavailable"
                continue
            ctx.tokens += res.get("input_tokens", 0) + res.get("output_tokens", 0)
            for k, item in enumerate(res["data"].get("projects", [])):
                p = self._freeform_project(item, n, k)
                if p is None:
                    skipped += 1
                    continue
                if p.id in ctx.board.projects:  # the same ID listed twice: keep both
                    p.id = f"{p.id}-{done + 1}"
                done += 1
                self._add(ctx, p, done, len(candidates), f"p.{n}: {p.name}")
            await ctx.pace(0.05)
        if skipped:
            self._check(ctx, "info", "submitted_rows_skipped", f"{_n(skipped, 'listed item')} skipped in {s.filename}",
                        "The pipeline caught items without a name or a readable in-service date.")
        ctx.log(f"{self.spec.name}: {done} projects from {s.owner}'s document.")
        return f"{done} {s.owner} projects"

    def _freeform_project(self, item: dict[str, Any], page: int, k: int) -> Project | None:
        s = self.sub
        name = str(item.get("name") or "").strip()
        isd, precision = parse_when(item.get("in_service"), end=True)
        if not name or not isd:
            return None
        start, _ = parse_when(item.get("start"), end=False)
        ref = str(item.get("project_id") or "").strip()
        pid = f"{s.id}-p{page}-{re.sub(r'[^A-Za-z0-9]+', '', ref)[:16] or k + 1}"
        ends = [Endpoint(name=str(e).strip()) for e in (item.get("endpoint_a"), item.get("endpoint_b")) if e and str(e).strip()]
        return Project(id=pid, utility=s.owner_key, sponsor=s.owner, name=name[:200],
                       description=str(item.get("description") or "")[:1500], status=str(item.get("status") or "")[:60],
                       in_service_date=isd, in_service_raw=str(item.get("in_service")), date_precision=precision,
                       build_start=start, endpoints=ends, state=s.state, source_file=s.url or s.filename, source_page=page,
                       source_ref=f"p.{page}" + (f", ID {ref}" if ref else ""), extracted_by="gemini")
