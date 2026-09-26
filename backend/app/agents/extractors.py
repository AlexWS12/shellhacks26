from app import config
from app.clients import gemini
from app.core import extract_desc, extract_ga
from app.core.models import Project
from app.core.pdftext import read_pdf
from app.core.sample import read_sample, sample_checks
from app.runtime.agent import Agent, AgentSpec, Ctx

DESC_SOURCE = {"id": "desc", "label": "Dominion Energy SC",
               "detail": "Planned transmission projects $2M+, 2024–2028", "file": config.DESC_PDF.name, "total": 44}
GA_SOURCE = {"id": "ga", "label": "Georgia Power IRP, Vol. 3",
             "detail": "2024 GA ITS Ten-Year Plan, 2025–2034", "file": config.GA_PDF.name, "total": 208}
SAMPLE_SOURCE = {"id": "sample", "label": "Benchmark set", "detail": "10 projects with surveyed coordinates, 6 known overlaps",
                 "file": config.SAMPLE_XLSX.name, "total": 10}

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
    spec = AgentSpec("extract_desc", "Reader · DESC", "Reads DESC's 44-page project list, one project per page",
                     ["code", "gemini"], engine="Parser + Gemini")

    async def run(self, ctx: Ctx) -> str:
        async with ctx.tool("pdftotext", {"file": config.DESC_PDF.name}) as out:
            pdf = read_pdf(config.DESC_PDF)
            out["summary"] = f"{len(pdf.pages)} pages via {pdf.method}"
        ctx.emit("source.opened", source_id="desc", pages=len(pdf.pages), method=pdf.method)
        done = 0
        for i, text in enumerate(pdf.pages, start=1):
            project = None
            try:
                raw = extract_desc.parse_page(text, i)
                if raw is None:
                    continue
                project, checks = extract_desc.to_project(raw, config.DESC_PDF.name)
                ctx.board.pending_checks.extend(checks)
            except ValueError as e:
                ctx.emit("project.extract_failed", source_id="desc", page=i, reason=str(e))
                project = await self._gemini_fallback(ctx, text, i)
            if project is None:
                continue
            done += 1
            ctx.board.projects[project.id] = project
            ctx.emit("project.extracted", source_id="desc", project=project.model_dump(), actor=project.extracted_by)
            ctx.emit("source.progress", source_id="desc", read=done, total=DESC_SOURCE["total"],
                     current=f"p.{i}: {project.name}")
            await ctx.pace(0.07)
        ctx.log(f"Reader · DESC: {done} projects with IDs, dates, yearly costs and descriptions.")
        return f"{done} Dominion projects extracted"

    async def _gemini_fallback(self, ctx: Ctx, text: str, page: int) -> Project | None:
        # Only for pages the parser couldn't read.
        res = await gemini.generate_json(
            "Extract one transmission project from this utility filing page. Copy values exactly; never invent.",
            text, GEMINI_PROJECT_SCHEMA)
        if not res:
            return None
        ctx.tokens += res.get("input_tokens", 0) + res.get("output_tokens", 0)
        d = res["data"]
        raw = extract_desc.DescPage(page=page, name=d["name"], pid=d["project_id"], description=d.get("description", ""),
                                    need=d.get("need", ""), status=d.get("status", ""), isd_raw=d["in_service_date"])
        try:
            project, _ = extract_desc.to_project(raw, config.DESC_PDF.name)
        except ValueError:
            return None
        project.extracted_by = "gemini"
        return project


class GaExtractor(Agent):
    spec = AgentSpec("extract_ga", "Reader · GA",
                     "Reads the ten-year plan table and joins each row to its project page", ["code"],
                     engine="Parser")

    async def run(self, ctx: Ctx) -> str:
        async with ctx.tool("pdftotext", {"file": config.GA_PDF.name}) as out:
            pdf = read_pdf(config.GA_PDF)
            out["summary"] = f"{len(pdf.pages)} pages via {pdf.method}"
        ctx.emit("source.opened", source_id="ga", pages=len(pdf.pages), method=pdf.method)
        async with ctx.tool("parse_table", {"table": "Georgia ITS 10 Year Plan Project List"}) as out:
            rows = extract_ga.parse_table(pdf.pages)
            out["summary"] = f"{len(rows)} table rows"
        async with ctx.tool("parse_project_pages", {"key": "Teams #"}) as out:
            details = extract_ga.parse_details(pdf.pages)
            out["summary"] = f"{len(details)} project pages, {sum(r.teams in details for r in rows)} joined"
        ctx.board.ga_page_count = len(pdf.pages)
        ctx.board.ga_ceii_pages = sum("CRITICAL ENERGY INFRASTRUCTURE INFORMATION" in p for p in pdf.pages)
        for n, row in enumerate(rows, start=1):
            project, checks = extract_ga.to_project(row, details.get(row.teams), config.GA_PDF.name)
            ctx.board.pending_checks.extend(checks)
            ctx.board.projects[project.id] = project
            ctx.emit("project.extracted", source_id="ga", project=project.model_dump(), actor="code")
            ctx.emit("source.progress", source_id="ga", read=n, total=len(rows),
                     current=f"TEAMS {row.teams}: {project.name}")
            await ctx.pace(0.022)
        ctx.log(f"Reader · GA: {len(rows)} projects with start dates, need dates and owners.")
        return f"{len(rows)} Georgia projects extracted"
