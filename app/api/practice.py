from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from openai import APIError, OpenAI
from pydantic import BaseModel

from app.core.config import OPENAI_API_KEY
from app.core.realtime import REALTIME_MODEL, build_realtime_session

router = APIRouter()
client = OpenAI(api_key=OPENAI_API_KEY)


class PracticeSessionRequest(BaseModel):
    instructions: Optional[str] = None
    first_message: Optional[str] = None
    voice: Optional[str] = None


@router.post("/practice/session")
def create_practice_session(body: Optional[PracticeSessionRequest] = None):
    """Mint a short-lived Realtime client secret for browser voice practice."""
    payload = body or PracticeSessionRequest()
    try:
        secret = client.realtime.client_secrets.create(
            expires_after={"anchor": "created_at", "seconds": 600},
            session=build_realtime_session(
                "practice",
                instructions=payload.instructions,
                first_message=payload.first_message,
                voice=payload.voice,
            ),
        )
    except APIError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502,
            detail=str(exc) or "Could not start practice session",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not start practice session: {exc}",
        ) from exc

    return {
        "value": secret.value,
        "expires_at": secret.expires_at,
        "model": REALTIME_MODEL,
    }
