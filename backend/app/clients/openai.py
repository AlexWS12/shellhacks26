# OpenAI adapter (openai SDK, Responses API). Makes one call to one model and turns every failure into a ModelError.
# Retries, fallbacks and the circuit breaker are the registry's job (clients/models.py), so the SDK's own retries are off.
#
# Error mapping (SDK exception classes):
#   AuthenticationError / PermissionDeniedError -> AuthError     NotFoundError, 400 "model ... does not exist" -> ModelNotFound
#   429 insufficient_quota                      -> QuotaExceeded other 429                                  -> RateLimited
#   5xx, APIConnectionError                     -> ProviderUnavailable   APITimeoutError, our own timeout     -> Timeout
#   other 4xx, a refusal, an incomplete reply, bad JSON, schema mismatch -> BadResponse
#
# JSON and judge roles use strict structured outputs (text.format json_schema). Strict mode needs every property
# required, so an optional one may come back null; those nulls are taken out before the reply is checked against the
# original schema. Search uses the hosted web_search tool; its url citations become Gemini's {text, sources, supports}.

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from app import config
from app.clients import schema as jsonschema
from app.clients.base import Reply, Request, parse_judgment, render_judgment
from app.clients.errors import (AuthError, BadResponse, ModelError, ModelNotFound, ProviderUnavailable, QuotaExceeded,
                                RateLimited, Timeout)
from app.clients.redact import remember

PROVIDER = "openai"
CACHE_VERSION = "openai-v1"
KINDS = {"json", "text", "search", "judge"}
TIMEOUT_S = 120  # reasoning models think before they answer
_SEMAPHORE = asyncio.Semaphore(4)
_clients: dict[str, Any] = {}  # one SDK client per key, so a changed key takes effect


def disabled() -> str | None:
    return None  # a missing key is an AuthError, so the run records it


def configured() -> bool:
    return bool(config.OPENAI_API_KEY)


def _client(api_key: str | None = None):
    key = api_key if api_key is not None else config.OPENAI_API_KEY
    if not key:
        raise AuthError("OPENAI_API_KEY is not set")
    remember(key)
    c = _clients.get(key)
    if c is None:
        import openai

        c = _clients[key] = openai.AsyncOpenAI(api_key=key, max_retries=0, timeout=TIMEOUT_S * 2)
    return c


def classify(e: BaseException) -> ModelError:
    import openai

    if isinstance(e, ModelError):
        return e
    if isinstance(e, (TimeoutError, asyncio.TimeoutError, openai.APITimeoutError)):
        return Timeout(f"no answer within {TIMEOUT_S} s")
    if isinstance(e, openai.APIStatusError):
        code, err, msg = e.status_code, getattr(e, "code", None) or "", e.message or str(e)
        text = f"{code} {err}: {msg}".strip()
        low = msg.lower()
        if isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return AuthError(text)
        if err == "insufficient_quota" or "exceeded your current quota" in low:
            return QuotaExceeded(text)
        if isinstance(e, openai.NotFoundError) or err == "model_not_found" or (
                code == 400 and "model" in low and ("does not exist" in low or "not found" in low)):
            return ModelNotFound(text)
        if isinstance(e, openai.RateLimitError):
            try:
                after = float(e.response.headers.get("retry-after") or 0) or None
            except ValueError:
                after = None
            return RateLimited(text, retry_after=after)
        if code in (408, 504):
            return Timeout(text)
        if code >= 500:
            return ProviderUnavailable(text)
        return BadResponse(text)  # another model may accept the request
    if isinstance(e, (openai.APIConnectionError, ConnectionError, OSError)):
        return ProviderUnavailable(f"network: {type(e).__name__}")
    if isinstance(e, (TypeError, AttributeError, NameError)):
        raise e  # a bug in our code, not a provider failure
    return ProviderUnavailable(f"{type(e).__name__}: {e}")


def _usage(resp: Any) -> dict[str, int]:
    u = getattr(resp, "usage", None)
    return {"input_tokens": getattr(u, "input_tokens", 0) or 0, "output_tokens": getattr(u, "output_tokens", 0) or 0}


def _finished(resp: Any) -> None:
    if resp.status == "incomplete":
        why = getattr(resp.incomplete_details, "reason", None) or "unknown"
        raise BadResponse(f"reply was cut off ({why})")
    if resp.status == "failed":
        raise ProviderUnavailable(getattr(resp.error, "message", None) or "the response failed")
    for item in resp.output:
        if item.type == "message" and any(c.type == "refusal" for c in item.content):
            raise BadResponse("the model declined to answer")


def _json_form(req: Request) -> tuple[str, str, dict[str, Any]]:
    if req.kind == "judge":
        return render_judgment(req.judgment)  # type: ignore[arg-type]
    return req.system, req.prompt, req.schema or {}


def payload(model: str, req: Request) -> dict[str, Any]:
    if req.kind in ("json", "judge"):
        system, prompt, schema = _json_form(req)
        return {"model": model, "system": system, "prompt": prompt, "schema": schema}
    if req.kind == "search":
        return {"model": model, "system": req.system, "prompt": req.prompt, "tool": "web_search"}
    return {"model": model, "system": req.system, "prompt": req.prompt}


def cached_value(req: Request, hit: dict[str, Any]) -> Any:
    if req.kind == "json":
        return hit["data"]
    if req.kind == "judge":
        return parse_judgment(req.judgment, hit["data"])  # type: ignore[arg-type]
    if req.kind == "search":
        return {k: hit[k] for k in ("text", "sources", "supports")}
    return hit["text"]


