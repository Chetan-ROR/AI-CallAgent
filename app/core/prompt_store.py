from __future__ import annotations

from pathlib import Path

PROMPT_FILE = Path(__file__).resolve().parent / "agent_prompt.txt"

DEFAULT_PROMPT = (
    "You are a helpful gym receptionist. Be warm, clear, and concise. "
    "Help callers with membership info, trial bookings, hours, and class schedules. "
    "If you cannot help, offer to take a message for the front desk."
)


def get_prompt() -> str:
    if PROMPT_FILE.exists():
        text = PROMPT_FILE.read_text(encoding="utf-8").strip()
        if text:
            return text
    return DEFAULT_PROMPT


def set_prompt(instructions: str) -> str:
    cleaned = instructions.strip()
    if not cleaned:
        raise ValueError("Prompt cannot be empty")
    PROMPT_FILE.write_text(cleaned + "\n", encoding="utf-8")
    return cleaned
