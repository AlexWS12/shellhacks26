# The one door to every LLM. Each job (a "role") has an ordered list of {provider, model} in config/models.json,
# optionally replaced per role by config/models.local.json. Keys come only from env vars.
#
# Fallback policy, per model in the role's order:
#   AuthError, ModelNotFound, QuotaExceeded -> the model is dead for the rest of the run; go to the next one now
#   RateLimited, Timeout, ProviderUnavailable -> up to MAX_ATTEMPTS tries with exponential backoff, then the next one
#   BadResponse -> one retry, then the next one
#   every model failed -> RoleExhausted with the full attempt history
# A cached answer from a model is used before that model is called (so offline reruns work), even if its
# provider is off. A provider switched off on purpose (JEV_PROVIDER='' or 'mock') is skipped without an event.
#
# Events (into the run's JSONL when a run is active): model.call_failed, model.fallback_used, role.exhausted.
# No LLM touches distance or date math; those stay in core/overlap.py and core/sheets.py.

import asyncio
import contextvars
import json
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app import config
from app.clients import cache, claude, gemini, jev, openai
from app.clients.base import Judgment, Request
from app.clients.errors import (AuthError, BadResponse, ModelError, ModelNotFound, ProviderUnavailable, QuotaExceeded,
                                RateLimited, RoleExhausted, Timeout, describe)
from app.clients.redact import install_logging, remember

install_logging()
log = logging.getLogger("models")

ADAPTERS: dict[str, Any] = {"gemini": gemini, "claude": claude, "openai": openai, "jev": jev}
KINDS = {"json", "text", "search", "judge"}
MAX_ATTEMPTS = 3  # tries per model for RateLimited, Timeout and ProviderUnavailable
BAD_RESPONSE_ATTEMPTS = 2  # one retry
DEAD = (AuthError, ModelNotFound, QuotaExceeded)
BACKOFF_CAP_S = 20.0
_sleep = asyncio.sleep  # tests replace this

