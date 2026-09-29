from __future__ import annotations

from app.core.compliance import recording_consent_message
from app.core.crm_tools import agent_needs_crm_fetch, agent_tool_enabled, end_call_enabled
from app.core.realtime import agent_language_instructions
from app.core.prompts import F45_SYSTEM_PROMPT

CALL_MECHANICS = """
# LIVE PHONE
This is a live phone call.
At most TWO short sentences per turn, then STOP and wait.
About 20 words max per turn. Ask only one question, then go silent.
Do not invent CRM facts, prices, class names, or bookings.
Do not call send_sms. Do not create a member. Do not check slots.
If they are interested, say someone from the studio will follow up, then end the call.
After they say yes, first pitch $60 off the monthly plan and a free week, then share studio classes and membership plans from CRM.
"""

DEFAULT_FIRST_MESSAGE = (
    "Hi, this is Matt calling from Total Bizz gym. Can I have 2 minutes?"
)


def _agent(agent) -> dict:
    return agent if isinstance(agent, dict) else {}


def _attach_language_policy(text: str, agent=None) -> str:
    block = agent_language_instructions(agent).strip()
    if block and block not in (text or ""):
        text = f"{(text or '').rstrip()}\n\n{block}\n"
    policy = tool_source_policy(agent).strip()
    if policy and policy not in (text or ""):
        text = f"{(text or '').rstrip()}\n\n{policy}\n"
    return text or ""


def use_agent_prompt_only(agent=None) -> bool:
    """LLC agents (e.g. Receptionist) with conversation_prompt — no hardcoded outbound sales scripts."""
    agent = _agent(agent)
    return bool((agent.get("conversation_prompt") or "").strip())


def agent_conversation_prompt(agent=None) -> str:
    agent = _agent(agent)
    custom = (agent.get("conversation_prompt") or "").strip()
    if custom:
        return custom
    return F45_SYSTEM_PROMPT


def agent_first_message(agent=None) -> str:
    agent = _agent(agent)
    custom = (agent.get("first_message") or "").strip()
    if custom:
        return custom
    return DEFAULT_FIRST_MESSAGE


def _member_if_enabled(agent, member: dict | None) -> dict:
    """Caller name, contact, and visit history only when the member tool is on."""
    if agent_tool_enabled(agent, "crm_member", default=False):
        return member or {}
    return {}


def tool_source_policy(agent=None) -> str:
    """Memberships, classes, the caller, and hang-up come only from enabled tools."""
    member_on = agent_tool_enabled(agent, "crm_member", default=False)
    classes_on = agent_tool_enabled(agent, "crm_class_schedule", default=False)
    pricing_on = agent_tool_enabled(agent, "crm_pricing", default=False)
    end_on = end_call_enabled(agent)

    member_rule = (
        "Member profile is ON. The caller's name, phone, email, visits, and their own membership status come only from the member section below. Do not invent a caller."
        if member_on
        else "Member profile is OFF. Do not use a caller name, phone, email, visit history, or their membership status. Ignore {{contact.*}} leftovers and any name in an older script."
    )
    class_rule = (
        "Class schedule is ON. Class names, days, and times come only from the CLASS SCHEDULE section. Do not use class lists written in the agent script."
        if classes_on
        else "Class schedule is OFF. Do not name classes, days, or class times. If they ask, say you don't have the class schedule on this call."
    )
    pricing_rule = (
        "Pricing & plans is ON. Membership plans, packs, and prices come only from the pricing section. Do not quote a plan or dollar amount from the agent script unless that same row is in the pricing section."
        if pricing_on
        else "Pricing & plans is OFF. Do not name membership plans, packs, discounts, or prices. If they ask, say you don't have plan details on this call."
    )
    end_rule = (
        "End call is ON. Hang up only by calling the end_call tool, and only after a short goodbye."
        if end_on
        else "End call is OFF. Do not call end_call. Do not try to hang up the line."
    )
    return f"""
# ENABLED TOOLS (highest priority for memberships, classes, the caller, and hang-up)

{member_rule}
{class_rule}
{pricing_rule}
{end_rule}
If a section for that tool is missing, say you don't have it. Do not fill the gap from the script.
"""


