# The setup screen's API (/api/models/*), the run preflight, and hosted mode. Gemini is faked; the committed
# config/models.json is used as is, so these tests start where a fresh clone with only GEMINI_API_KEY starts.

import json
import os
import stat

import pytest
from fastapi.testclient import TestClient

from app import api_models, config, main, setup
from app.clients import models
from app.clients.errors import AuthError, ModelNotFound, QuotaExceeded, RateLimited

KEY = "AIzaSyFAKE-setup-key-0123456789abcdefghij"
NEW_KEY = "AIzaSyFAKE-pasted-key-9876543210zyxwvuts"


class FakeGemini:
    # Stands in for the Gemini adapter: validate() answers per model from self.status, lists self.listed.
    PROVIDER, CACHE_VERSION, KINDS = "gemini", "gemini-v1", {"json", "text", "search", "judge"}

    def __init__(self) -> None:
        self.status: dict[str, Exception | None] = {}
        self.listed = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-9-pro"]
        self.validated: list[str] = []
        self.good_keys = {KEY, NEW_KEY}

    def disabled(self):
        return None

    def configured(self) -> bool:
        return bool(config.GEMINI_API_KEY)

    async def validate(self, model: str, api_key: str) -> None:
        self.validated.append(model)
        if api_key not in self.good_keys:
            raise AuthError("400 INVALID_ARGUMENT: API key not valid")
        if err := self.status.get(model):
            raise err
        if model not in self.listed:
            raise ModelNotFound(f"models/{model} is not found")

    async def list_models(self, api_key: str | None = None) -> list[dict[str, str]]:
        if api_key not in self.good_keys:
            raise AuthError("400 INVALID_ARGUMENT: API key not valid")
        return [{"id": m, "label": m.upper(), "description": ""} for m in self.listed]

    async def check_key(self, api_key: str) -> None:
        await self.list_models(api_key)


@pytest.fixture
def env(monkeypatch, tmp_path):
    # Only GEMINI_API_KEY, Jev off, no local config yet, every file this touches in tmp_path.
    fake = FakeGemini()
    monkeypatch.setitem(models.ADAPTERS, "gemini", fake)
    monkeypatch.setattr(config, "GEMINI_API_KEY", KEY)
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setattr(config, "JEV_PROVIDER", "")
    monkeypatch.setattr(config, "RESEARCH_LIVE", False)
    monkeypatch.setattr(config, "APP_MODE", "local")
    monkeypatch.setattr(config, "MODELS_LOCAL_FILE", tmp_path / "models.local.json")
    monkeypatch.setattr(config, "ENV_LOCAL_FILE", tmp_path / ".env.local")
    monkeypatch.setattr(config, "ENV_FILES", (tmp_path / ".env.local",))
    monkeypatch.setattr(models, "_loaded", None)
    monkeypatch.setattr(setup, "_checked", {})
    monkeypatch.setattr(setup, "_lists", {})
    monkeypatch.setattr(api_models, "_fails", api_models.deque())
    from app.runtime.run import RUNS

    monkeypatch.setattr(main, "RUNS", {})  # no live run left over from another test to join
    started: list = []

    def fake_start(coro) -> None:
        started.append(coro)
        coro.close()  # the pipeline itself is covered elsewhere
    monkeypatch.setattr(main, "start_background", fake_start)
    fake.started = started
    yield fake
    RUNS.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


def all_text(*responses) -> str:
    return " ".join(r.text for r in responses)


# ---- first launch with only a Gemini key

def test_fresh_clone_opens_setup_and_never_shows_the_key(env, client):
    h = client.get("/api/health").json()
    assert h["app_mode"] == "local" and h["models_setup"]["first_launch"] and h["models_setup"]["ready"]
    cfg = client.get("/api/models/config")
    body = cfg.json()
    gem = next(p for p in body["providers"] if p["id"] == "gemini")
    assert gem["key_present"] and gem["can_enter_key"] and gem["fields"][0]["present"]
    jev = next(p for p in body["providers"] if p["id"] == "jev")
    assert not jev["key_present"] and jev["off_reason"]
    roles = {r["name"]: r for r in body["roles"]}
    assert roles["confirm_osm"]["label"] == "Confirm substations" and roles["confirm_osm"]["description"]
    assert roles["watchdog"]["not_used"] == "Only used when Jev is on."
    assert roles["analyst"]["providers"] == ["gemini", "claude", "openai"]  # Jev only does quick decisions
    assert set(roles["confirm_osm"]["providers"]) == {"gemini", "claude", "openai", "jev"}
    lst = client.get("/api/models/providers/gemini/models")
    assert [m["id"] for m in lst.json()["models"]] == env.listed
    assert KEY not in all_text(cfg, lst, client.get("/api/health"))


