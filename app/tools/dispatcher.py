import asyncio
import json
import re
import time

from app.core.crm_tools import agent_tool_enabled
from app.llc.client import LlcClient

_AGREE = re.compile(
    r"(?:"
    r"\b(yes|yeah|yep|yup|sure|okay|ok|alright|absolutely|please|book|go ahead|"
    r"sounds good|i want|i'll take|let's do|sign me up|haan|han|haa|theek|thik|"
    r"kar do|karo|ji)\b"
    r"|हाँ|हां|जी\s*हाँ|जी\s*हां|ठीक|ठीक\s*है|कर\s*दो|करो"
    r")",
    re.I,
)
_REFUSE = re.compile(
    r"(?:"
    r"\b(no|nope|nah|not now|don't|do not|later|busy|nahi|nahin|mat)\b"
    r"|नहीं|नही|मत"
    r")",
    re.I,
)
_PASS_ASK = re.compile(r"guest\s*pass", re.I)
# STT often invents "caller" / "कॉलर" when the last name is unclear.
_GARBAGE_NAME = re.compile(
    r"^(caller|unknown|test|n/?a|none|null|कॉलर)(\s+\1)*$",
    re.I,
)
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


def _utterance_agrees(text: str) -> bool:
    return bool(_AGREE.search(text or "") or _PASS_ASK.search(text or ""))


def _utterance_refuses(text: str) -> bool:
    return bool(_REFUSE.search(text or ""))


def guest_pass_agreement(stream_info: dict) -> tuple[bool, str]:
    if stream_info.get("caller_requested_guest_pass") or stream_info.get("guest_pass_agreed"):
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
    agreed = _utterance_agrees(latest)
    refused = _utterance_refuses(latest)
    if refused and not agreed:
        return False, "They did not agree. Do not book the 7 Day Guest Pass."
    if agreed:
        stream_info["guest_pass_agreed"] = True
        return True, ""
    # Sticky: any clear yes after the offer counts (email collection may follow).
    if any(_utterance_agrees(u) for u in utterances):
        if _utterance_refuses(latest) and not _utterance_agrees(latest):
            return False, "They did not agree. Do not book the 7 Day Guest Pass."
        stream_info["guest_pass_agreed"] = True
        return True, ""
    return False, (
        "They have not clearly agreed. Ask once if they want the 7 Day Guest Pass "
        "and wait for yes before calling book_guest_pass."
    )


def _clean_name(value: str | None) -> str:
    text = " ".join((value or "").split()).strip()
    if not text or _GARBAGE_NAME.match(text):
        return ""
    return text


def _booking_identity(arguments: dict | None, stream_info: dict) -> dict[str, str]:
    """Prefer tool args, then CRM member; drop STT garbage names."""
    member = stream_info.get("member") or {}
    args = arguments or {}
    first = _clean_name(args.get("first_name")) or _clean_name(member.get("first_name"))
    last = _clean_name(args.get("last_name")) or _clean_name(member.get("last_name"))
    email = " ".join(str(args.get("email") or member.get("email") or "").split()).strip()
    return {"first_name": first, "last_name": last, "email": email}


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


def _booking_failure(error: str, *, ask_again: bool = True) -> dict:
    """Friendly spoken line for the caller; keep technical detail in error only."""
    reason = " ".join((error or "the studio system could not complete the booking").split())
    # Missing name/email — ask once. System/Mindbody failures — soft apology, offer callback.
    needs_details = "need a real" in reason.lower() or "not linked to mindbody" in reason.lower()
    if needs_details and ask_again:
        spoken = (
            "Tell the caller you still need their last name and email to add the pass. "
            "Ask once for the missing details. Do NOT hang up yet."
        )
    else:
        spoken = (
            "Say something friendly like: sorry, something went wrong adding the pass on our side — "
            "we can call you back later to sort it out. "
            "Do not read technical errors. Do NOT hang up in this turn; wait for their reply, "
            "then say goodbye and call end_call only if they are done."
        )
    return {"success": False, "error": reason, "say_to_user": spoken}


