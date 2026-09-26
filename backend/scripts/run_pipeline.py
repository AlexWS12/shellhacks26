# Runs the pipeline without the server.
#   uv run python scripts/run_pipeline.py [--fast] [--quiet]

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline import run_live  # noqa: E402
from app.runtime.run import new_run  # noqa: E402

COLORS = {"agent.spawned": 36, "agent.done": 32, "agent.error": 31, "check.found": 33, "overlap.found": 35,
          "reference.result": 34, "run.done": 32, "run.failed": 31, "handoff": 90}
QUIET_SKIP = {"project.extracted", "source.progress", "project.placed", "project.unlocated", "project.classified",
              "agent.progress", "judgment", "agent.thinking", "cost.ready", "endpoint.rejected", "agent.health"}


def line(e: dict) -> str:
    t = e["type"]
    body = {k: v for k, v in e.items() if k not in ("type", "run_id", "seq", "ts")}
    text = str(body)[:160]
    c = COLORS.get(t, 0)
    return f"\033[{c}m{e['seq']:>5} {t:<20}\033[0m {text}"


async def main(fast: bool, quiet: bool) -> int:
    run = new_run("live")
    if fast:
        run.pace = 0
    past, queue = run.subscribe()
    task = asyncio.create_task(run_live(run))
    while (e := await queue.get()) is not None:
        if not quiet or e["type"] not in QUIET_SKIP:
            print(line(e))
    await task
    last = run.events[-1]
    print(f"\nrun {run.id}: {last['type']} {last.get('stats')} in {last.get('ms')} ms, "
          f"{last.get('judgments')} judgments")
    return 0 if last["type"] == "run.done" and last.get("ok") else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.fast, a.quiet)))