def agent_spoken_opening(agent=None, member: dict | None = None, studio: dict | None = None) -> str:
    """Greeting the caller hears, including the recording-consent line when set."""
    member = _member_if_enabled(agent, member)
    opening = apply_contact_tokens(agent_first_message(agent), member, studio).strip()
    consent = apply_contact_tokens(recording_consent_message(agent), member, studio).strip()
    if consent and opening:
        return f"{consent} {opening}"
    return consent or opening


def apply_contact_tokens(
    text: str,
    member: dict | None = None,
    studio: dict | None = None,
) -> str:
    member = member or {}
    studio = studio or {}
    if is_known_member(member):
        first_name = member_display_first_name(member) or _value(member, "first_name", default="")
        full_name = _value(member, "full_name", "first_name", default="")
    else:
        first_name = ""
        full_name = ""
    email = _value(member, "email") if member.get("id") else "unknown"
    phone = _value(member, "phone") if member.get("id") else "unknown"
    studio_name = _value(studio, "title", default="Total Bizz gym")
    if studio_name in {"unknown", ""}:
        studio_name = "Total Bizz gym"

    return (
        (text or "")
        .replace("{{contact.name}}", full_name or "there")
        .replace("{{contact.first_name}}", first_name or "there")
        .replace("{{contact.email}}", email)
        .replace("{{contact.phone}}", phone)
        .replace("{{studio.name}}", studio_name)
        .replace("{{company.name}}", studio_name)
    )


def _agent_phase_instructions(
    agent=None,
    member: dict | None = None,
    studio: dict | None = None,
    phase: str = "",
) -> str:
    agent = _agent(agent)
    member = _member_if_enabled(agent, member)
    base = apply_contact_tokens(agent_conversation_prompt(agent), member, studio)
    phase = (phase or "").strip()
    parts = [base]
    if not use_agent_prompt_only(agent):
        parts.append(CALL_MECHANICS.strip())
    elif phase:
        parts.append(
            "# LIVE PHONE\n"
            "This is a live phone call. Short turns. Follow your agent instructions above.\n"
            "Memberships, classes, and the caller come only from enabled tool sections below."
        )
    if phase:
        parts.append(f"# CURRENT CALL PHASE\n{phase}")
    return _attach_language_policy("\n\n".join(parts), agent)


def greeting_instructions(agent=None, member: dict | None = None, studio: dict | None = None) -> str:
    opening = agent_spoken_opening(agent, member, studio)
    phase = f"""
You are on a live outbound phone call.

Say this opening, then STOP completely. If it includes a recording notice, say that first, then the greeting. Do not add anything else:
"{opening}"

Rules until the customer answers:
- Do not mention any offer, discount, price, free week, membership, trial, or trainer consultation unless your agent instructions above explicitly allow it on the greeting turn (default: do not).
- Do not add a second sentence.
- Do not keep talking if they have not answered yet.
"""
    return _agent_phase_instructions(agent, member, studio, phase)

def member_display_first_name(member: dict | None) -> str:
    member = member or {}
    first = (member.get("first_name") or "").strip()
    if first:
        return first
    full = (member.get("full_name") or "").strip()
    if full:
        return full.split()[0]
    return ""


def waiting_instructions(
    agent=None,
    studio: dict | None = None,
    member: dict | None = None,
) -> str:
    studio = studio or {}
    member = member or {}

    if use_agent_prompt_only(agent):
        phase = """
You are on a live phone call. You already gave your greeting (or opening line).

The customer is responding now. Follow ONLY your agent instructions above for what to say next.
Do NOT use any offer, discount, or script that is not written in your agent instructions.
Membership plans, class days, and the caller's profile come only from enabled tool sections.

- If they cannot talk: one short sentence offering to call back, then stop.
- If they ask to stop calling: one polite goodbye, then stop.
- If you could not hear them: one short clarification question, then stop.
"""
        return _agent_phase_instructions(agent, member, studio, phase)

    if is_known_member(member):
        first_name = member_display_first_name(member) or "there"
        yes_turn = (
            f"Awesome, thanks {first_name} — you had a profile with us. "
            f"We've got an offer at Total Bizz gym — sixty dollars off the monthly plan, "
            f"plus a free week to try the studio."
        )
    else:
        yes_turn = (
            "Awesome, thanks. We've got an offer at Total Bizz gym — sixty dollars off the monthly plan, "
            "plus a free week to try the studio."
        )

    phase = f"""
You are on a live phone call.
You already asked if they have a minute.
They are answering now.

Follow your agent instructions above for tone and offer details. For this phase, use these lines unless your agent prompt overrides them:

- If they said yes, yeah, ok, sure, or go ahead:
  Say ONLY this (one short beat — not a lecture), then STOP completely and wait:
  "{yes_turn}"
  Do NOT list class names, membership plans, or prices on this turn.
  Do NOT ask about the free trainer consult on this turn.
  Do NOT add extra sentences. Sound natural, not like reading a script.
  Do not say they had a profile unless this exact script includes that line (known CRM member only).

- If they are busy or cannot talk right now:
  One short sentence: "Totally get it. When's a better time I can call you back?" then STOP.

- If they said don't call, not interested, or no:
  One short sentence: "No problem, I'll let you go." then STOP.

- If you could not hear them:
  One short sentence: "Sorry, I didn't catch that — is now okay?" then STOP.
"""
    return _agent_phase_instructions(agent, member, studio, phase)


