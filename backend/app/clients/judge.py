# Small decisions: try Jev, then Gemini, then a local rule.
# The verdict records who actually answered.

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app import config
from app.clients import gemini, jev


@dataclass
class Verdict:
    value: Any  # float for noul (probability "true"), str for choice
    confidence: float
    actor: str  # jev | gemini | heuristic | jev-mock
    latency_ms: int = 0
    cost_usd: float = 0.0
    cached: bool = False
    probabilities: dict[str, float] = field(default_factory=dict)


def _state_text(state: Any) -> str:
    return state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)


def _heuristic_actor() -> str:
    return "jev-mock" if config.JEV_PROVIDER == "mock" else "heuristic"


async def noul(state: Any, instructions: str, true: str, false: str, heuristic: Callable[[], float]) -> Verdict:
    q = {"q": {"type": "noul", "instructions": instructions, "criteria": {"true": true, "false": false}}}
    r = await jev.evaluate(state, q)
    if r:
        p = float(r["answers"]["q"]["noul"])
        return Verdict(p, abs(p - 0.5) * 2, "jev", r["latency_ms"], r["cost_usd"], r.get("cached", False))
    g = await gemini.generate_json(
        "You make one yes/no judgment. Reply with the probability that the statement is true.",
        f"{instructions}\nTRUE means: {true}\nFALSE means: {false}\n\nState:\n{_state_text(state)}",
        {"type": "object", "properties": {"p_true": {"type": "number"}}, "required": ["p_true"]})
    if g and isinstance(g.get("data", {}).get("p_true"), (int, float)):
        p = max(0.0, min(1.0, float(g["data"]["p_true"])))
        return Verdict(p, abs(p - 0.5) * 2, "gemini", g.get("latency_ms", 0), 0.0, g.get("cached", False))
    p = heuristic()
    return Verdict(p, abs(p - 0.5) * 2, _heuristic_actor())


async def choice(state: Any, instructions: str, options: dict[str, str], heuristic: Callable[[], str]) -> Verdict:
    q = {"q": {"type": "choice", "instructions": instructions, "criteria": options}}
    r = await jev.evaluate(state, q)
    if r:
        a = r["answers"]["q"]
        return Verdict(a["choice"], float(a.get("confidence", 0)), "jev", r["latency_ms"], r["cost_usd"],
                       r.get("cached", False), a.get("probabilities", {}))
    g = await gemini.generate_json(
        "You pick exactly one option.",
        f"{instructions}\nOptions:\n" + "\n".join(f"- {k}: {v}" for k, v in options.items())
        + f"\n\nState:\n{_state_text(state)}",
        {"type": "object", "properties": {"choice": {"type": "string", "enum": list(options)}}, "required": ["choice"]})
    if g and g.get("data", {}).get("choice") in options:
        return Verdict(g["data"]["choice"], 0.7, "gemini", g.get("latency_ms", 0), 0.0, g.get("cached", False))
    return Verdict(heuristic(), 0.5, _heuristic_actor())
