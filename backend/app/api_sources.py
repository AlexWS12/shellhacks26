# /api/sources/*: the Sources menu. Listing is public (GET /api/sources in main.py); adding, reading, reviewing,
# activating and removing need the admin passcode in hosted mode, like the model setup.

import asyncio
import base64
import binascii
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import sources_admin as admin
from app.api_models import require_admin
from app.runtime.run import new_run, start_background
from app.store import sources

router = APIRouter(prefix="/api/sources")
guarded = APIRouter(prefix="/api/sources", dependencies=[Depends(require_admin)])


def _fail(e: admin.AdminError) -> HTTPException:
    return HTTPException(e.status, {"message": e.message, **e.details})


@router.get("/meta")
def meta() -> dict[str, Any]:
    return admin.meta()


class UploadIn(BaseModel):
    filename: str
    content_b64: str


@guarded.post("/upload")
def upload(req: UploadIn) -> dict[str, Any]:
    limit = int(admin.config.UPLOAD_MAX_MB * 1024 * 1024)
    if len(req.content_b64) > limit * 4 // 3 + 4:
        raise HTTPException(413, {"message": f"Files up to {admin.config.UPLOAD_MAX_MB:g} MB."})
    try:
        data = base64.b64decode(req.content_b64, validate=True)
        return admin.upload(req.filename, data)
    except binascii.Error:
        raise HTTPException(400, {"message": "The file didn't arrive intact. Try again."}) from None
    except admin.AdminError as e:
        raise _fail(e) from None


class CreateIn(BaseModel):
    upload_id: str
    filename: str = "filing.pdf"
    display_name: str
    code: str
    states: list[str]
    operators: list[str] = []
    pages: str | None = None


@guarded.post("")
def create(req: CreateIn) -> dict[str, Any]:
    try:
        s = admin.create(req.upload_id, req.filename, req.display_name, req.code, req.states, req.operators, req.pages)
    except admin.AdminError as e:
        raise _fail(e) from None
    return {"source": sources.public(s)}


def _src(source_id: str):
    s = sources.get(source_id)
    if s is None:
        raise HTTPException(404, {"message": "No source with that id."})
    if s.builtin:
        raise HTTPException(400, {"message": "Built-in sources have their own parser."})
    return s


@guarded.get("/{source_id}/estimate")
async def estimate(source_id: str, pages: str | None = None) -> dict[str, Any]:
    from app.readers.ai_reader import CostLimitExceeded, estimate_for

    s = _src(source_id)
    try:
        return {"estimate": await asyncio.to_thread(estimate_for, s, pages or s.display.get("page_range")), "allowed": True}
    except CostLimitExceeded as e:
        return {"estimate": None, "allowed": False, "message": str(e)}
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        raise HTTPException(400, {"message": str(e)}) from None


class ExtractIn(BaseModel):
    pages: str | None = None  # "1-10,15"; None = the range given when it was added, or the whole document


@guarded.post("/{source_id}/extract")
async def extract(source_id: str, req: ExtractIn) -> dict[str, Any]:
    # Runs the AI reader. The cost is checked first (MAX_RUN_COST_USD); the projects go to the source's draft for
    # review. Follow it like any run: GET /api/runs/{run_id}/events.
    from app.pipeline import run_extraction
    from app.readers.ai_reader import CostLimitExceeded, estimate_for

    s = _src(source_id)
    pages = req.pages or s.display.get("page_range")
    try:
        est = await asyncio.to_thread(estimate_for, s, pages)
    except CostLimitExceeded as e:
        raise HTTPException(409, {"message": str(e)}) from None
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        raise HTTPException(400, {"message": str(e)}) from None
    run = new_run("live")
    run.purpose = "extract"
    start_background(run_extraction(run, source_id, pages))
    return {"run_id": run.id, "estimate": est}


@guarded.get("/{source_id}/draft")
def draft(source_id: str) -> dict[str, Any]:
    try:
        d = sources.load_draft(source_id)
    except ValueError:
        d = None
    if d is None:
        raise HTTPException(404, {"message": "No draft for that source yet."})
    return d


@guarded.get("/{source_id}/review")
def review(source_id: str) -> dict[str, Any]:
    try:
        return admin.review(source_id)
    except admin.AdminError as e:
        raise _fail(e) from None


class EditIn(BaseModel):
    field: str
    value: Any = None


@guarded.patch("/{source_id}/review/{project_id}")
def edit(source_id: str, project_id: str, req: EditIn) -> dict[str, Any]:
    try:
        return admin.edit(source_id, project_id, req.field, req.value)
    except admin.AdminError as e:
        raise _fail(e) from None


class DecideIn(BaseModel):
    status: Literal["accepted", "rejected", "pending"]
    project_id: str | None = None  # None: every row still pending ("accept all")


@guarded.post("/{source_id}/review/decide")
def decide(source_id: str, req: DecideIn) -> dict[str, Any]:
    try:
        return admin.decide(source_id, req.project_id, req.status)
    except admin.AdminError as e:
        raise _fail(e) from None


@guarded.post("/{source_id}/activate")
async def activate(source_id: str) -> dict[str, Any]:
    # Accepted rows become the source's projects; a recorded run geocodes them and recomputes the overlaps.
    from app.pipeline import run_activation

    try:
        rows = admin.activate(source_id)
    except admin.AdminError as e:
        raise _fail(e) from None
    run = new_run("live")
    run.purpose = "activate"
    start_background(run_activation(run, source_id))
    return {"run_id": run.id, "projects": len(rows)}


@guarded.post("/{source_id}/deactivate")
def deactivate(source_id: str) -> dict[str, Any]:
    try:
        return {"removed_projects": admin.deactivate(source_id), "source": sources.public(sources.get(source_id))}
    except admin.AdminError as e:
        raise _fail(e) from None


@guarded.delete("/{source_id}")
def delete(source_id: str) -> dict[str, Any]:
    try:
        return {"removed_projects": admin.delete(source_id)}
    except admin.AdminError as e:
        raise _fail(e) from None


@guarded.get("/{source_id}/pages/{n}/text")
def page_text(source_id: str, n: int) -> dict[str, Any]:
    try:
        return {"page": n, "text": admin.page_text(source_id, n)}
    except admin.AdminError as e:
        raise _fail(e) from None


@guarded.get("/{source_id}/pages/{n}.png")
def page_image(source_id: str, n: int) -> FileResponse:
    try:
        return FileResponse(admin.page_image(source_id, n), media_type="image/png")
    except admin.AdminError as e:
        raise _fail(e) from None
