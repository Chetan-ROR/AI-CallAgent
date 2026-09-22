from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.prompt_store import get_prompt, set_prompt

router = APIRouter()


class PromptUpdate(BaseModel):
    instructions: str = Field(..., min_length=1)


@router.get("/prompt")
def read_prompt():
    return {"instructions": get_prompt()}


@router.put("/prompt")
def update_prompt(body: PromptUpdate):
    try:
        saved = set_prompt(body.instructions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"instructions": saved, "status": "saved"}
