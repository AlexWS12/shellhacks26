# Small decisions: the role's models in order (config/models.json; usually Jev, then Gemini), then a local rule.
# The verdict records who actually answered, and which model.

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app import config
from app.clients import models
from app.clients.base import Judgment


@dataclass
class Verdict:
    value: Any  # float for noul (probability "true"), str for choice
    confidence: float
    actor: str  # jev | gemini | heuristic | jev-mock
    latency_ms: int = 0
    cost_usd: float = 0.0
    cached: bool = False
    probabilities: dict[str, float] = field(default_factory=dict)
    model: str | None = None  # None when a local rule answered


def _heuristic_actor() -> str:
    return "jev-mock" if config.JEV_PROVIDER == "mock" else "heuristic"


async def noul(state: Any, instructions: str, true: str, false: str, heuristic: Callable[[], float], *,
               role: str) -> Verdict:
    q = {"q": {"type": "noul", "instructions": instructions, "criteria": {"true": true, "false": false}}}
    try:
        r = await models.call(role, Judgment(state, q))
    except models.RoleExhausted:
        p = heuristic()
        return Verdict(p, abs(p - 0.5) * 2, _heuristic_actor())
    p = float(r.value["q"]["noul"])
    return Verdict(p, abs(p - 0.5) * 2, r.provider, r.latency_ms, r.cost_usd, r.cached, model=r.model)


async def choice(state: Any, instructions: str, options: dict[str, str], heuristic: Callable[[], str], *,
                 role: str) -> Verdict:
    q = {"q": {"type": "choice", "instructions": instructions, "criteria": options}}
    try:
        r = await models.call(role, Judgment(state, q))
    except models.RoleExhausted:
        return Verdict(heuristic(), 0.5, _heuristic_actor())
    a = r.value["q"]
    return Verdict(a["choice"], float(a.get("confidence", 0)), r.provider, r.latency_ms, r.cost_usd, r.cached,
                   a.get("probabilities", {}), model=r.model)