__all__ = ["AuthError", "BadResponse", "ConfigError", "Judgment", "ModelError", "ModelNotFound", "ProviderUnavailable",
           "QuotaExceeded", "RateLimited", "RoleExhausted", "Timeout", "available", "call", "chain", "describe",
           "session", "start", "stream", "validate"]


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ModelRef:
    provider: str
    model: str

    @property
    def name(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass
class Role:
    name: str
    kind: str
    models: list[ModelRef]
    description: str = ""
    label: str = ""
    group: str = ""
    when: str = "always"  # always | jev | research_live | startup


WHEN_NOTE = {"jev": "Only used when Jev is on.", "research_live": "Only used when live web search is on (RESEARCH_LIVE=true).",
             "startup": "Only used for the check when the server starts."}


def in_use(role: Role) -> str | None:
    # None when runs need this role now; otherwise a plain reason it can be left alone.
    if role.when == "jev" and jev.disabled():
        return WHEN_NOTE["jev"]
    if role.when == "research_live" and not config.RESEARCH_LIVE:
        return WHEN_NOTE["research_live"]
    if role.when == "startup":
        return WHEN_NOTE["startup"]
    return None


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ConfigError(f"{path.name} is not valid JSON: {e}") from None


def load(local: bool = True) -> dict[str, Role]:
    # local=False: the committed defaults only.
    base = _read(config.MODELS_FILE).get("roles", {})
    extra = _read(config.MODELS_LOCAL_FILE).get("roles", {}) if local else {}
    merged = {**base, **{k: {**base.get(k, {}), **v} for k, v in extra.items()}}
    return parse(merged)


def parse(merged: dict[str, Any]) -> dict[str, Role]:
    out: dict[str, Role] = {}
    for name, r in merged.items():
        kind = r.get("kind")
        if kind not in KINDS:
            raise ConfigError(f"role '{name}': kind must be one of {', '.join(sorted(KINDS))}")
        refs = []
        for m in r.get("models", []):
            p, model = m.get("provider"), m.get("model")
            if p not in ADAPTERS:
                raise ConfigError(f"role '{name}': unknown provider '{p}' (known: {', '.join(ADAPTERS)})")
            if kind not in ADAPTERS[p].KINDS:
                raise ConfigError(f"role '{name}': {p} can't answer {kind} roles")
            if not isinstance(model, str) or not model.strip():
                raise ConfigError(f"role '{name}': every entry needs a model name")
            refs.append(ModelRef(p, model.strip()))
        out[name] = Role(name, kind, refs, r.get("description", ""), r.get("label") or name, r.get("group", ""),
                         r.get("when", "always"))
    return out


def prices(on: date | None = None) -> dict[str, dict[str, float]]:
    # "provider/model" -> {input_per_mtok, output_per_mtok} in USD, from "prices" in the config files. Only what's
    # written there is known; nothing is guessed. An entry can be a list of dated prices ({"from": "2027-01-01", ...}):
    # the latest one in effect on `on` (today, by the calendar) applies.
    base, local = _read(config.MODELS_FILE).get("prices", {}), _read(config.MODELS_LOCAL_FILE).get("prices", {})
    day = (on or date.today()).isoformat()
    out: dict[str, dict[str, float]] = {}
    for name, entry in {**base, **local}.items():
        if isinstance(entry, list):
            live = [e for e in entry if isinstance(e, dict) and str(e.get("from") or "") <= day]
            entry = max(live, key=lambda e: str(e.get("from") or ""), default=None)
        if isinstance(entry, dict):
            out[name] = {k: float(v) for k, v in entry.items() if k in ("input_per_mtok", "output_per_mtok")}
    return out


_loaded: tuple[tuple, dict[str, Role]] | None = None


def roles() -> dict[str, Role]:
    # Re-read when either file changes, so an edited config applies to the next run without a restart.
    global _loaded
    files = (config.MODELS_FILE, config.MODELS_LOCAL_FILE)
    stamp = tuple((str(p), p.stat().st_mtime_ns if p.exists() else 0) for p in files)
    if _loaded is None or _loaded[0] != stamp:
        _loaded = (stamp, load())
    return _loaded[1]


class Session:
    # One run's view: the role config at its start, which models are dead, and where events go.
    def __init__(self, emit: Callable[..., Any] | None = None, role_map: dict[str, Role] | None = None) -> None:
        self.emit_target = emit
        self.roles = role_map if role_map is not None else roles()
        self.dead: dict[ModelRef, str] = {}
        self.alive: set[ModelRef] = set()  # answered at least once this run
        # The first call to a model not yet known to work is a probe; parallel callers wait for its outcome,
        # so a wrong model name fails once, not once per concurrent call.
        self.probes: dict[ModelRef, asyncio.Event] = {}
        self.reported: set[str] = set()  # roles whose exhaustion was already reported

    def emit(self, type: str, **payload: Any) -> None:
        if self.emit_target:
            self.emit_target(type, agent_id=AGENT.get(), **payload)

    def role(self, name: str) -> Role:
        try:
            return self.roles[name]
        except KeyError:
            raise ConfigError(f"no role '{name}' in config/models.json") from None


SESSION: contextvars.ContextVar[Session | None] = contextvars.ContextVar("models_session", default=None)
AGENT: contextvars.ContextVar[str | None] = contextvars.ContextVar("models_agent", default=None)


def session() -> Session:
    # Outside a run (startup check, scripts) every call gets a fresh breaker and no events.
    return SESSION.get() or Session()


def start(emit: Callable[..., Any] | None) -> Session:
    s = Session(emit)
    SESSION.set(s)
    return s


@dataclass
class Result:
    value: Any
    provider: str
    model: str
    cached: bool = False
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    attempts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def _limit(e: ModelError) -> int:
    if isinstance(e, BadResponse):
        return BAD_RESPONSE_ATTEMPTS
    if isinstance(e, (RateLimited, Timeout, ProviderUnavailable)):
        return MAX_ATTEMPTS
    return 1


def _delay(attempt: int, e: ModelError) -> float:
    if isinstance(e, BadResponse):
        return 0.0
    if e.retry_after:
        return min(BACKOFF_CAP_S, e.retry_after)
    return min(BACKOFF_CAP_S, 2 ** (attempt - 1) + random.uniform(0, 0.5))  # 1 s, 2 s (+ jitter)


def _entry(ref: ModelRef, error_class: str, message: str, attempt: int = 0) -> dict[str, Any]:
    return {"provider": ref.provider, "model": ref.model, "error_class": error_class, "message": message,
            "attempt": attempt}


def _next_live(s: Session, role: Role, i: int) -> ModelRef | None:
    return next((r for r in role.models[i + 1:] if r not in s.dead and not ADAPTERS[r.provider].disabled()), None)


def _fell_back(s: Session, role: str, failed: list[ModelRef], to: ModelRef) -> None:
    src = next((f for f in failed if f != to), None)
    if src is not None:
        s.emit("model.fallback_used", role=role, **{"from": src.name, "to": to.name})


Op = Callable[[Any, str, Request], Awaitable[Any]]


async def _chain(role_name: str, req: Request, op: Op, use_cache: bool, store: bool) -> Result:
    s = session()
    role = s.role(role_name)
    attempts: list[dict[str, Any]] = []
    failed: list[ModelRef] = []
    for i, ref in enumerate(role.models):
        ad = ADAPTERS[ref.provider]
        payload = ad.payload(ref.model, req)
        if use_cache and (hit := cache.get(ref.provider, ad.CACHE_VERSION, payload)) is not None \
                and hit.get("model") in (None, ref.model):
            try:
                value = ad.cached_value(req, hit)
            except (ModelError, KeyError, TypeError):
                value = None  # an unreadable cache file is a miss
            if value is not None:
                _fell_back(s, role_name, failed, ref)
                return Result(value, ref.provider, ref.model, cached=True, attempts=attempts)
        if why := ad.disabled():
            attempts.append(_entry(ref, "Disabled", why))
            continue
        probe = None
        if ref not in s.alive and ref not in s.dead:
            if (waiting := s.probes.get(ref)) is not None and not waiting.is_set():
                await waiting.wait()
            elif waiting is None:
                probe = s.probes[ref] = asyncio.Event()
        if ref in s.dead:
            attempts.append(_entry(ref, s.dead[ref], "skipped: failed earlier in this run"))
            continue
        try:
            reply, latency = await _attempts(s, role_name, role, i, ref, ad, req, op, attempts, failed)
        finally:
            if probe is not None:
                probe.set()
                del s.probes[ref]  # a model that only failed transiently gets probed again by the next call
        if reply is None:
            continue
        s.alive.add(ref)
        if use_cache and store:
            cache.put(ref.provider, ad.CACHE_VERSION, payload, reply.stored)
        _fell_back(s, role_name, failed, ref)
        if isinstance(reply, tuple):  # an open stream: (first chunk, the rest)
            return Result(reply, ref.provider, ref.model, latency_ms=latency, attempts=attempts)
        return Result(reply.value, ref.provider, ref.model, False, latency, reply.input_tokens, reply.output_tokens,
                      reply.cost_usd, attempts)
    # Report exhaustion when something was really tried now, or the first time a role runs out.
    live = any(a["attempt"] for a in attempts)
    if live or (role_name not in s.reported and any(a["error_class"] != "Disabled" for a in attempts)):
        s.reported.add(role_name)
        s.emit("role.exhausted", role=role_name, attempts=attempts)
    raise RoleExhausted(role_name, attempts)


async def _attempts(s: Session, role_name: str, role: Role, i: int, ref: ModelRef, ad: Any, req: Request, op: Op,
                    attempts: list[dict[str, Any]], failed: list[ModelRef]) -> tuple[Any, int]:
    # One model, with the retry policy. (reply, latency_ms), or (None, 0) when the chain should move on.
    attempt = 0
    while True:
        attempt += 1
        started = time.perf_counter()
        try:
            reply = await op(ad, ref.model, req)
        except ModelError as e:
            e.provider, e.model = ref.provider, ref.model
            if isinstance(e, DEAD):
                s.dead[ref] = e.error_class
            retry = not isinstance(e, DEAD) and attempt < _limit(e)
            attempts.append(_entry(ref, e.error_class, e.message, attempt))
            failed.append(ref)
            s.emit("model.call_failed", role=role_name, provider=ref.provider, model=ref.model,
                   error_class=e.error_class, message=e.message, attempt=attempt, will_retry=retry,
                   will_fallback=not retry and _next_live(s, role, i) is not None)
            log.warning("%s: %s attempt %d failed: %s", role_name, ref.name, attempt, describe(e))
            if retry:
                await _sleep(_delay(attempt, e))
                continue
            return None, 0
        return reply, round((time.perf_counter() - started) * 1000)


async def call(role: str, prompt: str | Judgment, schema: dict[str, Any] | None = None, *, system: str = "",
               cache: bool = True) -> Result:
    # prompt: text for json / search roles, a Judgment for judge roles. Raises RoleExhausted.
    kind = session().role(role).kind
    if isinstance(prompt, Judgment):
        if kind != "judge":
            raise ConfigError(f"role '{role}' is a {kind} role, not a judge role")
        req = Request("judge", judgment=prompt)
    else:
        if kind == "judge":
            raise ConfigError(f"role '{role}' needs a Judgment")
        if kind == "json" and schema is None:
            raise ConfigError(f"role '{role}' needs a schema")
        req = Request(kind, system, prompt, schema)
    return await _chain(role, req, lambda ad, m, r: ad.call(m, r), cache, True)


class Stream:
    # async for chunk in models.stream("analyst", prompt, system=...): ... ; then .model / .provider / .cached
    # A failure before the first chunk follows the fallback policy. After it, text is on screen, so the error
    # (a ModelError) is raised instead of starting over on another model.
    def __init__(self, role: str, prompt: str, system: str, pace: float) -> None:
        self.role, self.prompt, self.system, self.pace = role, prompt, system, pace
        self.provider: str | None = None
        self.model: str | None = None
        self.cached = False

    def __aiter__(self) -> AsyncIterator[str]:
        return self._run()

    async def _run(self) -> AsyncIterator[str]:
        s = session()
        if s.role(self.role).kind != "text":
            raise ConfigError(f"role '{self.role}' is not a text role")
        req = Request("text", self.system, self.prompt)
        res = await _chain(self.role, req, lambda ad, m, r: ad.open_stream(m, r), True, False)
        self.provider, self.model, self.cached = res.provider, res.model, res.cached
        if res.cached:
            text: str = res.value
            for i in range(0, len(text), 24):  # replay cached text in chunks
                yield text[i : i + 24]
                if self.pace > 0:
                    await asyncio.sleep(0.015 * self.pace)
            return
        first, rest = res.value
        parts = [first]
        if first:
            yield first
        try:
            async for chunk in rest:
                parts.append(chunk)
                yield chunk
        except ModelError as e:
            e.provider, e.model = res.provider, res.model
            ref = ModelRef(res.provider, res.model)
            if isinstance(e, DEAD):
                s.dead[ref] = e.error_class
            s.emit("model.call_failed", role=self.role, provider=res.provider, model=res.model,
                   error_class=e.error_class, message=e.message, attempt=1, will_retry=False, will_fallback=False,
                   during="stream")
            raise
        ad = ADAPTERS[res.provider]
        cache.put(res.provider, ad.CACHE_VERSION, ad.payload(res.model, req), {"text": "".join(parts), "model": res.model})


def stream(role: str, prompt: str, *, system: str = "", pace: float = 1.0) -> Stream:
    return Stream(role, prompt, system, pace)


def available(role: str) -> bool:
    # Whether any model for the role can be called now (its provider is on and has a key).
    return any(ADAPTERS[r.provider].configured() for r in session().role(role).models)


def chain(role: str) -> list[str]:
    return [r.name for r in session().role(role).models]


def summary() -> dict[str, dict[str, Any]]:
    # For /api/health: what each role will try, in order, and its plain name for failure notices. No keys.
    return {name: {"kind": r.kind, "label": r.label, "models": [m.name for m in r.models]} for name, r in roles().items()}


async def validate(provider: str, model: str, key: str) -> dict[str, str]:
    # ok, or the error class (AuthError, ModelNotFound, ...) with a key-free message.
    remember(key)
    ad = ADAPTERS.get(provider)
    if ad is None:
        return {"status": "ConfigError", "message": f"unknown provider '{provider}'"}
    try:
        await asyncio.wait_for(ad.validate(model, key), timeout=30)
    except ModelError as e:
        return {"status": e.error_class, "message": e.message}
    except (TimeoutError, asyncio.TimeoutError):
        return {"status": "Timeout", "message": "no answer within 30 s"}
    return {"status": "ok", "message": ""}