def offer_followup_instructions(
    studio: dict | None = None,
    agent=None,
    member: dict | None = None,
) -> str:
    _ = studio
    phase = """
You already told them about the offer intro (discount / free week). Do NOT repeat that whole intro.

Say ONLY this one question, then STOP and wait for their answer:
"Would you like to hear about our classes or our membership plans at Total Bizz gym?"

Do not list class names or membership names yet.
Do not ask about the free trainer consult yet.
Do not repeat "Awesome thanks" or the profile line. Keep it brief and natural.
"""
    return _agent_phase_instructions(agent, member, studio, phase)


def _hangup_line(agent, when: str) -> str:
    if end_call_enabled(agent):
        return f"After {when}, call the end_call tool."
    return f"After {when}, stop talking. Do not call end_call."


def callback_instructions(agent=None) -> str:
    hangup = _hangup_line(agent, "the goodbye line")
    phase = f"""
You are on a live phone call. The customer is busy.
You already asked when you can call them back.

One short turn, then STOP.

- If they give a day or time: "Perfect, I'll try you then. Thanks for your time."
- If they say don't call, not now, no, or not interested: "Appreciate it, have a good one."
- If unclear: "Would later today or tomorrow work better?"

Do not pitch. {hangup}
"""
    return _agent_phase_instructions(agent, None, None, phase)


def _value(data: dict, *keys, default="unknown"):
    for key in keys:
        value = (data or {}).get(key)
        if value:
            return str(value)
    return default


def _visit_line(visit: dict | None) -> str:
    if not visit:
        return "None on file"
    parts = [
        visit.get("start_time"),
        visit.get("class_name") or visit.get("service_name"),
        visit.get("staff_name"),
        visit.get("status"),
    ]
    return " — ".join(str(part) for part in parts if part) or "None on file"


def _membership_line(membership) -> str:
    if not membership:
        return "Unknown"
    if isinstance(membership, list):
        if not membership:
            return "Unknown"
        membership = membership[0]
    name = membership.get("name")
    status = membership.get("status")
    remaining = membership.get("remaining")
    bits = [bit for bit in [name, status] if bit]
    if remaining not in (None, ""):
        bits.append(f"remaining {remaining}")
    return ", ".join(bits) if bits else "Unknown"


def _format_schedule_days(days) -> str:
    if not days:
        return ""
    if isinstance(days, list):
        return ", ".join(str(item).strip() for item in days if str(item).strip())
    return str(days).strip()


def _schedule_clock(value) -> str:
    text = str(value or "").strip()
    if "T" in text and len(text) >= 16:
        # Dated occurrences repeat the same weekly slot. Keep the clock only.
        clock = text[11:16]
        hour, minute = clock.split(":")
        hour_i = int(hour)
        suffix = "AM" if hour_i < 12 else "PM"
        hour_12 = hour_i % 12 or 12
        return f"{hour_12}:{minute} {suffix}"
    return text


