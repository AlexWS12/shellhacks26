# Gemini adapter (google-genai SDK). Makes one call to one model and turns every failure into a ModelError.
# Retries, fallbacks and the circuit breaker are the registry's job (clients/models.py).
#
# Error mapping, checked against the live API on 2026-09-27:
#   400 "API key not valid" / 401 / 403  -> AuthError        404 NOT_FOUND "models/x is not found" -> ModelNotFound
#   429 with a per-day or zero quotaId   -> QuotaExceeded    other 429 (per minute)                -> RateLimited
#   500 / 502 / 503 / network            -> ProviderUnavailable   504, our own timeout             -> Timeout
#   other 4xx, bad JSON, schema mismatch -> BadResponse

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app import config
from app.clients import schema as jsonschema
from app.clients.base import Reply, Request, parse_judgment, render_judgment
from app.clients.errors import (AuthError, BadResponse, ModelError, ModelNotFound, ProviderUnavailable, QuotaExceeded,
                                RateLimited, Timeout)
from app.clients.redact import remember

PROVIDER = "gemini"
CACHE_VERSION = "gemini-v1"
KINDS = {"json", "text", "search", "judge"}
TIMEOUT_S = 45
_SEMAPHORE = asyncio.Semaphore(4)
_clients: dict[str, Any] = {}  # one SDK client per key, so a changed key takes effect
# A daily (or zero) limit won't recover during a run. Every 429 message says "check your plan and billing details",
# so the structured quotaId ('GenerateRequestsPerDayPerProjectPerModel-FreeTier') decides, not the prose.
QUOTA_RE = re.compile(r"PerDay|per ?day|limit: 0\b|quotaValue['\"]?\s*:\s*['\"]0['\"]", re.I)
RETRY_RE = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s")


def disabled() -> str | None:
    return None  # a missing key is an AuthError, so the run records it


def configured() -> bool:
    return bool(config.GEMINI_API_KEY)


def _client(api_key: str | None = None):
    key = api_key if api_key is not None else config.GEMINI_API_KEY
    if not key:
        raise AuthError("GEMINI_API_KEY is not set")
    remember(key)
    c = _clients.get(key)
    if c is None:
        from google import genai

        c = _clients[key] = genai.Client(api_key=key)
    return c


def classify(e: BaseException) -> ModelError:
    from google.genai import errors

    if isinstance(e, ModelError):
        return e
    if isinstance(e, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException)):
        return Timeout(f"no answer within {TIMEOUT_S} s")
    if isinstance(e, errors.APIError):
        code, status, msg = e.code or 0, e.status or "", e.message or str(e)
        text = f"{code} {status}: {msg}".strip()
        low = msg.lower()
        if code in (401, 403) or (code == 400 and ("api key" in low or "api_key" in low)):
            return AuthError(text)
        if code == 404 or (code == 400 and "model" in low and ("not found" in low or "deprecated" in low)):
            return ModelNotFound(text)
        if code == 429:
            if QUOTA_RE.search(str(e)):  # str(e) includes the structured details
                return QuotaExceeded(text)
            m = RETRY_RE.search(str(e))
            return RateLimited(text, retry_after=float(m[1]) if m else None)
        if code in (408, 504):
            return Timeout(text)
        if code >= 500:
            return ProviderUnavailable(text)
        return BadResponse(text)  # another model may accept the request
    if isinstance(e, (httpx.TransportError, ConnectionError, OSError)):
        return ProviderUnavailable(f"network: {type(e).__name__}")
    if isinstance(e, (TypeError, AttributeError, NameError)):
        raise e  # a bug in our code, not a provider failure
    return ProviderUnavailable(f"{type(e).__name__}: {e}")


def _config(system: str, temperature: float, **extra: Any):
    from google.genai import types

    return types.GenerateContentConfig(
        system_instruction=system, temperature=temperature,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True), **extra)


def _usage(resp: Any) -> dict[str, int]:
    u = getattr(resp, "usage_metadata", None)
    return {"input_tokens": getattr(u, "prompt_token_count", 0) or 0,
            "output_tokens": getattr(u, "candidates_token_count", 0) or 0}


def _json_form(req: Request) -> tuple[str, str, dict[str, Any]]:
    if req.kind == "judge":
        return render_judgment(req.judgment)  # type: ignore[arg-type]
    return req.system, req.prompt, req.schema or {}


def payload(model: str, req: Request) -> dict[str, Any]:
    # Same shapes as before the registry, so existing cache files keep hitting for the same model.
    if req.kind in ("json", "judge"):
        system, prompt, schema = _json_form(req)
        return {"model": model, "system": system, "prompt": prompt, "schema": schema}
    if req.kind == "search":
        return {"model": model, "system": req.system, "prompt": req.prompt, "tool": "google_search"}
    return {"model": model, "system": req.system, "prompt": req.prompt}


def cached_value(req: Request, hit: dict[str, Any]) -> Any:
    if req.kind == "json":
        return hit["data"]
    if req.kind == "judge":
        return parse_judgment(req.judgment, hit["data"])  # type: ignore[arg-type]
    if req.kind == "search":
        return {k: hit[k] for k in ("text", "sources", "supports")}
    return hit["text"]


