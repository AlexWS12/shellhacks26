# One real Jev call and one real Gemini call.
#   uv run python scripts/smoke.py

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.clients import gemini, jev  # noqa: E402


async def main() -> int:
    ok = True
    print(f"Jev provider: {config.JEV_PROVIDER or 'off'}")
    if jev.enabled():
        state, questions = jev.smoke_payload()
        r = await jev.evaluate(state, questions, use_cache=False)
        print("  Jev:", json.dumps(r) if r else "FAILED (see log above)")
        ok &= bool(r)
    print(f"Gemini model: {config.GEMINI_MODEL} ({'key set' if gemini.enabled() else 'no key'})")
    if gemini.enabled():
        r = await gemini.generate_json("Answer in JSON.", f"Return {{\"ok\": true}}. nonce={asyncio.get_running_loop().time()}",
                                       {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]})
        print("  Gemini:", r if r else "FAILED (check GEMINI_MODEL against the current model list)")
        ok &= bool(r)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
