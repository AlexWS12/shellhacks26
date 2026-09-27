# Jev adapter (TypeSafe System One): POST /v1/systemone with {model, state, questions}.
# Docs: https://docs.typesafe.ai/api. Question types: noul (probability it's true), choice (pick one),
# score (0-based level). JEV_PROVIDER picks the host; the model comes from config/models.json.
#
# Error mapping, checked against the live API on 2026-09-27:
#   401 / 403 -> AuthError      400 / 404 / 422 "Unknown model: x" -> ModelNotFound     402 -> QuotaExceeded
#   429 -> RateLimited (QuotaExceeded if it mentions quota or credits)     529 / 5xx / network -> ProviderUnavailable
#   timeout -> Timeout          other 4xx, missing or mistyped answers -> BadResponse

import asyncio
import logging
import os
import time
from typing import Any

import httpx

from app import config
from app.clients.base import Reply, Request, check_answers
from app.clients.errors import (AuthError, BadResponse, ModelError, ModelNotFound, ProviderUnavailable, QuotaExceeded,
                                RateLimited, Timeout)
from app.clients.redact import remember

log = logging.getLogger("jev")

PROVIDER = "jev"
CACHE_VERSION = "jev-v1"
KINDS = {"judge"}
PRICE_PER_INPUT_TOKEN = 0.042 / 1_000_000  # output tokens are free
TIMEOUT_S = 10
HOSTS = {"typesafe": "TYPESAFE_API_KEY", "openrouter": "OPENROUTER_API_KEY", "cloudflare": "CLOUDFLARE_API_TOKEN"}
MODELS_URL = "https://api.typesafe.ai/v1/models"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_SEMAPHORE = asyncio.Semaphore(8)
_warned = False


def disabled() -> str | None:
    # 'mock' and '' turn Jev off on purpose: the judge's local rules answer, and no failure is recorded.
    global _warned
    host = config.JEV_PROVIDER
    if host in HOSTS:
        return None
    if host not in ("", "mock") and not _warned:
        _warned = True
        log.warning("JEV_PROVIDER=%s is not one of %s; Jev is off", host, ", ".join(HOSTS))
    return "Jev is mocked" if host == "mock" else "Jev is off (JEV_PROVIDER)"


def configured() -> bool:
    return disabled() is None and bool(os.getenv(HOSTS[config.JEV_PROVIDER], "").strip())


def _key(api_key: str | None) -> str:
    var = HOSTS[config.JEV_PROVIDER]
    value = (api_key if api_key is not None else os.getenv(var, "")).strip()
    if not value:
        raise AuthError(f"JEV_PROVIDER={config.JEV_PROVIDER} needs {var}")
    remember(value)
    return value


def _request(model: str, state: Any, questions: dict[str, Any], api_key: str | None = None
             ) -> tuple[str, dict[str, str], dict[str, Any]]:
    host = config.JEV_PROVIDER
    headers = {"Authorization": f"Bearer {_key(api_key)}"}
    if host == "typesafe":
        return "https://api.typesafe.ai/v1/systemone", headers, {"model": model, "state": state, "questions": questions}
    hosted = model if "/" in model else f"typesafe/{model}"
    if host == "openrouter":
        return "https://openrouter.ai/api/v1/systemone", headers, {"model": hosted, "state": state, "questions": questions}
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not account:
        raise AuthError("JEV_PROVIDER=cloudflare needs CLOUDFLARE_ACCOUNT_ID")
    return (f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run", headers,
            {"model": hosted, "input": {"state": state, "questions": questions}})


def _message(r: httpx.Response) -> str:
    try:
        body = r.json()
        d = body.get("detail", body) if isinstance(body, dict) else body
        if isinstance(d, dict):
            return str(d.get("message") or d.get("error") or d)
        return str(d)
    except ValueError:
        return r.text[:200]


def classify_status(r: httpx.Response) -> ModelError:
    code, msg = r.status_code, _message(r)
    text = f"{code}: {msg}"
    low = msg.lower()
    if code in (401, 403):
        return AuthError(text)
    if code == 402:
        return QuotaExceeded(text)
    if code in (400, 404, 422) and "model" in low and ("unknown" in low or "not found" in low or "deprecated" in low
                                                         or "invalid" in low):
        return ModelNotFound(text)
    if code == 429:
        if "quota" in low or "credit" in low:
            return QuotaExceeded(text)
        try:
            after = float(r.headers.get("retry-after") or 0) or None
        except ValueError:
            after = None
        return RateLimited(text, retry_after=after)
    if code == 529 or code >= 500:
        return ProviderUnavailable(text)
    return BadResponse(text)