def _params(model: str, system: str, prompt: str, **extra: Any) -> dict[str, Any]:
    # No temperature: reasoning models reject it. store=False: nothing is kept on OpenAI's side for later turns.
    p: dict[str, Any] = {"model": model, "input": prompt, "store": False, **extra}
    if system:
        p["instructions"] = system
    return p


async def _create(params: dict[str, Any], timeout: float = TIMEOUT_S, api_key: str | None = None) -> Any:
    try:
        async with _SEMAPHORE:
            return await asyncio.wait_for(_client(api_key).responses.create(**params), timeout=timeout)
    except Exception as e:
        raise classify(e) from None


async def call(model: str, req: Request) -> Reply:
    if req.kind == "search":
        return await _search(model, req)
    if req.kind == "text":
        resp = await _create(_params(model, req.system, req.prompt))
        _finished(resp)
        return Reply(resp.output_text, {"text": resp.output_text, "model": model}, **_usage(resp))
    system, prompt, schema = _json_form(req)
    resp = await _create(_params(model, system, prompt, text={"format": {
        "type": "json_schema", "name": "reply", "strict": True, "schema": jsonschema.closed(schema, all_required=True)}}))
    _finished(resp)
    try:
        data = jsonschema.without_added_nulls(json.loads(resp.output_text), schema)
    except ValueError:
        raise BadResponse("reply is not valid JSON") from None
    if bad := jsonschema.problems(data, schema):
        raise BadResponse("reply doesn't match the schema: " + "; ".join(bad[:3]))
    usage = _usage(resp)
    value = parse_judgment(req.judgment, data) if req.kind == "judge" else data  # type: ignore[arg-type]
    return Reply(value, {"data": data, "model": model, **usage}, **usage)


def _grounding(resp: Any) -> tuple[str, list[dict[str, str]], list[dict[str, Any]]]:
    # (text, sources, supports) in Gemini's shape: each url citation marks the end of the span it backs, as a UTF-8
    # byte offset (the API gives character offsets into that output text).
    text, sources, supports, index = "", [], [], {}
    for item in resp.output:
        if item.type != "message":
            continue
        for c in item.content:
            if c.type != "output_text":
                continue
            for a in c.annotations or []:
                if a.type != "url_citation":
                    continue
                if a.url not in index:
                    index[a.url] = len(sources)
                    sources.append({"url": a.url, "title": a.title or ""})
                end = len((text + c.text[: a.end_index]).encode("utf-8"))
                supports.append({"end": end, "chunks": [index[a.url]]})
            text += c.text
    return text, sources, supports


async def _search(model: str, req: Request) -> Reply:
    resp = await _create(_params(model, req.system, req.prompt, tools=[{"type": "web_search"}]), timeout=TIMEOUT_S * 2)
    _finished(resp)
    text, sources, supports = _grounding(resp)
    value = {"text": text, "sources": sources, "supports": supports}
    usage = _usage(resp)
    return Reply(value, {**value, "model": model, **usage}, **usage)


async def open_stream(model: str, req: Request) -> tuple[str, AsyncIterator[str]]:
    # Opens a stream and waits for its first text, so a failure up to here can still be retried or fall back.
    # The iterator returned yields the rest; a failure there raises a ModelError (it can't be retried).
    def delta(event: Any) -> str:
        if event.type == "response.output_text.delta":
            return event.delta
        if event.type == "error":
            raise ProviderUnavailable(f"{event.code or 'error'}: {event.message}")
        if event.type in ("response.failed", "response.incomplete"):
            _finished(event.response)
        return ""

    try:
        async with _SEMAPHORE:
            stream = await asyncio.wait_for(_client().responses.create(
                **_params(model, req.system, req.prompt), stream=True), timeout=TIMEOUT_S)
            it = aiter(stream)
            first = ""
            while (event := await asyncio.wait_for(anext(it, None), timeout=TIMEOUT_S)) is not None:
                if first := delta(event):
                    break
    except Exception as e:
        raise classify(e) from None

    async def rest() -> AsyncIterator[str]:
        try:
            while (event := await asyncio.wait_for(anext(it, None), timeout=TIMEOUT_S)) is not None:
                if chunk := delta(event):
                    yield chunk
        except Exception as e:
            raise classify(e) from None

    return first, rest()


# Models the setup screen offers: the chat and reasoning families. Speech, image, embedding, moderation and
# realtime models are left out.
TEXT = re.compile(r"^(gpt-|o\d|chatgpt-)", re.I)
NOT_TEXT = re.compile(r"embedding|tts|whisper|dall-e|image|audio|realtime|transcribe|moderation|search|computer-use", re.I)


async def list_models(api_key: str | None = None) -> list[dict[str, str]]:
    # Live from the API, never a hardcoded list.
    client = _client(api_key)
    try:
        return [{"id": m.id, "label": m.id, "description": ""} async for m in client.models.list()
                if TEXT.search(m.id) and not NOT_TEXT.search(m.id)]
    except Exception as e:
        raise classify(e) from None


async def check_key(api_key: str) -> None:
    # Listing models needs a valid key and costs nothing.
    await list_models(api_key)


async def validate(model: str, api_key: str) -> None:
    # Looks the model up (checks the key and the name), then one tiny call.
    try:
        await _client(api_key).models.retrieve(model)
    except Exception as e:
        raise classify(e) from None
    await _create(_params(model, "", "Reply with the word OK.", max_output_tokens=64), api_key=api_key)
