# Jev API: POST /v1/systemone with {model, state, questions}. Docs: https://docs.typesafe.ai/api
# Question types: noul (probability it's true), choice (pick one), score (0-based level).

import asyncio
import json
import logging
import os
import random
import time
from typing import Any

import httpx

from app import config
from app.clients import cache

log = logging.getLogger("jev")

PRICE_PER_INPUT_TOKEN = 0.042 / 1_000_000  # output tokens are free
RETRY_STATUSES = {429, 529}
MAX_RETRIES = 2
TIMEOUT_S = 10
CACHE_VERSION = "jev-v1"
_SEMAPHORE = asyncio.Semaphore(8)


class JevConfigError(Exception):
    pass


def enabled() -> bool:
    # 'mock' means use the local rules instead of the API.
    return config.JEV_PROVIDER in {"typesafe", "openrouter", "cloudflare"}


def _key(var: str) -> str:
    value = os.getenv(var, "").strip()
    if not value:
        raise JevConfigError(f"JEV_PROVIDER={config.JEV_PROVIDER} needs {var} in backend/.env")
    return value


def _request(state: Any, questions: dict[str, Any]) -> tuple[str, dict[str, str], dict[str, Any]]:
    name = config.JEV_PROVIDER
    body: dict[str, Any] = {"state": state, "questions": questions}
    if name == "typesafe":
        body["model"] = os.getenv("JEV_MODEL", "jev-latest")
        return "https://api.typesafe.ai/v1/systemone", {"Authorization": f"Bearer {_key('TYPESAFE_API_KEY')}"}, body
    if name == "openrouter":
        body["model"] = os.getenv("JEV_MODEL", "typesafe/jev-latest")
        return "https://openrouter.ai/api/v1/systemone", {"Authorization": f"Bearer {_key('OPENROUTER_API_KEY')}"}, body
    if name == "cloudflare":
        url = f"https://api.cloudflare.com/client/v4/accounts/{_key('CLOUDFLARE_ACCOUNT_ID')}/ai/run"
        return url, {"Authorization": f"Bearer {_key('CLOUDFLARE_API_TOKEN')}"}, {
            "model": os.getenv("JEV_MODEL", "typesafe/jev"), "input": body}
    raise JevConfigError(f"unknown JEV_PROVIDER '{name}'")


def _cost(data: dict[str, Any]) -> float:
    usage = data.get("usage") or {}
    if "cost" in usage:
        return float(usage["cost"])
    return usage.get("input_tokens", 0) * PRICE_PER_INPUT_TOKEN


async def evaluate(state: Any, questions: dict[str, Any], use_cache: bool = True) -> dict[str, Any]:
    # Returns {} on any failure so the caller can fall back.
    payload = {"state": state, "questions": questions}
    if use_cache and (hit := cache.get("jev", CACHE_VERSION, payload)) is not None:
        return {**hit, "cached": True, "latency_ms": 0, "cost_usd": 0.0}
    if not enabled():
        return {}
    try:
        url, headers, body = _request(state, questions)
    except JevConfigError as e:
        log.warning("Jev disabled: %s", e)
        return {}
    started = time.perf_counter()
    try:
        async with _SEMAPHORE, httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            for attempt in range(MAX_RETRIES + 1):
                r = await client.post(url, headers=headers, json=body)
                if r.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
                    delay = float(r.headers.get("retry-after") or 0.5 * 2**attempt * (1 + random.random() * 0.2))
                    await asyncio.sleep(delay)
                    continue
                r.raise_for_status()
                break
        data: dict[str, Any] = r.json()
        data = data.get("result", data)
        result = {"answers": data["answers"], "cost_usd": _cost(data),
                  "latency_ms": round((time.perf_counter() - started) * 1000)}
        if use_cache:  # live-only calls (the watchdog's run checks) never read it back, so don't store them
            cache.put("jev", CACHE_VERSION, payload, result)
        return {**result, "cached": False}
    except Exception as e:  # the judge falls back
        log.warning("Jev call failed: %s: %s", type(e).__name__, str(e)[:200])
        return {}


def smoke_payload() -> tuple[str, dict[str, Any]]:
    return "Substation name in filing: 'Okatie'. Candidate OSM feature: 'Okatie Substation', operator SCE&G.", {
        "same": {"type": "noul", "instructions": "Is the candidate the substation the filing names?",
                 "criteria": {"true": "Same facility.", "false": "A different facility."}}}


if __name__ == "__main__":
    s, q = smoke_payload()
    print(json.dumps(asyncio.run(evaluate(s, q, use_cache=False)), indent=1))
