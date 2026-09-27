# Claude adapter (anthropic SDK, Messages API). Makes one call to one model and turns every failure into a ModelError.
# Retries, fallbacks and the circuit breaker are the registry's job (clients/models.py), so the SDK's own retries are off.
#
# Error mapping (SDK exception classes):
#   AuthenticationError / PermissionDeniedError -> AuthError     NotFoundError, 400 naming the model -> ModelNotFound
#   402 billing_error, 400 "credit balance"     -> QuotaExceeded RateLimitError (429)                  -> RateLimited
#   5xx, 529 overloaded, APIConnectionError     -> ProviderUnavailable   APITimeoutError, our own timeout -> Timeout
#   other 4xx, a refusal, a cut-off reply, bad JSON, schema mismatch -> BadResponse
#
# JSON and judge roles use structured outputs (output_config.format) with the schema closed the way it requires
# (schema.closed); the reply is still checked against the original schema. Search uses the server-side web search
# tool; its citations become the same {text, sources, supports} shape Gemini's grounding gives.

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

PROVIDER = "claude"
CACHE_VERSION = "claude-v1"
KINDS = {"json", "text", "search", "judge"}
TIMEOUT_S = 120  # adaptive thinking is on by default on the current models, so answers take longer than Gemini's
MAX_TOKENS = 16000
PAUSE_LIMIT = 5  # a long web search pauses its turn (pause_turn); it's continued up to this many times
_SEMAPHORE = asyncio.Semaphore(4)
_clients: dict[str, Any] = {}  # one SDK client per key, so a changed key takes effect
# Models older than Opus 4.6 / Sonnet 4.6 (and every Haiku) only have the basic web search tool.
BASIC_SEARCH = re.compile(r"haiku|claude-3|(opus|sonnet)-4(-[015])?(-\d{8})?$")


def disabled() -> str | None:
    return None  # a missing key is an AuthError, so the run records it


def configured() -> bool:
    return bool(config.ANTHROPIC_API_KEY)


def _client(api_key: str | None = None):
    key = api_key if api_key is not None else config.ANTHROPIC_API_KEY
    if not key:
        raise AuthError("ANTHROPIC_API_KEY is not set")
    remember(key)
    c = _clients.get(key)
    if c is None:
        import anthropic

        c = _clients[key] = anthropic.AsyncAnthropic(api_key=key, max_retries=0, timeout=TIMEOUT_S * 2)
    return c


def classify(e: BaseException) -> ModelError:
    import anthropic

    if isinstance(e, ModelError):
        return e
    if isinstance(e, (TimeoutError, asyncio.TimeoutError, anthropic.APITimeoutError)):
        return Timeout(f"no answer within {TIMEOUT_S} s")
    if isinstance(e, anthropic.APIStatusError):
        code, kind, msg = e.status_code, getattr(e, "type", None) or "", e.message or str(e)
        text = f"{code} {kind}: {msg}".strip()
        low = msg.lower()
        if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            return AuthError(text)
        if code == 402 or kind == "billing_error" or "credit balance" in low:
            return QuotaExceeded(text)
        if isinstance(e, anthropic.NotFoundError) or (code == 400 and "model" in low and "not found" in low):
            return ModelNotFound(text)
        if isinstance(e, anthropic.RateLimitError):
            try:
                after = float(e.response.headers.get("retry-after") or 0) or None
            except ValueError:
                after = None
            return RateLimited(text, retry_after=after)
        if code in (408, 504):
            return Timeout(text)
        if code >= 500:  # includes 529 overloaded
            return ProviderUnavailable(text)
        return BadResponse(text)  # another model may accept the request
    if isinstance(e, (anthropic.APIConnectionError, ConnectionError, OSError)):
        return ProviderUnavailable(f"network: {type(e).__name__}")
    if isinstance(e, (TypeError, AttributeError, NameError)):
        raise e  # a bug in our code, not a provider failure
    return ProviderUnavailable(f"{type(e).__name__}: {e}")


def _usage(msg: Any) -> dict[str, int]:
    u = getattr(msg, "usage", None)
    return {"input_tokens": getattr(u, "input_tokens", 0) or 0, "output_tokens": getattr(u, "output_tokens", 0) or 0}


def _finished(msg: Any) -> None:
    # A reply that stopped for any reason but finishing (or pausing a search) can't be used as it is.
    if msg.stop_reason == "refusal":
        raise BadResponse("the model declined to answer")
    if msg.stop_reason in ("max_tokens", "model_context_window_exceeded"):
        raise BadResponse(f"reply was cut off ({msg.stop_reason})")


def _text(msg: Any) -> str:
    return "".join(b.text for b in msg.content if b.type == "text")


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


