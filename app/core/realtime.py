from __future__ import annotations

from typing import Any, Literal, Optional

from app.core.openai_realtime_models import (
    DEFAULT_REALTIME_MODEL,
    resolve_realtime_model_id,
)
from app.core.prompt_store import get_prompt
from app.core.compliance import allows_call_transcript
from app.core.crm_tools import agent_tool_enabled
from app.tools.definitions import OPENAI_TOOLS

AGENT_LANGUAGES: dict[str, str] = {"en": "English", "hi": "Hindi"}

# All built-in voices for gpt-realtime (OpenAI Realtime API).
# OpenAI does not publish official gender labels; "character" is informal guidance only.
REALTIME_VOICE_CATALOG: tuple[dict[str, str | bool], ...] = (
    {
        "id": "echo",
        "label": "Echo",
        "description": "Best choice for a male-sounding phone agent",
        "character": "masculine",
        "recommended": True,
    },
    {
        "id": "cedar",
        "label": "Cedar",
        "description": "Warm, clear (Realtime; often male-leaning)",
        "character": "masculine",
        "recommended": True,
    },
    {
        "id": "ash",
        "label": "Ash",
        "description": "Clear and steady",
        "character": "neutral",
    },
    {
        "id": "sage",
        "label": "Sage",
        "description": "Calm and thoughtful",
        "character": "neutral",
    },
    {
        "id": "verse",
        "label": "Verse",
        "description": "Dynamic and engaging",
        "character": "neutral",
    },
    {
        "id": "alloy",
        "label": "Alloy",
        "description": "Neutral and balanced",
        "character": "neutral",
    },
    {
        "id": "marin",
        "label": "Marin",
        "description": "Natural, expressive (OpenAI default quality pick)",
        "character": "feminine",
    },
    {
        "id": "ballad",
        "label": "Ballad",
        "description": "Melodic and calm",
        "character": "feminine",
    },
    {
        "id": "coral",
        "label": "Coral",
        "description": "Bright and friendly",
        "character": "feminine",
    },
    {
        "id": "shimmer",
        "label": "Shimmer",
        "description": "Light and upbeat",
        "character": "feminine",
    },
)

# Hints for Speech API previews (Realtime calls use the raw voice preset only).
VOICE_PREVIEW_INSTRUCTIONS: dict[str, str] = {
    "echo": (
        "Adult male voice. Natural, conversational, calm and clear. Medium pace, not theatrical."
    ),
    "cedar": (
        "Adult male voice. Warm, professional receptionist tone. Steady pace, friendly but not bubbly."
    ),
    "ash": "Neutral voice with a slightly lower register. Clear and professional.",
    "sage": "Neutral, calm, measured pace. Gender-neutral professional tone.",
    "verse": "Neutral, energetic but not high-pitched. Clear diction.",
    "alloy": "Neutral professional tone. Balanced pitch, not overly bright.",
    "marin": "Natural feminine voice. Warm and expressive, conversational.",
    "ballad": "Feminine voice. Smooth and melodic, calm pace.",
    "coral": "Feminine voice. Friendly and bright, natural pace.",
    "shimmer": "Feminine voice. Light and upbeat, clear articulation.",
}

VALID_VOICES = frozenset(str(v["id"]) for v in REALTIME_VOICE_CATALOG)

REALTIME_MODEL = DEFAULT_REALTIME_MODEL
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

# Browser mic picks up speaker echo; stricter VAD reduces phantom "user" transcripts.
PRACTICE_TURN_DETECTION: dict[str, Any] = {
    **TURN_DETECTION,
    "threshold": 0.88,
    "prefix_padding_ms": 400,
    "silence_duration_ms": 1200,
}

PRACTICE_ADDENDUM = """

# PRACTICE SESSION
This conversation is happening over browser audio, not a PSTN phone line.
Speak exactly as you would on a live outbound call.
Start with ONE short greeting, then STOP and wait for the customer.
After every reply, STOP. Do not keep talking. Do not fill silence.
Never invent what the customer said. If you are not sure they spoke, ask one short clarification and wait.
Do not assume the customer spoke from background noise or your own voice echoing on their line.
Only call end_call after the customer clearly says goodbye, hang up, not interested, or stop calling.
Never mention that this is a practice session, a chatbot, or a browser unless the customer asks.
"""


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, "", 0, "0", "false", "False"):
        return False
    return True


def resolve_agent_language(agent: dict | None, *, default: str = "en") -> str:
    voice_settings = (agent or {}).get("voice_settings") or {}
    if isinstance(voice_settings, dict):
        code = str(voice_settings.get("language") or "").strip().lower()
        if code in AGENT_LANGUAGES:
            return code
    fallback = (default or "en").strip().lower()
    return fallback if fallback in AGENT_LANGUAGES else "en"


