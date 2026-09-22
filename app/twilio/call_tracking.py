"""Track whether Twilio media stream actually connected for an outbound call."""

from __future__ import annotations

_answered: set[str] = set()
_streamed: set[str] = set()


def note_call_status(call_sid: str | None, status: str | None) -> None:
    if not call_sid or not status:
        return
    if status == "in-progress":
        _answered.add(call_sid)
        return
    if status in {"completed", "busy", "failed", "no-answer", "canceled"}:
        if call_sid in _answered and call_sid not in _streamed:
            print(
                "❌ CALL ENDED WITHOUT MEDIA STREAM:",
                call_sid,
                f"status={status}",
                "— Twilio never opened /media-stream (tunnel/WSS or TwiML fetch failed).",
            )
        _answered.discard(call_sid)
        _streamed.discard(call_sid)


def mark_media_stream_started(call_sid: str | None) -> None:
    if not call_sid:
        return
    _streamed.add(call_sid)
    print("✅ Media stream connected for CallSid", call_sid)
