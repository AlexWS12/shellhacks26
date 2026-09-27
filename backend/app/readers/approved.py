# The Reader of an AI-read source after review: loads the projects a person accepted (with their provenance and any
# human overrides) into the run. No model call; what the review approved is what every run uses.

from pathlib import Path

from app.core.models import Project
from app.runtime.agent import Agent, AgentSpec, Ctx
from app.store import sources
from app.store.sources import Source


class ApprovedReader(Agent):
    def __init__(self, source: Source) -> None:
        self.source = source
        self.spec = AgentSpec(sources.agent_id(source), f"Reader · {source.code}",
                              f"Loads {source.display_name}'s reviewed projects, each with the page it came from",
                              ["code"], engine="Reviewed")

    async def run(self, ctx: Ctx) -> str:
        sid = self.source.id
        rows = sources.load_published(sid)
        ctx.emit("source.opened", source_id=sid, pages=0, method="reviewed")
        for n, row in enumerate(rows, start=1):
            p = Project(**row)
            p.source_id = sid
            ctx.board.projects[p.id] = p
            ctx.emit("project.extracted", source_id=sid, project=p.model_dump(), actor="code")
            ctx.emit("source.progress", source_id=sid, read=n, total=len(rows), current=f"p.{p.source_page}: {p.name}")
        ctx.log(f"{self.spec.name}: {len(rows)} reviewed projects from {Path(self.source.file_path or '').name}.")
        return f"{len(rows)} reviewed projects"
