from pathlib import Path

from app import config
from app.clients import models
from app.core import extract_desc, extract_ga
from app.core.models import Project
from app.core.pdftext import read_pdf
from app.core.sample import read_sample, sample_checks
from app.runtime.agent import Agent, AgentSpec, Ctx
from app.store.sources import Source

SAMPLE_SOURCE = {"id": "sample", "label": "Benchmark set", "detail": "10 projects with surveyed coordinates, 6 known overlaps",
                 "file": config.SAMPLE_XLSX.name, "total": 10}


def card(source: Source) -> dict:
    # The source's row in the pipeline panel.
    d = source.display
    return {"id": source.id, "label": d.get("card_label", source.display_name), "detail": d.get("card_detail", ""),
            "file": Path(source.file_path or "").name, "total": d.get("expected_total", 0), "source_id": source.id,
            "owner_key": source.utility_key}


def _file(source: Source) -> Path:
    return config.DATA_DIR / (source.file_path or "")


GEMINI_PROJECT_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "project_id": {"type": "string"}, "description": {"type": "string"},
                   "need": {"type": "string"}, "status": {"type": "string"},
                   "in_service_date": {"type": "string", "description": "MM/DD/YYYY, final phase if phased"}},
    "required": ["name", "project_id", "in_service_date"],
}


class SampleReader(Agent):
    spec = AgentSpec("sample", "Benchmark", "Loads 10 surveyed projects and 6 known overlaps",
                     ["code"], engine="Parser")

    async def run(self, ctx: Ctx) -> str:
        async with ctx.tool("read_xlsx", {"file": config.SAMPLE_XLSX.name}) as out:
            sample = read_sample(config.SAMPLE_XLSX)
            out["summary"] = f"{len(sample.projects)} projects, {len(sample.overlaps)} reference overlaps"
        ctx.board.sample = sample
        ctx.board.pending_checks.extend(sample_checks(sample))
        ctx.emit("sample.loaded", overlaps=[o.__dict__ for o in sample.overlaps],
                 projects=[{"ref_id": s.ref_id, "name": s.name, "center": s.center} for s in sample.projects.values()])
        ctx.emit("source.progress", source_id="sample", read=len(sample.projects), total=len(sample.projects),
                 current="Projects_Overlaps.xlsx")
        return f"{len(sample.projects)} surveyed projects, {len(sample.overlaps)} known overlaps"


class DescExtractor(Agent):
    # reader 'builtin:desc': Dominion's project list, one project per page.
    def __init__(self, source: Source) -> None:
        self.source, self.path = source, _file(source)
        self.spec = AgentSpec("extract_desc", f"Reader · {source.code}", f"Reads {source.code}'s 44-page project list, "
                              "one project per page", ["code", "gemini"], engine="Parser + Gemini",
                              roles=["extract_fallback"])

    async def run(self, ctx: Ctx) -> str:
        self.models: dict[str, str] = {}  # project id -> the model that read its page
        sid = self.source.id
        async with ctx.tool("pdftotext", {"file": self.path.name}) as out:
            pdf = read_pdf(self.path)
            out["summary"] = f"{len(pdf.pages)} pages via {pdf.method}"
        ctx.emit("source.opened", source_id=sid, pages=len(pdf.pages), method=pdf.method)
        done = 0
        for i, text in enumerate(pdf.pages, start=1):
            project = None
            try:
                raw = extract_desc.parse_page(text, i)
                if raw is None:
                    continue
                project, checks = extract_desc.to_project(raw, self.path.name)
                ctx.board.pending_checks.extend(checks)
            except ValueError as e:
                ctx.emit("project.extract_failed", source_id=sid, page=i, reason=str(e))
                project = await self._gemini_fallback(ctx, text, i)
            if project is None:
                continue
            done += 1
            project.source_id = sid
            ctx.board.projects[project.id] = project
            ctx.emit("project.extracted", source_id=sid, project=project.model_dump(), actor=project.extracted_by,
                     model=self.models.get(project.id))
            ctx.emit("source.progress", source_id=sid, read=done, total=self.source.display.get("expected_total", 0),
                     current=f"p.{i}: {project.name}")
            await ctx.pace(0.07)
        ctx.log(f"Reader · DESC: {done} projects with IDs, dates, yearly costs and descriptions.")
        return f"{done} Dominion projects extracted"

    async def _gemini_fallback(self, ctx: Ctx, text: str, page: int) -> Project | None:
        # Only for pages the parser couldn't read.
        try:
            res = await models.call("extract_fallback", text, GEMINI_PROJECT_SCHEMA,
                                    system="Extract one transmission project from this utility filing page. "
                                           "Copy values exactly; never invent.")
        except models.RoleExhausted as e:
            ctx.log(f"Reader · DESC: page {page} not read ({models.describe(e)}).")
            return None
        ctx.tokens += res.tokens
        d = res.value
        raw = extract_desc.DescPage(page=page, name=d["name"], pid=d["project_id"], description=d.get("description", ""),
                                    need=d.get("need", ""), status=d.get("status", ""), isd_raw=d["in_service_date"])
        try:
            project, _ = extract_desc.to_project(raw, self.path.name)
        except ValueError:
            return None
        self.models[project.id] = res.model
        project.extracted_by = res.provider
        return project


class GaExtractor(Agent):
    # reader 'builtin:gpc': Georgia's ten-year plan table joined to its project pages.
    def __init__(self, source: Source) -> None:
        self.source, self.path = source, _file(source)
        self.spec = AgentSpec("extract_ga", "Reader · GA",
                              "Reads the ten-year plan table and joins each row to its project page", ["code"],
                              engine="Parser")

    async def run(self, ctx: Ctx) -> str:
        sid = self.source.id
        async with ctx.tool("pdftotext", {"file": self.path.name}) as out:
            pdf = read_pdf(self.path)
            out["summary"] = f"{len(pdf.pages)} pages via {pdf.method}"
        ctx.emit("source.opened", source_id=sid, pages=len(pdf.pages), method=pdf.method)
        async with ctx.tool("parse_table", {"table": "Georgia ITS 10 Year Plan Project List"}) as out:
            rows = extract_ga.parse_table(pdf.pages)
            out["summary"] = f"{len(rows)} table rows"
        async with ctx.tool("parse_project_pages", {"key": "Teams #"}) as out:
            details = extract_ga.parse_details(pdf.pages)
            out["summary"] = f"{len(details)} project pages, {sum(r.teams in details for r in rows)} joined"
        ctx.board.ga_page_count = len(pdf.pages)
        ctx.board.ga_ceii_pages = sum("CRITICAL ENERGY INFRASTRUCTURE INFORMATION" in p for p in pdf.pages)
        for n, row in enumerate(rows, start=1):
            project, checks = extract_ga.to_project(row, details.get(row.teams), self.path.name)
            project.source_id = sid
            ctx.board.pending_checks.extend(checks)
            ctx.board.projects[project.id] = project
            ctx.emit("project.extracted", source_id=sid, project=project.model_dump(), actor="code")
            ctx.emit("source.progress", source_id=sid, read=n, total=len(rows),
                     current=f"TEAMS {row.teams}: {project.name}")
            await ctx.pace(0.022)
        ctx.log(f"Reader · GA: {len(rows)} projects with start dates, need dates and owners.")
        return f"{len(rows)} Georgia projects extracted"
