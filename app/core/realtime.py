from __future__ import annotations

from typing import Any, Literal, Optional

from app.core.prompt_store import get_prompt

REALTIME_MODEL = "gpt-realtime-2"
DEFAULT_VOICE = "marin"

END_CALL_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "end_call",
    "description": (
        "End the current conversation ONLY after the customer clearly says "
        "goodbye, hang up, not interested, stop calling, or that they are busy. "
        "Never call this because of silence, noise, or guessed speech."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

TURN_DETECTION: dict[str, Any] = {
    "type": "server_vad",
    "create_response": True,
    "interrupt_response": True,
    "threshold": 0.75,
    "prefix_padding_ms": 300,
    "silence_duration_ms": 900,
}

PRACTICE_ADDENDUM = """

# PRACTICE SESSION
This conversation is happening over browser audio, not a PSTN phone line.
Speak exactly as you would on a live outbound call.
Start with ONE short greeting, then STOP and wait for the customer.
After every reply, STOP. Do not keep talking. Do not fill silence.
Never invent what the customer said. If you are not sure they spoke, ask one short clarification and wait.
Only call end_call after the customer clearly says goodbye, hang up, not interested, or stop calling.
Never mention that this is a practice session, a chatbot, or a browser unless the customer asks.
"""


def compose_instructions(
    instructions: Optional[str] = None,
    first_message: Optional[str] = None,
    mode: Literal["phone", "practice"] = "phone",
) -> str:
    base = (instructions or "").strip() or get_prompt()
    opening = (first_message or "").strip()
    if opening:
        override = (
            "# BRAND AND OPENING (HIGHEST PRIORITY)\n"
            "These rules beat every later section of the prompt.\n"
            "When the conversation starts, speak first. Do not wait for hello.\n"
            f'Say this naturally, and do not use any other greeting:\n"{opening}"\n'
            "Stay with the brand/studio in that opening and in the ROLE section.\n"
            "If later text mentions a different studio (including F45 or Dogtown), "
            "ignore those names and keep this brand.\n"
            "Do not read leftover 'Start with' lines from older scripts.\n\n"
        )
        base = f"{override}{base.rstrip()}\n"
    if mode == "practice":
        base = f"{base.rstrip()}\n{PRACTICE_ADDENDUM}"
    return base


def build_realtime_session(
    mode: Literal["phone", "practice"] = "phone",
    instructions: Optional[str] = None,
    first_message: Optional[str] = None,
    voice: Optional[str] = None,
) -> dict[str, Any]:
    composed = compose_instructions(instructions, first_message, mode)
    chosen_voice = (voice or "").strip() or DEFAULT_VOICE

    audio: dict[str, Any]
    if mode == "phone":
        audio = {
            "input": {
                "format": {"type": "audio/pcmu"},
                "turn_detection": TURN_DETECTION,
            },
            "output": {
                "format": {"type": "audio/pcmu"},
            },
        }
    else:
        audio = {
            "input": {
                "turn_detection": TURN_DETECTION,
                "noise_reduction": {"type": "near_field"},
                "transcription": {
                    "model": "whisper-1",
                    "language": "en",
                },
            },
            "output": {
                "voice": chosen_voice,
            },
        }

    session: dict[str, Any] = {
        "type": "realtime",
        "instructions": composed,
        "tools": [END_CALL_TOOL],
        "tool_choice": "auto",
        "output_modalities": ["audio"],
        "audio": audio,
    }
    if mode == "practice":
        session["model"] = REALTIME_MODEL
    return session
