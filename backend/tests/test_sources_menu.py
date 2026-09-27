# The Sources menu end to end: upload a filing, describe it, read it (fake model), review, activate, see its
# projects and overlaps in the results, then deactivate and delete. The page text is faked; OSM, towns and the
# built-in results are the real committed data.

import asyncio
import base64
import json

import pytest
from fastapi.testclient import TestClient

from app import config, pipeline, sources_admin
from app.clients import cache, models
from app.core.overlap import Filters
from app.core.pdftext import PdfText
from app.readers import ai_reader
from app.runtime.run import new_run
from app.store import dataset, sources
from tests.test_ai_reader import cell, project
from tests.test_models import Fake

PAGES = ["Ten-year plan\nContents",
         "Project ID\nTT-1\nPritchardville – Bluffton 115 kV Tie\nPlanned In-Service Date\n12/31/2028\nCost\nTotal $4,000,000",
         "Project ID\nTT-2\nRiver Road Upgrade\nIn service when funding allows",
         "Project ID\nTT-3\nTransmission Line Rebuilds\nPlanned In-Service Date\n06/30/2030"]
PDF = b"%PDF-1.4 test filing for the Sources menu"
LOCATE = {"pages": [{"page": n, "kind": "project_page", "reason": "one project"} for n in (2, 3, 4)]}
ROWS = [project(project_id=cell("TT-1", 2), name=cell("Pritchardville – Bluffton 115 kV Tie", 2),
                in_service_date=cell("12/31/2028", 2), costs=[{"label": "Total", **cell("$4,000,000", 2)}]),
        project(project_id=cell("TT-2", 3), name=cell("River Road Upgrade", 3)),  # no date: can't be compared
        project(project_id=cell("TT-3", 4), name=cell("Transmission Line Rebuilds", 4),  # no ends: can't be placed
                in_service_date=cell("06/30/2030", 4))]


