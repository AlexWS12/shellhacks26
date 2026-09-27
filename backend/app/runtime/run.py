import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.clients.redact import contains_secret, redact
from app.config import PACE, RUNS_DIR
from app.core.models import RESEARCH_CATEGORIES
from app.runtime.board import Board

Event = dict[str, Any]


@dataclass
class Run:
    id: str
    mode: str  # live | replay
    pace: float = PACE  # demo delay multiplier, 0 = instant
    source: str | None = None  # replay: the recorded run id
    templates: bool = True  # False: a Gemini failure fails the agent instead of writing a template
    purpose: str = "pipeline"  # pipeline | extract (an AI reader reading one source; not a replay default)
    research: list[str] = field(default_factory=lambda: list(RESEARCH_CATEGORIES))  # chosen before the run
    preflight_skipped: list[dict[str, Any]] = field(default_factory=list)  # jobs started without a working model
    events: list[Event] = field(default_factory=list)
    finished: bool = False
    board: Board = field(default_factory=Board)
    _subscribers: set[asyncio.Queue[Event | None]] = field(default_factory=set)
    _file: Any = None

    def open_log(self) -> Path:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        path = RUNS_DIR / f"{self.id}.jsonl"
        self._file = path.open("w", encoding="utf-8")
        return path

    def emit(self, type: str, **payload: Any) -> Event:
        event: Event = {"type": type, "run_id": self.id, "seq": len(self.events), "ts": round(time.time(), 3), **payload}
        line = json.dumps(event, default=str)
        if contains_secret(line):  # last line of defense: no key reaches the log, the UI or Tiger Data
            line = redact(line)
            event = json.loads(line)
        self.events.append(event)
        if self._file:
            self._file.write(line + "\n")
            self._file.flush()  # a crashed run still leaves a usable log
        for q in self._subscribers:
            q.put_nowait(event)
        if type in ("run.done", "run.failed"):
            self.close()
        return event

    def close(self) -> None:
        self.finished = True
        for q in self._subscribers:
            q.put_nowait(None)
        if self._file:
            self._file.close()
            self._file = None

    async def sleep(self, seconds: float) -> None:
        if self.pace > 0 and seconds > 0:
            await asyncio.sleep(seconds * self.pace)

    def subscribe(self) -> tuple[list[Event], asyncio.Queue[Event | None]]:
        # No await between the snapshot and subscribing, so no event gets missed.
        q: asyncio.Queue[Event | None] = asyncio.Queue()
        if self.finished:
            q.put_nowait(None)
        else:
            self._subscribers.add(q)
        return list(self.events), q

    def unsubscribe(self, q: asyncio.Queue[Event | None]) -> None:
        self._subscribers.discard(q)


RUNS: dict[str, Run] = {}
_TASKS: set[asyncio.Task[Any]] = set()


def new_run(mode: str, source: str | None = None) -> Run:
    run = Run(id=f"run_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}", mode=mode, source=source)
    RUNS[run.id] = run
    return run


def start_background(coro: Any) -> None:
    t = asyncio.create_task(coro)
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
