import asyncio
import json
import logging
from pathlib import Path

from app.config import RUNS_DIR
from app.runtime.run import Run
from app.store import dataset

log = logging.getLogger("replay")

MAX_GAP_S = 1.5  # long pauses in a recording (slow API calls) are shortened


def recorded_runs() -> list[dict]:
    out = []
    for p in sorted(RUNS_DIR.glob("*.jsonl"), reverse=True):
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
            first, last = json.loads(lines[0]), json.loads(lines[-1])
        except (IndexError, ValueError, OSError):
            continue  # empty or corrupt recording: not offered for replay
        out.append({"run_id": p.stem, "events": len(lines), "complete": last["type"] == "run.done",
                    "mode": first.get("mode"), "purpose": first.get("purpose", "pipeline"), "ok": last.get("ok"),
                    "stats": last.get("stats"),
                    "started": first.get("ts"), "seconds": round(last["ts"] - first["ts"], 1)})
    return out


async def replay(run: Run, source: Path, speed: float = 1.0) -> None:
    try:
        await _replay(run, source, speed)
    except Exception as e:
        log.exception("replay failed")
        if not run.finished:
            run.emit("run.failed", message=f"Replay failed: {type(e).__name__}: {e}")


async def _replay(run: Run, source: Path, speed: float) -> None:
    events = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    snapshot = source.with_name(f"{source.stem}.snapshot.json")
    prev_ts = None
    for e in events:
        if prev_ts is not None and run.pace > 0:
            gap = min(MAX_GAP_S, max(0.0, e["ts"] - prev_ts)) / max(speed, 0.01)
            await asyncio.sleep(gap * run.pace)
        prev_ts = e["ts"]
        payload = {k: v for k, v in e.items() if k not in ("type", "run_id", "seq", "ts")}
        if e["type"] == "run.done" and snapshot.exists():
            # so filters and pair details match the replayed run
            dataset.load_snapshot(json.loads(snapshot.read_text(encoding="utf-8")))
        if e["type"] == "run.started":
            payload["mode"] = "replay"
            payload["replay_of"] = source.stem
        run.emit(e["type"], **payload)
    if not run.finished:
        run.emit("run.failed", message="recording ended before run.done")