async def _generate(model: str, prompt: str, cfg: Any, timeout: float = TIMEOUT_S, api_key: str | None = None) -> Any:
    try:
        async with _SEMAPHORE:
            return await asyncio.wait_for(_client(api_key).aio.models.generate_content(
                model=model, contents=prompt, config=cfg), timeout=timeout)
    except Exception as e:
        raise classify(e) from None


async def call(model: str, req: Request) -> Reply:
    if req.kind == "search":
        return await _search(model, req)
    if req.kind == "text":
        resp = await _generate(model, req.prompt, _config(req.system, 0.3))
        return Reply(resp.text or "", {"text": resp.text or "", "model": model}, **_usage(resp))
    system, prompt, schema = _json_form(req)
    resp = await _generate(model, prompt, _config(system, 0, response_mime_type="application/json",
                                                  response_json_schema=schema))
    try:
        data = json.loads(resp.text or "")
    except ValueError:
        raise BadResponse("reply is not valid JSON") from None
    if bad := jsonschema.problems(data, schema):
        raise BadResponse("reply doesn't match the schema: " + "; ".join(bad[:3]))
    usage = _usage(resp)
    value = parse_judgment(req.judgment, data) if req.kind == "judge" else data  # type: ignore[arg-type]
    return Reply(value, {"data": data, "model": model, **usage}, **usage)


def _grounding(resp: Any) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    # (sources, supports): which web pages Google Search returned, and which answer spans each one backs.
    meta = getattr((resp.candidates or [None])[0], "grounding_metadata", None)
    chunks = [{"url": getattr(c.web, "uri", "") or "", "title": getattr(c.web, "title", "") or ""}
              for c in (getattr(meta, "grounding_chunks", None) or []) if getattr(c, "web", None)]
    supports = [{"end": s.segment.end_index, "chunks": list(s.grounding_chunk_indices or [])}
                for s in (getattr(meta, "grounding_supports", None) or []) if getattr(s, "segment", None)]
    return chunks, supports


async def _search(model: str, req: Request) -> Reply:
    from google.genai import types

    resp = await _generate(model, req.prompt, _config(req.system, 0, tools=[types.Tool(google_search=types.GoogleSearch())]),
                           timeout=TIMEOUT_S * 2)
    chunks, supports = _grounding(resp)
    value = {"text": resp.text or "", "sources": chunks, "supports": supports}
    usage = _usage(resp)
    return Reply(value, {**value, "model": model, **usage}, **usage)


async def open_stream(model: str, req: Request) -> tuple[str, AsyncIterator[str]]:
    # Opens a stream and waits for its first text, so a failure up to here can still be retried or fall back.
    # The iterator returned yields the rest; a failure there raises a ModelError (it can't be retried).
    try:
        async with _SEMAPHORE:
            stream = await asyncio.wait_for(_client().aio.models.generate_content_stream(
                model=model, contents=req.prompt, config=_config(req.system, 0.3)), timeout=TIMEOUT_S)
            it = aiter(stream)
            first = ""
            while (chunk := await asyncio.wait_for(anext(it, None), timeout=TIMEOUT_S)) is not None:
                if chunk.text:
                    first = chunk.text
                    break
    except Exception as e:
        raise classify(e) from None

    async def rest() -> AsyncIterator[str]:
        try:
            while (chunk := await asyncio.wait_for(anext(it, None), timeout=TIMEOUT_S)) is not None:
                if chunk.text:
                    yield chunk.text
        except Exception as e:
            raise classify(e) from None

    return first, rest()


# Models the setup screen offers: ones that generate text. Speech, image, video and embedding models are left out.
NOT_TEXT = re.compile(r"embedding|tts|image|imagen|audio|veo|aqa|live|robotics|computer-use", re.I)


async def list_models(api_key: str | None = None) -> list[dict[str, str]]:
    # Live from the API, never a hardcoded list.
    client = _client(api_key)
    out: list[dict[str, str]] = []
    try:
        async for m in await client.aio.models.list():
            name = (m.name or "").removeprefix("models/")
            if "generateContent" in (getattr(m, "supported_actions", None) or []) and not NOT_TEXT.search(name):
                out.append({"id": name, "label": getattr(m, "display_name", None) or name,
                            "description": (getattr(m, "description", None) or "")[:200]})
    except Exception as e:
        raise classify(e) from None
    return out


async def check_key(api_key: str) -> None:
    # Listing models needs a valid key and costs no quota.
    await list_models(api_key)


async def validate(model: str, api_key: str) -> None:
    # A list-models call (checks the key and that the model exists), then one tiny generate call.
    client = _client(api_key)
    try:
        names = {m.name.removeprefix("models/") async for m in await client.aio.models.list()}
    except Exception as e:
        raise classify(e) from None
    if model.removeprefix("models/") not in names:
        raise ModelNotFound(f"'{model}' is not in this key's model list ({len(names)} models)")
    await _generate(model, "Reply with the word OK.", _config("", 0, max_output_tokens=16), api_key=api_key)


def cite(text: str, supports: list[dict[str, Any]]) -> str:
    # Puts [n] after each grounded span (n = source index + 1). Span ends are UTF-8 byte offsets.
    raw = text.encode("utf-8")
    for s in sorted(supports, key=lambda s: -s["end"]):
        marks = "".join(f"[{i + 1}]" for i in sorted(set(s["chunks"])))
        if marks and 0 <= s["end"] <= len(raw):
            raw = raw[: s["end"]] + marks.encode() + raw[s["end"] :]
    return raw.decode("utf-8", errors="ignore")

