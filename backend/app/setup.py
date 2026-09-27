# Behind the setup screen (Models button): which keys are present, entering a key (local mode only), live model
# lists, checking models, saving config/models.local.json, and the check before a live run starts.
# No key ever leaves this module: status says only whether a key is present and where it was set.

import asyncio
import hashlib
import json
import os
import re
import time
from typing import Any

from dotenv import dotenv_values

from app import config
from app.clients import jev, models
from app.clients.models import ModelRef, Role

PROVIDERS: dict[str, dict[str, Any]] = {
    "gemini": {"label": "Gemini", "what": "Reads hard pages, splits awkward names and writes the text.",
               "get_key": "Google AI Studio (aistudio.google.com)"},
    "jev": {"label": "Jev by TypeSafe", "what": "Makes the quick yes-or-no and pick-one decisions, like whether a map "
                                               "match is the right substation.",
            "get_key": "TypeSafe (typesafe.ai), or through OpenRouter or Cloudflare"},
}
HOST_LABEL = {"typesafe": "TypeSafe", "openrouter": "OpenRouter", "cloudflare": "Cloudflare Workers AI"}
JEV_FIELDS = {"typesafe": ["TYPESAFE_API_KEY"], "openrouter": ["OPENROUTER_API_KEY"],
              "cloudflare": ["CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"]}
NOT_SECRET = {"CLOUDFLARE_ACCOUNT_ID"}
FIELD_LABEL = {"GEMINI_API_KEY": "API key", "TYPESAFE_API_KEY": "API key", "OPENROUTER_API_KEY": "API key",
               "CLOUDFLARE_ACCOUNT_ID": "Account ID", "CLOUDFLARE_API_TOKEN": "API token"}
KEY_RE = re.compile(r"^[A-Za-z0-9._\-:/+=]{8,300}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9._\-/:@]{1,120}$")
MAX_MODELS_PER_ROLE = 6

# Plain words for each result. The UI shows these as they are.
PLAIN = {"ok": "Works", "NoKey": "No key yet", "Off": "Turned off", "AuthError": "Key rejected",
         "ModelNotFound": "Model name not found", "RateLimited": "Rate limited, try again",
         "QuotaExceeded": "Usage limit reached, try again later or pick another model",
         "Timeout": "No answer in time, try again", "ProviderUnavailable": "Service unavailable, try again",
         "BadResponse": "Gave an unusable answer", "ConfigError": "Setup problem"}
TEMPORARY = {"RateLimited", "QuotaExceeded", "Timeout", "ProviderUnavailable"}
OK_TTL_S, FAIL_TTL_S, LIST_TTL_S = 900.0, 60.0, 300.0
_checked: dict[tuple, tuple[float, dict[str, Any]]] = {}
_lists: dict[tuple, tuple[float, list[dict[str, str]]]] = {}
_CHECKS = asyncio.Semaphore(4)


class SetupError(Exception):
    # A request the setup screen should show as it is: message plus details for the UI.
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message, self.details = message, details


# ---- keys

def key_vars(provider: str) -> list[str]:
    if provider == "gemini":
        return ["GEMINI_API_KEY"]
    return JEV_FIELDS.get(config.JEV_PROVIDER, [])


def _key(provider: str) -> str:
    if provider == "gemini":
        return config.GEMINI_API_KEY
    return os.getenv(JEV_FIELDS[config.JEV_PROVIDER][-1], "").strip() if config.JEV_PROVIDER in JEV_FIELDS else ""


def key_source(var: str) -> str | None:
    # Where a variable is set, without reading out its value. The process environment beats every file.
    if config.from_shell(var) and os.getenv(var, "").strip():
        return "the server's environment"
    root = config.BACKEND_DIR.parent
    for f in config.ENV_FILES:
        if f.exists() and (dotenv_values(f).get(var) or "").strip():
            return str(f.relative_to(root)) if f.is_relative_to(root) else f.name
    return None


def provider_status() -> list[dict[str, Any]]:
    out = []
    for pid, meta in PROVIDERS.items():
        ad = models.ADAPTERS[pid]
        fields = [{"var": v, "label": FIELD_LABEL[v], "secret": v not in NOT_SECRET, "present": bool(os.getenv(v, "").strip()),
                   "source": key_source(v)} for v in key_vars(pid)]
        row = {"id": pid, "label": meta["label"], "what": meta["what"], "get_key": meta["get_key"],
               "kinds": sorted(ad.KINDS), "fields": fields, "key_present": ad.configured(),
               "can_enter_key": config.APP_MODE == "local"}
        if pid == "jev":
            row |= {"host": config.JEV_PROVIDER if config.JEV_PROVIDER in JEV_FIELDS else "",
                    "off_reason": jev.disabled(), "hosts": [{"id": h, "label": HOST_LABEL[h], "fields": [
                        {"var": v, "label": FIELD_LABEL[v], "secret": v not in NOT_SECRET} for v in vs]}
                        for h, vs in JEV_FIELDS.items()]}
        out.append(row)
    return out


