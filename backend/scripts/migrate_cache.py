# One-time cache migration for the model registry: every cached answer is filed under the model that gave it.
#   jev:    old keys had no model. They were all made with jev-latest (the old JEV_MODEL default), so each file
#           moves to the key that includes "model": "jev-latest".
#   gemini: keys already had the primary model, but an answer from the fallback model was filed under the
#           primary's key. Those move to the key of the model that answered.
# Safe to run twice: files already in place are left alone.
#   uv run python scripts/migrate_cache.py [--jev-model jev-latest] [--dry-run]

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.clients import cache  # noqa: E402
from app.config import CACHE_DIR  # noqa: E402


def move(path: Path, provider: str, version: str, payload: dict, response: dict, dry: bool) -> str:
    target = cache._path(provider, version, payload)
    if target == path:
        return "kept"
    if not dry:
        if not target.exists():  # an answer already filed there wins; this copy is a duplicate
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"provider": provider, "version": version, "payload": payload,
                                          "response": response}, indent=1, default=str), encoding="utf-8")
        path.unlink()
    return "moved"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jev-model", default="jev-latest")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    counts = {"jev moved": 0, "jev kept": 0, "gemini moved": 0, "gemini kept": 0}
    for path in sorted((CACHE_DIR / "jev").glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        payload, response = d["payload"], d["response"]
        model = payload.get("model") or args.jev_model
        payload = {"model": model, **{k: v for k, v in payload.items() if k != "model"}}
        response = {**response, "model": response.get("model") or model}
        counts["jev " + move(path, "jev", d["version"], payload, response, args.dry_run)] += 1
    for path in sorted((CACHE_DIR / "gemini").glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        payload, response = d["payload"], d["response"]
        answered = response.get("model") or payload.get("model")
        payload = {**payload, "model": answered}
        response = {**response, "model": answered}
        counts["gemini " + move(path, "gemini", d["version"], payload, response, args.dry_run)] += 1
    print(("Would change: " if args.dry_run else "") + ", ".join(f"{v} {k}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
