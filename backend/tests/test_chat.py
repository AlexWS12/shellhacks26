# The chat endpoint: it refuses before a run, answers from the run's facts with the on-demand 'chat' job, and
# reports when no model is set up. Everything is offline; the facts are the real committed run.

import json

import pytest
from fastapi.testclient import TestClient

from app import config, setup
from app.clients import cache, models
from app.core import chat as chat_core
from app.store import dataset
from tests.test_models import Fake


@pytest.fixture
def env(tmp_path, monkeypatch, finished_run):
    fake = Fake()
    monkeypatch.setitem(models.ADAPTERS, "fake", fake)
    for name, value in {"MODELS_FILE": tmp_path / "models.json", "MODELS_LOCAL_FILE": tmp_path / "models.local.json",
                        "SNAPSHOT_PATH": tmp_path / "snapshot.json", "RUNS_DIR": tmp_path / "runs",
                        "JEV_PROVIDER": "", "GEMINI_API_KEY": ""}.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    roles = json.loads((config.REPO_DIR / "config" / "models.json").read_text())["roles"]
    roles["chat"] = {"kind": "text", "when": "on_demand", "models": [{"provider": "fake", "model": "c1"}]}
    (tmp_path / "models.json").write_text(json.dumps({"roles": roles}))
    monkeypatch.setattr(models, "_loaded", None)
    dataset.CURRENT = dataset.Dataset()  # start with nothing: the "before a run" case
    return fake


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def load(finished_run):
    dataset.load_snapshot({**finished_run.board.to_snapshot(), "run_id": finished_run.id})


def test_chat_refuses_before_a_run(env, client):
    r = client.post("/api/chat", json={"question": "What did you find?"})
    assert r.status_code == 409 and "Run the pipeline first" in r.json()["detail"]


def test_chat_rejects_an_empty_question(env, client, finished_run):
    load(finished_run)
    assert client.post("/api/chat", json={"question": "   "}).status_code == 400


def test_chat_answers_from_the_run_facts(env, client, finished_run):
    load(finished_run)
    env.script = {"c1": ["The research team found other owners near the Savannah River."]}
    r = client.post("/api/chat", json={"question": "Which other utilities are nearby?",
                                       "history": [{"role": "user", "text": "hi"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] == "The research team found other owners near the Savannah River."
    assert body["model"] == "fake/c1" and env.calls == ["c1"]


def test_chat_reports_when_no_model_is_available(env, client, finished_run):
    load(finished_run)
    env.off = "turned off on purpose"
    r = client.post("/api/chat", json={"question": "hi"})
    assert r.status_code == 503 and "No model is set up" in r.json()["detail"]


def test_context_has_counts_and_research(env, finished_run):
    load(finished_run)
    assert dataset.CURRENT.research, "the offline run should have research records"
    ctx = chat_core.build_context()
    assert "Counts:" in ctx and "Opportunities" in ctx
    assert any(r.utility in ctx and r.name in ctx for r in dataset.CURRENT.research)


def test_prompt_carries_history_and_the_question(env):
    prompt = chat_core.build_prompt("FACTS", "Why?", [{"role": "user", "text": "Which pair is closest?"},
                                                      {"role": "assistant", "text": "The first one."}])
    assert prompt.startswith("FACTS")
    assert "Question: Which pair is closest?" in prompt and "Answer: The first one." in prompt
    assert prompt.rstrip().endswith("Question: Why?\nAnswer:")


def test_chat_job_is_on_demand_and_never_blocks_a_run(env):
    roles = models.load()
    assert models.in_use(roles["chat"])  # a plain reason it can be left alone
    assert "chat" not in {p["role"] for p in setup.problems(roles)}
