from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from openai import APIError, OpenAI
from pydantic import BaseModel, Field

from app.core.config import OPENAI_API_KEY
from app.core.prompt_builder import agent_spoken_opening, build_instructions
from app.core.crm_tools import AGENT_TOOL_CATALOG, agent_needs_crm_fetch
from app.core.openai_realtime_models import get_realtime_models_catalog
from app.core.realtime import (
    REALTIME_VOICE_CATALOG,
    VALID_VOICES,
    VOICE_PREVIEW_INSTRUCTIONS,
    build_realtime_session,
    resolve_agent_model,
)
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


VOICE_PREVIEW_TEXT = (
    "Hello, thanks for calling. This is how I'll sound when I speak with your customers."
)


class VoicePreviewRequest(BaseModel):
    voice: str = Field(..., min_length=1)
    text: Optional[str] = None


@router.get("/practice/voices")
def list_realtime_voices():
    """OpenAI Realtime built-in voices for agent configuration."""
    return {"voices": list(REALTIME_VOICE_CATALOG)}


@router.get("/practice/agent-tools")
def list_agent_tools():
    """Agent tool toggles (OpenAI functions + CRM prompt data sources)."""
    return {"tools": list(AGENT_TOOL_CATALOG)}


@router.get("/practice/models")
def list_realtime_models():
    """Realtime speech models your OpenAI API key can use (live from GET /v1/models)."""
    models, source = get_realtime_models_catalog(force_refresh=True)
    return {"models": models, "source": source}


def _speech_preview_bytes(*, voice: str, text: str) -> bytes:
    """Preview via Speech API (approximates Realtime timbre; not identical to live calls)."""
    instructions = VOICE_PREVIEW_INSTRUCTIONS.get(voice)
    models = ("gpt-4o-mini-tts", "gpt-4o-mini-tts-2025-03-20")
    last_error: Exception | None = None
    for model in models:
        try:
            kwargs: dict = {
                "model": model,
                "voice": voice,
                "input": text,
                "response_format": "mp3",
            }
            if instructions:
                kwargs["instructions"] = instructions
            result = client.audio.speech.create(**kwargs)
            return result.content
        except APIError as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    raise RuntimeError("Could not generate voice preview")


@router.post("/practice/voice-preview")
def create_voice_preview(body: VoicePreviewRequest):
    """Short MP3 sample for the Realtime voice picker (uses OpenAI Speech API)."""
    voice = body.voice.strip().lower()
    if voice not in VALID_VOICES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported voice. Choose one of: {', '.join(sorted(VALID_VOICES))}",
        )
    text = (body.text or "").strip() or VOICE_PREVIEW_TEXT
    try:
        audio = _speech_preview_bytes(voice=voice, text=text)
    except APIError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502,
            detail=str(exc) or "Could not generate voice preview",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not generate voice preview: {exc}",
        ) from exc
    return Response(content=audio, media_type="audio/mpeg")


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

    agent_res = await llc.lookup_agent(client_id=client_id, agent_id=agent_id)
    if (agent_res or {}).get("success"):
        agent = agent_res.get("agent") or {}

    if agent_needs_crm_fetch(agent):
        crm = await llc.lookup_member(client_id=client_id, member_id=member_id)
        if (crm or {}).get("success"):
            studio = crm.get("studio") or {}
            member = crm.get("member") or {}
    else:
        print("👤 Practice CRM skipped — no CRM tools enabled on agent")

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

    member: dict = {}
    studio: dict = {}
    agent: dict = {}
    if client_id:
        member, studio, agent = await _practice_llc_context(
            client_id=client_id,
            agent_id=agent_id,
            member_id=member_id,
        )
        if agent or studio:
            instructions = build_instructions(member, studio, agent)
        if agent:
            first_message = agent_spoken_opening(agent, member, studio) or first_message

    try:
        secret = client.realtime.client_secrets.create(
            expires_after={"anchor": "created_at", "seconds": 600},
            session=build_realtime_session(
                "practice",
                instructions=instructions,
                first_message=first_message,
                voice=voice,
                agent=agent or None,
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
        "model": resolve_agent_model(agent or None),
    }