def agent_language_instructions(agent: dict | None) -> str:
    code = resolve_agent_language(agent)
    name = AGENT_LANGUAGES.get(code, "English")
    other_labels = [label for lang, label in AGENT_LANGUAGES.items() if lang != code]
    others = ", ".join(other_labels) if other_labels else "other languages"
    return f"""
# AGENT LANGUAGE (MANDATORY)

This agent is configured for {name} ({code}) only.
Speak and reply ONLY in {name} on every turn (greeting, answers, goodbye).
If the customer uses {others}, asks to switch language, or mixes languages, stay in {name}.
Do not mirror their language choice. Brief acknowledgment in {name} is OK; content must remain {name} only.
Do not use full sentences in a language other than {name}.
"""


def resolve_agent_voice(
    agent: dict | None,
    *,
    override: str | None = None,
    fallback: str = "alloy",
) -> str:
    fb = (fallback or "alloy").strip().lower()
    if fb not in VALID_VOICES:
        fb = "alloy"
    voice_settings = (agent or {}).get("voice_settings") or {}
    if isinstance(voice_settings, dict):
        from_agent = str(voice_settings.get("voice") or "").strip().lower()
        if from_agent in VALID_VOICES:
            return from_agent
    over = (override or "").strip().lower()
    if over in VALID_VOICES:
        return over
    return fb


def resolve_agent_model(
    agent: dict | None,
    *,
    override: str | None = None,
    fallback: str | None = None,
) -> str:
    fb = (fallback or REALTIME_MODEL).strip()
    voice_settings = (agent or {}).get("voice_settings") or {}
    from_agent = ""
    if isinstance(voice_settings, dict):
        from_agent = str(voice_settings.get("model") or "").strip()
    over = (override or "").strip()
    if over:
        return resolve_realtime_model_id(over, fallback=fb)
    if from_agent:
        return resolve_realtime_model_id(from_agent, fallback=fb)
    return resolve_realtime_model_id(fb, fallback=REALTIME_MODEL)


def agent_allow_interrupt(agent: dict | None, *, default: bool = True) -> bool:
    voice_settings = (agent or {}).get("voice_settings") or {}
    if isinstance(voice_settings, dict) and voice_settings.get("allow_interrupt") is False:
        return False
    return default


def resolve_agent_tools(agent: dict | None, *, tools_enabled: bool = True) -> list[dict[str, Any]]:
    if not tools_enabled:
        return []
    selected = [
        tool
        for tool in OPENAI_TOOLS
        if agent_tool_enabled(agent, tool.get("name") or "", default=True)
    ]
    return selected


def turn_detection_for_agent(
    agent: dict | None,
    *,
    create_response: bool = True,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    allow_interrupt = agent_allow_interrupt(agent)
    detection = dict(base or {})
    detection.setdefault("type", "server_vad")
    detection["create_response"] = create_response
    detection["interrupt_response"] = allow_interrupt
    detection["threshold"] = 0.65 if allow_interrupt else 0.9
    detection.setdefault("prefix_padding_ms", 300)
    detection.setdefault("silence_duration_ms", 900)
    return detection


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
    agent: Optional[dict] = None,
) -> dict[str, Any]:
    composed = compose_instructions(instructions, first_message, mode)
    if agent and "AGENT LANGUAGE (MANDATORY)" not in composed:
        composed = f"{composed.rstrip()}\n\n{agent_language_instructions(agent).strip()}\n"
    chosen_voice = resolve_agent_voice(
        agent,
        override=voice,
        fallback=DEFAULT_VOICE,
    )
    tools = resolve_agent_tools(agent)
    chosen_model = resolve_agent_model(agent)
    vad_base = PRACTICE_TURN_DETECTION if mode == "practice" else TURN_DETECTION
    turn_detection = turn_detection_for_agent(agent, base=vad_base)

    audio: dict[str, Any]
    if mode == "phone":
        audio = {
            "input": {
                "format": {"type": "audio/pcmu"},
                "turn_detection": turn_detection,
            },
            "output": {
                "format": {"type": "audio/pcmu"},
                "voice": chosen_voice,
            },
        }
    else:
        audio = {
            "input": {
                "turn_detection": turn_detection,
                "noise_reduction": {"type": "near_field"},
                **(
                    {"transcription": {"model": "gpt-4o-transcribe"}}
                    if allows_call_transcript(agent)
                    else {}
                ),
            },
            "output": {
                "voice": chosen_voice,
            },
        }

    session: dict[str, Any] = {
        "type": "realtime",
        "model": chosen_model,
        "instructions": composed,
        "tools": tools,
        "tool_choice": "auto" if tools else "none",
        "output_modalities": ["audio"],
        "audio": audio,
    }
    return session
