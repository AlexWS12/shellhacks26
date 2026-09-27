import asyncio
import base64
import binascii
import csv
import io
import json
import logging
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app import config
from app.clients import fetch, gemini, jev
from app.core.models import Confidence
from app.core.overlap import Filters
from app.core.sheets import FIELDS as SHEET_FIELDS
from app.core.sheets import read_table, validate_mapping
from app.core.sheets import suggest as suggest_columns
from app.core.sheets import to_project as sheet_row
from app.export import build_xlsx, owner_label, safe_cell
from app.pipeline import build_pipeline, run_live
from app.runtime.replay import recorded_runs, replay
from app.runtime.run import RUNS, new_run, start_background
from app.store import dataset, submissions, tiger

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")
_gemini_ok: bool | None = None  # None until the startup check finishes


async def check_gemini() -> None:
    global _gemini_ok
    res = await gemini.smoke() if gemini.enabled() else None
    _gemini_ok = bool(res and res["data"].get("ok"))
    if not _gemini_ok:
        log.warning("Gemini smoke check failed: analyses and briefs will fall back to templates")


@asynccontextmanager
async def lifespan(_: FastAPI):
    dataset.load_from_disk()
    gemini_check = asyncio.create_task(check_gemini())  # in the background so startup isn't blocked
    if tiger.enabled():
        try:
            await asyncio.to_thread(tiger.init_schema)
        except Exception as e:
            log.warning("Tiger Data unavailable at startup: %s", str(e)[:200])
    yield
    gemini_check.cancel()


app = FastAPI(title="Tandem API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


def filters(all_sponsors: bool, min_conf: Confidence, hide_finished: bool, sort: str) -> Filters:
    return Filters(all_sponsors=all_sponsors, min_confidence=min_conf, hide_finished=hide_finished,  # type: ignore[arg-type]
                   today=config.TODAY, sort=sort)




@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "dataset_run": dataset.CURRENT.run_id, "projects": len(dataset.CURRENT.projects),
            "gemini": gemini.enabled(), "gemini_ok": _gemini_ok, "gemini_model": config.GEMINI_MODEL,
            "gemini_fallback_model": config.GEMINI_FALLBACK_MODEL, "jev": config.JEV_PROVIDER or "off",
            "tiger": tiger.enabled(), "today": config.TODAY}


@app.get("/api/agents")
def agents(of: Literal["next", "latest"] = "next") -> dict:
    # next: the graph the next live run will use (saved plans now). latest: the graph of the dataset being shown.
    if of == "latest" and dataset.CURRENT.agents:
        return {"agents": dataset.CURRENT.agents, "sources": dataset.CURRENT.sources}
    agents, sources = build_pipeline()
    return {"agents": [a.spec.public() for a in agents], "sources": sources}


@app.get("/api/projects")
def projects() -> list[dict]:
    return [p.model_dump() for p in dataset.CURRENT.projects.values()]


@app.get("/api/overlaps")
def overlaps(all_sponsors: bool = False, min_conf: Confidence = "town", hide_finished: bool = False,
             sort: str = "distance") -> dict:
    f = filters(all_sponsors, min_conf, hide_finished, sort)
    found = dataset.overlaps(f)
    visible = sum(1 for p in dataset.CURRENT.projects.values() if _visible(p, f))
    return {"overlaps": [o.model_dump() for o in found], "visible_projects": visible,
            "total_projects": len(dataset.CURRENT.projects)}


def _visible(p, f) -> bool:
    from app.core.overlap import visible

    return visible(p, f)


@app.get("/api/pair/{a_id}/{b_id}")
def pair(a_id: str, b_id: str) -> dict:
    d = dataset.pair_detail(a_id, b_id)
    if not d:
        raise HTTPException(404, "pair not found or not located")
    return d


@app.get("/api/checks")
def checks() -> list[dict]:
    return [c.model_dump() for c in dataset.CURRENT.checks]


@app.get("/api/research")
def research(all_sponsors: bool = False, min_conf: Confidence = "town", hide_finished: bool = False) -> dict:
    ovs = dataset.overlaps(filters(all_sponsors, min_conf, hide_finished, "distance"))
    return {"selected": dataset.CURRENT.research_selected,
            "records": [r.model_dump() for r in dataset.CURRENT.research],
            "links": [t.model_dump() for t in dataset.others(ovs, min_conf)]}


@app.get("/api/report")
def report() -> dict:
    if not dataset.CURRENT.report:
        raise HTTPException(404, "no report yet: run the pipeline")
    return dataset.CURRENT.report