def _params(model: str, system: str, messages: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    # No temperature: current models reject sampling parameters.
    p: dict[str, Any] = {"model": model, "max_tokens": MAX_TOKENS, "messages": messages, **extra}
    if system:
        p["system"] = system
    return p


async def _create(params: dict[str, Any], timeout: float = TIMEOUT_S, api_key: str | None = None) -> Any:
    try:
        async with _SEMAPHORE:
            return await asyncio.wait_for(_client(api_key).messages.create(**params), timeout=timeout)
    except Exception as e:
        raise classify(e) from None


async def call(model: str, req: Request) -> Reply:
    if req.kind == "search":
        return await _search(model, req)
    if req.kind == "text":
        msg = await _create(_params(model, req.system, [{"role": "user", "content": req.prompt}]))
        _finished(msg)
        text = _text(msg)
        return Reply(text, {"text": text, "model": model}, **_usage(msg))
    system, prompt, schema = _json_form(req)
    msg = await _create(_params(model, system, [{"role": "user", "content": prompt}], output_config={
        "format": {"type": "json_schema", "schema": jsonschema.closed(schema)}}))
    _finished(msg)
    try:
        data = json.loads(_text(msg))
    except ValueError:
        raise BadResponse("reply is not valid JSON") from None
    if bad := jsonschema.problems(data, schema):
        raise BadResponse("reply doesn't match the schema: " + "; ".join(bad[:3]))
    usage = _usage(msg)
    value = parse_judgment(req.judgment, data) if req.kind == "judge" else data  # type: ignore[arg-type]
    return Reply(value, {"data": data, "model": model, **usage}, **usage)


def _grounding(blocks: list[Any]) -> tuple[str, list[dict[str, str]], list[dict[str, Any]]]:
    # (text, sources, supports) in Gemini's shape: sources are the cited pages (or, with no citation, every page the
    # search returned); each support ends a cited text block, as a UTF-8 byte offset, with the sources it cites.
    text, sources, supports, index = "", [], [], {}

    def source(url: str, title: str) -> int:
        if url not in index:
            index[url] = len(sources)
            sources.append({"url": url, "title": title or ""})
        return index[url]

    for b in blocks:
        if b.type != "text":
            continue
        text += b.text
        cited = [source(c.url, c.title) for c in (b.citations or []) if c.type == "web_search_result_location"]
        if cited:
            supports.append({"end": len(text.encode("utf-8")), "chunks": sorted(set(cited))})
    if not sources:
        for b in blocks:
            if b.type == "web_search_tool_result" and isinstance(b.content, list):
                for r in b.content:
                    source(r.url, r.title)
    return text, sources, supports


async def _search(model: str, req: Request) -> Reply:
    kind = "web_search_20250305" if BASIC_SEARCH.search(model) else "web_search_20260209"
    messages: list[dict[str, Any]] = [{"role": "user", "content": req.prompt}]
    blocks: list[Any] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    for _ in range(PAUSE_LIMIT):
        msg = await _create(_params(model, req.system, messages, tools=[{"type": kind, "name": "web_search"}]),
                            timeout=TIMEOUT_S * 2)
        _finished(msg)
        blocks += msg.content
        usage = {k: usage[k] + v for k, v in _usage(msg).items()}
        if msg.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": msg.content})  # the API continues a paused turn from here
    else:
        raise BadResponse(f"web search didn't finish in {PAUSE_LIMIT} turns")
    text, sources, supports = _grounding(blocks)
    value = {"text": text, "sources": sources, "supports": supports}
    return Reply(value, {**value, "model": model, **usage}, **usage)


async def open_stream(model: str, req: Request) -> tuple[str, AsyncIterator[str]]:
    # Opens a stream and waits for its first text, so a failure up to here can still be retried or fall back.
    # The iterator returned yields the rest; a failure there raises a ModelError (it can't be retried).
    def delta(event: Any) -> str:
        if event.type == "content_block_delta" and event.delta.type == "text_delta":
            return event.delta.text
        if event.type == "message_delta" and event.delta.stop_reason in ("refusal", "max_tokens"):
            raise BadResponse(f"reply stopped early ({event.delta.stop_reason})")
        return ""

    try:
        async with _SEMAPHORE:
            stream = await asyncio.wait_for(_client().messages.create(
                **_params(model, req.system, [{"role": "user", "content": req.prompt}]), stream=True), timeout=TIMEOUT_S)
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


async def list_models(api_key: str | None = None) -> list[dict[str, str]]:
    # Live from the Models API, never a hardcoded list. Every Claude model generates text.
    client = _client(api_key)
    try:
        return [{"id": m.id, "label": getattr(m, "display_name", None) or m.id, "description": ""}
                async for m in client.models.list()]
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
    await _create({"model": model, "max_tokens": 64, "messages": [{"role": "user", "content": "Reply with the word OK."}]},
                  api_key=api_key)
