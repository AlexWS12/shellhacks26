import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, TypeVar

from app import config
from app.clients import cache

log = logging.getLogger("gemini")
CACHE_VERSION = "gemini-v1"
TIMEOUT_S = 45
_SEMAPHORE = asyncio.Semaphore(4)
_client = None
T = TypeVar("T")


def enabled() -> bool:
    return bool(config.GEMINI_API_KEY)


def _get_client():
    global _client
    if _client is None:
        from google import genai

        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def _usage(resp: Any) -> dict[str, int]:
    u = getattr(resp, "usage_metadata", None)
    return {"input_tokens": getattr(u, "prompt_token_count", 0) or 0,
            "output_tokens": getattr(u, "candidates_token_count", 0) or 0}


def _config(system: str, temperature: float, **extra: Any):
    from google.genai import types

    return types.GenerateContentConfig(
        system_instruction=system, temperature=temperature,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True), **extra)


def _models() -> list[str]:
    return list(dict.fromkeys(m for m in (config.GEMINI_MODEL, config.GEMINI_FALLBACK_MODEL) if m))


def _transient(e: Exception) -> bool:
    # Overload, rate limit and timeouts are worth another try. A bad key or model name (400/401/403/404) is not.
    from google.genai import errors

    if isinstance(e, TimeoutError):
        return True
    if isinstance(e, errors.ServerError):
        return e.code in (500, 503)
    if isinstance(e, errors.ClientError):
        return e.code == 429
    return False


def describe(e: Exception) -> str:
    # "ClientError 429 RESOURCE_EXHAUSTED" for API errors, just the class name otherwise
    return " ".join(str(x) for x in (type(e).__name__, getattr(e, "code", None), getattr(e, "status", None)) if x)


def _retry_after(e: Exception) -> float | None:
    headers = getattr(getattr(e, "response", None), "headers", None)
    try:
        return float(headers.get("retry-after")) if headers else None
    except (TypeError, ValueError):
        return None


async def _backoff(attempt: int, e: Exception) -> None:
    await asyncio.sleep(_retry_after(e) or 2**attempt + random.uniform(0, 0.5))  # 1 s, 2 s, 4 s + jitter


async def _with_retries(call: Callable[[str], Awaitable[T]]) -> tuple[T, str]:
    # Tries the primary model, then the fallback, each up to GEMINI_RETRIES times. Returns (result, model).
    last: Exception | None = None
    for model in _models():
        for attempt in range(config.GEMINI_RETRIES):
            try:
                return await call(model), model
            except Exception as e:
                if not _transient(e):
                    raise
                last = e
                log.warning("Gemini %s attempt %d failed: %s: %s", model, attempt + 1, type(e).__name__, str(e)[:200])
                if attempt + 1 < config.GEMINI_RETRIES:
                    await _backoff(attempt, e)
    raise last or RuntimeError("No Gemini model configured")


async def generate_json(system: str, prompt: str, schema: dict[str, Any],
                        use_cache: bool = True) -> dict[str, Any] | None:
    # Returns None if Gemini is off or the call fails.
    payload = {"model": config.GEMINI_MODEL, "system": system, "prompt": prompt, "schema": schema}
    if use_cache and (hit := cache.get("gemini", CACHE_VERSION, payload)) is not None:
        return {**hit, "cached": True}
    if not enabled():
        return None

    async def call(model: str) -> Any:
        async with _SEMAPHORE:
            return await asyncio.wait_for(_get_client().aio.models.generate_content(
                model=model, contents=prompt,
                config=_config(system, 0, response_mime_type="application/json", response_json_schema=schema)),
                timeout=TIMEOUT_S)

    started = time.perf_counter()
    try:
        resp, model = await _with_retries(call)
        result = {"data": json.loads(resp.text or "{}"), "model": model, **_usage(resp),
                  "latency_ms": round((time.perf_counter() - started) * 1000)}
    except Exception as e:
        log.warning("Gemini JSON call failed: %s: %s", type(e).__name__, str(e)[:300])
        return None
    if use_cache:
        cache.put("gemini", CACHE_VERSION, payload, result)
    return {**result, "cached": False}