@app.get("/api/report.md")
def report_md() -> Response:
    if not dataset.CURRENT.report:
        raise HTTPException(404, "no report yet: run the pipeline")
    return Response(dataset.CURRENT.report["markdown"], media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="Tandem_report.md"'})


@app.get("/api/reference-test")
def reference() -> list[dict]:
    return [r.model_dump() for r in dataset.CURRENT.reference]


@app.get("/api/export.xlsx")
def export_xlsx(all_sponsors: bool = False, min_conf: Confidence = "town", hide_finished: bool = False) -> Response:
    data = build_xlsx(dataset.overlaps(filters(all_sponsors, min_conf, hide_finished, "distance")))
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="Tandem_Projects_Overlaps.xlsx"'})


@app.get("/api/export.csv")
def export_csv(all_sponsors: bool = False, min_conf: Confidence = "town", hide_finished: bool = False) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["overlap_id", "distance_mi", "time_gap (day)", "utility_a", "project_id_a", "project_name_a",
                "utility_b", "project_id_b", "project_name_b", "location_confidence", "other_utilities_nearby"])
    ovs = dataset.overlaps(filters(all_sponsors, min_conf, hide_finished, "distance"))
    research = {r.id: r for r in dataset.CURRENT.research}
    near: dict[str, list[str]] = {}
    for t in dataset.others(ovs):
        near.setdefault(t.overlap_id, []).append(f"{research[t.research_id].utility}: {research[t.research_id].name}")
    for i, o in enumerate(ovs, start=1):
        a, b = dataset.CURRENT.projects[o.project_a], dataset.CURRENT.projects[o.project_b]
        w.writerow([safe_cell(v) for v in (f"OVL_{i}", o.distance_mi, o.time_gap_days, owner_label(a), a.id, a.name,
                    owner_label(b), b.id, b.name, o.pair_confidence, "; ".join(near.get(o.id, [])))])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="Tandem_overlaps.csv"'})


class RunRequest(BaseModel):
    mode: Literal["live", "replay"] = "live"
    replay_of: str | None = None  # recorded run id; default = newest complete recording
    speed: float = 1.0
    templates: bool = True  # live only: fall back to a template when Gemini fails
    research: list[Literal["electric", "gas", "roads_water"]] | None = None  # live only; None = all


@app.post("/api/runs")
async def start_run(req: RunRequest) -> dict:
    if req.mode == "live":
        busy = [r for r in RUNS.values() if r.mode == "live" and not r.finished]
        if busy:
            return {"run_id": busy[0].id, "joined": True}  # one live run at a time; join it
        run = new_run("live")
        run.templates = req.templates
        if req.research is not None:
            run.research = list(dict.fromkeys(req.research))
        start_background(run_live(run))
        return {"run_id": run.id}
    recs = [r for r in recorded_runs() if r["complete"] and r["ok"]]
    source = req.replay_of or (recs[0]["run_id"] if recs else None)
    path = config.RUNS_DIR / f"{source}.jsonl"
    if not source or not path.exists():
        raise HTTPException(404, "no recorded run to replay")
    run = new_run("replay", source=source)
    start_background(replay(run, path, req.speed))
    return {"run_id": run.id, "replay_of": source}


@app.post("/api/runs/{run_id}/skip")
def skip(run_id: str) -> dict:
    # Skip to results: drop the demo delays. Every event still goes out.
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    run.pace = 0
    return {"ok": True}


@app.get("/api/runs")
def runs() -> dict:
    return {"recorded": recorded_runs(), "active": [r.id for r in RUNS.values() if not r.finished]}


@app.get("/api/runs/{run_id}/events")
async def events(run_id: str) -> StreamingResponse:
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "run not found (the server may have restarted); try a replay")

    async def stream():
        past, queue = run.subscribe()
        try:
            for e in past:
                yield f"data: {json.dumps(e, default=str)}\n\n"
            while True:
                try:
                    e = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if e is None:
                    break
                yield f"data: {json.dumps(e, default=str)}\n\n"
            yield "event: end\ndata: {}\n\n"
        finally:
            run.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/stats/agents")
def agent_stats() -> dict:
    if not tiger.enabled():
        return {"enabled": False, "rows": []}
    try:
        return {"enabled": True, "rows": tiger.agent_stats()}
    except Exception as e:
        raise HTTPException(503, f"Tiger Data unavailable: {str(e)[:200]}")


@app.get("/api/jev/status")
def jev_status() -> dict:
    return {"provider": config.JEV_PROVIDER or "off", "live": jev.enabled()}


# ---- Sources menu: plans people submit. Saved until removed; each ready one gets a Reader on every live run.

class SubmissionIn(BaseModel):
    owner: str
    label: str | None = None
    state: Literal["SC", "GA"] = "SC"
    kind: Literal["spreadsheet", "pdf", "url"]
    filename: str | None = None
    content_b64: str | None = None  # spreadsheet or pdf
    url: str | None = None  # link


