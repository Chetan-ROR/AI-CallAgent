import asyncio
import json
import re
import time

from app.core.crm_tools import agent_tool_enabled
from app.llc.client import LlcClient

_AGREE = re.compile(
    r"\b(yes|yeah|yep|yup|sure|okay|ok|alright|absolutely|please|book|go ahead|"
    r"sounds good|i want|i'll take|let's do|sign me up|haan|han|theek|thik|kar do|karo)\b",
    re.I,
)
_REFUSE = re.compile(
    r"\b(no|nope|nah|not now|don't|do not|later|busy|nahi|nahin|mat)\b",
    re.I,
)
_PASS_ASK = re.compile(r"guest\s*pass", re.I)
_NEED_YES = (
    "Do not book yet. Offer only the 7 Day Guest Pass, then wait. "
    "Call book_guest_pass only after they clearly say yes."
)


def note_guest_pass_offer(stream_info: dict, spoken: str) -> None:
    """Mark the offer once, so a later yes is not confused with the greeting."""
    if stream_info.get("guest_pass_offered"):
        return
    if "guest pass" not in (spoken or "").lower():
        return
    stream_info["guest_pass_offered"] = True
    stream_info["caller_turns_after_offer"] = 0
    stream_info["caller_utterances_after_offer"] = []
    print("🎟️ Guest pass offered — waiting for a clear yes")


def note_caller_turn_after_offer(stream_info: dict) -> None:
    if stream_info.get("guest_pass_offered"):
        stream_info["caller_turns_after_offer"] = int(stream_info.get("caller_turns_after_offer") or 0) + 1


def note_caller_transcript(stream_info: dict, text: str) -> None:
    text = " ".join((text or "").split())
    if not text:
        return
    recent = stream_info.setdefault("recent_caller_lines", [])
    if recent and recent[-1] == text:
        return
    recent.append(text)
    print("👤 caller:", text)
    if _PASS_ASK.search(text):
        stream_info["caller_requested_guest_pass"] = True
    if stream_info.get("guest_pass_offered"):
        stream_info.setdefault("caller_utterances_after_offer", []).append(text)


def guest_pass_agreement(stream_info: dict) -> tuple[bool, str]:
    if stream_info.get("caller_requested_guest_pass"):
        return True, ""
    if not stream_info.get("guest_pass_offered"):
        return False, _NEED_YES
    utterances = stream_info.get("caller_utterances_after_offer") or []
    turns = int(stream_info.get("caller_turns_after_offer") or 0)
    if turns < 1 and not utterances:
        return False, (
            "They have not answered the offer yet. Wait until they agree to the 7 Day Guest Pass, "
            "then call book_guest_pass."
        )
    if not utterances:
        return False, (
            "Their answer was not clear. Ask once whether they want the 7 Day Guest Pass, "
            "wait for a clear yes, then call book_guest_pass."
        )
    latest = utterances[-1]
    agreed = bool(_AGREE.search(latest) or _PASS_ASK.search(latest))
    refused = bool(_REFUSE.search(latest))
    if refused and not agreed:
        return False, "They did not agree. Do not book the 7 Day Guest Pass."
    if agreed:
        return True, ""
    return False, (
        "They have not clearly agreed. Ask once if they want the 7 Day Guest Pass "
        "and wait for yes before calling book_guest_pass."
    )


async def _wait_for_agreement(stream_info: dict) -> tuple[bool, str]:
    for _ in range(6):
        ok, message = guest_pass_agreement(stream_info)
        if ok:
            return ok, message
        offered = stream_info.get("guest_pass_offered")
        answered = int(stream_info.get("caller_turns_after_offer") or 0) >= 1
        heard = bool(stream_info.get("caller_utterances_after_offer"))
        if offered and answered and heard:
            return ok, message
        await asyncio.sleep(0.25)
    return guest_pass_agreement(stream_info)


def protect_guest_pass_playback(stream_info: dict, seconds: float = 8.0) -> None:
    stream_info["guest_pass_booked"] = True
    stream_info["protect_playback_until"] = time.monotonic() + seconds


async def _book_guest_pass(arguments: dict, stream_info: dict) -> dict:
    ok, message = await _wait_for_agreement(stream_info)
    if not ok:
        print("🎟️ guest pass blocked:", message)
        return {"success": False, "error": message, "say_to_user": message}

    client_id = (stream_info.get("client_id") or "").strip()
    phone = (stream_info.get("phone") or "").strip()
    if not client_id or not phone:
        return {
            "success": False,
            "error": "This call has no studio or phone number, so the pass cannot be booked.",
        }
    result = await LlcClient().book_guest_pass(
        client_id=client_id,
        phone=phone,
        member_id=stream_info.get("member_id"),
        first_name=(arguments or {}).get("first_name"),
        last_name=(arguments or {}).get("last_name"),
        email=(arguments or {}).get("email"),
        call_sid=stream_info.get("call_sid"),
        agent_id=(stream_info.get("agent") or {}).get("id") or stream_info.get("agent_id"),
    )
    if result.get("success") and result.get("member_id"):
        stream_info["member_id"] = result["member_id"]
    # Protect confirmation whether they just booked or already had the pass.
    if result.get("success"):
        protect_guest_pass_playback(stream_info)
    return result


async def dispatch_tool(name: str, arguments: dict, stream_info: dict) -> dict:
    if name == "end_call":
        if not stream_info.get("call_sid"):
            return {"success": False, "error": "Call SID not available"}
        guest_pass_on = agent_tool_enabled(stream_info.get("agent") or {}, "book_guest_pass", default=False)
        if guest_pass_on:
            goodbye = (
                "Say ONE short goodbye (one or two sentences max): thank them by first name "
                "if you know it, and say have a good one. Do not mention a trainer consult, "
                "a follow-up call, another plan, a card, or a price. Do not ask another question."
            )
        else:
            goodbye = (
                "Say ONE short goodbye (one or two sentences max): thank them by first name "
                "if you know it, and say have a good one. Do not ask another question."
            )
        return {
            "success": True,
            "hangup_pending": True,
            "say_to_user": goodbye,
        }

    if name == "book_guest_pass":
        return await _book_guest_pass(arguments, stream_info)

    return {"success": False, "error": f"Unknown tool: {name}"}


def parse_tool_arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}
