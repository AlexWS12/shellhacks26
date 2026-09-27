# Each agent starts when its dependencies finish, so independent agents run at the same time.

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from app.config import env_float
from app.runtime.agent import Agent, Ctx
from app.runtime.run import Run

log = logging.getLogger("executor")
AGENT_TIMEOUT_S = env_float("TANDEM_AGENT_TIMEOUT_S", 240.0)  # so one hung API call cannot hang the run


def topological_order(agents: list[Agent]) -> list[str] | None:
    remaining = {a.spec.id: set(a.spec.depends_on) for a in agents}
    order: list[str] = []
    while remaining:
        ready = [aid for aid, deps in remaining.items() if not deps]
        if not ready:
            return None
        for aid in sorted(ready):
            order.append(aid)
            del remaining[aid]
        for deps in remaining.values():
            deps.difference_update(ready)
    return order


async def execute(run: Run, agents: list[Agent], sources: list[dict], on_done: Callable[[Run], Awaitable[None]] | None = None) -> None:
    if topological_order(agents) is None:
        raise ValueError("agent graph has a cycle")
    started = time.perf_counter()
    run.emit("run.started", mode=run.mode, templates=run.templates, research=run.research,
             agents=[a.spec.public() for a in agents], sources=sources)
    finished = {a.spec.id: asyncio.Event() for a in agents}
    summaries: dict[str, str] = {}
    ctxs: dict[str, Ctx] = {}

    async def node(agent: Agent) -> None:
        spec = agent.spec
        try:
            await asyncio.gather(*(finished[d].wait() for d in spec.depends_on))
            blocked = [d for d in spec.depends_on if d not in summaries]
            if blocked:
                run.emit("agent.error", agent_id=spec.id, message=f"Skipped: upstream {', '.join(blocked)} failed")
                return
            for dep in spec.depends_on:
                run.emit("handoff", from_agent=dep, to_agent=spec.id, summary=summaries[dep])
            ctx = ctxs[spec.id] = Ctx(run, spec)
            run.emit("agent.spawned", agent_id=spec.id)
            t0 = time.perf_counter()
            summary = await asyncio.wait_for(agent.run(ctx), timeout=AGENT_TIMEOUT_S)
            summaries[spec.id] = summary
            run.emit("agent.done", agent_id=spec.id, summary=summary, judgments=ctx.judgments,
                     tokens=ctx.tokens, cost_usd=round(ctx.cost_usd, 6), ms=round((time.perf_counter() - t0) * 1000))
        except TimeoutError:
            run.emit("agent.error", agent_id=spec.id, message=f"Timed out after {AGENT_TIMEOUT_S:.0f} s")
        except Exception as e:
            log.exception("agent %s failed", spec.id)
            run.emit("agent.error", agent_id=spec.id, message=f"{type(e).__name__}: {e}")
        finally:
            finished[spec.id].set()

    await asyncio.gather(*(node(a) for a in agents))
    failed = [a.spec.id for a in agents if a.spec.id not in summaries]
    if on_done and not failed:
        await on_done(run)
    b = run.board
    texts = [*b.analyses.values(), *(s for brief in b.briefs.values() for s in brief.values())]
    run.emit("run.done", ok=not failed, failed=failed, ms=round((time.perf_counter() - started) * 1000),
             stats={"projects": len(b.projects),
                    "located": sum(1 for p in b.projects.values() if p.lat is not None),
                    "overlaps": len(b.overlaps), "checks": len(b.checks),
                    "reference_passed": sum(r.passed for r in b.reference), "reference_total": len(b.reference),
                    "texts": len(texts), "templates": sum(t["actor"] == "template" for t in texts)},
             total_cost_usd=round(sum(c.cost_usd for c in ctxs.values()), 6),
             judgments=sum(c.judgments for c in ctxs.values()))
