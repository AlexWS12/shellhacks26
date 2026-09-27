# The model registry: error mapping, fallback order, circuit breaker, events, key redaction, cache separation.
# Providers are faked; nothing here calls a real API.

import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors as gerrors

from app import config
from app.clients import cache, gemini, jev, judge, models
from app.clients.base import Judgment, Reply, Request
from app.clients.errors import (AuthError, BadResponse, ModelError, ModelNotFound, ProviderUnavailable, QuotaExceeded,
                                RateLimited, RoleExhausted, Timeout)
from app.clients.redact import redact

SECRET = "AIzaSyTESTKEY-0123456789abcdefghijklmnop"
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
YESNO = {"q": {"type": "noul", "instructions": "Is it?", "criteria": {"true": "yes", "false": "no"}}}


class Fake:
    # A provider whose answers are scripted per model: each call pops the next outcome (an exception or a value).
    # Text roles: the value is a list of chunks, where an exception in the list fails the stream at that point.
    PROVIDER, CACHE_VERSION, KINDS = "fake", "fake-v1", {"json", "text", "search", "judge"}

    def __init__(self) -> None:
        self.script: dict[str, list] = {}
        self.calls: list[str] = []
        self.off: str | None = None

    def disabled(self):
        return self.off

    def configured(self) -> bool:
        return self.off is None

    def payload(self, model, req):
        return {"model": model, "system": req.system, "prompt": req.prompt,
                "judgment": [req.judgment.state, req.judgment.questions] if req.judgment else None}

    def cached_value(self, req, hit):
        return hit["data"] if "data" in hit else hit["text"]

    def _next(self, model):
        self.calls.append(model)
        out = self.script.get(model, [ModelNotFound("not scripted")]).pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    async def call(self, model, req):
        out = self._next(model)
        return Reply(out, {"data": out, "model": model}, input_tokens=3, output_tokens=2)

    async def open_stream(self, model, req):
        chunks = list(self._next(model))

        async def rest():
            for c in chunks[1:]:
                if isinstance(c, Exception):
                    raise c
                yield c
        return chunks[0], rest()


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = Fake()
    monkeypatch.setitem(models.ADAPTERS, "fake", f)
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(models, "_sleep", _no_sleep)
    return f


async def _no_sleep(seconds):
    _no_sleep.waits.append(seconds)


_no_sleep.waits = []


@pytest.fixture
def roles(monkeypatch, tmp_path):
    # roles({"name": ("kind", ["model", ...])}) writes a config/models.json of fake models and loads it.
    def use(spec: dict, local: dict | None = None, provider: str = "fake"):
        base = {name: {"kind": kind, "models": [{"provider": provider, "model": m} for m in ms]}
                for name, (kind, ms) in spec.items()}
        (tmp_path / "models.json").write_text(json.dumps({"roles": base}))
        monkeypatch.setattr(config, "MODELS_FILE", tmp_path / "models.json")
        monkeypatch.setattr(config, "MODELS_LOCAL_FILE", tmp_path / "models.local.json")
        if local is not None:
            (tmp_path / "models.local.json").write_text(json.dumps({"roles": local}))
        monkeypatch.setattr(models, "_loaded", None)
    return use


def session() -> list[dict]:
    # Starts a run session whose events land in the returned list.
    events: list[dict] = []
    models.start(lambda type, **p: events.append({"type": type, **p}))
    return events


# ---- error mapping: Gemini

def gerr(code: int, message: str = "test", status: str = "X") -> gerrors.APIError:
    cls = gerrors.ServerError if code >= 500 else gerrors.ClientError
    return cls(code, {"error": {"code": code, "status": status, "message": message}})