def test_a_role_without_any_key_means_setup_is_needed(env, client, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    s = client.get("/api/health").json()["models_setup"]
    assert not s["ready"] and {p["role"] for p in s["problems"]} >= {"analyst", "extract_fallback"}
    assert client.get("/api/models/providers/gemini/models").json()["plain"] == "No key yet"


# ---- Test all

def test_validate_reports_plain_results(env, client):
    env.status = {"gemini-3.7-flash": RateLimited("429")}
    r = client.post("/api/models/validate", json={"models": [
        {"provider": "gemini", "model": "gemini-3.8-flash"}, {"provider": "gemini", "model": "gemini-3.7-flash"},
        {"provider": "gemini", "model": "gemini-nope"}, {"provider": "jev", "model": "jev-latest"}]}).json()["results"]
    assert [(x["status"], x["plain"]) for x in r] == [
        ("ok", "Works"), ("RateLimited", "Rate limited, try again"), ("ModelNotFound", "Model name not found"),
        ("Off", "Turned off")]
    assert r[1]["temporary"] and not r[2]["temporary"]


def test_a_rejected_key_reads_as_key_rejected(env, client, monkeypatch):
    env.good_keys = set()
    r = client.post("/api/models/validate", json={"models": [{"provider": "gemini", "model": "gemini-3.8-flash"}]})
    assert r.json()["results"][0]["plain"] == "Key rejected"


def test_test_all_asks_again_but_save_reuses_recent_results(env, client):
    ref = {"provider": "gemini", "model": "gemini-3.8-flash"}
    client.post("/api/models/validate", json={"models": [ref]})
    client.post("/api/models/validate", json={"models": [ref]})
    assert env.validated.count("gemini-3.8-flash") == 2  # the Test button always asks
    client.post("/api/models/validate", json={"models": [ref], "fresh": False})
    assert env.validated.count("gemini-3.8-flash") == 2


# ---- Save

def test_save_writes_only_changed_roles_and_warns_about_a_failing_fallback(env, client):
    env.status = {"gemini-3.7-flash": QuotaExceeded("daily")}
    r = client.put("/api/models/config", json={"roles": {"analyst": [
        {"provider": "gemini", "model": "gemini-9-pro"}, {"provider": "gemini", "model": "gemini-3.7-flash"}]}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] == ["analyst"]
    assert ("Write up opportunities: backup gemini-3.7-flash failed "
            "(Usage limit reached, try again later or pick another model)") in body["warnings"]
    assert all("gemini-3.7-flash" in w for w in body["warnings"])  # every job that keeps it as a backup
    saved = json.loads(config.MODELS_LOCAL_FILE.read_text())
    assert list(saved["roles"]) == ["analyst"]
    assert models.chain("analyst") == ["gemini/gemini-9-pro", "gemini/gemini-3.7-flash"]
    cfg = {x["name"]: x for x in client.get("/api/models/config").json()["roles"]}
    assert cfg["analyst"]["customized"] and not cfg["writer"]["customized"]
    assert not client.get("/api/health").json()["models_setup"]["first_launch"]
    assert client.delete("/api/models/config").json()["reset"]
    assert models.chain("analyst") == ["gemini/gemini-3.8-flash", "gemini/gemini-3.7-flash"]


def test_a_switched_off_provider_is_not_a_failing_backup(env, client):
    # The judge jobs list Jev first; with Jev off on purpose, saving them warns about nothing.
    r = client.put("/api/models/config", json={"roles": {"confirm_osm": [
        {"provider": "jev", "model": "jev-latest"}, {"provider": "gemini", "model": "gemini-9-pro"}]}})
    assert r.status_code == 200 and r.json()["warnings"] == []


def test_save_is_blocked_when_a_job_has_no_working_model(env, client):
    r = client.put("/api/models/config", json={"roles": {"writer": [
        {"provider": "gemini", "model": "gemini-nope"}, {"provider": "gemini", "model": "gemini-also-nope"}]}})
    assert r.status_code == 400
    d = r.json()["detail"]
    assert [b["role"] for b in d["blocked"]] == ["writer"] and "Model name not found" in d["blocked"][0]["reason"]
    assert not config.MODELS_LOCAL_FILE.exists()


def test_a_job_that_runs_dont_need_never_blocks_saving(env, client):
    # the watchdog only runs with Jev; with Jev off its Jev-only list is fine as it is
    r = client.put("/api/models/config", json={"roles": {"watchdog": [{"provider": "jev", "model": "jev-latest"}]}})
    assert r.status_code == 200, r.text


def test_a_fallback_can_use_another_provider(env, client):
    r = client.put("/api/models/config", json={"roles": {"confirm_osm": [
        {"provider": "jev", "model": "jev-latest"}, {"provider": "gemini", "model": "gemini-3.8-flash"}]}})
    assert r.status_code == 200, r.text
    assert models.chain("confirm_osm") == ["jev/jev-latest", "gemini/gemini-3.8-flash"]


@pytest.mark.parametrize("roles, words", [
    ({"analyst": [{"provider": "jev", "model": "jev-latest"}]}, "can't do this job"),
    ({"analyst": []}, "at least one model"),
    ({"nope": [{"provider": "gemini", "model": "m"}]}, "no job called"),
    ({"analyst": [{"provider": "gemini", "model": "a b"}]}, "isn't a model name"),
    ({"analyst": [{"provider": "gemini", "model": "m"}, {"provider": "gemini", "model": "m"}]}, "listed twice"),
])
def test_save_refuses_a_bad_config(env, client, roles, words):
    r = client.put("/api/models/config", json={"roles": roles})
    assert r.status_code == 400 and words in r.json()["detail"]["message"]


# ---- keys (local mode)

def test_a_pasted_key_is_checked_then_written_to_env_local(env, client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    bad = client.put("/api/models/keys", json={"provider": "gemini", "values": {"GEMINI_API_KEY": "AIzaSy-wrong-key-000000"}})
    assert bad.status_code == 200 and bad.json()["plain"] == "Key rejected" and not bad.json()["saved"]
    assert not config.ENV_LOCAL_FILE.exists() and config.GEMINI_API_KEY == ""
    good = client.put("/api/models/keys", json={"provider": "gemini", "values": {"GEMINI_API_KEY": NEW_KEY}})
    assert good.json()["saved"] and good.json()["plain"] == "Key works and is saved"
    assert f"GEMINI_API_KEY={NEW_KEY}" in config.ENV_LOCAL_FILE.read_text()
    assert stat.S_IMODE(os.stat(config.ENV_LOCAL_FILE).st_mode) == 0o600
    assert config.GEMINI_API_KEY == NEW_KEY and os.environ["GEMINI_API_KEY"] == NEW_KEY
    assert NEW_KEY not in all_text(bad, good, client.get("/api/models/config"))
    gem = next(p for p in good.json()["providers"] if p["id"] == "gemini")
    assert gem["key_present"] and gem["fields"][0]["source"].endswith(".env.local")


def test_a_key_with_spaces_is_refused_before_any_call(env, client):
    r = client.put("/api/models/keys", json={"provider": "gemini", "values": {"GEMINI_API_KEY": "not a key"}})
    assert r.status_code == 400 and "doesn't look right" in r.json()["detail"]["message"]


def test_env_local_keeps_other_lines(env, client):
    config.ENV_LOCAL_FILE.write_text("# mine\nNOMINATIM_USER_AGENT=me\nGEMINI_API_KEY=old\n")
    client.put("/api/models/keys", json={"provider": "gemini", "values": {"GEMINI_API_KEY": NEW_KEY}})
    assert config.ENV_LOCAL_FILE.read_text() == f"# mine\nNOMINATIM_USER_AGENT=me\nGEMINI_API_KEY={NEW_KEY}\n"


# ---- hosted mode

def test_hosted_mode_needs_the_passcode_for_every_setup_endpoint(env, client, monkeypatch):
    monkeypatch.setattr(config, "APP_MODE", "hosted")
    monkeypatch.setattr(config, "ADMIN_PASSCODE", "correct horse")
    calls = [("GET", "/api/models/config", None), ("GET", "/api/models/providers/gemini/models", None),
             ("POST", "/api/models/validate", {"models": []}), ("PUT", "/api/models/config", {"roles": {}}),
             ("DELETE", "/api/models/config", None),
             ("PUT", "/api/models/keys", {"provider": "gemini", "values": {"GEMINI_API_KEY": NEW_KEY}})]
    for method, path, body in calls:
        assert client.request(method, path, json=body).status_code == 401, path
        assert client.request(method, path, json=body, headers={"X-Admin-Passcode": "wrong"}).status_code == 401, path
        monkeypatch.setattr(api_models, "_fails", api_models.deque())  # keep the lockout out of this loop
    ok = {"X-Admin-Passcode": "correct horse"}
    assert client.get("/api/models/config", headers=ok).json()["mode"] == "hosted"
    providers = client.get("/api/models/config", headers=ok).json()["providers"]
    assert not any(p["can_enter_key"] for p in providers)
    r = client.put("/api/models/keys", headers=ok, json={"provider": "gemini", "values": {"GEMINI_API_KEY": NEW_KEY}})
    assert r.status_code == 403 and not config.ENV_LOCAL_FILE.exists()
    assert client.put("/api/models/config", headers=ok, json={"roles": {}}).status_code == 200


def test_hosted_mode_without_a_passcode_is_locked(env, client, monkeypatch):
    monkeypatch.setattr(config, "APP_MODE", "hosted")
    monkeypatch.setattr(config, "ADMIN_PASSCODE", "")
    r = client.get("/api/models/config", headers={"X-Admin-Passcode": ""})
    assert r.status_code == 503 and "ADMIN_PASSCODE" in r.json()["detail"]


def test_wrong_passcodes_are_slowed_down(env, client, monkeypatch):
    monkeypatch.setattr(config, "APP_MODE", "hosted")
    monkeypatch.setattr(config, "ADMIN_PASSCODE", "correct horse")
    for _ in range(api_models.MAX_FAILS_PER_MIN):
        client.get("/api/models/config", headers={"X-Admin-Passcode": "guess"})
    assert client.get("/api/models/config", headers={"X-Admin-Passcode": "correct horse"}).status_code == 429


def test_health_stays_public_in_hosted_mode(env, client, monkeypatch):
    monkeypatch.setattr(config, "APP_MODE", "hosted")
    monkeypatch.setattr(config, "ADMIN_PASSCODE", "correct horse")
    h = client.get("/api/health")
    assert h.status_code == 200 and h.json()["app_mode"] == "hosted" and "correct horse" not in h.text


# ---- preflight at POST /api/runs

def live(client, **extra):
    return client.post("/api/runs", json={"mode": "live", **extra})


def test_preflight_passes_on_the_first_model_and_starts_the_run(env, client):
    r = live(client)
    assert r.status_code == 200 and r.json()["run_id"] and len(env.started) == 1
    assert set(env.validated) == {"gemini-3.8-flash"}  # only first models, each once


def test_preflight_uses_a_fallback_when_the_first_model_fails(env, client):
    env.status = {"gemini-3.8-flash": ModelNotFound("gone")}
    assert live(client).status_code == 200
    assert set(env.validated) == {"gemini-3.8-flash", "gemini-3.7-flash"}


def test_preflight_refuses_and_names_the_job(env, client):
    client.put("/api/models/config", json={"roles": {"writer": [{"provider": "gemini", "model": "gemini-9-pro"}]}})
    env.listed.remove("gemini-9-pro")  # the model went away after it was saved
    setup._checked.clear()
    r = live(client)
    assert r.status_code == 409 and not env.started
    d = r.json()["detail"]
    assert [p["role"] for p in d["problems"]] == ["writer"]
    assert d["problems"][0]["label"] == "Write the report" and "Model name not found" in d["problems"][0]["reason"]
    assert d["message"].startswith("Can't start: Write the report") and d["can_force"] is False
    assert live(client, force=True).status_code == 409  # a wrong name can't be forced past


def test_preflight_can_be_forced_past_models_that_are_only_busy(env, client):
    env.status = {"gemini-3.8-flash": QuotaExceeded("daily"), "gemini-3.7-flash": RateLimited("429")}
    r = live(client)
    assert r.status_code == 409 and r.json()["detail"]["can_force"] is True
    r = live(client, force=True)
    assert r.status_code == 200 and set(r.json()["preflight_skipped"]) >= {"analyst", "writer"}


def test_preflight_names_a_missing_key(env, client, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    d = live(client).json()["detail"]
    assert "No key yet" in d["problems"][0]["reason"] and d["can_force"] is False


def test_replays_skip_the_preflight(env, client, monkeypatch, tmp_path):
    env.status = {m: ModelNotFound("gone") for m in env.listed}
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    (tmp_path / "run_x.jsonl").write_text(json.dumps({"type": "run.started", "ts": 1}) + "\n"
                                          + json.dumps({"type": "run.done", "ts": 2, "ok": True}) + "\n")
    monkeypatch.setattr(main, "recorded_runs", lambda: [{"run_id": "run_x", "complete": True, "ok": True}])
    assert client.post("/api/runs", json={"mode": "replay"}).status_code == 200 and not env.validated
