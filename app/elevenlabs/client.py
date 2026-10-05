"""ElevenLabs REST helpers — voices fetched live (account + shared library)."""

from __future__ import annotations

import base64
from typing import Any, AsyncIterator

import httpx

from app.core.config import ELEVENLABS_API_KEY, ELEVENLABS_TTS_MODEL

API_BASE = "https://api.elevenlabs.io/v1"
API_BASE_V2 = "https://api.elevenlabs.io/v2"
# ~20ms of μ-law @ 8 kHz — matches Twilio media frame sizing.
_ULAW_CHUNK = 160


def _headers(*, json_body: bool = True) -> dict[str, str]:
    key = (ELEVENLABS_API_KEY or "").strip()
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    headers = {"xi-api-key": key}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _character_from_gender(gender: str | None) -> str:
    value = str(gender or "").strip().lower()
    if value in ("male", "masculine"):
        return "masculine"
    if value in ("female", "feminine"):
        return "feminine"
    return "neutral"


def _character_from_labels(labels: dict | None) -> str:
    return _character_from_gender((labels or {}).get("gender"))


def map_voice_row(raw: dict[str, Any]) -> dict[str, Any]:
    labels = raw.get("labels") if isinstance(raw.get("labels"), dict) else {}
    use_case = str(labels.get("use_case") or "").strip().lower()
    name = str(raw.get("name") or raw.get("voice_id") or "Voice").strip()
    description = str(raw.get("description") or "").strip()
    accent = str(labels.get("accent") or "").strip()
    if accent and description:
        description = f"{description} ({accent})"
    elif accent and not description:
        description = accent.title()
    return {
        "id": str(raw.get("voice_id") or "").strip(),
        "label": name,
        "description": description,
        "character": _character_from_labels(labels),
        "recommended": use_case in ("conversational", "informative_educational"),
        "provider": "elevenlabs",
        "source": "account",
        "accent": accent,
        "use_case": use_case,
        "gender": str(labels.get("gender") or "").strip(),
        "preview_url": raw.get("preview_url") or None,
    }


def map_shared_voice_row(raw: dict[str, Any]) -> dict[str, Any]:
    voice_id = str(raw.get("voice_id") or "").strip()
    name = str(raw.get("name") or voice_id or "Voice").strip()
    description = str(raw.get("description") or raw.get("descriptive") or "").strip()
    accent = str(raw.get("accent") or "").strip()
    gender = str(raw.get("gender") or "").strip()
    use_case = str(raw.get("use_case") or "").strip().lower()
    category = str(raw.get("category") or "").strip()
    language = str(raw.get("language") or "").strip()
    if accent and description:
        description = f"{description} ({accent})"
    elif accent and not description:
        description = accent.title()
    return {
        "id": voice_id,
        "label": name,
        "description": description,
        "character": _character_from_gender(gender),
        "recommended": bool(raw.get("featured"))
        or use_case in ("conversational", "informative_educational"),
        "provider": "elevenlabs",
        "source": "library",
        "accent": accent,
        "use_case": use_case,
        "gender": gender,
        "category": category,
        "language": language,
        "preview_url": raw.get("preview_url") or None,
        "public_owner_id": str(raw.get("public_owner_id") or "").strip() or None,
        "is_added_by_user": bool(raw.get("is_added_by_user")),
        "cloned_by_count": raw.get("cloned_by_count"),
    }


