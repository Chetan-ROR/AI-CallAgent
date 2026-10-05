"""Mix Twilio media-stream audio in memory, then hand the WAV to LLC (Rails).

Inbound caller audio and outbound agent audio are both mulaw 8 kHz from the
media stream. They are mixed into one stereo WAV (caller left, agent right).
Nothing is written to disk in gym-ai-poc — Rails stores the file.
"""

from __future__ import annotations

import array
import base64
import io
import threading
import time
import wave

_SAMPLE_RATE = 8000
_SILENCE = 0xFF


def _mulaw_table() -> tuple[int, ...]:
    table = []
    for raw in range(256):
        value = ~raw & 0xFF
        magnitude = ((value & 0x0F) << 3) + 0x84
        magnitude <<= (value & 0x70) >> 4
        sample = (0x84 - magnitude) if (value & 0x80) else (magnitude - 0x84)
        table.append(max(-32768, min(32767, sample)))
    return tuple(table)


_MULAW = _mulaw_table()


def _place(buf: bytearray, origin: int, index: int, data: bytes) -> tuple[bytearray, int]:
    if not data:
        return buf, origin
    if not buf:
        return bytearray(data), index
    start = index
    end = index + len(data)
    buf_end = origin + len(buf)
    new_start = min(origin, start)
    new_end = max(buf_end, end)
    if new_start != origin or new_end != buf_end:
        grown = bytearray(b"\xff" * (new_end - new_start))
        grown[origin - new_start : buf_end - new_start] = buf
        buf = grown
        origin = new_start
    offset = start - origin
    buf[offset : offset + len(data)] = data
    return buf, origin


def _truncate(buf: bytearray, origin: int, sample_index: int) -> tuple[bytearray, int]:
    if not buf:
        return buf, origin
    keep = sample_index - origin
    if keep <= 0:
        return bytearray(), sample_index
    if keep < len(buf):
        return buf[:keep], origin
    return buf, origin


def _pcm_track(buf: bytearray, origin: int, length: int) -> array.array:
    samples = array.array("h", bytes(length * 2))
    for index, raw in enumerate(buf):
        position = origin + index
        if 0 <= position < length:
            samples[position] = _MULAW[raw]
    return samples


def _wav_bytes(inbound: bytearray, in_origin: int, outbound: bytearray, out_origin: int) -> bytes:
    in_end = in_origin + len(inbound)
    out_end = out_origin + len(outbound)
    length = max(in_end, out_end, 0)
    if length <= 0:
        return b""
    left = _pcm_track(inbound, in_origin, length)
    right = _pcm_track(outbound, out_origin, length)
    mixed = array.array("h")
    for index in range(length):
        mixed.append(left[index])
        mixed.append(right[index])
    handle = io.BytesIO()
    with wave.open(handle, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(_SAMPLE_RATE)
        wav.writeframes(mixed.tobytes())
    return handle.getvalue()


class CallRecorder:
    def __init__(self, *, call_sid: str | None, client_id: str | None):
        self.call_sid = (call_sid or "").strip() or uuid.uuid4().hex
        self.client_id = (client_id or "").strip()
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self._inbound = bytearray()
        self._in_origin = 0
        self._outbound = bytearray()
        self._out_origin = 0
        self._out_playhead = 0
        self._closed = False

    def _now_index(self) -> int:
        return int((time.monotonic() - self._t0) * _SAMPLE_RATE)

    def add_inbound(self, media: dict) -> None:
        payload = (media or {}).get("payload")
        if not payload:
            return
        try:
            raw = base64.b64decode(payload)
        except Exception:
            return
        timestamp = (media or {}).get("timestamp")
        if timestamp not in (None, ""):
            try:
                index = int(float(timestamp) * _SAMPLE_RATE / 1000)
            except (TypeError, ValueError):
                index = self._now_index()
        else:
            index = self._now_index()
        with self._lock:
            if self._closed:
                return
            self._inbound, self._in_origin = _place(self._inbound, self._in_origin, index, raw)

    def add_outbound(self, payload: str) -> None:
        if not payload:
            return
        try:
            raw = base64.b64decode(payload)
        except Exception:
            return
        with self._lock:
            if self._closed:
                return
            now = self._now_index()
            start = max(self._out_playhead, now)
            self._outbound, self._out_origin = _place(self._outbound, self._out_origin, start, raw)
            self._out_playhead = start + len(raw)

    def clear_outbound(self) -> None:
        """Drop agent audio that was queued but not yet played."""
        with self._lock:
            if self._closed:
                return
            now = self._now_index()
            self._outbound, self._out_origin = _truncate(self._outbound, self._out_origin, now)
            self._out_playhead = now

    def finish(self) -> dict | None:
        with self._lock:
            if self._closed:
                return None
            self._closed = True
            inbound = bytearray(self._inbound)
            in_origin = self._in_origin
            outbound = bytearray(self._outbound)
            out_origin = self._out_origin
        wav = _wav_bytes(inbound, in_origin, outbound, out_origin)
        if not wav:
            print("🎙️ No media-stream audio to save", self.call_sid)
            return None
        filename = f"{self.call_sid}.wav"
        print("🎙️ Recording ready to upload to LLC", filename, f"({len(wav)} bytes)")
        return {
            "storage": "local",
            "recording_bytes": wav,
            "filename": filename,
        }

    def duration_seconds(self) -> int:
        return max(0, int(time.monotonic() - self._t0))
