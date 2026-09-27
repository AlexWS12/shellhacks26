# /api/models: the setup screen's endpoints. In hosted mode (APP_MODE=hosted) every one needs the admin passcode
# in the X-Admin-Passcode header, checked here on the server; key entry is refused there altogether.

import hmac
import time
from collections import deque
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app import config, setup

MAX_FAILS_PER_MIN = 10
_fails: deque[float] = deque()


def require_admin(x_admin_passcode: str | None = Header(default=None)) -> None:
    if config.APP_MODE != "hosted":
        return
    if not config.ADMIN_PASSCODE:
        raise HTTPException(503, "Setup is locked: the server has no ADMIN_PASSCODE set.")
    now = time.monotonic()
    while _fails and now - _fails[0] > 60:
        _fails.popleft()
    if len(_fails) >= MAX_FAILS_PER_MIN:
        raise HTTPException(429, "Too many wrong passcodes. Wait a minute and try again.")
    if not x_admin_passcode or not hmac.compare_digest(x_admin_passcode.encode(), config.ADMIN_PASSCODE.encode()):
        _fails.append(now)
        raise HTTPException(401, "Wrong passcode." if x_admin_passcode else "This server needs the admin passcode.")


router = APIRouter(prefix="/api/models", dependencies=[Depends(require_admin)])


def _fail(e: setup.SetupError, status: int = 400) -> HTTPException:
    return HTTPException(status, {"message": e.message, **e.details})


class Ref(BaseModel):
    provider: str
    model: str


class ValidateIn(BaseModel):
    models: list[Ref]
    fresh: bool = True  # the Test button asks the providers again; Save reuses recent results


class ConfigIn(BaseModel):
    roles: dict[str, list[Ref]]


class KeysIn(BaseModel):
    provider: str
    host: str | None = None  # Jev: typesafe | openrouter | cloudflare
    values: dict[str, str]


@router.get("/config")
def get_config() -> dict[str, Any]:
    return setup.config_view()


@router.get("/providers/{provider}/models")
async def provider_models(provider: str) -> dict[str, Any]:
    try:
        return await setup.provider_models(provider)
    except setup.SetupError as e:
        raise _fail(e, 404) from None


@router.post("/validate")
async def validate(req: ValidateIn) -> dict[str, Any]:
    if len(req.models) > 40:
        raise HTTPException(400, "Up to 40 models at a time.")
    return {"results": await setup.check_many([(m.provider, m.model) for m in req.models], fresh=req.fresh)}


@router.put("/config")
async def put_config(req: ConfigIn) -> dict[str, Any]:
    try:
        return await setup.save({k: [r.model_dump() for r in v] for k, v in req.roles.items()})
    except setup.SetupError as e:
        raise _fail(e) from None


@router.delete("/config")
def reset_config() -> dict[str, Any]:
    return setup.reset()


@router.put("/keys")
async def put_keys(req: KeysIn) -> dict[str, Any]:
    if config.APP_MODE != "local":
        raise HTTPException(403, "Keys can't be entered here on a hosted server. Set them as environment variables there.")
    try:
        r = await setup.save_keys(req.provider, req.values, req.host)
    except setup.SetupError as e:
        raise _fail(e) from None
    return {**r, "providers": setup.provider_status()}