async def _open_stream(model: str, system: str, prompt: str) -> tuple[AsyncIterator[Any], str]:
    # Opens a stream and waits for its first text, so a failure up to here can still be retried.
    stream = await asyncio.wait_for(_get_client().aio.models.generate_content_stream(
        model=model, contents=prompt, config=_config(system, 0.3)), timeout=TIMEOUT_S)
    it = aiter(stream)
    while (chunk := await asyncio.wait_for(anext(it, None), timeout=TIMEOUT_S)) is not None:
        if chunk.text:
            return it, chunk.text
    return it, ""


async def stream_text(system: str, prompt: str, pace: float = 1.0) -> AsyncIterator[str]:
    payload = {"model": config.GEMINI_MODEL, "system": system, "prompt": prompt}
    if (hit := cache.get("gemini", CACHE_VERSION, payload)) is not None:
        text: str = hit["text"]
        for i in range(0, len(text), 24):  # replay cached text in chunks
            yield text[i : i + 24]
            if pace > 0:
                await asyncio.sleep(0.015 * pace)
        return
    if not enabled():
        raise RuntimeError("Gemini is not configured (set GEMINI_API_KEY)")

    async with _SEMAPHORE:
        (it, first), model = await _with_retries(lambda m: _open_stream(m, system, prompt))
        parts = [first]
        if first:
            yield first
        # Text is on screen now: a failure from here on raises instead of retrying, which would duplicate it.
        while (chunk := await asyncio.wait_for(anext(it, None), timeout=TIMEOUT_S)) is not None:
            if chunk.text:
                parts.append(chunk.text)
                yield chunk.text
    cache.put("gemini", CACHE_VERSION, payload, {"text": "".join(parts), "model": model})


def _grounding(resp: Any) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    # (sources, supports): which web pages Google Search returned, and which answer spans each one backs.
    meta = getattr((resp.candidates or [None])[0], "grounding_metadata", None)
    chunks = [{"url": getattr(c.web, "uri", "") or "", "title": getattr(c.web, "title", "") or ""}
              for c in (getattr(meta, "grounding_chunks", None) or []) if getattr(c, "web", None)]
    supports = [{"end": s.segment.end_index, "chunks": list(s.grounding_chunk_indices or [])}
                for s in (getattr(meta, "grounding_supports", None) or []) if getattr(s, "segment", None)]
    return chunks, supports


async def search_grounded(system: str, prompt: str, use_cache: bool = True) -> dict[str, Any] | None:
    # Gemini answer grounded in Google Search, with the pages it used. None if Gemini is off or the call fails.
    payload = {"model": config.GEMINI_MODEL, "system": system, "prompt": prompt, "tool": "google_search"}
    if use_cache and (hit := cache.get("gemini", CACHE_VERSION, payload)) is not None:
        return {**hit, "cached": True}
    if not enabled():
        return None
    from google.genai import types

    async def call(model: str) -> Any:
        async with _SEMAPHORE:
            return await asyncio.wait_for(_get_client().aio.models.generate_content(
                model=model, contents=prompt,
                config=_config(system, 0, tools=[types.Tool(google_search=types.GoogleSearch())])),
                timeout=TIMEOUT_S * 2)

    started = time.perf_counter()
    try:
        resp, model = await _with_retries(call)
        chunks, supports = _grounding(resp)
        result = {"text": resp.text or "", "sources": chunks, "supports": supports, "model": model, **_usage(resp),
                  "latency_ms": round((time.perf_counter() - started) * 1000)}
    except Exception as e:
        log.warning("Gemini search call failed: %s: %s", type(e).__name__, str(e)[:300])
        return None
    if use_cache:
        cache.put("gemini", CACHE_VERSION, payload, result)
    return {**result, "cached": False}


def cite(text: str, supports: list[dict[str, Any]]) -> str:
    # Puts [n] after each grounded span (n = source index + 1). Span ends are UTF-8 byte offsets.
    raw = text.encode("utf-8")
    for s in sorted(supports, key=lambda s: -s["end"]):
        marks = "".join(f"[{i + 1}]" for i in sorted(set(s["chunks"])))
        if marks and 0 <= s["end"] <= len(raw):
            raw = raw[: s["end"]] + marks.encode() + raw[s["end"] :]
    return raw.decode("utf-8", errors="ignore")


async def smoke() -> dict[str, Any] | None:
    # Live call (never cached), so it tells whether the key and model work right now.
    return await generate_json("Answer in JSON.", "Return {\"ok\": true}.",
                               {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
                               use_cache=False)


if __name__ == "__main__":
    print(asyncio.run(smoke()))
