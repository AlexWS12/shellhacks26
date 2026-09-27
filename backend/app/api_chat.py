# POST /api/chat: answer a person's question about the finished run. Public, like the Sources menu.
# Uses the 'chat' role from config/models.json. The role is 'on_demand', so it is never needed to start a run.

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.clients import models
from app.core import chat as chat_core
from app.store import dataset

log = logging.getLogger("chat")
router = APIRouter(prefix="/api/chat")

MAX_QUESTION = 1000
MAX_HISTORY = 6  # turns of conversation put back into the prompt


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = ""


class ChatIn(BaseModel):
    question: str
    history: list[Turn] = Field(default_factory=list)


@router.post("")
async def ask(req: ChatIn) -> dict:
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Type a question.")
    if len(question) > MAX_QUESTION:
        raise HTTPException(400, f"Questions up to {MAX_QUESTION} characters.")
    if dataset.CURRENT.run_id is None:
        raise HTTPException(409, "Run the pipeline first, then ask about what it found.")
    if not models.available("chat"):
        raise HTTPException(503, "No model is set up for questions yet. Open Models and add one for "
                                 "'Answer questions about the run'.")
    history = [t.model_dump() for t in req.history[-MAX_HISTORY:]]
    prompt = chat_core.build_prompt(chat_core.build_context(), question, history)
    try:
        res = await models.call("chat", prompt, system=chat_core.SYSTEM)
    except models.RoleExhausted as e:
        raise HTTPException(503, f"No configured model could answer right now ({models.describe(e)}).") from None
    return {"answer": str(res.value).strip(), "model": f"{res.provider}/{res.model}", "cached": res.cached}
