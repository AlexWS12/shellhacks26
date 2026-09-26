# Every external call is cached here, so reruns are free and work offline.

import hashlib
import json
from typing import Any

from app.config import CACHE_DIR


def _path(provider: str, version: str, payload: Any):
    key = hashlib.sha256(json.dumps([provider, version, payload], sort_keys=True, default=str).encode()).hexdigest()
    return CACHE_DIR / provider / f"{key}.json"


def get(provider: str, version: str, payload: Any) -> Any | None:
    p = _path(provider, version, payload)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))["response"]
    return None


def put(provider: str, version: str, payload: Any, response: Any) -> None:
    p = _path(provider, version, payload)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"provider": provider, "version": version, "payload": payload, "response": response},
                            indent=1, default=str), encoding="utf-8")