@pytest.fixture
def env(tmp_path, monkeypatch, finished_run):
    fake = Fake()
    monkeypatch.setitem(models.ADAPTERS, "fake", fake)
    for name, value in {"SOURCES_DB": tmp_path / "sources.db", "SUBMISSIONS_DIR": tmp_path / "subs",
                        "DRAFTS_DIR": tmp_path / "drafts", "UPLOADS_DIR": tmp_path / "uploads",
                        "SNAPSHOT_PATH": tmp_path / "snapshot.json", "RUNS_DIR": tmp_path / "runs",
                        "MODELS_FILE": tmp_path / "models.json", "MODELS_LOCAL_FILE": tmp_path / "models.local.json",
                        "APP_MODE": "local", "MAX_RUN_COST_USD": 0.0, "OSM_LIVE": False, "JEV_PROVIDER": "",
                        "GEMINI_API_KEY": "", "NOMINATIM_BUDGET_S": 0.0}.items():
        monkeypatch.setattr(config, name, value)
    from app.runtime import run as runmod

    monkeypatch.setattr(runmod, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(cache, "CACHE_DIR", config.CACHE_DIR)  # real OSM / Nominatim caches, read only in effect
    monkeypatch.setattr(cache, "put", lambda *a, **k: None)
    (tmp_path / "models.json").write_text(json.dumps({"roles": {**json.loads(
        (config.REPO_DIR / "config" / "models.json").read_text())["roles"], "reader": {
            "kind": "json", "models": [{"provider": "fake", "model": "r1"}]}}}))
    monkeypatch.setattr(models, "_loaded", None)
    fake_pdf = lambda *a, **k: PdfText(pages=PAGES, method="test", sha="t")  # noqa: E731
    monkeypatch.setattr(ai_reader, "read_pdf", fake_pdf)
    monkeypatch.setattr(sources_admin, "read_pdf", fake_pdf)
    fake.script = {"r1": [LOCATE, {"projects": ROWS}]}
    # the results on screen before: the offline Dominion-Georgia run, with a write-up to keep
    snap = {**finished_run.board.to_snapshot(), "run_id": finished_run.id, "analyses": {"X|Y": {"text": "kept"}}}
    config.SNAPSHOT_PATH.write_text(json.dumps(snap, default=str))
    dataset.load_snapshot(snap)
    return fake


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def add(client) -> str:
    up = client.post("/api/sources/upload", json={"filename": "plan.pdf", "content_b64": base64.b64encode(PDF).decode()})
    assert up.status_code == 200, up.text
    assert up.json()["pages"] == 4
    r = client.post("/api/sources", json={"upload_id": up.json()["upload_id"], "filename": "plan.pdf",
                                          "display_name": "Test Transmission Co", "code": "TTC", "states": ["SC"],
                                          "operators": ["Test Transmission", "Old Test Power"]})
    assert r.status_code == 200, r.text
    return r.json()["source"]["id"]


def read(sid: str):
    run = new_run("live")
    run.pace, run.purpose = 0, "extract"
    asyncio.run(pipeline.run_extraction(run, sid))
    return run


def activate(client, sid: str):
    r = client.post(f"/api/sources/{sid}/activate")
    assert r.status_code == 200, r.text
    run = new_run("live")
    run.pace, run.purpose = 0, "activate"
    asyncio.run(pipeline.run_activation(run, sid))  # what the endpoint starts in the background
    return run


def test_upload_rules(env, client):
    assert client.post("/api/sources/upload", json={"filename": "x.csv", "content_b64": base64.b64encode(b"a,b").decode()}
                       ).json()["detail"]["message"].startswith("That isn't a PDF")
    desc = config.DESC_PDF.read_bytes()
    r = client.post("/api/sources/upload", json={"filename": "d.pdf", "content_b64": base64.b64encode(desc).decode()})
    assert r.status_code == 409 and "Dominion Energy South Carolina (DESC)" in r.json()["detail"]["message"]
    sid = add(client)
    again = client.post("/api/sources/upload", json={"filename": "p.pdf", "content_b64": base64.b64encode(PDF).decode()})
    assert again.status_code == 409 and again.json()["detail"]["source_id"] == sid
    s = sources.get(sid)
    assert s.status == "draft" and s.reader == "ai" and s.file_path.endswith(f"uploads/{s.file_sha256}.pdf")
    assert (config.UPLOADS_DIR / f"{s.file_sha256}.pdf").exists()
    assert s.osm_operator_patterns == ["test transmission", "old test power"]


def test_describe_rules(env, client, monkeypatch):
    up = client.post("/api/sources/upload", json={"filename": "p.pdf", "content_b64": base64.b64encode(PDF).decode()}).json()
    base = {"upload_id": up["upload_id"], "display_name": "Test Co", "code": "DESC", "states": ["SC"]}
    assert "already used by Dominion" in client.post("/api/sources", json=base).json()["detail"]["message"]
    assert "state" in client.post("/api/sources", json=base | {"code": "TC", "states": ["XX"]}).json()["detail"]["message"]
    assert "Page range" in client.post("/api/sources", json=base | {"code": "TC", "pages": "9-12"}).json()["detail"]["message"]
    monkeypatch.setattr(config, "UPLOAD_MAX_MB", 0.00001)
    r = client.post("/api/sources/upload", json={"filename": "p.pdf", "content_b64": base64.b64encode(PDF * 10).decode()})
    assert r.status_code == 413


def test_the_whole_flow_puts_the_new_source_on_the_map_with_overlaps(env, client):
    sid = add(client)
    assert client.get(f"/api/sources/{sid}/estimate").json()["allowed"]
    read(sid)
    rv = client.get(f"/api/sources/{sid}/review").json()
    assert rv["counts"] == {"pending": 3, "accepted": 0, "rejected": 0} and not rv["ready"]
    flags = {pid: r["incomplete"] for pid, r in rv["review"].items()}
    assert flags == {"TTC-TT1": [], "TTC-TT2": ["in_service_date"], "TTC-TT3": ["endpoints"]}
    # an edit is kept with what it replaced; a bad date is refused
    assert client.patch(f"/api/sources/{sid}/review/TTC-TT2", json={"field": "in_service_date", "value": "soon"}).status_code == 400
    rv = client.patch(f"/api/sources/{sid}/review/TTC-TT1", json={"field": "cost_total", "value": "$4,250,000"}).json()
    p1 = next(p for p in rv["projects"] if p["id"] == "TTC-TT1")
    assert p1["cost_total"] == 4_250_000 and p1["provenance"]["cost_total"]["human_override"]["previous"] == 4_000_000
    assert rv["review"]["TTC-TT1"]["edited"]
    assert client.post(f"/api/sources/{sid}/activate").status_code == 400  # rows still pending
    client.post(f"/api/sources/{sid}/review/decide", json={"project_id": "TTC-TT3", "status": "rejected"})
    rv = client.post(f"/api/sources/{sid}/review/decide", json={"status": "accepted"}).json()
    assert rv["counts"] == {"pending": 0, "accepted": 2, "rejected": 1} and rv["ready"]
    run = activate(client, sid)
    assert run.events[-1]["ok"], run.events[-1]
    assert run.board.sample is not None and not run.board.blind  # benchmark points in, no benchmark re-check
    assert sources.get(sid).status == "active"
    # TT-2 has no date: accepted but not compared, so only TT-1 joins the results
    mine = [p for p in dataset.CURRENT.projects.values() if p.source_id == sid]
    assert [p.id for p in mine] == ["TTC-TT1"] and mine[0].lat is not None
    assert mine[0].provenance["name"] == {"page": 2, "snippet": "Pritchardville – Bluffton 115 kV Tie"}
    ov = dataset.overlaps(Filters(today=config.TODAY))
    sides = {dataset.CURRENT.projects[o.project_a].utility for o in ov if o.project_b == "TTC-TT1"}
    assert sides == {"DESC", "GA"}  # cross-utility overlaps against both built-ins
    assert dataset.CURRENT.analyses == {"X|Y": {"text": "kept"}}  # the last full run's write-ups stay
    base = {(o.id, o.distance_mi, o.time_gap_days) for o in ov if "TTC" not in o.id}
    assert len(base) == len({o.id for o in ov if "TTC" not in o.id}) > 0  # the Dominion-Georgia pairs are all still there
    listed = {s["id"]: s for s in client.get("/api/sources").json()["sources"]}
    assert listed[sid]["projects"] == 1 and listed[sid]["draft_projects"] == 3
    # every later full run reads it from the review, without a model
    from app.pipeline import build_pipeline

    assert f"extract_{sid}" in [a.spec.id for a in build_pipeline()[0]]


def test_deactivate_and_delete_take_its_projects_and_overlaps_out(env, client):
    sid = add(client)
    read(sid)
    client.post(f"/api/sources/{sid}/review/decide", json={"status": "accepted"})
    activate(client, sid)
    before = len(dataset.overlaps(Filters(today=config.TODAY)))
    r = client.post(f"/api/sources/{sid}/deactivate").json()
    assert r["removed_projects"] == 2 and r["source"]["status"] == "review"  # TT-1 and TT-3; TT-2 has no date
    assert not any(p.source_id == sid for p in dataset.CURRENT.projects.values())
    assert len(dataset.overlaps(Filters(today=config.TODAY))) < before
    assert json.loads(config.SNAPSHOT_PATH.read_text())["analyses"] == {"X|Y": {"text": "kept"}}
    activate(client, sid)  # reviewed once, it can go back on
    assert any(p.source_id == sid for p in dataset.CURRENT.projects.values())
    sha = sources.get(sid).file_sha256
    assert client.delete(f"/api/sources/{sid}").json()["removed_projects"] == 2
    assert sources.get(sid) is None and not (config.UPLOADS_DIR / f"{sha}.pdf").exists()
    assert not sources.draft_path(sid).parent.exists()


def test_built_ins_cant_be_removed(env, client):
    assert client.delete("/api/sources/desc").status_code == 403
    assert client.post("/api/sources/gpc/deactivate").status_code == 403


def test_page_text_for_the_review(env, client):
    sid = add(client)
    assert "Pritchardville" in client.get(f"/api/sources/{sid}/pages/2/text").json()["text"]
    assert client.get(f"/api/sources/{sid}/pages/9/text").status_code == 404


def test_hosted_mode_needs_the_passcode(env, client, monkeypatch):
    from app import api_models

    monkeypatch.setattr(config, "APP_MODE", "hosted")
    monkeypatch.setattr(config, "ADMIN_PASSCODE", "pw")
    monkeypatch.setattr(api_models, "_fails", api_models.deque())
    body = {"filename": "p.pdf", "content_b64": base64.b64encode(PDF).decode()}
    assert client.post("/api/sources/upload", json=body).status_code == 401
    assert client.post("/api/sources/upload", json=body, headers={"X-Admin-Passcode": "pw"}).status_code == 200
    assert client.get("/api/sources").status_code == 200 and client.get("/api/sources/meta").status_code == 200
