# Everything the UI shows goes through ctx.emit. That's what makes replay work.

import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from app.clients import judge
from app.clients.judge import Verdict
from app.runtime.board import Board
from app.runtime.run import Run


@dataclass
class AgentSpec:
    id: str
    name: str  # 'Reader · DESC'
    role: str  # one line: what it does
    actors: list[str]  # who makes its decisions: code | gemini | jev | osm | sponsor_file
    depends_on: list[str] = field(default_factory=list)
    kind: str = "agent"  # agent | tool (plain code, no judgment)
    engine: str = "Rules"  # the one label the UI shows: what powers this agent

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "role": self.role, "actors": self.actors,
                "depends_on": self.depends_on, "kind": self.kind, "engine": self.engine}


class Ctx:
    def __init__(self, run: Run, spec: AgentSpec) -> None:
        self.run, self.spec = run, spec
        self.board: Board = run.board
        self.tokens = 0
        self.cost_usd = 0.0
        self.judgments = 0

    def emit(self, type: str, **payload: Any) -> dict[str, Any]:
        return self.run.emit(type, agent_id=self.spec.id, **payload)

    async def pace(self, seconds: float) -> None:
        await self.run.sleep(seconds)

    def think(self, chunk: str) -> None:
        self.emit("agent.thinking", chunk=chunk)

    def progress(self, count: int, total: int | None = None, label: str = "") -> None:
        self.emit("agent.progress", count=count, total=total, label=label)

    def log(self, text: str) -> None:
        self.emit("agent.log", text=text)

    @asynccontextmanager
    async def tool(self, name: str, args: dict[str, Any], actor: str = "code") -> AsyncIterator[dict[str, str]]:
        self.emit("tool.call", tool=name, args=args, actor=actor)
        started = time.perf_counter()
        out: dict[str, str] = {"summary": ""}
        try:
            yield out
        except Exception as e:
            self.emit("tool.result", tool=name, summary=f"ERROR {type(e).__name__}: {str(e)[:160]}", ok=False,
                      ms=round((time.perf_counter() - started) * 1000))
            raise
        self.emit("tool.result", tool=name, summary=out["summary"][:200], ok=True,
                  ms=round((time.perf_counter() - started) * 1000))

    def _record(self, v: Verdict, question: str, subject: str, project_id: str | None, answer: Any) -> None:
        self.judgments += 1
        self.cost_usd += v.cost_usd
        self.emit("judgment", actor=v.actor, question=question, subject=subject, project_id=project_id,
                  answer=answer, confidence=round(v.confidence, 3), latency_ms=v.latency_ms,
                  cost_usd=round(v.cost_usd, 7), cached=v.cached)

    async def ask_noul(self, question: str, subject: str, state: Any, true: str, false: str,
                       heuristic: Callable[[], float], project_id: str | None = None) -> Verdict:
        v = await judge.noul(state, question, true, false, heuristic)
        self._record(v, question, subject, project_id, round(float(v.value), 3))
        return v

    async def ask_choice(self, question: str, subject: str, state: Any, options: dict[str, str],
                         heuristic: Callable[[], str], project_id: str | None = None) -> Verdict:
        v = await judge.choice(state, question, options, heuristic)
        self._record(v, question, subject, project_id, v.value)
        return v


class Agent:
    spec: AgentSpec

    async def run(self, ctx: Ctx) -> str:
        # Return a one-line summary.
        raise NotImplementedError
