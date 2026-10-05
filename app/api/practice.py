from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from httpx import HTTPStatusError
from openai import APIError, OpenAI
from pydantic import BaseModel, Field

from app.core.config import ELEVENLABS_API_KEY, OPENAI_API_KEY
from app.core.prompt_builder import agent_spoken_opening, build_instructions
from app.core.crm_tools import AGENT_TOOL_CATALOG, agent_needs_crm_fetch
from app.core.realtime import (
    AGENT_LANGUAGES,
    DEFAULT_VOICE,
    REALTIME_VOICE_CATALOG,
    VALID_VOICES,
    build_realtime_session,
    resolve_agent_model,
    resolve_agent_voice,
    resolve_chat_model,
    uses_elevenlabs_voice,
)
from app.core.openai_chat_models import agent_uses_v2_live
from app.elevenlabs.client import (
    add_shared_voice_to_account,
    list_account_voices_catalog,
    list_shared_voices_catalog,
    list_voices_catalog,
    tts_preview_mp3,
)
from app.openai.tts import openai_tts_preview_mp3
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
    provider: Optional[str] = None
    text: Optional[str] = None


class ClaimSharedVoiceRequest(BaseModel):
    voice_id: str = Field(..., min_length=1)
    public_owner_id: str = Field(..., min_length=1)
    new_name: Optional[str] = None


def _openai_voices_catalog() -> list[dict]:
    out: list[dict] = []
    for row in REALTIME_VOICE_CATALOG:
        item = dict(row)
        item["provider"] = "openai"
        item["source"] = "static"
        out.append(item)
    return out


@router.get("/practice/languages")
def list_agent_languages():
    """Languages offered in the agent Voice Settings picker."""
    languages = [
        {"value": code, "label": label}
        for code, label in AGENT_LANGUAGES.items()
    ]
    return {"languages": languages, "source": "static"}


@router.get("/practice/voices")
async def list_agent_voices(
    provider: Optional[str] = None,
    source: Optional[str] = None,
    page: int = 0,
    page_size: int = 48,
    search: Optional[str] = None,
    gender: Optional[str] = None,
    accent: Optional[str] = None,
    language: Optional[str] = None,
):
    """
    Voice picker catalog (dynamic for ElevenLabs).

    provider=openai | elevenlabs | all
    source=account | library  (elevenlabs only; default account for elevenlabs/all)
    """
    wanted = (provider or "all").strip().lower()
    src = (source or "account").strip().lower()
    if wanted not in ("openai", "elevenlabs", "all"):
        raise HTTPException(status_code=400, detail="provider must be openai, elevenlabs, or all")
    if src not in ("account", "library"):
        raise HTTPException(status_code=400, detail="source must be account or library")

    openai_voices = _openai_voices_catalog() if wanted in ("openai", "all") else []

    if wanted == "openai":
        return {"voices": openai_voices, "provider": "openai", "source": "static"}

    if not ELEVENLABS_API_KEY:
        if wanted == "elevenlabs":
            raise HTTPException(
                status_code=503,
                detail="ELEVENLABS_API_KEY is not set in gym-ai-poc/.env",
            )
        return {
            "voices": openai_voices,
            "providers": {"openai": openai_voices, "elevenlabs": []},
            "provider": "all",
            "source": "mixed",
        }

    try:
        if src == "library" and wanted == "elevenlabs":
            payload = await list_shared_voices_catalog(
                page=page,
                page_size=page_size,
                search=search,
                gender=gender,
                accent=accent,
                language=language,
            )
            return payload

        eleven_voices = await list_account_voices_catalog()
    except HTTPStatusError as exc:
        detail = exc.response.text[:400] if exc.response is not None else str(exc)
        raise HTTPException(
            status_code=exc.response.status_code if exc.response is not None else 502,
            detail=detail or "ElevenLabs voices request failed",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not load ElevenLabs voices: {exc}",
        ) from exc

    if wanted == "elevenlabs":
        return {
            "voices": eleven_voices,
            "provider": "elevenlabs",
            "source": "account",
            "total_count": len(eleven_voices),
        }
    return {
        "voices": openai_voices + eleven_voices,
        "providers": {
            "openai": openai_voices,
            "elevenlabs": eleven_voices,
        },
        "provider": "all",
        "source": "mixed",
        "elevenlabs_total_count": len(eleven_voices),
    }