def _class_schedule_block(studio: dict) -> str:
    catalog = [cls for cls in (studio.get("classes") or []) if isinstance(cls, dict)]
    names_only = [str(item).strip() for item in (studio.get("class_names") or []) if str(item).strip()]
    lines: list[str] = []
    if not catalog and not names_only:
        lines.append("No class rows were returned from CRM.")
    seen_names: set[str] = set()
    for cls in catalog:
        name = (cls.get("name") or "Class").strip()
        if not name:
            continue
        seen_names.add(name.lower())
        schedules = [sch for sch in (cls.get("schedules") or []) if isinstance(sch, dict)]
        if not schedules:
            teacher = (cls.get("teacher") or "").strip()
            hint = f" with {teacher}" if teacher else ""
            lines.append(f"- {name}: days and times are not listed{hint}")
            continue
        slots: list[str] = []
        seen_slots: set[tuple[str, str, str]] = set()
        for sch in schedules:
            days = _format_schedule_days(sch.get("days"))
            time = _schedule_clock(sch.get("time") or sch.get("start_time"))
            teacher = (sch.get("staff_name") or cls.get("teacher") or "").strip()
            key = (days.lower(), time.lower(), teacher.lower())
            if key in seen_slots:
                continue
            seen_slots.add(key)
            bit = " ".join(part for part in (days, time) if part).strip() or "time not listed"
            if teacher:
                bit = f"{bit} ({teacher})"
            slots.append(bit)
            if len(slots) >= 6:
                break
        lines.append(f"- {name}: " + "; ".join(slots))

    for name in names_only:
        if name.lower() in seen_names:
            continue
        lines.append(f"- {name}: days and times are not listed")

    body = "\n".join(lines) if lines else "- (empty)"
    return f"# CLASS SCHEDULE (CRM)\n\n{body}\n"


def _pricing_options_block(studio: dict) -> str:
    options = studio.get("pricing_options") or []
    if not options:
        return ""
    lines: list[str] = []
    for opt in options[:60]:
        if not isinstance(opt, dict):
            continue
        name = (opt.get("name") or "").strip()
        if not name:
            continue
        parts = [name]
        if opt.get("unlimited"):
            parts.append("unlimited visits")
        elif opt.get("sessions"):
            parts.append(f"{opt['sessions']} session(s)")
        exp_bits = []
        if opt.get("expiration_length") not in (None, "", 0):
            exp_bits.append(str(opt.get("expiration_length")))
        if opt.get("expiration_unit"):
            exp_bits.append(str(opt.get("expiration_unit")))
        if exp_bits:
            parts.append("expires " + " ".join(exp_bits))
        if opt.get("program_name"):
            parts.append(str(opt.get("program_name")))
        price = opt.get("display_price") or opt.get("online_price") or opt.get("price")
        if price not in (None, "", 0, "0"):
            parts.append(f"Price {price}")
        lines.append("- " + " · ".join(str(part) for part in parts if part))
    body = "\n".join(lines) if lines else "- none listed"
    return f"""
# PRICING OPTIONS (CRM — same list as studio Pricing Option table; HIGHEST PRIORITY FOR PLANS)

When they ask about memberships, plans, packs, drop-in, class cards, bootcamps, or prices, answer ONLY from this list.
Use these exact product names. Do not invent Corporate Member, Monthly Member, PIF Member, $60 off, or any name that is not below.
If they ask about non-member drop-in and it is on this list, say yes and use this row. Do not say you do not have it.
If Price is listed you may say it. If Price is missing, say the front desk can confirm the current price. Never invent a dollar amount.

{body}
"""


def _plans_block(studio: dict) -> str:
    plans = studio.get("membership_plans") or []
    lines = []
    for plan in plans:
        if isinstance(plan, str):
            if plan.strip():
                lines.append(f"- {plan.strip()}")
            continue
        name = (plan.get("name") or "").strip()
        if not name:
            continue
        description = (plan.get("description") or "").strip()
        price = plan.get("display_price") or plan.get("price") or plan.get("online_price")
        parts = [name]
        if description:
            parts.append(description)
        if price not in (None, "", 0, "0"):
            parts.append(f"Price {price}")
        lines.append("- " + ". ".join(parts))
    return "\n".join(lines) if lines else "- not listed in CRM"


def _membership_block(studio: dict) -> str:
    pricing = _pricing_options_block(studio)
    if pricing:
        return pricing
    return f"# MEMBERSHIPS AND PRICING\n\n{_plans_block(studio)}\n"


