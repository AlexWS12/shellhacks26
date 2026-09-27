# Checks every model in config/models.json with its key: a list-models call where the provider has one,
# then one tiny live call. Prints ok, AuthError or ModelNotFound per model. Never prints a key.
#   uv run python scripts/smoke.py

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.clients import jev, models  # noqa: E402


def key_for(provider: str) -> str:
    if provider == "gemini":
        return config.GEMINI_API_KEY
    return os.getenv(jev.HOSTS[config.JEV_PROVIDER], "") if config.JEV_PROVIDER in jev.HOSTS else ""


async def main() -> int:
    seen: set[str] = set()
    ok = True
    print(f"Jev host: {config.JEV_PROVIDER or 'off'}")
    for role in models.roles().values():
        for ref in role.models:
            if ref.name in seen:
                continue
            seen.add(ref.name)
            if ref.provider == "jev" and jev.disabled():
                print(f"  {ref.name}: skipped ({jev.disabled()})")
                continue
            r = await models.validate(ref.provider, ref.model, key_for(ref.provider))
            print(f"  {ref.name}: {r['status']}" + (f" ({r['message']})" if r["message"] else ""))
            ok &= r["status"] == "ok"
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
