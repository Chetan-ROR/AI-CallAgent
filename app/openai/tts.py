"""OpenAI Speech TTS → Twilio μ-law 8 kHz payloads (no Realtime)."""

from __future__ import annotations

import audioop
import base64
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from app.core.config import OPENAI_API_KEY, OPENAI_TTS_MODEL

_client: AsyncOpenAI | None = None
_ULAW_CHUNK = 160  # 20ms @ 8 kHz
_PCM_IN_RATE = 24_000
_PCM_OUT_RATE = 8_000
_PCM_WIDTH = 2


def _client_openai() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _client


async def iter_openai_tts_ulaw_chunks(
    *,
    voice: str,
    text: str,
    model: str | None = None,
    should_cancel=None,
) -> AsyncIterator[str]:
    """
    Stream OpenAI TTS as base64 μ-law 8 kHz frames for Twilio media.
    Speech API returns PCM 24 kHz; we downsample and encode μ-law.
    """
    spoken = (text or "").strip()
    if not spoken:
        return

    tts_model = (model or OPENAI_TTS_MODEL or "gpt-4o-mini-tts").strip()
    voice_id = (voice or "alloy").strip().lower() or "alloy"

    pcm_buf = b""
    rate_state = None
    ulaw_buf = b""

    async with _client_openai().audio.speech.with_streaming_response.create(
        model=tts_model,
        voice=voice_id,
        input=spoken,
        response_format="pcm",
    ) as response:
        async for chunk in response.iter_bytes():
            if should_cancel and should_cancel():
                return
            if not chunk:
                continue
            pcm_buf += chunk
            # ratecv wants whole frames (sample width * channels).
            usable = len(pcm_buf) - (len(pcm_buf) % _PCM_WIDTH)
            if usable <= 0:
                continue
            frame, pcm_buf = pcm_buf[:usable], pcm_buf[usable:]
            pcm8, rate_state = audioop.ratecv(
                frame, _PCM_WIDTH, 1, _PCM_IN_RATE, _PCM_OUT_RATE, rate_state
            )
            if not pcm8:
                continue
            ulaw_buf += audioop.lin2ulaw(pcm8, _PCM_WIDTH)
            while len(ulaw_buf) >= _ULAW_CHUNK:
                if should_cancel and should_cancel():
                    return
                piece, ulaw_buf = ulaw_buf[:_ULAW_CHUNK], ulaw_buf[_ULAW_CHUNK:]
                yield base64.b64encode(piece).decode("ascii")

    if pcm_buf:
        pad = b"\x00" * (_PCM_WIDTH - (len(pcm_buf) % _PCM_WIDTH)) if len(pcm_buf) % _PCM_WIDTH else b""
        pcm8, rate_state = audioop.ratecv(
            pcm_buf + pad, _PCM_WIDTH, 1, _PCM_IN_RATE, _PCM_OUT_RATE, rate_state
        )
        if pcm8:
            ulaw_buf += audioop.lin2ulaw(pcm8, _PCM_WIDTH)

    if ulaw_buf and not (should_cancel and should_cancel()):
        yield base64.b64encode(ulaw_buf).decode("ascii")


async def openai_tts_preview_mp3(*, voice: str, text: str, model: str | None = None) -> bytes:
    """Short MP3 sample for the OpenAI voice picker."""
    spoken = (text or "").strip()
    if not spoken:
        spoken = "Hello, thanks for calling. This is how I'll sound when I speak with your customers."
    tts_model = (model or OPENAI_TTS_MODEL or "gpt-4o-mini-tts").strip()
    voice_id = (voice or "alloy").strip().lower() or "alloy"
    response = await _client_openai().audio.speech.create(
        model=tts_model,
        voice=voice_id,
        input=spoken,
        response_format="mp3",
    )
    return response.content