def is_known_member(member: dict | None) -> bool:
    member = member or {}
    if not member.get("id"):
        return False
    first = member_display_first_name(member).lower()
    if first in {"guest", "unknown", "caller", ""}:
        return False
    return True


def build_crm_prompt_addon(
    agent: dict | None,
    member: dict,
    studio: dict,
    *,
    studio_name: str,
    studio_location: str,
) -> str:
    """CRM text blocks — only sections whose crm_* tools are enabled on the agent."""
    if not agent_needs_crm_fetch(agent):
        return ""

    parts: list[str] = ["\n# LIVE CRM CONTEXT (ground truth from studio CRM)\n"]
    parts.append(f"Studio: {studio_name}\nStudio location: {studio_location}")

    if agent_tool_enabled(agent, "crm_member", default=False):
        if is_known_member(member):
            first_name = member_display_first_name(member) or _value(member, "first_name", default="")
            parts.append(
                f"""
Member CRM id: {member.get("id")}
Membership: {_membership_line(member.get("membership"))}
Total visits: {member.get("total_visits", 0)}
Last visit: {_visit_line(member.get("last_visit"))}
Next visit: {_visit_line(member.get("next_visit"))}
Use their first name ({first_name or "from CRM"}) when appropriate.
""".strip()
            )
        else:
            parts.append(
                "No matching member profile for this call. Do not invent their name or visit history."
            )

    if agent_tool_enabled(agent, "crm_class_schedule", default=False):
        parts.append(_class_schedule_block(studio).strip())

    if agent_tool_enabled(agent, "crm_pricing", default=False):
        parts.append(_membership_block(studio).strip())

    return "\n\n".join(parts) + "\n"


def build_instructions(member: dict | None = None, studio: dict | None = None, agent: dict | None = None) -> str:
    studio = studio or {}
    agent = _agent(agent)
    member = _member_if_enabled(agent, member or {})

    if not is_known_member(member):
        first_name = ""
    else:
        first_name = member_display_first_name(member) or _value(member, "first_name", default="")

    prompt = apply_contact_tokens(agent_conversation_prompt(agent), member, studio)

    studio_name = _value(studio, "title", default="Total Bizz gym")
    if studio_name in {"unknown", ""}:
        studio_name = "Total Bizz gym"
    studio_location = _value(studio, "location", default="6322 Clayton Avenue, 63139")

    crm_addon = build_crm_prompt_addon(
        agent,
        member,
        studio,
        studio_name=studio_name,
        studio_location=studio_location,
    )

    hangup = _hangup_line(agent, "the thank-you line")

    if use_agent_prompt_only(agent):
        return _attach_language_policy(prompt + crm_addon, agent)

    if is_known_member(member):
        history = f"""

# CALL CONTEXT

Use the customer's real first name ({first_name or "from CRM"}) from the member tool.
Share class or membership details only when that tool's section is present below.
Do not call send_sms. Do not promise a text.
If they want the free consult: thank them by first name, say someone from Total Bizz gym will reach out to schedule it, say thanks for your time. {hangup}
Do not create a member. Do not book them into a class on this call.
"""
    else:
        history = f"""

# CALL CONTEXT

Studio: {studio_name}
Studio location: {studio_location}

This is a NEW / unknown number unless the member tool section appears below.
Do not invent the customer's name, email, visit history, or membership status.
After they agree to talk, you may pitch. Not before they have clearly said yes, sure, okay, or that they have a minute.

Tell them about memberships or classes only from the enabled tool sections below.

OVERRIDE: Do not call send_sms. Do not use iMessage. Do not promise a text. Do not create a CRM member.
If they are interested in the free trainer consult (yes, sure, book it, sign me up):
1. Do not hang up on the same turn they said yes.
2. Do not save them in CRM and do not invent a booking time.
3. NEW caller (no CRM member name): ask ONE question — "Great — what name should I put this under?" — then wait.
4. After they give a name: say "Perfect, someone from Total Bizz gym will reach out to schedule your free trainer consult. Thanks for your time — have a good one." {hangup}
5. Do not hang up before that thank-you line.
Do not invent a booking or say they are locked in for a visit unless staff confirmed it.
"""

    return _attach_language_policy(prompt + history + crm_addon, agent)
