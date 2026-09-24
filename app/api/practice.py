from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from openai import APIError, OpenAI
from pydantic import BaseModel

from app.core.config import OPENAI_API_KEY
from app.core.prompt_builder import agent_first_message, build_instructions
from app.core.realtime import REALTIME_MODEL, build_realtime_session
from app.llc.client import LlcClient

router = APIRouter()
client = OpenAI(api_key=OPENAI_API_KEY)


class PracticeSessionRequest(BaseModel):
    instructions: Optional[str] = None
    first_message: Optional[str] = None
    voice: Optional[str] = None
    client_id: Optional[str] = None
    agent_id: Optional[str] = None
    member_id: Optional[str] = None


async def _practice_llc_context(
    *,
    client_id: str,
    agent_id: str | None,
    member_id: str | None,
) -> tuple[dict, dict, dict]:
    llc = LlcClient()
    member: dict = {}
    studio: dict = {}
    agent: dict = {}
    if not llc.enabled:
        return member, studio, agent

    crm = await llc.lookup_member(client_id=client_id, member_id=member_id)
    if (crm or {}).get("success"):
        studio = crm.get("studio") or {}
        member = crm.get("member") or {}

    agent_res = await llc.lookup_agent(client_id=client_id, agent_id=agent_id)
    if (agent_res or {}).get("success"):
        agent = agent_res.get("agent") or {}

    return member, studio, agent


@router.post("/practice/session")
async def create_practice_session(body: Optional[PracticeSessionRequest] = None):
    """Mint a short-lived Realtime client secret for browser voice practice."""
    payload = body or PracticeSessionRequest()
    instructions = payload.instructions
    first_message = payload.first_message
    voice = payload.voice

    client_id = (payload.client_id or "").strip() or None
    agent_id = (payload.agent_id or "").strip() or None
    member_id = (payload.member_id or "").strip() or None

    if client_id:
        member, studio, agent = await _practice_llc_context(
            client_id=client_id,
            agent_id=agent_id,
            member_id=member_id,
        )
        if agent or studio:
            instructions = build_instructions(member, studio, agent)
            first_message = agent_first_message(agent) or first_message
            voice_settings = agent.get("voice_settings") if isinstance(agent, dict) else None
            if isinstance(voice_settings, dict) and voice_settings.get("voice"):
                voice = voice_settings.get("voice") or voice

    try:
        secret = client.realtime.client_secrets.create(
            expires_after={"anchor": "created_at", "seconds": 600},
            session=build_realtime_session(
                "practice",
                instructions=instructions,
                first_message=first_message,
                voice=voice,
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