def classify(e: BaseException) -> ModelError:
    if isinstance(e, ModelError):
        return e
    if isinstance(e, (httpx.TimeoutException, TimeoutError, asyncio.TimeoutError)):
        return Timeout(f"no answer within {TIMEOUT_S} s")
    if isinstance(e, (httpx.TransportError, ConnectionError, OSError)):
        return ProviderUnavailable(f"network: {type(e).__name__}")
    if isinstance(e, (TypeError, AttributeError, NameError)):
        raise e  # a bug in our code, not a provider failure
    return ProviderUnavailable(f"{type(e).__name__}: {e}")


def _cost(data: dict[str, Any]) -> float:
    usage = data.get("usage") or {}
    if "cost" in usage:
        return float(usage["cost"])
    return usage.get("input_tokens", 0) * PRICE_PER_INPUT_TOKEN


def payload(model: str, req: Request) -> dict[str, Any]:
    j = req.judgment
    return {"model": model, "state": j.state, "questions": j.questions}  # type: ignore[union-attr]


def cached_value(req: Request, hit: dict[str, Any]) -> Any:
    return hit["answers"]


async def _post(model: str, req: Request, api_key: str | None = None) -> dict[str, Any]:
    j = req.judgment
    url, headers, body = _request(model, j.state, j.questions, api_key)  # type: ignore[union-attr]
    try:
        async with _SEMAPHORE, httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.post(url, headers=headers, json=body)
    except Exception as e:
        raise classify(e) from None
    if r.status_code >= 400:
        raise classify_status(r)
    try:
        data = r.json()
    except ValueError:
        raise BadResponse("reply is not valid JSON") from None
    return data.get("result", data) if isinstance(data, dict) else {}


async def call(model: str, req: Request) -> Reply:
    if req.kind != "judge":
        raise BadResponse(f"Jev only answers typed judgments, not '{req.kind}'")
    started = time.perf_counter()
    data = await _post(model, req)
    answers = check_answers(req.judgment, data.get("answers"))  # type: ignore[arg-type]
    cost = _cost(data)
    usage = data.get("usage") or {}
    return Reply(answers, {"answers": answers, "cost_usd": cost, "model": model,
                           "latency_ms": round((time.perf_counter() - started) * 1000)},
                 input_tokens=usage.get("input_tokens", 0) or 0, cost_usd=cost)


async def _get(url: str, key: str) -> Any:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.get(url, headers={"Authorization": f"Bearer {key}"})
    except Exception as e:
        raise classify(e) from None
    if r.status_code >= 400:
        raise classify_status(r)
    try:
        return r.json()
    except ValueError:
        raise BadResponse("model list is not valid JSON") from None


async def list_models(api_key: str | None = None) -> list[dict[str, str]]:
    # Live from the host's API, never a hardcoded list.
    if disabled():
        raise AuthError(disabled() or "Jev is off")
    key, host = _key(api_key), config.JEV_PROVIDER
    if host == "typesafe":
        items = (await _get(MODELS_URL, key)).get("models", [])
        return [{"id": m["name"], "label": m["name"], "description": m.get("description", "")[:200]}
                for m in items if isinstance(m, dict) and m.get("name")]
    if host == "openrouter":
        items = (await _get(OPENROUTER_MODELS_URL, key)).get("data", [])
        return [{"id": m["id"], "label": m.get("name") or m["id"], "description": (m.get("description") or "")[:200]}
                for m in items if isinstance(m, dict) and str(m.get("id", "")).startswith("typesafe/")]
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not account:
        raise AuthError("JEV_PROVIDER=cloudflare needs CLOUDFLARE_ACCOUNT_ID")
    items = (await _get(f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/models/search?search=typesafe",
                        key)).get("result", [])
    return [{"id": m["name"], "label": m["name"], "description": (m.get("description") or "")[:200]}
            for m in items if isinstance(m, dict) and m.get("name")]


async def check_key(api_key: str) -> None:
    # TypeSafe's model list needs a valid key. OpenRouter's list is public, so there a tiny call decides.
    models = await list_models(api_key)
    if config.JEV_PROVIDER != "typesafe":
        if not models:
            raise ModelNotFound("no Jev model is listed for this host")
        await validate(models[0]["id"], api_key)


async def validate(model: str, api_key: str) -> None:
    # TypeSafe lists its models (GET /v1/models); versioned ids like jev-1.13.0 work without being listed, so
    # the test call decides. OpenRouter and Cloudflare get the test call only.
    from app.clients.base import Judgment

    if disabled():
        raise AuthError(disabled() or "Jev is off")
    key = _key(api_key)
    if config.JEV_PROVIDER == "typesafe":
        await _get(MODELS_URL, key)
    j = Judgment("Substation name in filing: 'Okatie'. Candidate OSM feature: 'Okatie Substation'.",
                 {"same": {"type": "noul", "instructions": "Is the candidate the substation the filing names?",
                           "criteria": {"true": "Same facility.", "false": "A different facility."}}})
    check_answers(j, (await _post(model, Request("judge", judgment=j), key)).get("answers"))

