# What the registry hands a provider adapter, and what it gets back.
#
# An adapter is a module with: PROVIDER, CACHE_VERSION, KINDS, disabled() -> reason | None, configured() -> bool,
# payload(model, req) -> cache payload, cached_value(req, hit) -> value, async call(model, req) -> Reply,
# async validate(model, key) (raises a ModelError), async list_models(key) (live from the provider's API),
# async check_key(key), and, for text roles, async open_stream(model, req).
# Adapters raise only ModelError subclasses. The registry owns retries, fallbacks and the circuit breaker.

import json
from dataclasses import dataclass, field
from typing import Any

from app.clients.errors import BadResponse


@dataclass
class Judgment:
    # Typed questions about a state: Jev's native shape. {id: {type: noul|choice|score, instructions, criteria}}
    state: Any
    questions: dict[str, Any]


@dataclass
class Request:
    kind: str  # json | text | search | judge
    system: str = ""
    prompt: str = ""
    schema: dict[str, Any] | None = None
    judgment: Judgment | None = None


@dataclass
class Reply:
    value: Any  # json: the object; search: {text, sources, supports}; judge: {id: answer}; text: str
    stored: dict[str, Any] = field(default_factory=dict)  # what goes in the cache file
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def state_text(state: Any) -> str:
    return state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)


def render_judgment(j: Judgment) -> tuple[str, str, dict[str, Any]]:
    # For providers without typed questions: one JSON reply with a field per question id.
    # A single yes/no or choice question keeps the wording the judge used before the registry.
    qs = j.questions
    if len(qs) == 1:
        (qid, q), = qs.items()
        c = q["criteria"]
        if q["type"] == "noul":
            return ("You make one yes/no judgment. Reply with the probability that the statement is true.",
                    f"{q['instructions']}\nTRUE means: {c['true']}\nFALSE means: {c['false']}\n\nState:\n{state_text(j.state)}",
                    {"type": "object", "properties": {"p_true": {"type": "number"}}, "required": ["p_true"]})
        if q["type"] == "choice":
            return ("You pick exactly one option.",
                    f"{q['instructions']}\nOptions:\n" + "\n".join(f"- {k}: {v}" for k, v in c.items())
                    + f"\n\nState:\n{state_text(j.state)}",
                    {"type": "object", "properties": {"choice": {"type": "string", "enum": list(c)}}, "required": ["choice"]})
    lines, props = [], {}
    for qid, q in qs.items():
        c = q["criteria"]
        if q["type"] == "noul":
            lines.append(f"{qid} (probability 0 to 1 that this is true): {q['instructions']}\n  TRUE means: {c['true']}\n"
                         f"  FALSE means: {c['false']}")
            props[qid] = {"type": "number", "minimum": 0, "maximum": 1}
        elif q["type"] == "choice":
            lines.append(f"{qid} (pick one): {q['instructions']}\n" + "\n".join(f"  - {k}: {v}" for k, v in c.items()))
            props[qid] = {"type": "string", "enum": list(c)}
        else:  # score: criteria is the ordered list of levels
            lines.append(f"{qid} (level number): {q['instructions']}\n" + "\n".join(f"  {i}: {v}" for i, v in enumerate(c)))
            props[qid] = {"type": "number", "minimum": 0, "maximum": len(c) - 1}
    return ("Answer each question about the state. Reply in JSON with one field per question id.",
            "\n\n".join(lines) + f"\n\nState:\n{state_text(j.state)}",
            {"type": "object", "properties": props, "required": list(props)})


def parse_judgment(j: Judgment, data: Any) -> dict[str, Any]:
    # The reply to render_judgment, in Jev's answer shape.
    qs = j.questions
    if len(qs) == 1 and next(iter(qs.values()))["type"] in ("noul", "choice"):
        (qid, q), = qs.items()
        if q["type"] == "noul":
            data = {qid: (data or {}).get("p_true")}
        else:
            data = {qid: (data or {}).get("choice")}
    out: dict[str, Any] = {}
    for qid, q in qs.items():
        v = (data or {}).get(qid)
        if q["type"] == "noul":
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise BadResponse(f"no probability for '{qid}'")
            out[qid] = {"noul": max(0.0, min(1.0, float(v)))}
        elif q["type"] == "choice":
            if v not in q["criteria"]:
                raise BadResponse(f"'{qid}' is not one of the options")
            out[qid] = {"choice": v, "confidence": 0.7, "probabilities": {}}
        else:
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= len(q["criteria"]) - 1:
                raise BadResponse(f"no level for '{qid}'")
            out[qid] = {"score": float(v)}
    return out


def check_answers(j: Judgment, answers: Any) -> dict[str, Any]:
    # A typed provider's answers must still answer every question with the right type.
    if not isinstance(answers, dict):
        raise BadResponse("reply has no answers")
    for qid, q in j.questions.items():
        a = answers.get(qid)
        key = {"noul": "noul", "choice": "choice", "score": "score"}[q["type"]]
        if not isinstance(a, dict) or key not in a:
            raise BadResponse(f"no {q['type']} answer for '{qid}'")
        if q["type"] == "choice" and a["choice"] not in q["criteria"]:
            raise BadResponse(f"'{qid}' is not one of the options")
        if q["type"] != "choice" and (not isinstance(a[key], (int, float)) or isinstance(a[key], bool)):
            raise BadResponse(f"'{qid}' is not a number")
    return answers