class MappingIn(BaseModel):
    mapping: dict[str, str]


def _preview(s: submissions.Submission) -> dict:
    header, rows = read_table(submissions.file_of(s))
    return {"columns": header, "rows": [["" if c is None else str(c)[:80] for c in r] for _, r in rows[:5]],
            "total_rows": len(rows)}


def _sniff(data: bytes, content_type: str, name: str) -> tuple[str, str]:
    # (kind, extension) from the bytes, not the name alone.
    low = name.lower()
    if data[:5] == b"%PDF-":
        return "pdf", ".pdf"
    if data[:2] == b"PK" and (low.endswith(".xlsx") or "spreadsheet" in content_type):
        return "spreadsheet", ".xlsx"
    if low.endswith(".csv") or "text/csv" in content_type:
        return "spreadsheet", ".csv"
    if "html" in content_type or data.lstrip()[:15].lower().startswith((b"<!doctype html", b"<html")):
        return "url", ".html"
    raise ValueError("Use a CSV or XLSX spreadsheet, a PDF, or a link to a web page or PDF.")


@app.get("/api/submissions")
def list_submissions() -> dict:
    return {"submissions": [s.model_dump() for s in submissions.list_all()],
            "fields": {k: {"label": v[0], "required": v[1]} for k, v in SHEET_FIELDS.items()},
            "limits": {"max_mb": submissions.MAX_BYTES // (1024 * 1024), "max_plans": submissions.MAX_SUBMISSIONS},
            "gemini": gemini.enabled()}


@app.post("/api/submissions")
async def add_submission(req: SubmissionIn) -> dict:
    try:
        if req.kind == "url":
            if not req.url:
                raise ValueError("Paste a link.")
            data, ctype, final = await fetch.download(req.url, submissions.MAX_BYTES)
            name = final.rstrip("/").rsplit("/", 1)[-1] or "page"
        else:
            if not req.content_b64 or not req.filename:
                raise ValueError("Choose a file.")
            if len(req.content_b64) > submissions.MAX_BYTES * 4 // 3 + 4:
                raise ValueError(f"Files up to {submissions.MAX_BYTES // (1024 * 1024)} MB.")
            data, ctype, name = base64.b64decode(req.content_b64, validate=True), "", req.filename
        kind, ext = _sniff(data, ctype, name)
        if req.kind != "url" and kind != req.kind:
            raise ValueError(f"That file looks like a {kind}, not a {req.kind}.")
        columns: list[str] = []
        if kind == "spreadsheet":  # check it parses before saving
            tmp = config.SUBMISSIONS_DIR / f"_check{ext}"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(data)
            try:
                columns, _ = read_table(tmp)
            finally:
                tmp.unlink(missing_ok=True)
        s = submissions.create(req.owner, req.label, req.state, kind, name, data, ext, url=req.url, content_type=ctype,
                               columns=columns)
    except (ValueError, binascii.Error, httpx.HTTPError) as e:
        raise HTTPException(400, str(e) or type(e).__name__)
    out: dict = {"submission": s.model_dump()}
    if s.kind == "spreadsheet":
        out |= {"preview": _preview(s), "suggested": suggest_columns(s.columns)}
    return out


@app.get("/api/submissions/{sid}/preview")
def submission_preview(sid: str) -> dict:
    s = submissions.get(sid)
    if not s or s.kind != "spreadsheet":
        raise HTTPException(404, "no spreadsheet with that id")
    return {"submission": s.model_dump(), "preview": _preview(s), "suggested": s.mapping or suggest_columns(s.columns)}


@app.put("/api/submissions/{sid}/mapping")
def submission_mapping(sid: str, req: MappingIn) -> dict:
    s = submissions.get(sid)
    if not s or s.kind != "spreadsheet":
        raise HTTPException(404, "no spreadsheet with that id")
    try:
        mapping = validate_mapping(req.mapping, s.columns)
    except ValueError as e:
        raise HTTPException(400, str(e))
    header, rows = read_table(submissions.file_of(s))
    results = [(i, *sheet_row(s, mapping, header, r, i)) for i, r in rows]
    ok = sum(1 for _, p, _ in results if p)
    if ok == 0:
        raise HTTPException(400, "No row has both a project name and a readable in-service date with these columns.")
    s = submissions.set_mapping(sid, mapping)
    return {"submission": s.model_dump(), "rows": len(rows), "usable": ok,
            "skipped": [f"row {i}: {why}" for i, p, why in results if not p][:8]}


@app.delete("/api/submissions/{sid}")
def remove_submission(sid: str) -> dict:
    if not submissions.delete(sid):
        raise HTTPException(404, "no plan with that id")
    return {"ok": True}