@pytest.mark.parametrize("exc, cls", [
    (gerr(400, "API key not valid. Please pass a valid API key.", "INVALID_ARGUMENT"), AuthError),
    (gerr(401), AuthError),
    (gerr(403, "Permission denied", "PERMISSION_DENIED"), AuthError),
    (gerr(404, "models/gemini-nope is not found for API version v1beta", "NOT_FOUND"), ModelNotFound),
    (gerr(400, "model gemini-1.0-pro is deprecated", "INVALID_ARGUMENT"), ModelNotFound),
    (gerr(429, "Resource exhausted. Please try again later.", "RESOURCE_EXHAUSTED"), RateLimited),
    (gerr(429, "Quota exceeded for metric: GenerateRequestsPerDayPerProjectPerModel", "RESOURCE_EXHAUSTED"),
     QuotaExceeded),
    (gerr(429, "Quota exceeded, limit: 0", "RESOURCE_EXHAUSTED"), QuotaExceeded),
    # the live per-minute message also mentions billing: still just a rate limit
    (gerr(429, "You exceeded your current quota, please check your plan and billing details. * Quota exceeded for "
               "metric: generate_content_free_tier_requests, limit: 10", "RESOURCE_EXHAUSTED"), RateLimited),
    (gerr(500), ProviderUnavailable),
    (gerr(503, "The model is overloaded", "UNAVAILABLE"), ProviderUnavailable),
    (gerr(504, "Deadline exceeded", "DEADLINE_EXCEEDED"), Timeout),
    (gerr(400, "Invalid JSON schema", "INVALID_ARGUMENT"), BadResponse),
    (TimeoutError(), Timeout),
    (httpx.ConnectError("no route"), ProviderUnavailable),
])
def test_gemini_errors_are_mapped(exc, cls):
    assert type(gemini.classify(exc)) is cls


def test_gemini_daily_quota_is_read_from_the_structured_details():
    # The shape the live API returned on 2026-09-27.
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                      "message": "You exceeded your current quota, please check your plan and billing details.",
                      "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
                          {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": "20"}]},
                          {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "20s"}]}}
    assert type(gemini.classify(gerrors.ClientError(429, body))) is QuotaExceeded
    body["error"]["details"][0]["violations"][0]["quotaId"] = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
    e = gemini.classify(gerrors.ClientError(429, body))
    assert type(e) is RateLimited and e.retry_after == 20.0


def test_gemini_rate_limit_keeps_retry_delay():
    e = gemini.classify(gerrors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                                            "message": "slow down", "details": [{"retryDelay": "7s"}]}}))
    assert isinstance(e, RateLimited) and e.retry_after == 7.0


class FakeGeminiModels:
    def __init__(self, outcomes: list) -> None:
        self.outcomes, self.calls = outcomes, []

    async def generate_content(self, model, contents, config):
        self.calls.append(model)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(text=out, usage_metadata=None, candidates=[])