@router.post("/practice/voices/claim")
async def claim_shared_voice(body: ClaimSharedVoiceRequest):
    """Add an ElevenLabs Voice Library voice to this account; returns usable voice_id."""
    if not ELEVENLABS_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="ELEVENLABS_API_KEY is not set in gym-ai-poc/.env",
        )
    try:
        result = await add_shared_voice_to_account(
            public_owner_id=body.public_owner_id,
            voice_id=body.voice_id,
            new_name=body.new_name,
        )
    except HTTPStatusError as exc:
        detail = exc.response.text[:400] if exc.response is not None else str(exc)
        raise HTTPException(
            status_code=exc.response.status_code if exc.response is not None else 502,
            detail=detail or "Could not add ElevenLabs library voice",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not add ElevenLabs library voice: {exc}",
        ) from exc
    return result


@router.get("/practice/agent-tools")
def list_agent_tools():
    """Agent tool toggles (OpenAI functions + CRM prompt data sources)."""
    return {"tools": list(AGENT_TOOL_CATALOG)}


@router.get("/practice/chat-models")
def list_chat_models():
    """Curated OpenAI models for V2 Live + V1 pipeline (Aidan-style)."""
    from app.core.openai_chat_models import get_chat_models_catalog

    models, source = get_chat_models_catalog(force_refresh=True)
    return {"models": models, "source": source, "mode": "mixed"}


@router.post("/practice/voice-preview")
async def create_voice_preview(body: VoicePreviewRequest):
    """Short MP3 sample for OpenAI or ElevenLabs voice picker."""
    voice = body.voice.strip()
    text = (body.text or "").strip() or VOICE_PREVIEW_TEXT
    provider = (body.provider or "").strip().lower()
    if not provider:
        provider = "openai" if voice.lower() in VALID_VOICES else "elevenlabs"

    if provider in ("openai", "realtime"):
        if voice.lower() not in VALID_VOICES:
            raise HTTPException(status_code=400, detail="Unknown OpenAI voice")
        try:
            audio = await openai_tts_preview_mp3(voice=voice.lower(), text=text)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Could not generate OpenAI voice preview: {exc}",
            ) from exc
        return Response(content=audio, media_type="audio/mpeg")

    if provider != "elevenlabs":
        raise HTTPException(status_code=400, detail="provider must be openai or elevenlabs")
    if not ELEVENLABS_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="ELEVENLABS_API_KEY is not set in gym-ai-poc/.env",
        )
    if len(voice) < 8:
        raise HTTPException(status_code=400, detail="Invalid ElevenLabs voice id")
    try:
        audio = await tts_preview_mp3(voice_id=voice, text=text)
    except HTTPStatusError as exc:
        detail = exc.response.text[:400] if exc.response is not None else str(exc)
        raise HTTPException(
            status_code=exc.response.status_code if exc.response is not None else 502,
            detail=detail or "Could not generate voice preview",
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
    """
    Browser practice session.

    V2 Live agents → OpenAI Realtime (same as phone V2).
    V1 / ElevenLabs agents → OpenAI Realtime practice with agent prompt/language/tools;
    audio voice is OpenAI (browser cannot stream ElevenLabs μ-law like Twilio).
    Prompt, first_message, model, language, tools always come from the LLC agent when loaded.
    """
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

    # Practice browser audio is always OpenAI Realtime. Prefer override → agent OpenAI voice → default.
    # ElevenLabs voice ids are not valid Realtime voices; fall back to DEFAULT_VOICE.
    practice_voice = resolve_agent_voice(
        agent or None,
        override=voice,
        fallback=DEFAULT_VOICE,
    )
    if uses_elevenlabs_voice(agent) and not (voice or "").strip():
        practice_voice = DEFAULT_VOICE
        print(
            "🎧 Practice uses OpenAI voice",
            practice_voice,
            "(agent ElevenLabs voice applies on phone calls)",
        )

    v2 = agent_uses_v2_live(agent or None)
    model = resolve_agent_model(agent or None) if v2 else resolve_chat_model(agent or None)

    try:
        secret = client.realtime.client_secrets.create(
            expires_after={"anchor": "created_at", "seconds": 600},
            session=build_realtime_session(
                "practice",
                instructions=instructions,
                first_message=first_message,
                voice=practice_voice,
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
        "model": resolve_agent_model(agent or None) if v2 else model,
        "mode": "v2_live" if v2 else "v1_practice_realtime",
        "voice": practice_voice,
        "language": (agent.get("voice_settings") or {}).get("language") if agent else None,
        "agent_id": (agent.get("id") if agent else None) or agent_id,
    }