async def list_account_voices_catalog() -> list[dict[str, Any]]:
    """All voices on this ElevenLabs account (live, paginated via /v2/voices)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    next_token: str | None = None
    async with httpx.AsyncClient(timeout=45.0) as client:
        for _ in range(50):  # hard stop
            params: dict[str, Any] = {"page_size": 100}
            if next_token:
                params["next_page_token"] = next_token
            response = await client.get(
                f"{API_BASE_V2}/voices",
                headers=_headers(json_body=False),
                params=params,
            )
            response.raise_for_status()
            payload = response.json() if isinstance(response.json(), dict) else {}
            rows = payload.get("voices") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                break
            for row in rows:
                if not isinstance(row, dict) or not row.get("voice_id"):
                    continue
                mapped = map_voice_row(row)
                if not mapped["id"] or mapped["id"] in seen:
                    continue
                seen.add(mapped["id"])
                out.append(mapped)
            if not payload.get("has_more"):
                break
            next_token = payload.get("next_page_token")
            if not next_token:
                break
    # Fallback to legacy endpoint if v2 returned nothing.
    if out:
        return out
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{API_BASE}/voices",
            headers=_headers(json_body=False),
            params={"show_legacy": "true"},
        )
        response.raise_for_status()
        payload = response.json()
    voices = payload.get("voices") if isinstance(payload, dict) else None
    if not isinstance(voices, list):
        return []
    mapped = [map_voice_row(row) for row in voices if isinstance(row, dict) and row.get("voice_id")]
    return [row for row in mapped if row["id"]]


# Back-compat alias used by older imports.
async def list_voices_catalog() -> list[dict[str, Any]]:
    return await list_account_voices_catalog()


async def list_shared_voices_catalog(
    *,
    page: int = 0,
    page_size: int = 48,
    search: str | None = None,
    gender: str | None = None,
    accent: str | None = None,
    language: str | None = None,
    sort: str = "trending",
) -> dict[str, Any]:
    """Live page from ElevenLabs public Voice Library (~19k+ voices)."""
    params: dict[str, Any] = {
        "page_size": max(1, min(int(page_size or 48), 100)),
        "page": max(0, int(page or 0)),
        "sort": (sort or "trending").strip() or "trending",
    }
    if (search or "").strip():
        params["search"] = search.strip()
    if (gender or "").strip():
        params["gender"] = gender.strip().lower()
    if (accent or "").strip():
        params["accent"] = accent.strip().lower()
    if (language or "").strip():
        params["language"] = language.strip().lower()

    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.get(
            f"{API_BASE}/shared-voices",
            headers=_headers(json_body=False),
            params=params,
        )
        response.raise_for_status()
        payload = response.json() if isinstance(response.json(), dict) else {}

    rows = payload.get("voices") if isinstance(payload, dict) else None
    voices = [
        map_shared_voice_row(row)
        for row in (rows or [])
        if isinstance(row, dict) and row.get("voice_id")
    ]
    voices = [row for row in voices if row["id"]]
    return {
        "voices": voices,
        "page": params["page"],
        "page_size": params["page_size"],
        "has_more": bool(payload.get("has_more")),
        "total_count": int(payload.get("total_count") or 0),
        "source": "library",
        "provider": "elevenlabs",
        "filters": {
            "search": (search or "").strip() or None,
            "gender": (gender or "").strip().lower() or None,
            "accent": (accent or "").strip().lower() or None,
            "language": (language or "").strip().lower() or None,
        },
    }


async def add_shared_voice_to_account(
    *,
    public_owner_id: str,
    voice_id: str,
    new_name: str | None = None,
) -> dict[str, Any]:
    """
    Add a Voice Library voice to this API key's collection.
    Returns the account-scoped voice_id to persist on the agent.
    """
    owner = (public_owner_id or "").strip()
    vid = (voice_id or "").strip()
    if not owner or not vid:
        raise ValueError("public_owner_id and voice_id are required")
    body = {
        "new_name": (new_name or "").strip() or f"Library {vid[:8]}",
    }
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(
            f"{API_BASE}/voices/add/{owner}/{vid}",
            headers=_headers(),
            json=body,
        )
        response.raise_for_status()
        payload = response.json() if isinstance(response.json(), dict) else {}
    account_id = str(payload.get("voice_id") or "").strip()
    if not account_id:
        raise RuntimeError("ElevenLabs did not return an account voice_id")
    return {"voice_id": account_id, "provider": "elevenlabs", "source": "account"}


async def tts_preview_mp3(*, voice_id: str, text: str, model_id: str | None = None) -> bytes:
    """Short MP3 sample for the agent voice picker."""
    model = (model_id or ELEVENLABS_TTS_MODEL).strip() or "eleven_flash_v2_5"
    url = f"{API_BASE}/text-to-speech/{voice_id}"
    params = {"output_format": "mp3_44100_128"}
    body = {"text": text, "model_id": model}
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, headers=_headers(), params=params, json=body)
        response.raise_for_status()
        return response.content


async def iter_tts_ulaw_chunks(
    *,
    voice_id: str,
    text: str,
    model_id: str | None = None,
    language_code: str | None = None,
    should_cancel=None,
) -> AsyncIterator[str]:
    """
    Stream ElevenLabs μ-law 8 kHz audio as base64 payloads (Twilio media format).
    """
    model = (model_id or ELEVENLABS_TTS_MODEL).strip() or "eleven_flash_v2_5"
    url = f"{API_BASE}/text-to-speech/{voice_id}/stream"
    params = {
        "output_format": "ulaw_8000",
        "optimize_streaming_latency": 3,
    }
    body: dict[str, Any] = {"text": text, "model_id": model}
    lang = (language_code or "").strip().lower()
    if lang and model.startswith("eleven_flash"):
        body["language_code"] = lang

    async with httpx.AsyncClient(timeout=90.0) as client:
        async with client.stream(
            "POST",
            url,
            headers=_headers(),
            params=params,
            json=body,
        ) as response:
            response.raise_for_status()
            buffer = b""
            async for chunk in response.aiter_bytes():
                if should_cancel and should_cancel():
                    break
                if not chunk:
                    continue
                buffer += chunk
                while len(buffer) >= _ULAW_CHUNK:
                    if should_cancel and should_cancel():
                        return
                    piece, buffer = buffer[:_ULAW_CHUNK], buffer[_ULAW_CHUNK:]
                    yield base64.b64encode(piece).decode("ascii")
            if buffer and not (should_cancel and should_cancel()):
                yield base64.b64encode(buffer).decode("ascii")
