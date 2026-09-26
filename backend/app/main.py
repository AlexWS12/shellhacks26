import asyncio
import csv
import io
import json
import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app import config
from app.clients import gemini, jev
from app.core.models import Confidence
from app.core.overlap import Filters
from app.export import build_xlsx
from app.pipeline import SOURCES, build_agents, run_live
from app.runtime.replay import recorded_runs, replay
from app.runtime.run import RUNS, new_run, start_background
from app.store import dataset, tiger

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    dataset.load_from_disk()
    if tiger.enabled():
        try:
            await asyncio.to_thread(tiger.init_schema)
        except Exception as e:
            log.warning("Tiger Data unavailable at startup: %s", str(e)[:200])
    yield


app = FastAPI(title="Tandem API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


def filters(all_sponsors: bool, min_conf: Confidence, hide_finished: bool, sort: str) -> Filters:
    return Filters(all_sponsors=all_sponsors, min_confidence=min_conf, hide_finished=hide_finished,  # type: ignore[arg-type]
                   today=config.TODAY, sort=sort)




@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "dataset_run": dataset.CURRENT.run_id, "projects": len(dataset.CURRENT.projects),
            "gemini": gemini.enabled(), "gemini_model": config.GEMINI_MODEL, "jev": config.JEV_PROVIDER or "off",
            "tiger": tiger.enabled(), "today": config.TODAY}


@app.get("/api/agents")
def agents() -> dict:
    return {"agents": [a.spec.public() for a in build_agents()], "sources": SOURCES}


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
                "utility_b", "project_id_b", "project_name_b", "location_confidence"])
    for i, o in enumerate(dataset.overlaps(filters(all_sponsors, min_conf, hide_finished, "distance")), start=1):
        a, b = dataset.CURRENT.projects[o.project_a], dataset.CURRENT.projects[o.project_b]
        w.writerow([f"OVL_{i}", o.distance_mi, o.time_gap_days, "Dominion Energy South Carolina", a.id, a.name,
                    "Georgia Power", b.id, b.name, o.pair_confidence])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="Tandem_overlaps.csv"'})


class RunRequest(BaseModel):
    mode: Literal["live", "replay"] = "live"
    replay_of: str | None = None  # recorded run id; default = newest complete recording
    speed: float = 1.0


@app.post("/api/runs")
async def start_run(req: RunRequest) -> dict:
    if req.mode == "live":
        busy = [r for r in RUNS.values() if r.mode == "live" and not r.finished]
        if busy:
            return {"run_id": busy[0].id, "joined": True}  # one live run at a time; join it
        run = new_run("live")
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
