# The Claude and OpenAI adapters: schema shaping for strict structured output, error mapping, replies turned into the
# registry's shapes (json, judge, search, streamed text). No network: the SDK clients are replaced by fakes.

import json
from types import SimpleNamespace as NS

import anthropic
import httpx2
import openai as openai_sdk
import pytest

from app.clients import claude, gemini, models, openai
from app.clients import schema as jsonschema
from app.clients.base import Judgment, Request
from app.clients.errors import (AuthError, BadResponse, ModelNotFound, ProviderUnavailable, QuotaExceeded, RateLimited,
                                Timeout)

SCHEMA = {"type": "object", "properties": {
    "name": {"type": "string"}, "score": {"type": "number", "minimum": 0, "maximum": 1},
    "note": {"type": "string"}, "when": {"type": ["string", "null"]},
    "items": {"type": "array", "maxItems": 2, "items": {"type": "object", "properties": {"x": {"type": "integer"}},
                                                        "required": ["x"]}}},
    "required": ["name", "score", "items"]}


# ---- schema shaping

def test_closed_schema_for_claude():
    s = jsonschema.closed(SCHEMA)
    assert s["additionalProperties"] is False and s["required"] == ["name", "score", "items"]
    assert s["properties"]["score"] == {"type": "number"}  # constraints are checked on our side instead
    assert s["properties"]["when"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    item = s["properties"]["items"]
    assert "maxItems" not in item and item["items"]["additionalProperties"] is False


def test_closed_schema_for_openai_makes_every_property_required_and_undoes_it():
    s = jsonschema.closed(SCHEMA, all_required=True)
    assert s["required"] == list(SCHEMA["properties"])
    assert s["properties"]["note"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert s["properties"]["when"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}  # already nullable
    reply = {"name": "a", "score": 0.5, "note": None, "when": None, "items": [{"x": 1}]}
    back = jsonschema.without_added_nulls(reply, SCHEMA)
    assert back == {"name": "a", "score": 0.5, "when": None, "items": [{"x": 1}]}
    assert not jsonschema.problems(back, SCHEMA)


def test_nullable_object_keeps_its_properties():
    s = jsonschema.closed({"type": ["object", "null"], "properties": {"v": {"type": "string"}}, "required": ["v"]})
    obj, null = s["anyOf"]
    assert obj["properties"] == {"v": {"type": "string"}} and obj["additionalProperties"] is False and null == {"type": "null"}


# ---- error mapping

def _resp(code: int, headers: dict | None = None) -> httpx2.Response:
    return httpx2.Response(code, headers=headers or {}, request=httpx2.Request("POST", "https://api.example"))


def _anth(cls, code: int, kind: str, msg: str, headers: dict | None = None):
    return cls(msg, response=_resp(code, headers), body={"type": "error", "error": {"type": kind, "message": msg}})


@pytest.mark.parametrize("exc,cls", [
    (_anth(anthropic.AuthenticationError, 401, "authentication_error", "invalid x-api-key"), AuthError),
    (_anth(anthropic.PermissionDeniedError, 403, "permission_error", "no access"), AuthError),
    (_anth(anthropic.NotFoundError, 404, "not_found_error", "model: claude-nope"), ModelNotFound),
    (_anth(anthropic.RateLimitError, 429, "rate_limit_error", "slow down"), RateLimited),
    (_anth(anthropic.BadRequestError, 400, "invalid_request_error", "Your credit balance is too low"), QuotaExceeded),
    (_anth(anthropic.BadRequestError, 400, "invalid_request_error", "max_tokens too large"), BadResponse),
    (_anth(anthropic.InternalServerError, 529, "overloaded_error", "Overloaded"), ProviderUnavailable),
    (anthropic.APITimeoutError(httpx2.Request("POST", "https://api.example")), Timeout),
    (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.example")), ProviderUnavailable),
])
def test_claude_errors_are_mapped(exc, cls):
    assert type(claude.classify(exc)) is cls


def test_claude_rate_limit_keeps_retry_after():
    e = claude.classify(_anth(anthropic.RateLimitError, 429, "rate_limit_error", "slow", {"retry-after": "7"}))
    assert isinstance(e, RateLimited) and e.retry_after == 7.0


def _oai(cls, code: int, msg: str, err: str | None = None, headers: dict | None = None):
    return cls(msg, response=_resp(code, headers), body={"message": msg, "code": err, "type": "x"})


@pytest.mark.parametrize("exc,cls", [
    (_oai(openai_sdk.AuthenticationError, 401, "Incorrect API key provided"), AuthError),
    (_oai(openai_sdk.NotFoundError, 404, "The model `gpt-nope` does not exist", "model_not_found"), ModelNotFound),
    (_oai(openai_sdk.BadRequestError, 400, "The requested model 'gpt-nope' does not exist."), ModelNotFound),
    (_oai(openai_sdk.RateLimitError, 429, "You exceeded your current quota", "insufficient_quota"), QuotaExceeded),
    (_oai(openai_sdk.RateLimitError, 429, "Rate limit reached", "rate_limit_exceeded", {"retry-after": "3"}), RateLimited),
    (_oai(openai_sdk.BadRequestError, 400, "Invalid schema for response_format"), BadResponse),
    (_oai(openai_sdk.InternalServerError, 500, "server error"), ProviderUnavailable),
    (openai_sdk.APITimeoutError(httpx2.Request("POST", "https://api.example")), Timeout),
])
def test_openai_errors_are_mapped(exc, cls):
    assert type(openai.classify(exc)) is cls


# ---- Claude replies

class FakeMessages:
    def __init__(self, *replies) -> None:
        self.replies, self.calls = list(replies), []

    async def create(self, **params):
        self.calls.append(params)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _msg(*content, stop="end_turn", usage=(10, 5)):
    return NS(content=list(content), stop_reason=stop, usage=NS(input_tokens=usage[0], output_tokens=usage[1]))


def _text(t, citations=None):
    return NS(type="text", text=t, citations=citations)


@pytest.fixture
def fake_claude(monkeypatch):
    def install(*replies):
        fake = FakeMessages(*replies)
        monkeypatch.setattr(claude, "_client", lambda api_key=None: NS(messages=fake))
        return fake
    return install


async def test_claude_json_reply(fake_claude):
    fake = fake_claude(_msg(_text(json.dumps({"name": "a", "score": 0.4, "items": []}))))
    r = await claude.call("claude-opus-5", Request("json", "sys", "prompt", SCHEMA))
    assert r.value == {"name": "a", "score": 0.4, "items": []} and (r.input_tokens, r.output_tokens) == (10, 5)
    sent = fake.calls[0]
    assert sent["system"] == "sys" and sent["messages"] == [{"role": "user", "content": "prompt"}]
    assert sent["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert "temperature" not in sent


async def test_claude_reply_outside_the_constraints_is_bad(fake_claude):
    fake_claude(_msg(_text(json.dumps({"name": "a", "score": 3, "items": []}))))
    with pytest.raises(BadResponse, match="above 1"):
        await claude.call("claude-opus-5", Request("json", "", "p", SCHEMA))


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
async def test_claude_refusal_or_cut_off_is_bad(fake_claude, stop):
    fake_claude(_msg(_text("{}"), stop=stop))
    with pytest.raises(BadResponse):
        await claude.call("claude-opus-5", Request("json", "", "p", SCHEMA))


async def test_claude_judge_reply(fake_claude):
    fake_claude(_msg(_text('{"p_true": 0.9}')))
    j = Judgment("state", {"q": {"type": "noul", "instructions": "same?", "criteria": {"true": "y", "false": "n"}}})
    r = await claude.call("claude-opus-5", Request("judge", judgment=j))
    assert r.value == {"q": {"noul": 0.9}}


async def test_claude_search_continues_a_paused_turn_and_cites(fake_claude):
    cite = NS(type="web_search_result_location", url="https://a.example", title="A", cited_text="x")
    results = NS(type="web_search_tool_result", content=[NS(url="https://a.example", title="A"),
                                                         NS(url="https://b.example", title="B")])
    fake = fake_claude(_msg(NS(type="server_tool_use"), results, stop="pause_turn"),
                       _msg(_text("Intro. "), _text("Line é built", [cite]), _text(".")))
    r = await claude.call("claude-sonnet-5", Request("search", "", "find"))
    assert fake.calls[0]["tools"] == [{"type": "web_search_20260209", "name": "web_search"}]
    assert fake.calls[1]["messages"][-1]["role"] == "assistant"
    assert r.value["sources"] == [{"url": "https://a.example", "title": "A"}]
    assert gemini.cite(r.value["text"], r.value["supports"]) == "Intro. Line é built[1]."


async def test_claude_search_on_older_models_uses_the_basic_tool(fake_claude):
    fake = fake_claude(_msg(_text("nothing")))
    await claude.call("claude-haiku-4-5", Request("search", "", "find"))
    assert fake.calls[0]["tools"][0]["type"] == "web_search_20250305"


class FakeStream:
    def __init__(self, events) -> None:
        self.events = events

    def __aiter__(self):
        async def gen():
            for e in self.events:
                if isinstance(e, Exception):
                    raise e
                yield e
        return gen()


def _delta(t):
    return NS(type="content_block_delta", delta=NS(type="text_delta", text=t))


async def test_claude_stream_yields_text(fake_claude):
    events = [NS(type="message_start"), _delta("Hel"), _delta("lo"),
              NS(type="message_delta", delta=NS(stop_reason="end_turn"))]
    fake_claude(FakeStream(events))
    first, rest = await claude.open_stream("claude-opus-5", Request("text", "", "hi"))
    assert first + "".join([c async for c in rest]) == "Hello"


async def test_claude_stream_failure_before_text_can_fall_back(fake_claude):
    fake_claude(_anth(anthropic.InternalServerError, 529, "overloaded_error", "Overloaded"))
    with pytest.raises(ProviderUnavailable):
        await claude.open_stream("claude-opus-5", Request("text", "", "hi"))


# ---- OpenAI replies

class FakeResponses(FakeMessages):
    pass


def _resp_obj(text, annotations=(), status="completed", refusal=False):
    content = [NS(type="refusal", refusal="no")] if refusal else [NS(type="output_text", text=text,
                                                                   annotations=list(annotations))]
    return NS(status=status, incomplete_details=NS(reason="max_output_tokens"), error=None,
              output=[NS(type="web_search_call"), NS(type="message", content=content)],
              output_text="" if refusal else text, usage=NS(input_tokens=7, output_tokens=3))


@pytest.fixture
def fake_openai(monkeypatch):
    def install(*replies):
        fake = FakeResponses(*replies)
        monkeypatch.setattr(openai, "_client", lambda api_key=None: NS(responses=fake))
        return fake
    return install


async def test_openai_json_reply_drops_the_nulls_strict_mode_added(fake_openai):
    fake = fake_openai(_resp_obj(json.dumps({"name": "a", "score": 1, "note": None, "when": None, "items": []})))
    r = await openai.call("gpt-5", Request("json", "sys", "prompt", SCHEMA))
    assert r.value == {"name": "a", "score": 1, "when": None, "items": []} and r.input_tokens == 7
    sent = fake.calls[0]
    fmt = sent["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["schema"]["required"] == list(SCHEMA["properties"])
    assert sent["instructions"] == "sys" and sent["input"] == "prompt" and sent["store"] is False


@pytest.mark.parametrize("kw", [{"status": "incomplete"}, {"refusal": True}])
async def test_openai_incomplete_or_refused_is_bad(fake_openai, kw):
    fake_openai(_resp_obj("{}", **kw))
    with pytest.raises(BadResponse):
        await openai.call("gpt-5", Request("json", "", "p", SCHEMA))


async def test_openai_search_cites(fake_openai):
    text = "Line é built. Other."
    ann = NS(type="url_citation", url="https://a.example", title="A", start_index=0, end_index=text.index(".") + 1)
    fake = fake_openai(_resp_obj(text, [ann]))
    r = await openai.call("gpt-5", Request("search", "", "find"))
    assert fake.calls[0]["tools"] == [{"type": "web_search"}]
    assert r.value["sources"] == [{"url": "https://a.example", "title": "A"}]
    assert gemini.cite(r.value["text"], r.value["supports"]) == "Line é built.[1] Other."


async def test_openai_stream_yields_text(fake_openai):
    events = [NS(type="response.created"), NS(type="response.output_text.delta", delta="Hel"),
              NS(type="response.output_text.delta", delta="lo"), NS(type="response.completed")]
    fake_openai(FakeStream(events))
    first, rest = await openai.open_stream("gpt-5", Request("text", "", "hi"))
    assert first + "".join([c async for c in rest]) == "Hello"


async def test_openai_stream_error_event_after_text_raises(fake_openai):
    fake_openai(FakeStream([NS(type="response.output_text.delta", delta="Hel"),
                            NS(type="error", code="server_error", message="boom")]))
    first, rest = await openai.open_stream("gpt-5", Request("text", "", "hi"))
    assert first == "Hel"
    with pytest.raises(ProviderUnavailable):
        [c async for c in rest]


# ---- registry

def test_registry_accepts_claude_and_openai_for_every_gemini_kind():
    roles = models.parse({"r": {"kind": "search", "models": [{"provider": "claude", "model": "claude-opus-5"},
                                                              {"provider": "openai", "model": "gpt-5"}]}})
    assert [m.name for m in roles["r"].models] == ["claude/claude-opus-5", "openai/gpt-5"]
    with pytest.raises(models.ConfigError):
        models.parse({"r": {"kind": "judge", "models": [{"provider": "nope", "model": "x"}]}})


def test_missing_keys_are_auth_errors(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    with pytest.raises(AuthError, match="ANTHROPIC_API_KEY"):
        claude._client()
    with pytest.raises(AuthError, match="OPENAI_API_KEY"):
        openai._client()
