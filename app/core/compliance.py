from __future__ import annotations

from typing import Any


def _flag(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def agent_compliance(agent: dict | None) -> dict[str, Any]:
    raw = (agent or {}).get("compliance")
    settings = raw if isinstance(raw, dict) else {}
    return {
        "recording_consent_message": str(settings.get("recording_consent_message") or "").strip(),
        "hipaa": _flag(settings.get("hipaa"), False),
        "zero_data_retention": _flag(settings.get("zero_data_retention"), False),
        "audio_recording": _flag(settings.get("audio_recording"), True),
        "transcript": _flag(settings.get("transcript"), True),
    }


def allows_audio_recording(agent: dict | None) -> bool:
    settings = agent_compliance(agent)
    if settings["zero_data_retention"]:
        return False
    return bool(settings["audio_recording"])


def allows_call_transcript(agent: dict | None) -> bool:
    settings = agent_compliance(agent)
    if settings["zero_data_retention"]:
        return False
    return bool(settings["transcript"])


def recording_consent_message(agent: dict | None) -> str:
    return agent_compliance(agent)["recording_consent_message"]