def _write_env_local(updates: dict[str, str]) -> None:
    path = config.ENV_LOCAL_FILE
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else [
        "# Written by the setup screen: keys for this machine only. Never commit this file."]
    seen: set[str] = set()
    out = []
    for line in lines:
        m = re.match(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=", line)
        if m and m[1] in updates:
            out.append(f"{m[1]}={updates[m[1]]}")
            seen.add(m[1])
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


async def save_keys(provider: str, values: dict[str, str], host: str | None = None) -> dict[str, Any]:
    # Checks the key with the provider first; only a key that works is written to backend/.env.local.
    if config.APP_MODE != "local":
        raise SetupError("Keys can't be entered here on a hosted server. Set them as environment variables there.")
    if provider not in PROVIDERS:
        raise SetupError(f"Unknown provider '{provider}'.")
    if provider == "jev":
        if host not in JEV_FIELDS:
            raise SetupError("Choose where Jev runs: " + ", ".join(HOST_LABEL.values()) + ".")
        wanted = JEV_FIELDS[host]
    else:
        wanted = ["GEMINI_API_KEY"]
    clean: dict[str, str] = {}
    for var in wanted:
        v = (values.get(var) or "").strip()
        if not v and os.getenv(var, "").strip():
            continue  # keep the one already set
        if not KEY_RE.match(v):
            raise SetupError(f"The {FIELD_LABEL[var].lower()} doesn't look right: paste it without spaces or quotes.")
        clean[var] = v
    before = {v: os.environ.get(v) for v in [*clean, "JEV_PROVIDER"]}
    before_cfg = (config.GEMINI_API_KEY, config.JEV_PROVIDER)
    _apply(clean, host if provider == "jev" else None)
    try:
        ad = models.ADAPTERS[provider]
        await asyncio.wait_for(ad.check_key(_key(provider)), timeout=30)
    except (models.ModelError, TimeoutError, asyncio.TimeoutError) as e:
        for var, old in before.items():  # the old keys stay in force
            if old is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = old
        config.GEMINI_API_KEY, config.JEV_PROVIDER = before_cfg
        status = e.error_class if isinstance(e, models.ModelError) else "Timeout"
        return {"status": status, "plain": PLAIN.get(status, status), "message": getattr(e, "message", ""), "saved": False}
    _write_env_local({**clean, **({"JEV_PROVIDER": host} if provider == "jev" else {})})
    _forget(provider)
    shadowed = [v for v in clean if config.from_shell(v)]
    return {"status": "ok", "plain": "Key works and is saved", "saved": True,
            "warning": (f"{', '.join(shadowed)} is also set in the server's environment, which wins after a restart."
                        if shadowed else "")}


def _apply(values: dict[str, str], host: str | None) -> None:
    from app.clients.redact import remember

    for var, v in values.items():
        os.environ[var] = v
        if var not in NOT_SECRET:
            remember(v)
    if "GEMINI_API_KEY" in values:
        config.GEMINI_API_KEY = values["GEMINI_API_KEY"]
    if host:
        os.environ["JEV_PROVIDER"] = host
        config.JEV_PROVIDER = host


def _forget(provider: str) -> None:
    for store in (_checked, _lists):
        for k in [k for k in store if k[0] == provider]:
            del store[k]


# ---- models

def _fingerprint(provider: str) -> tuple:
    # Results are kept per key (by hash, never the key) and per Jev host.
    return (hashlib.sha256(_key(provider).encode()).hexdigest()[:16], config.JEV_PROVIDER if provider == "jev" else "")


def _result(provider: str, model: str, status: str, message: str = "") -> dict[str, Any]:
    return {"provider": provider, "model": model, "status": status, "plain": PLAIN.get(status, status),
            "message": message, "ok": status == "ok", "temporary": status in TEMPORARY}


async def check(provider: str, model: str, fresh: bool = False) -> dict[str, Any]:
    # validate() with this server's key, remembered for a while so Save and the run check don't repeat calls.
    if provider not in models.ADAPTERS:
        return _result(provider, model, "ConfigError", f"unknown provider '{provider}'")
    if provider == "jev" and jev.disabled():
        return _result(provider, model, "Off", jev.disabled() or "")
    if not _key(provider):
        return _result(provider, model, "NoKey", f"{key_vars(provider)[-1] if key_vars(provider) else 'The key'} is not set")
    ck = (provider, model, *_fingerprint(provider))
    hit = _checked.get(ck)
    if hit and not fresh and time.monotonic() < hit[0]:
        return hit[1]
    async with _CHECKS:
        r = await models.validate(provider, model, _key(provider))
    res = _result(provider, model, r["status"], r["message"])
    _checked[ck] = (time.monotonic() + (OK_TTL_S if res["ok"] else FAIL_TTL_S), res)
    return res


async def check_many(refs: list[tuple[str, str]], fresh: bool = False) -> list[dict[str, Any]]:
    unique = list(dict.fromkeys(refs))
    return list(await asyncio.gather(*(check(p, m, fresh) for p, m in unique)))


async def provider_models(provider: str) -> dict[str, Any]:
    if provider not in models.ADAPTERS:
        raise SetupError(f"Unknown provider '{provider}'.")
    ad = models.ADAPTERS[provider]
    if provider == "jev" and jev.disabled():
        return {"provider": provider, "models": [], "status": "Off", "plain": "Jev is turned off"}
    if not _key(provider):
        return {"provider": provider, "models": [], "status": "NoKey", "plain": PLAIN["NoKey"]}
    ck = (provider, *_fingerprint(provider))
    hit = _lists.get(ck)
    if hit and time.monotonic() < hit[0]:
        return {"provider": provider, "models": hit[1], "status": "ok", "plain": ""}
    try:
        items = await asyncio.wait_for(ad.list_models(_key(provider)), timeout=30)
    except models.ModelError as e:
        return {"provider": provider, "models": [], "status": e.error_class, "plain": PLAIN.get(e.error_class, ""),
                "message": e.message}
    except (TimeoutError, asyncio.TimeoutError):
        return {"provider": provider, "models": [], "status": "Timeout", "plain": PLAIN["Timeout"]}
    _lists[ck] = (time.monotonic() + LIST_TTL_S, items)
    return {"provider": provider, "models": items, "status": "ok", "plain": ""}


# ---- the config

def _refs(role: Role) -> list[dict[str, str]]:
    return [{"provider": r.provider, "model": r.model} for r in role.models]


def _local_roles() -> dict[str, Any]:
    try:
        return models._read(config.MODELS_LOCAL_FILE).get("roles", {})
    except models.ConfigError:
        return {}


def problems(roles: dict[str, Role]) -> list[dict[str, str]]:
    # Cheap, no calls: roles that runs need now but that have no model with a key.
    out = []
    for name, r in roles.items():
        if models.in_use(r) is None and not any(models.ADAPTERS[m.provider].configured() for m in r.models):
            out.append({"role": name, "label": r.label, "reason": "None of its models has a key yet."})
    return out


def readiness() -> dict[str, Any]:
    try:
        roles, error = models.load(), None
    except models.ConfigError as e:
        roles, error = {}, str(e)
    found = problems(roles)
    return {"ready": error is None and not found, "first_launch": not config.MODELS_LOCAL_FILE.exists(),
            "problems": found, "error": error}


def config_view() -> dict[str, Any]:
    defaults = models.load(local=False)
    try:
        roles, error = models.load(), None
    except models.ConfigError as e:
        roles, error = defaults, f"config/models.local.json has a problem and was ignored here: {e}"
    local = _local_roles()
    view = []
    for name, r in roles.items():
        d = defaults.get(name, r)
        view.append({"name": name, "label": r.label, "group": r.group, "description": r.description, "kind": r.kind,
                     "not_used": models.in_use(r), "models": _refs(r), "default_models": _refs(d),
                     "customized": name in local, "providers": [p for p, ad in models.ADAPTERS.items() if r.kind in ad.KINDS
                                                                 and p in PROVIDERS]})
    return {"mode": config.APP_MODE, "providers": provider_status(), "roles": view, "error": error,
            "local_file": config.MODELS_LOCAL_FILE.exists(), "problems": problems(roles),
            "max_models_per_role": MAX_MODELS_PER_ROLE}


def _parse_body(body: dict[str, Any]) -> dict[str, Role]:
    current = models.load(local=False)
    try:
        current = models.load()
    except models.ConfigError:
        pass  # a broken local file is replaced by what's saved now
    raw = {name: {"kind": r.kind, "label": r.label, "group": r.group, "description": r.description, "when": r.when,
                  "models": _refs(r)} for name, r in current.items()}
    for name, refs in (body or {}).items():
        if name not in raw:
            raise SetupError(f"There's no job called '{name}'.")
        label = raw[name]["label"]
        if not isinstance(refs, list) or not refs:
            raise SetupError(f"{label}: choose at least one model.")
        if len(refs) > MAX_MODELS_PER_ROLE:
            raise SetupError(f"{label}: up to {MAX_MODELS_PER_ROLE} models.")
        clean = []
        for ref in refs:
            p, m = (ref or {}).get("provider"), str((ref or {}).get("model") or "").strip()
            if p not in PROVIDERS or raw[name]["kind"] not in models.ADAPTERS[p].KINDS:
                raise SetupError(f"{label}: {PROVIDERS.get(p, {}).get('label', p)} can't do this job.")
            if not MODEL_RE.match(m):
                raise SetupError(f"{label}: '{m[:40]}' isn't a model name.")
            if {"provider": p, "model": m} in clean:
                raise SetupError(f"{label}: {m} is listed twice.")
            clean.append({"provider": p, "model": m})
        raw[name]["models"] = clean
    try:
        return models.parse(raw)
    except models.ConfigError as e:
        raise SetupError(str(e)) from None


async def save(body: dict[str, Any]) -> dict[str, Any]:
    # Blocked when a job runs need has no working model; a failing fallback is only a warning.
    roles = _parse_body(body)
    needed = {n: r for n, r in roles.items() if models.in_use(r) is None}
    results = await check_many([(m.provider, m.model) for r in needed.values() for m in r.models])
    by = {(x["provider"], x["model"]): x for x in results}
    blocked, warnings = [], []
    for name, r in needed.items():
        rs = [by[(m.provider, m.model)] for m in r.models]
        if not any(x["ok"] for x in rs):
            blocked.append({"role": name, "label": r.label, "reason": "; ".join(f"{x['model']}: {x['plain']}" for x in rs)})
        else:  # a provider switched off on purpose (Jev with JEV_PROVIDER unset) is skipped, not a failure
            warnings += [f"{r.label}: backup {x['model']} failed ({x['plain']})" for x in rs if not x["ok"] and x["status"] != "Off"]
    if blocked:
        raise SetupError("Some jobs have no working model yet. Pick another model for them or fix their keys.",
                         blocked=blocked, results=results)
    defaults = models.load(local=False)
    changed = {n: {"models": [{"provider": m.provider, "model": m.model} for m in r.models]}
               for n, r in roles.items() if r.models != defaults[n].models}
    doc = {"_about": "Written by the setup screen. Each role here replaces the same role in config/models.json.",
           "roles": changed}
    path = config.MODELS_LOCAL_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    models._loaded = None
    return {"saved": True, "warnings": warnings, "results": results, "changed": sorted(changed)}


def reset() -> dict[str, Any]:
    config.MODELS_LOCAL_FILE.unlink(missing_ok=True)
    models._loaded = None
    return {"reset": True}


# ---- before a live run

async def preflight(force: bool = False, only: set[str] | None = None) -> list[dict[str, Any]]:
    # For each job the run needs: its first model, then the next ones only if that fails. Returns the jobs with no
    # working model (empty = go). force skips jobs whose models are only busy or out of quota for now. only: check
    # just these jobs (an extraction needs only the reader).
    try:
        roles = models.load()
    except models.ConfigError as e:
        raise SetupError("The model setup can't be read.", problems=[{"role": "", "label": "Model setup",
                                                                    "reason": str(e), "tried": []}], can_force=False)
    memo: dict[ModelRef, asyncio.Task] = {}

    def status(ref: ModelRef) -> asyncio.Task:
        if ref not in memo:
            memo[ref] = asyncio.ensure_future(check(ref.provider, ref.model))
        return memo[ref]

    async def job(name: str, r: Role) -> dict[str, Any] | None:
        tried = []
        for ref in r.models:
            res = await status(ref)
            if res["status"] == "Off":
                continue  # switched off on purpose, as the run itself would skip it
            if res["ok"]:
                return None
            tried.append(res)
        reason = "; ".join(f"{x['provider']}/{x['model']}: {x['plain']}" for x in tried) or "Every model for it is turned off."
        return {"role": name, "label": r.label, "reason": reason, "tried": tried,
                "temporary": bool(tried) and all(x["temporary"] for x in tried)}

    found = [p for p in await asyncio.gather(*(job(n, r) for n, r in roles.items()
                                              if models.in_use(r) is None and (only is None or n in only))) if p]
    can_force = bool(found) and all(p["temporary"] for p in found)
    if found and not (force and can_force):
        first = found[0]
        raise SetupError(f"Can't start: {first['label']} has no working model ({first['reason']}).",
                         problems=found, can_force=can_force and only is None)
    return found
