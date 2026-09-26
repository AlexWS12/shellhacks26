# Jev watchdog from Agent World. It only watches; it never slows the agents down.

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from app import config
from app.clients import jev
from app.clients.judge import _heuristic_actor
from app.runtime.run import Event, Run

DEBOUNCE_S = 2.0
TICK_S = 2.0  # also checks agents that went quiet

QUESTIONS: dict[str, Any] = {
    "stuck": {"type": "noul", "instructions": "Is this pipeline agent stuck, i.e. not making progress on its job?",
              "criteria": {"true": "Its counters stopped moving, it repeats the same step, or it keeps failing the same way.",
                           "false": "Recent events show it moving through its records or steps."}},
    "progress": {"type": "score", "instructions": "How far along is this agent in its job?",
                 "criteria": ["Not started", "Started", "About halfway", "Nearly done", "Done"]},
}


@dataclass
class Watch:
    name: str
    role: str
    started: float
    recent: list[str] = field(default_factory=list)
    count: int = 0
    total: int | None = None
    last_progress: float = 0.0
    last_check: float = 0.0
    done: bool = False
    working: bool = False


def _heuristic(w: Watch, now: float) -> dict[str, Any]:
    frac = (w.count / w.total) if w.total else 0.5
    stalled = now - (w.last_progress or w.started) > 8  # no events at all for 8 s
    return {"stuck": {"noul": 0.6 if stalled else 0.05}, "progress": {"score": round(min(4.0, frac * 4), 2)}}


async def watch(run: Run) -> None:
    if not config.JEV_PROVIDER:
        return
    past, queue = run.subscribe()
    agents: dict[str, Watch] = {}
    tasks: set[asyncio.Task[Any]] = set()

    async def check(aid: str) -> None:
        w = agents[aid]
        now = time.monotonic()
        state = {"agent": w.name, "role": w.role, "running_s": round(now - w.started, 1),
                 "records": f"{w.count}/{w.total}" if w.total else w.count, "recent_events": w.recent[-6:]}
        started = time.perf_counter()
        r = await jev.evaluate(state, QUESTIONS, use_cache=False) if jev.enabled() else {}
        answers, actor = (r["answers"], "jev") if r else (_heuristic(w, now), _heuristic_actor())
        if w.done or run.finished:
            return
        run.emit("agent.health", agent_id=aid, actor=actor, stuck=round(float(answers["stuck"]["noul"]), 3),
                 progress=round(float(answers["progress"]["score"]), 2),
                 latency_ms=r.get("latency_ms", round((time.perf_counter() - started) * 1000)) if r else 0,
                 cost_usd=round(r.get("cost_usd", 0.0), 7) if r else 0.0)

    def on_event(e: Event) -> None:
        t, aid = e["type"], e.get("agent_id")
        if t == "run.started":
            now = time.monotonic()
            for a in e["agents"]:
                agents[a["id"]] = Watch(a["name"], a["role"], now)
            return
        if not aid or aid not in agents or t == "agent.health":
            return
        w = agents[aid]
        if t == "agent.spawned":
            w.started, w.working = time.monotonic(), True
        if t in ("agent.done", "agent.error"):
            w.done, w.working = True, False
            return
        w.last_progress = time.monotonic()  # any event counts as a sign of life
        if t in ("agent.progress", "source.progress"):
            w.count, w.total = e.get("count", e.get("read", 0)), e.get("total")
        w.recent.append(f"{t}: {e.get('summary') or e.get('label') or e.get('current') or e.get('tool') or ''}"[:120])
        maybe_check(aid)

    def maybe_check(aid: str) -> None:
        w = agents[aid]
        now = time.monotonic()
        if now - w.last_check >= DEBOUNCE_S:
            w.last_check = now
            task = asyncio.create_task(check(aid))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    async def tick() -> None:
        while not run.finished:
            await asyncio.sleep(TICK_S)
            for aid, w in agents.items():
                if w.working and not w.done:
                    maybe_check(aid)

    ticker = asyncio.create_task(tick())
    try:
        for e in past:
            on_event(e)
        while (e := await queue.get()) is not None:
            on_event(e)
    finally:
        ticker.cancel()
        run.unsubscribe(queue)
