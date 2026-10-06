"""Energy VAD on Twilio μ-law 8 kHz frames (~20ms / 160 bytes)."""

from __future__ import annotations

import audioop
from dataclasses import dataclass, field


@dataclass
class UtteranceVad:
    """Detect end-of-utterance from silence after speech."""

    frame_ms: int = 20
    start_threshold: float = 450.0
    continue_threshold: float = 280.0
    # Slightly longer end-silence cuts fewer mid-phrase / noisy fragments
    # (short clips are what make gpt-4o-transcribe invent names/sentences).
    silence_ms: int = 600
    min_speech_ms: int = 300
    max_speech_ms: int = 12_000

    speaking: bool = False
    _speech_ms: int = 0
    _silence_ms: int = 0
    _buf: bytearray = field(default_factory=bytearray)

    def reset(self) -> None:
        self.speaking = False
        self._speech_ms = 0
        self._silence_ms = 0
        self._buf = bytearray()

    def push_mulaw_b64(self, payload_b64: str) -> bytes | None:
        """
        Append one Twilio media payload (base64 μ-law).
        Returns complete utterance μ-law bytes when a turn ends, else None.
        """
        import base64

        if not payload_b64:
            return None
        try:
            frame = base64.b64decode(payload_b64)
        except Exception:
            return None
        if not frame:
            return None

        pcm = audioop.ulaw2lin(frame, 2)
        rms = float(audioop.rms(pcm, 2))

        if not self.speaking:
            if rms >= self.start_threshold:
                self.speaking = True
                self._speech_ms = self.frame_ms
                self._silence_ms = 0
                self._buf = bytearray(frame)
            return None

        self._buf.extend(frame)
        self._speech_ms += self.frame_ms

        if rms >= self.continue_threshold:
            self._silence_ms = 0
        else:
            self._silence_ms += self.frame_ms

        timed_out = self._speech_ms >= self.max_speech_ms
        ended = (
            self._silence_ms >= self.silence_ms and self._speech_ms >= self.min_speech_ms
        )
        if ended or timed_out:
            utterance = bytes(self._buf)
            self.reset()
            return utterance
        return None
