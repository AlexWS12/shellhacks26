import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from app import config
from app.clients import cache

log = logging.getLogger("gemini")
CACHE_VERSION = "gemini-v1"
TIMEOUT_S = 45
_SEMAPHORE = asyncio.Semaphore(4)
_client = None


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


async def generate_json(system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any] | None:
    # Returns None if Gemini is off or the call fails.
    payload = {"model": config.GEMINI_MODEL, "system": system, "prompt": prompt, "schema": schema}
    if (hit := cache.get("gemini", CACHE_VERSION, payload)) is not None:
        return {**hit, "cached": True}
    if not enabled():
        return None
    from google.genai import types

    started = time.perf_counter()
    try:
        async with _SEMAPHORE:
            resp = await asyncio.wait_for(_get_client().aio.models.generate_content(
                model=config.GEMINI_MODEL, contents=prompt,
                config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                                   response_json_schema=schema, temperature=0)), timeout=TIMEOUT_S)
        result = {"data": json.loads(resp.text or "{}"), **_usage(resp),
                  "latency_ms": round((time.perf_counter() - started) * 1000)}
        cache.put("gemini", CACHE_VERSION, payload, result)
        return {**result, "cached": False}
    except Exception as e:
        log.warning("Gemini JSON call failed: %s: %s", type(e).__name__, str(e)[:300])
        return None


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
    from google.genai import types

    parts: list[str] = []
    async with _SEMAPHORE:
        stream = await _get_client().aio.models.generate_content_stream(
            model=config.GEMINI_MODEL, contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system, temperature=0.3))
        async for chunk in stream:
            if chunk.text:
                parts.append(chunk.text)
                yield chunk.text
    cache.put("gemini", CACHE_VERSION, payload, {"text": "".join(parts)})


async def smoke() -> dict[str, Any] | None:
    return await generate_json("Answer in JSON.", "Return {\"ok\": true}.",
                               {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]})


if __name__ == "__main__":
    print(asyncio.run(smoke()))