async def _book_guest_pass(arguments: dict, stream_info: dict) -> dict:
    ok, message = await _wait_for_agreement(stream_info)
    if not ok:
        print("🎟️ guest pass blocked:", message)
        return _booking_failure(message, ask_again=False)

    client_id = (stream_info.get("client_id") or "").strip()
    phone = (stream_info.get("phone") or "").strip()
    if not client_id or not phone:
        return _booking_failure(
            "This call has no studio or phone number, so the pass cannot be booked.",
            ask_again=False,
        )
    identity = _booking_identity(arguments, stream_info)
    if not identity["last_name"] or not identity["email"]:
        missing = []
        if not identity["last_name"]:
            missing.append("last name")
        if not identity["email"]:
            missing.append("email")
        need = " and ".join(missing)
        msg = f"Need a real {need} before booking the 7 Day Guest Pass."
        print("🎟️ guest pass needs identity:", identity)
        return _booking_failure(msg)

    print("🎟️ booking guest pass with", identity)
    result = await LlcClient().book_guest_pass(
        client_id=client_id,
        phone=phone,
        member_id=stream_info.get("member_id"),
        first_name=identity["first_name"],
        last_name=identity["last_name"],
        email=identity["email"],
        call_sid=stream_info.get("call_sid"),
        agent_id=(stream_info.get("agent") or {}).get("id") or stream_info.get("agent_id"),
    )
    if not result.get("success"):
        print("🎟️ guest pass LLC error:", result)
        stream_info["guest_pass_book_failed"] = True
        err = (
            (result or {}).get("error")
            or (result or {}).get("say_to_user")
            or "Could not book 7 Day Guest Pass."
        )
        return _booking_failure(str(err))
    if result.get("member_id"):
        stream_info["member_id"] = result["member_id"]
    protect_guest_pass_playback(stream_info)
    stream_info.pop("guest_pass_book_failed", None)
    stream_info["has_active_guest_pass"] = True
    member = dict(stream_info.get("member") or {})
    member["has_active_guest_pass"] = True
    member["guest_pass_status"] = "active"
    stream_info["member"] = member
    # Do not hang up on the booking turn — confirm, then ask if they need anything else.
    if result.get("booked") or result.get("already_has_pass"):
        result = dict(result)
        result["say_to_user"] = (
            "Confirm in one short sentence that the 7 Day Guest Pass is on their account. "
            "Then ask once if there is anything else you can help with. "
            "Do NOT call end_call in this turn — wait for their reply."
        )
    return result


async def _book_class_visit(arguments: dict, stream_info: dict) -> dict:
    client_id = (stream_info.get("client_id") or "").strip()
    phone = (stream_info.get("phone") or "").strip()
    class_name = " ".join(str((arguments or {}).get("class_name") or "").split()).strip()
    start_time = " ".join(str((arguments or {}).get("start_time") or "").split()).strip()
    day = " ".join(str((arguments or {}).get("day") or "").split()).strip()
    if not class_name:
        msg = "Need the class name from the schedule before booking a visit."
        return {"success": False, "error": msg, "say_to_user": msg}
    if not client_id or not phone:
        return {
            "success": False,
            "error": "This call has no studio or phone number, so the class cannot be booked.",
        }
    print("🗓️ booking class visit", class_name, start_time, day)
    result = await LlcClient().book_class_visit(
        client_id=client_id,
        phone=phone,
        member_id=stream_info.get("member_id"),
        class_name=class_name,
        start_time=start_time or None,
        day=day or None,
    )
    if not result.get("success"):
        print("🗓️ class visit LLC error:", result)
        err = (result or {}).get("error") or "Could not book that class visit."
        spoken = (result or {}).get("say_to_user") or ""
        if "can book" in spoken.lower() or (result or {}).get("bookable_classes") is not None:
            if spoken:
                return {"success": False, "error": err, "say_to_user": spoken}
        spoken = (
            f"Tell the caller the class was not booked: {err} "
            "Do NOT hang up yet. Ask if they want another class or time."
        )
        return {"success": False, "error": err, "say_to_user": spoken}
    result = dict(result)
    result.setdefault(
        "say_to_user",
        "Confirm the class visit is booked in one short sentence, then ask if they need anything else. Do not hang up yet.",
    )
    return result


async def dispatch_tool(name: str, arguments: dict, stream_info: dict) -> dict:
    if name == "end_call":
        if not stream_info.get("call_sid"):
            return {"success": False, "error": "Call SID not available"}
        if stream_info.get("block_end_call_after_book"):
            stream_info["block_end_call_after_book"] = False
            msg = (
                "Do not hang up yet. Confirm the booking (or the issue), "
                "ask if they need anything else, then wait for their reply."
            )
            return {"success": False, "error": msg, "say_to_user": msg}
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

    if name == "book_class_visit":
        return await _book_class_visit(arguments, stream_info)

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
