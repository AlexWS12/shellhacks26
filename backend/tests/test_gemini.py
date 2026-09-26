from types import SimpleNamespace

import pytest
from google.genai import errors

from app import config
from app.clients import cache, gemini

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


def err(code: int) -> errors.APIError:
    cls = errors.ServerError if code >= 500 else errors.ClientError
    return cls(code, {"error": {"code": code, "status": "X", "message": "test"}})


class FakeModels:
    # Each call pops the next outcome: an exception to raise, or the response / list of stream chunks to return.
    def __init__(self, outcomes: list) -> None:
        self.outcomes, self.calls = outcomes, []

    def _next(self, model: str):
        self.calls.append(model)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    async def generate_content(self, model, contents, config):
        return self._next(model)

    async def generate_content_stream(self, model, contents, config):
        items = self._next(model)

        async def gen():
            for item in items:
                if isinstance(item, Exception):
                    raise item
                yield SimpleNamespace(text=item)
        return gen()


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test")
    monkeypatch.setattr(config, "GEMINI_MODEL", "primary")
    monkeypatch.setattr(config, "GEMINI_FALLBACK_MODEL", "fallback")
    monkeypatch.setattr(config, "GEMINI_RETRIES", 3)

    async def no_backoff(attempt, e):
        pass
    monkeypatch.setattr(gemini, "_backoff", no_backoff)

    def install(outcomes: list) -> FakeModels:
        models = FakeModels(outcomes)
        monkeypatch.setattr(gemini, "_client", SimpleNamespace(aio=SimpleNamespace(models=models)))
        return models
    return install


def ok(text: str = '{"ok": true}'):
    return SimpleNamespace(text=text, usage_metadata=None)


async def collect(it) -> str:
    return "".join([c async for c in it])


async def test_retries_transient_errors(fake):
    m = fake([err(503), err(503), ok()])
    res = await gemini.generate_json("s", "p", SCHEMA)
    assert res["data"] == {"ok": True} and res["model"] == "primary"
    assert m.calls == ["primary"] * 3


async def test_falls_back_when_primary_keeps_failing(fake):
    m = fake([err(503)] * 3 + [ok()])
    res = await gemini.generate_json("s", "p", SCHEMA)
    assert res["model"] == "fallback"
    assert m.calls == ["primary"] * 3 + ["fallback"]


async def test_auth_error_is_not_retried(fake):
    m = fake([err(401)])
    assert await gemini.generate_json("s", "p", SCHEMA) is None
    assert m.calls == ["primary"]


async def test_stream_retries_before_first_chunk(fake):
    m = fake([err(429), ["Hello ", "world"]])
    assert await collect(gemini.stream_text("s", "p", pace=0)) == "Hello world"
    assert m.calls == ["primary"] * 2


async def test_stream_failure_after_first_chunk_raises(fake):
    m = fake([["Hello ", err(503)], ["should not be used"]])
    seen = []
    with pytest.raises(errors.ServerError):
        async for chunk in gemini.stream_text("s", "p", pace=0):
            seen.append(chunk)
    assert seen == ["Hello "] and m.calls == ["primary"]


async def test_cache_hit_makes_no_call(fake):
    fake([ok()])
    await gemini.generate_json("s", "p", SCHEMA)
    m = fake([])
    res = await gemini.generate_json("s", "p", SCHEMA)
    assert res["cached"] and res["data"] == {"ok": True} and m.calls == []

    fake([["cached text"]])
    await collect(gemini.stream_text("s", "q", pace=0))
    m = fake([])
    assert await collect(gemini.stream_text("s", "q", pace=0)) == "cached text"
    assert m.calls == []


async def test_cache_key_ignores_which_model_answered(fake, monkeypatch):
    fake([err(503)] * 3 + [ok()])
    await gemini.generate_json("s", "p", SCHEMA)
    monkeypatch.setattr(config, "GEMINI_FALLBACK_MODEL", "")
    m = fake([])
    res = await gemini.generate_json("s", "p", SCHEMA)
    assert res["cached"] and res["model"] == "fallback" and m.calls == []
