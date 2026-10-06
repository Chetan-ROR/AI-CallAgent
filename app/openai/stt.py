"""OpenAI file STT for Twilio μ-law utterances (no Realtime)."""

from __future__ import annotations

import audioop
import io
import unicodedata
import wave

from openai import AsyncOpenAI

from app.core.config import OPENAI_API_KEY, OPENAI_STT_MODEL

_client: AsyncOpenAI | None = None

# Telephony is 8 kHz; Whisper/transcribe models hear multilingual speech better at 16 kHz.
_STT_RATE = 16_000
_STT_PROMPT = (
    "Narrowband phone call. Caller may speak English, Hindi, or Hinglish. "
    "Transcribe ONLY clearly spoken words. Do not translate. "
    "Do not invent names, sentences, or filler. "
    "If the audio is noise, silence, or unintelligible, return an empty string."
)


def is_actionable_transcript(text: str | None) -> bool:
    """True when STT returned spoken words, not silence or punctuation like '।'."""
    if not text or not str(text).strip():
        return False
    return any(unicodedata.category(ch).startswith("L") for ch in str(text))


def _client_openai() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _client


def mulaw_to_wav_bytes(
    mulaw: bytes,
    *,
    sample_rate: int = 8000,
    out_rate: int | None = None,
) -> bytes:
    pcm = audioop.ulaw2lin(mulaw, 2)
    rate = sample_rate
    target = out_rate or sample_rate
    if target != rate and pcm:
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, target, None)
        rate = target
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buf.getvalue()


async def transcribe_mulaw(
    mulaw: bytes,
    *,
    language: str | None = None,
    model: str | None = None,
) -> str:
    """
    Transcribe one phone utterance.

    language=None → auto-detect (needed when callers mix Hindi/English).
    Pass an ISO code only when you intentionally lock the recognizer.
    """
    if not mulaw or len(mulaw) < 160:
        return ""
    # μ-law 8 kHz ≈ 8 bytes/ms
    duration_ms = len(mulaw) / 8.0
    wav = mulaw_to_wav_bytes(mulaw, sample_rate=8000, out_rate=_STT_RATE)
    stt_model = (model or OPENAI_STT_MODEL or "gpt-4o-transcribe").strip()
    kwargs: dict = {
        "model": stt_model,
        "file": ("utterance.wav", wav, "audio/wav"),
        "prompt": _STT_PROMPT,
        "temperature": 0,
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
    if not is_actionable_transcript(text) or text.startswith("Transcription("):
        return ""
    # Short noisy clips often hallucinate full English sentences ("My name is …").
    words = [w for w in text.split() if w]
    if duration_ms < 900 and len(words) >= 4:
        print(
            f"🎤 STT drop likely hallucination ({duration_ms:.0f}ms → {len(words)} words):",
            text[:120],
        )
        return ""
    if duration_ms < 500 and len(words) >= 3:
        print(
            f"🎤 STT drop likely hallucination ({duration_ms:.0f}ms → {len(words)} words):",
            text[:120],
        )
        return ""
    return text
