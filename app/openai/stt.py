"""OpenAI file STT for Twilio μ-law utterances (no Realtime)."""

from __future__ import annotations

import audioop
import io
import wave

from openai import AsyncOpenAI

from app.core.config import OPENAI_API_KEY, OPENAI_STT_MODEL

_client: AsyncOpenAI | None = None


def _client_openai() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _client


def mulaw_to_wav_bytes(mulaw: bytes, *, sample_rate: int = 8000) -> bytes:
    pcm = audioop.ulaw2lin(mulaw, 2)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


async def transcribe_mulaw(
    mulaw: bytes,
    *,
    language: str | None = "en",
    model: str | None = None,
) -> str:
    if not mulaw or len(mulaw) < 160:
        return ""
    wav = mulaw_to_wav_bytes(mulaw)
    stt_model = (model or OPENAI_STT_MODEL or "gpt-4o-mini-transcribe").strip()
    kwargs: dict = {
        "model": stt_model,
        "file": ("utterance.wav", wav, "audio/wav"),
    }
    lang = (language or "").strip().lower()
    if lang:
        kwargs["language"] = lang
    result = await _client_openai().audio.transcriptions.create(**kwargs)
    text = getattr(result, "text", None)
    if text is None and isinstance(result, str):
        text = result
    if not isinstance(text, str):
        text = ""
    text = text.strip()
    # Empty Whisper/transcribe replies used to fall through to str(result),
    # which logged as "Transcription(text='', ...)" and polluted the chat.
    if not text or text.startswith("Transcription("):
        return ""
    return text