@pytest.fixture
def fake_gemini(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GEMINI_API_KEY", SECRET)
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(models, "_sleep", _no_sleep)

    def install(outcomes: list) -> FakeGeminiModels:
        m = FakeGeminiModels(outcomes)
        monkeypatch.setattr(gemini, "_client", lambda api_key=None: SimpleNamespace(aio=SimpleNamespace(models=m)))
        return m
    return install


@pytest.mark.parametrize("text", ["not json", '{"ok": "yes"}', "{}"])
async def test_gemini_bad_json_or_schema_is_bad_response(fake_gemini, text):
    fake_gemini([text])
    with pytest.raises(BadResponse):
        await gemini.call("m", Request("json", "s", "p", SCHEMA))


async def test_gemini_missing_key_is_auth_error(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    with pytest.raises(AuthError, match="GEMINI_API_KEY"):
        await gemini.call("m", Request("json", "s", "p", SCHEMA))


async def test_gemini_answers_judgments_in_jev_shape(fake_gemini):
    fake_gemini(['{"p_true": 1.4}', '{"stuck": 0.2, "progress": 3}'])
    r = await gemini.call("m", Request("judge", judgment=Judgment("s", YESNO)))
    assert r.value == {"q": {"noul": 1.0}}  # clamped to a probability
    two = {"stuck": YESNO["q"], "progress": {"type": "score", "instructions": "How far?", "criteria": ["a", "b", "c", "d"]}}
    r = await gemini.call("m", Request("judge", judgment=Judgment("s", two)))
    assert r.value == {"stuck": {"noul": 0.2}, "progress": {"score": 3.0}}


# ---- error mapping: Jev

def jresp(code: int, body: dict | None = None, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(code, json=body or {}, headers=headers or {})


@pytest.mark.parametrize("resp, cls", [
    (jresp(401, {"detail": {"error_type": "authentication_error", "message": "Cannot authenticate"}}), AuthError),
    (jresp(403), AuthError),
    (jresp(400, {"detail": {"error_type": "api_usage_error", "message": "Unknown model: jev-nope-9"}}), ModelNotFound),
    (jresp(422, {"detail": {"message": "invalid model name"}}), ModelNotFound),
    (jresp(402, {"detail": {"message": "Out of credits"}}), QuotaExceeded),
    (jresp(429, {"detail": {"message": "Rate limit"}}, {"retry-after": "3"}), RateLimited),
    (jresp(429, {"detail": {"message": "Monthly quota exceeded"}}), QuotaExceeded),
    (jresp(529, {"detail": {"message": "Overloaded"}}), ProviderUnavailable),
    (jresp(500), ProviderUnavailable),
    (jresp(422, {"detail": {"message": "questions.q.criteria: field required"}}), BadResponse),
])
def test_jev_statuses_are_mapped(resp, cls):
    e = jev.classify_status(resp)
    assert type(e) is cls
    if cls is RateLimited:
        assert e.retry_after == 3.0


def test_jev_transport_errors_are_mapped():
    assert type(jev.classify(httpx.ReadTimeout("slow"))) is Timeout
    assert type(jev.classify(httpx.ConnectError("down"))) is ProviderUnavailable


class FakeHttp:
    # Stands in for httpx.AsyncClient; every post returns the same response.
    def __init__(self, resp: httpx.Response) -> None:
        self.resp, self.posts = resp, 0

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a) -> None:
        pass

    async def post(self, *a, **k):
        self.posts += 1
        return self.resp

    async def get(self, *a, **k):
        return self.resp


@pytest.fixture
def jev_on(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "JEV_PROVIDER", "typesafe")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test-key-123456")
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")

    def install(resp: httpx.Response) -> FakeHttp:
        h = FakeHttp(resp)
        monkeypatch.setattr(jev.httpx, "AsyncClient", h)
        return h
    return install


async def test_jev_reply_without_answers_is_bad_response(jev_on):
    jev_on(jresp(200, {"answers": {"q": {"choice": "x"}}}))
    with pytest.raises(BadResponse):
        await jev.call("jev-latest", Request("judge", judgment=Judgment("s", YESNO)))


async def test_jev_missing_key_is_auth_error(jev_on, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    with pytest.raises(AuthError, match="TYPESAFE_API_KEY"):
        await jev.call("jev-latest", Request("judge", judgment=Judgment("s", YESNO)))


def test_jev_off_or_mock_is_skipped_not_failed(monkeypatch):
    for host in ("", "mock"):
        monkeypatch.setattr(config, "JEV_PROVIDER", host)
        assert jev.disabled()


async def test_live_only_calls_are_neither_read_nor_cached(jev_on, roles, monkeypatch):
    # The watchdog asks every few seconds with cache=False; storing those answers only churned the cache.
    roles({"watchdog": ("judge", ["jev-latest"])}, provider="jev")
    h = jev_on(jresp(200, {"answers": {"q": {"noul": 0.5}}, "usage": {"input_tokens": 1}}))
    await models.call("watchdog", Judgment("s", YESNO), cache=False)
    await models.call("watchdog", Judgment("s", YESNO), cache=False)
    assert h.posts == 2 and not (cache.CACHE_DIR / "jev").exists()
    await models.call("watchdog", Judgment("s", YESNO))
    await models.call("watchdog", Judgment("s", YESNO))
    assert h.posts == 3 and len(list((cache.CACHE_DIR / "jev").glob("*.json"))) == 1


# ---- fallback policy and the circuit breaker

async def test_dead_models_are_skipped_at_once_and_for_the_rest_of_the_run(fake, roles):
    roles({"r": ("json", ["gone", "bad-key", "good"])})
    fake.script = {"gone": [ModelNotFound("no such model")], "bad-key": [AuthError("401")],
                   "good": [{"ok": True}, {"ok": True}]}
    events = session()
    r = await models.call("r", "p1", SCHEMA)
    assert (r.value, r.model) == ({"ok": True}, "good") and fake.calls == ["gone", "bad-key", "good"]
    await models.call("r", "p2", SCHEMA)
    assert fake.calls == ["gone", "bad-key", "good", "good"]  # both dead models skipped, no retry, no event
    failed = [e for e in events if e["type"] == "model.call_failed"]
    assert [(e["model"], e["error_class"], e["will_fallback"], e["will_retry"]) for e in failed] == [
        ("gone", "ModelNotFound", True, False), ("bad-key", "AuthError", True, False)]
    assert [(e["from"], e["to"]) for e in events if e["type"] == "model.fallback_used"] == [("fake/gone", "fake/good")]


async def test_a_new_run_tries_dead_models_again(fake, roles):
    roles({"r": ("json", ["flaky", "good"])})
    fake.script = {"flaky": [QuotaExceeded("daily"), {"ok": True}], "good": [{"ok": True}]}
    session()
    assert (await models.call("r", "p1", SCHEMA)).model == "good"
    session()  # the next run
    assert (await models.call("r", "p2", SCHEMA)).model == "flaky"


@pytest.mark.parametrize("err", [RateLimited("429"), Timeout("slow"), ProviderUnavailable("503")])
async def test_transient_errors_retry_three_times_with_backoff_then_fall_back(fake, roles, err):
    roles({"r": ("json", ["a", "b"])})
    fake.script = {"a": [err, err, err], "b": [{"ok": True}]}
    _no_sleep.waits.clear()
    events = session()
    assert (await models.call("r", "p", SCHEMA)).model == "b"
    assert fake.calls == ["a", "a", "a", "b"]
    assert len(_no_sleep.waits) == 2 and 1 <= _no_sleep.waits[0] < 2 <= _no_sleep.waits[1] < 3  # 1 s, 2 s + jitter
    failed = [e for e in events if e["type"] == "model.call_failed"]
    assert [(e["attempt"], e["will_retry"], e["will_fallback"]) for e in failed] == [
        (1, True, False), (2, True, False), (3, False, True)]


async def test_transient_error_recovers_on_the_same_model(fake, roles):
    roles({"r": ("json", ["a", "b"])})
    fake.script = {"a": [RateLimited("429", retry_after=4.0), {"ok": True}]}
    _no_sleep.waits.clear()
    events = session()
    assert (await models.call("r", "p", SCHEMA)).model == "a"
    assert _no_sleep.waits == [4.0]  # the provider's retry-after wins
    assert not [e for e in events if e["type"] == "model.fallback_used"]


async def test_bad_response_gets_one_retry_then_falls_back(fake, roles):
    roles({"r": ("json", ["a", "b"])})
    fake.script = {"a": [BadResponse("not json"), BadResponse("not json")], "b": [{"ok": True}]}
    session()
    assert (await models.call("r", "p", SCHEMA)).model == "b"
    assert fake.calls == ["a", "a", "b"]


async def test_role_exhausted_carries_every_attempt(fake, roles):
    roles({"r": ("json", ["a", "b"])})
    fake.script = {"a": [ModelNotFound("gone")], "b": [BadResponse("x"), BadResponse("y")]}
    events = session()
    with pytest.raises(RoleExhausted) as info:
        await models.call("r", "p", SCHEMA)
    assert [(a["model"], a["error_class"], a["attempt"]) for a in info.value.attempts] == [
        ("a", "ModelNotFound", 1), ("b", "BadResponse", 1), ("b", "BadResponse", 2)]
    exhausted = [e for e in events if e["type"] == "role.exhausted"]
    assert len(exhausted) == 1 and exhausted[0]["role"] == "r" and len(exhausted[0]["attempts"]) == 3
    # b isn't dead (a bad reply may be a one-off), so the next call tries it again and reports again
    fake.script["b"] = [BadResponse("z"), BadResponse("z")]
    with pytest.raises(RoleExhausted):
        await models.call("r", "p2", SCHEMA)
    assert len([e for e in events if e["type"] == "role.exhausted"]) == 2  # b was really tried again


async def test_exhaustion_from_dead_models_only_is_reported_once(fake, roles):
    roles({"r": ("json", ["a"])})
    fake.script = {"a": [AuthError("401")]}
    events = session()
    for p in ("p1", "p2", "p3"):
        with pytest.raises(RoleExhausted):
            await models.call("r", p, SCHEMA)
    assert len([e for e in events if e["type"] == "role.exhausted"]) == 1
    assert fake.calls == ["a"]


async def test_a_switched_off_provider_is_skipped_without_events(fake, roles):
    roles({"r": ("json", ["a"])})
    fake.off = "turned off"
    events = session()
    with pytest.raises(RoleExhausted):
        await models.call("r", "p", SCHEMA)
    assert fake.calls == [] and events == []


async def test_events_carry_the_agent(fake, roles):
    roles({"r": ("json", ["a", "b"])})
    fake.script = {"a": [ModelNotFound("gone")], "b": [{"ok": True}]}
    events = session()
    models.AGENT.set("extract_desc")
    await models.call("r", "p", SCHEMA)
    assert {e["agent_id"] for e in events} == {"extract_desc"}


# ---- streams

async def test_stream_falls_back_before_the_first_chunk(fake, roles):
    roles({"w": ("text", ["a", "b"])})
    fake.script = {"a": [ProviderUnavailable("503")] * 3, "b": [["Hel", "lo"]]}
    events = session()
    s = models.stream("w", "p", pace=0)
    assert "".join([c async for c in s]) == "Hello" and s.model == "b"
    assert [e["type"] for e in events].count("model.fallback_used") == 1
    fake.script["a"] = [ProviderUnavailable("503")] * 3  # a overloaded isn't dead, so it's asked again first
    again = models.stream("w", "p", pace=0)  # then b's answer comes from the cache
    assert "".join([c async for c in again]) == "Hello" and again.cached and again.model == "b"


async def test_stream_failure_after_first_chunk_raises_without_retry(fake, roles):
    roles({"w": ("text", ["a", "b"])})
    fake.script = {"a": [["Hel", ProviderUnavailable("reset")]]}
    events = session()
    with pytest.raises(ProviderUnavailable):
        _ = [c async for c in models.stream("w", "p", pace=0)]
    assert fake.calls == ["a"]  # b is never started: text is already on screen
    e = next(e for e in events if e["type"] == "model.call_failed")
    assert e["during"] == "stream" and not e["will_fallback"]


async def test_agent_fails_when_template_fallback_is_off(fake, roles):
    from app.agents.coordination import _write
    from app.runtime.agent import AgentSpec, Ctx
    from app.runtime.run import new_run

    roles({"mediator": ("text", ["a"])})
    run = new_run("live")
    ctx = Ctx(run, AgentSpec("mediator", "Mediator", "", ["gemini"]))
    fake.script = {"a": [AuthError("401")]}
    session()
    assert await _write(ctx, "mediator", "s", "p1", "fallback") == ("fallback", "template", None)
    run.templates = False
    session()
    fake.script = {"a": [AuthError("401")]}
    with pytest.raises(RuntimeError, match="template fallback is off"):
        await _write(ctx, "mediator", "s", "p2", "fallback")


# ---- the judge on top of the registry

async def test_judge_records_who_answered_and_falls_back_to_the_rule(fake, roles):
    roles({"confirm_osm": ("judge", ["a", "b"])})
    fake.script = {"a": [Timeout("slow")] * 3, "b": [{"q": {"noul": 0.9}}]}
    session()
    v = await judge.noul("state", "Same?", "yes", "no", lambda: 0.1, role="confirm_osm")
    assert (v.value, v.actor, v.model) == (0.9, "fake", "b")
    fake.script = {"b": [ModelNotFound("gone")]}
    v = await judge.noul("state 2", "Same?", "yes", "no", lambda: 0.1, role="confirm_osm")
    assert (v.value, v.actor, v.model) == (0.1, "heuristic", None)


# ---- cache: one entry per model

async def test_models_never_share_cached_answers(fake, roles):
    roles({"r": ("json", ["a"]), "r2": ("json", ["b"])})
    fake.script = {"a": [{"ok": True}], "b": [{"ok": False}]}
    session()
    assert (await models.call("r", "same prompt", SCHEMA)).value == {"ok": True}
    assert (await models.call("r2", "same prompt", SCHEMA)).value == {"ok": False}  # a's answer isn't reused
    assert fake.calls == ["a", "b"]
    assert len(list((cache.CACHE_DIR / "fake").glob("*.json"))) == 2
    hit = await models.call("r", "same prompt", SCHEMA)
    assert hit.cached and hit.model == "a" and fake.calls == ["a", "b"]


async def test_a_cached_answer_filed_under_the_wrong_model_is_ignored(fake, roles):
    roles({"r": ("json", ["a"])})
    ad = models.ADAPTERS["fake"]
    req = Request("json", "", "p", SCHEMA)
    cache.put("fake", "fake-v1", ad.payload("a", req), {"data": {"ok": False}, "model": "someone-else"})
    fake.script = {"a": [{"ok": True}]}
    session()
    r = await models.call("r", "p", SCHEMA)
    assert (r.value, r.cached) == ({"ok": True}, False)


def test_gemini_cache_key_names_the_model():
    req = Request("json", "s", "p", SCHEMA)
    a, b = gemini.payload("gemini-3.8-flash", req), gemini.payload("gemini-3.7-flash", req)
    assert cache._path("gemini", gemini.CACHE_VERSION, a) != cache._path("gemini", gemini.CACHE_VERSION, b)
    # the shape before the registry, so existing entries keep hitting for the model that made them
    assert a == {"model": "gemini-3.8-flash", "system": "s", "prompt": "p", "schema": SCHEMA}
    j = Request("judge", judgment=Judgment("s", YESNO))
    assert jev.payload("jev-latest", j) != jev.payload("jev-preview", j)


def test_committed_cache_still_hits_for_the_default_models():
    # scripts/migrate_cache.py filed every committed Jev answer under jev-latest, the configured default.
    files = list((config.CACHE_DIR / "jev").glob("*.json"))
    assert files
    d = json.loads(files[0].read_text(encoding="utf-8"))
    assert d["payload"]["model"] == "jev-latest"
    assert cache._path("jev", jev.CACHE_VERSION, d["payload"]) == files[0]
    roles_now = models.load()
    assert roles_now["confirm_osm"].models[0] == models.ModelRef("jev", "jev-latest")


# ---- keys never leak

async def test_keys_are_redacted_from_errors_events_logs_and_cache(fake, roles, monkeypatch, caplog):
    monkeypatch.setattr(config, "GEMINI_API_KEY", SECRET)
    roles({"r": ("json", ["a", "b"])})
    fake.script = {"a": [AuthError(f"key {SECRET} rejected")], "b": [{"ok": True}]}
    events = session()
    with caplog.at_level(logging.WARNING):
        logging.getLogger("x").warning("calling with key=%s", SECRET)
        await models.call("r", "p", SCHEMA)
    assert SECRET not in caplog.text and SECRET not in json.dumps(events)
    assert "[redacted]" in json.dumps(events)
    assert all(SECRET not in p.read_text() for p in (cache.CACHE_DIR / "fake").glob("*.json"))
    e = gemini.classify(gerr(400, f"API key not valid: {SECRET}"))
    assert SECRET not in str(e) and SECRET not in models.describe(e)
    assert SECRET not in str(RoleExhausted("r", [{"provider": "p", "model": SECRET, "error_class": "X"}]))


def test_run_log_redacts_a_key_in_any_event(tmp_path, monkeypatch):
    from app.runtime import run as runmod

    monkeypatch.setattr(config, "GEMINI_API_KEY", SECRET)
    monkeypatch.setattr(runmod, "RUNS_DIR", tmp_path)
    r = runmod.new_run("live")
    r.open_log()
    r.emit("agent.log", text=f"oops {SECRET}")
    r.close()
    assert SECRET not in (tmp_path / f"{r.id}.jsonl").read_text() and SECRET not in json.dumps(r.events)


def test_patterns_catch_keys_we_were_never_told_about():
    # Made-up values, joined at runtime so secret scanners don't flag this file.
    token, gkey, password = "-".join(["sk", "live", "abcdefgh12345"]), "AIza" + "AnotherKey0123456789abcdef", "hunter2" + "pass"
    text = (f"Authorization: Bearer {token} url=https://x.test/v1?key={gkey} "
            f"postgres://user:{password}@db.example:5432/tsdb")
    out = redact(text)
    assert token not in out and "AIzaAnotherKey" not in out and password not in out


def test_health_never_returns_a_key(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(config, "GEMINI_API_KEY", SECRET)
    body = TestClient(app).get("/api/health").text
    assert SECRET not in body and "extract_fallback" in body


# ---- validate()

class FakeListing:
    def __init__(self, names: list[str], err: Exception | None = None) -> None:
        self.names, self.err = names, err

    async def list(self):
        if self.err:
            raise self.err

        async def gen():
            for n in self.names:
                yield SimpleNamespace(name=f"models/{n}")
        return gen()

    async def generate_content(self, model, contents, config):
        return SimpleNamespace(text="OK", usage_metadata=None)


@pytest.mark.parametrize("listing, status", [
    (FakeListing(["gemini-3.8-flash"]), "ok"),
    (FakeListing(["gemini-3.7-flash"]), "ModelNotFound"),
    (FakeListing([], gerr(400, "API key not valid. Please pass a valid API key.", "INVALID_ARGUMENT")), "AuthError"),
])
async def test_validate_gemini(monkeypatch, listing, status):
    monkeypatch.setattr(gemini, "_client", lambda api_key=None: SimpleNamespace(aio=SimpleNamespace(models=listing)))
    r = await models.validate("gemini", "gemini-3.8-flash", "pasted-key-abcdefgh")
    assert r["status"] == status and "pasted-key-abcdefgh" not in r["message"]


@pytest.mark.parametrize("resp, status", [
    (jresp(200, {"models": [{"name": "jev-latest"}], "answers": {"same": {"noul": 0.9}}}), "ok"),
    (jresp(401, {"detail": {"message": "Cannot authenticate"}}), "AuthError"),
    (jresp(400, {"detail": {"message": "Unknown model: jev-nope"}}), "ModelNotFound"),
])
async def test_validate_jev(jev_on, resp, status):
    jev_on(resp)
    assert (await models.validate("jev", "jev-latest", "ts-pasted-key-1234"))["status"] == status


# ---- config

def test_local_file_replaces_a_role_and_bad_configs_are_refused(fake, roles):
    roles({"r": ("json", ["a", "b"])}, local={"r": {"models": [{"provider": "fake", "model": "local-only"}]}})
    assert models.chain("r") == ["fake/local-only"]
    roles({"r": ("json", ["a"])}, local={"r": {"models": [{"provider": "nope", "model": "x"}]}})
    with pytest.raises(models.ConfigError, match="unknown provider"):
        models.roles()
    roles({"r": ("text", ["jev-latest"])}, local={}, provider="jev")
    with pytest.raises(models.ConfigError, match="can't answer text"):
        models.roles()


ROLES = {"extract_fallback", "extract_submission", "reader", "endpoint_split", "confirm_osm", "confirm_town", "confirm_place",
         "classify_type", "validate_semantic", "research_confirm", "research_search", "research_extract", "analyst",
         "advocate", "mediator", "writer", "watchdog", "smoke"}


def test_committed_config_has_every_role_the_code_uses():
    from pathlib import Path

    assert set(models.load()) == ROLES
    source = "\n".join(p.read_text(encoding="utf-8") for p in Path("app").rglob("*.py"))
    for role in ROLES:
        assert f'"{role}"' in source, f"no call site uses {role}"


def test_no_llm_near_the_math():
    # Hard rule: distances and dates are code only.
    from pathlib import Path

    for f in ("app/core/overlap.py", "app/core/sheets.py", "app/core/research.py", "app/core/extract_desc.py",
              "app/core/extract_ga.py"):
        text = Path(f).read_text(encoding="utf-8")
        assert "clients" not in text and "models." not in text, f


def test_model_error_messages_are_short_and_classed():
    e = ModelError("x" * 1000)
    assert len(e.message) == 300 and e.error_class == "ModelError"
    assert models.describe(ModelNotFound("gone", "gemini", "m")) == "ModelNotFound (gemini/m): gone"



def test_a_run_with_a_wrong_primary_model_finishes_on_the_fallback(fake, monkeypatch, tmp_path):
    # The whole offline pipeline, with every writing role's primary set to a model that doesn't exist.
    from app.pipeline import build_pipeline
    from app.runtime.executor import execute
    from app.runtime.run import new_run

    committed = json.loads(config.MODELS_FILE.read_text(encoding="utf-8"))["roles"]
    local = {r: {"models": [{"provider": "fake", "model": "gemini-does-not-exist"}, {"provider": "fake", "model": "good"}]}
             for r in ("analyst", "advocate", "mediator", "writer")}
    (tmp_path / "models.json").write_text(json.dumps({"roles": committed}))
    (tmp_path / "models.local.json").write_text(json.dumps({"roles": local}))
    monkeypatch.setattr(config, "MODELS_FILE", tmp_path / "models.json")
    monkeypatch.setattr(config, "MODELS_LOCAL_FILE", tmp_path / "models.local.json")
    monkeypatch.setattr(models, "_loaded", None)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "JEV_PROVIDER", "")
    monkeypatch.setattr(config, "OSM_LIVE", False)
    monkeypatch.setattr(cache, "CACHE_DIR", config.CACHE_DIR)  # the committed caches, read only: fake answers aren't stored there
    fake.script = {"gemini-does-not-exist": [ModelNotFound("404 NOT_FOUND: models/gemini-does-not-exist is not found")],
                   "good": [["Both projects ", "are close."] for _ in range(40)]}
    puts: list = []
    real_put = cache.put
    monkeypatch.setattr(cache, "put", lambda p, *a: puts.append(p) if p == "fake" else real_put(p, *a))

    run = new_run("live")
    run.pace = 0
    asyncio.run(execute(run, *build_pipeline()))
    ev = run.events
    assert ev[-1]["type"] == "run.done" and ev[-1]["ok"], ev[-1]
    failed = [e for e in ev if e["type"] == "model.call_failed" and e["model"] == "gemini-does-not-exist"]
    assert len(failed) == 1  # the breaker: every later call skips the dead model
    assert (failed[0]["role"], failed[0]["error_class"], failed[0]["will_fallback"]) == ("analyst", "ModelNotFound", True)
    assert failed[0]["agent_id"] == "analyst"
    used = [e for e in ev if e["type"] == "model.fallback_used"]
    assert [(e["role"], e["from"], e["to"]) for e in used] == [("analyst", "fake/gemini-does-not-exist", "fake/good")]
    written = [e for e in ev if e["type"] in ("analysis.ready", "brief.side")]
    assert written and all((e["actor"], e["model"]) == ("fake", "good") for e in written)
    report = next(e for e in ev if e["type"] == "report.ready")
    assert report["model"] == "good" and report["report"]["next_steps"]["model"] == "good"
    assert not [e for e in ev if e["type"] == "role.exhausted" and e["role"] in local]
    assert all("model" in e for e in ev if e["type"] == "judgment")


async def test_parallel_calls_to_an_unproven_model_fail_it_once(fake, roles):
    # 8 calls at once to a wrong model name: one probe fails, the other 7 wait for it and skip without an event.
    roles({"r": ("judge", ["wrong", "right"])})
    fake.script = {"wrong": [ModelNotFound("gone")] * 8, "right": [{"q": {"noul": 0.5}}] * 8}
    events = session()
    rs = await asyncio.gather(*(models.call("r", Judgment(f"s{i}", YESNO)) for i in range(8)))
    assert {r.model for r in rs} == {"right"} and fake.calls.count("wrong") == 1
    assert [e["type"] for e in events].count("model.call_failed") == 1
    assert [e["type"] for e in events].count("model.fallback_used") == 1


def test_log_redaction_keeps_the_record_shape_formatters_expect():
    # uvicorn's access formatter unpacks record.args itself; redaction must not flatten them.
    rec = logging.getLogger("uvicorn.access").makeRecord("uvicorn.access", logging.INFO, "f", 1, '%s - "%s %s HTTP/%s" %d',
                                                         ("127.0.0.1:1", "GET", "/api/health", "1.1", 200), None)
    rec = logging.getLogRecordFactory()("uvicorn.access", logging.INFO, "f", 1, rec.msg, rec.args, None)
    assert rec.args == ("127.0.0.1:1", "GET", "/api/health", "1.1", 200)
    rec = logging.getLogRecordFactory()("x", logging.INFO, "f", 1, "key %s", (ValueError(SECRET),), None)
    assert SECRET not in rec.getMessage()


def test_dated_prices(tmp_path, monkeypatch):
    from datetime import date
    (tmp_path / "m.json").write_text(json.dumps({"roles": {}, "prices": {
        "g/a": [{"input_per_mtok": 1, "output_per_mtok": 2}, {"from": "2027-01-01", "input_per_mtok": 3, "output_per_mtok": 4}],
        "g/b": {"input_per_mtok": 0.25, "output_per_mtok": 1.5},
        "g/later": [{"from": "2030-01-01", "input_per_mtok": 9, "output_per_mtok": 9}]}}))
    monkeypatch.setattr(config, "MODELS_FILE", tmp_path / "m.json")
    monkeypatch.setattr(config, "MODELS_LOCAL_FILE", tmp_path / "none.json")
    now = models.prices(date(2026, 9, 27))
    assert now["g/a"] == {"input_per_mtok": 1, "output_per_mtok": 2} and now["g/b"]["output_per_mtok"] == 1.5
    assert "g/later" not in now  # no price in effect yet: unknown, not guessed
    assert models.prices(date(2027, 1, 1))["g/a"] == {"input_per_mtok": 3, "output_per_mtok": 4}


def test_committed_prices_cover_the_reader_models(monkeypatch):
    monkeypatch.setattr(config, "MODELS_LOCAL_FILE", config.MODELS_FILE.with_name("absent.json"))
    chain = [r.name for r in models.load()["reader"].models]
    assert all(n in models.prices() for n in chain)
