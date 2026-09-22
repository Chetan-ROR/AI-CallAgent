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
    )


def _agent_phase_instructions(
    agent=None,
    member: dict | None = None,
    studio: dict | None = None,
    phase: str = "",
) -> str:
    agent = _agent(agent)
    base = apply_contact_tokens(agent_conversation_prompt(agent), member, studio)
    phase = (phase or "").strip()
    parts = [base]
    if not use_agent_prompt_only(agent):
        parts.append(CALL_MECHANICS.strip())
    elif phase:
        parts.append(
            "# LIVE PHONE\n"
            "This is a live phone call. Short turns. Follow your agent instructions above.\n"
            "Use CRM context below for classes, schedules, and plans — do not invent facts."
        )
    if phase:
        parts.append(f"# CURRENT CALL PHASE\n{phase}")
    return "\n\n".join(parts)


def greeting_instructions(agent=None, member: dict | None = None, studio: dict | None = None) -> str:
    opening = agent_first_message(agent)
    phase = f"""
You are on a live outbound phone call.

Say this one line, then STOP completely:
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


def _plan_names_line(studio: dict) -> str:
    names = []
    for plan in studio.get("membership_plans") or []:
        if isinstance(plan, dict):
            name = (plan.get("name") or "").strip()
        else:
            name = str(plan).strip()
        if name:
            names.append(name)
    if names:
        return ", ".join(names[:4])
    return "monthly, paid-in-full, and corporate memberships"


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
Use CRM class schedules and membership data below when they ask about classes or plans.

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


def callback_instructions(agent=None) -> str:
    tools = _agent(agent).get("tools") or {}
    end_call = tools.get("end_call", True)
    hangup = (
        "After the goodbye line, you MUST call the end_call tool."
        if end_call
        else "After the goodbye line, stop talking."
    )
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


def _class_line(studio: dict) -> str:
    names = [str(item).strip() for item in (studio.get("class_names") or []) if str(item).strip()]
    types = [str(item).strip() for item in (studio.get("class_types") or []) if str(item).strip()]
    if names:
        return ", ".join(names[:12])
    if types:
        return ", ".join(types[:12])
    return "not listed in CRM"


def _format_schedule_days(days) -> str:
    if not days:
        return ""
    if isinstance(days, list):
        return ", ".join(str(item).strip() for item in days if str(item).strip())
    return str(days).strip()


def _class_schedule_block(studio: dict) -> str:
    catalog = studio.get("classes") or []
    if not catalog:
        return """
# CLASS SCHEDULE (CRM)
No per-class schedule rows were returned — only class names/types above.
If they ask when a class runs, say you only see class names in the system and staff can confirm times.
"""
    lines: list[str] = []
    for cls in catalog[:30]:
        if not isinstance(cls, dict):
            continue
        name = (cls.get("name") or "Class").strip()
        schedules = cls.get("schedules") or []
        if not schedules:
            teacher = (cls.get("teacher") or "").strip()
            hint = f" (instructor {teacher})" if teacher else ""
            lines.append(f"- {name}: no recurring schedule in CRM{hint}")
            continue
        for sch in schedules[:8]:
            if not isinstance(sch, dict):
                continue
            days = _format_schedule_days(sch.get("days"))
            time = (sch.get("time") or sch.get("start_time") or "").strip()
            teacher = (sch.get("staff_name") or cls.get("teacher") or "").strip()
            parts = [name]
            if days:
                parts.append(days)
            if time:
                parts.append(time)
            if teacher:
                parts.append(f"with {teacher}")
            loc = (sch.get("location_name") or cls.get("location") or "").strip()
            if loc:
                parts.append(f"at {loc}")
            lines.append("- " + " · ".join(parts))
    body = "\n".join(lines[:45]) if lines else "- (empty)"
    return f"""
# CLASS SCHEDULE (CRM — ground truth for this studio)

When the caller asks what classes you offer, when they run, or what times are available, answer using ONLY this schedule.
You may summarize (e.g. a few popular times) — do not invent days or times not listed here.
If a class has no schedule row, say times are not listed in the system and offer to have the front desk confirm.

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


def _offerings_block(studio: dict) -> str:
    return f"""
# WHAT THIS STUDIO OFFERS (from CRM)

Class types: {_class_line(studio)}

Membership plans:
{_plans_block(studio)}

This is what you can tell a new customer we offer.
When they ask what you have, or after they have two minutes, mention 2-4 class names and the membership plan names.
Do not read the whole list in one breath.
If a plan has Price listed, you may say that price. If a plan has no price, do not invent a number.
Do not say Monthly includes 1000 visits.
PIF is paid in full; if CRM lists a visit pack, you may say that number of class visits.
Corporate is a company plan.
{_class_schedule_block(studio)}
"""


def is_known_member(member: dict | None) -> bool:
    member = member or {}
    if not member.get("id"):
        return False
    first = member_display_first_name(member).lower()
    if first in {"guest", "unknown", "caller", ""}:
        return False
    return True


def _agent_crm_context(member: dict, studio: dict, *, studio_name: str, studio_location: str) -> str:
    """CRM facts for LLC-configured agents — no outbound sales script."""
    if is_known_member(member):
        first_name = member_display_first_name(member) or _value(member, "first_name", default="")
        member_block = f"""
Member CRM id: {member.get("id")}
Membership: {_membership_line(member.get("membership"))}
Total visits: {member.get("total_visits", 0)}
Last visit: {_visit_line(member.get("last_visit"))}
Next visit: {_visit_line(member.get("next_visit"))}
Use their first name ({first_name or "from CRM"}) when appropriate.
"""
    else:
        member_block = """
No matching member profile for this call. Do not invent their name or visit history.
"""
    return f"""

# LIVE CRM CONTEXT (ground truth from studio CRM)

Studio: {studio_name}
Studio location: {studio_location}
{member_block.strip()}
When they ask about class days or times, use the CLASS SCHEDULE section — do not guess.
{_offerings_block(studio)}
"""


def build_instructions(member: dict | None = None, studio: dict | None = None, agent: dict | None = None) -> str:
    member = member or {}
    studio = studio or {}
    agent = _agent(agent)

    if not is_known_member(member):
        first_name = ""
        full_name = ""
    else:
        first_name = member_display_first_name(member) or _value(member, "first_name", default="")
        full_name = _value(member, "full_name", "first_name", default="")
    email = _value(member, "email")
    phone = _value(member, "phone")

    prompt = apply_contact_tokens(agent_conversation_prompt(agent), member, studio)

    studio_name = _value(studio, "title", default="Total Bizz gym")
    if studio_name in {"unknown", ""}:
        studio_name = "Total Bizz gym"
    studio_location = _value(studio, "location", default="6322 Clayton Avenue, 63139")

    if use_agent_prompt_only(agent):
        return prompt + _agent_crm_context(
            member,
            studio,
            studio_name=studio_name,
            studio_location=studio_location,
        )

    if is_known_member(member):
        history = f"""

# LIVE CRM CONTEXT

This data was loaded from the studio CRM just before the call.
Treat it as ground truth. Do not invent extra personal details.

Studio: {studio_name}
Studio location: {studio_location}
Member CRM id: {member.get("id")}
Mindbody client id: {member.get("mindbody_client_id") or "not linked yet"}
Membership: {_membership_line(member.get("membership"))}
Total visits: {member.get("total_visits", 0)}
First visit: {_visit_line(member.get("first_visit"))}
Last visit: {_visit_line(member.get("last_visit"))}
Next visit: {_visit_line(member.get("next_visit"))}

Use the customer's real first name ({first_name or "from CRM"}). Never ask what name to put this under — you already have their CRM profile.
After they answer the classes-or-memberships question, share CRM details briefly, then ask about the free trainer consult.
Do not call send_sms. Do not promise a text.
If they want the free consult: thank them by first name, say someone from Total Bizz gym will reach out to schedule it, say thanks for your time, then end_call — do not hang up the instant they say yes.
Do not create a member. Do not book them into a class on this call unless your tools explicitly allow it.
When they ask about class days or times, use the CLASS SCHEDULE section below — do not guess.
{_offerings_block(studio)}
"""
    else:
        history = f"""

# LIVE CRM CONTEXT

No matching member profile was found in the studio CRM for this phone number.
Studio: {studio_name}
Studio location: {studio_location}

This is a NEW / unknown number. Do not say they created a profile a while back.
Do not invent the customer's name, email, visit history, or membership status.
After they agree to talk, you may pitch. Not before they have clearly said yes, sure, okay, or that they have a minute.
Do not invent the customer's name, email, visit history, or membership status.

Tell them what the studio offers using the CRM class types and membership plans below. Do not invent extra plans or prices.

After the "classes or membership plans" question, if they pick classes, name 2-4 from CRM; if memberships, name the plans (and prices only if listed); if both, keep it to one short turn each.
Then ask if they want the free trainer consult.

OVERRIDE: Do not call send_sms. Do not use iMessage. Do not promise a text. Do not create a CRM member.
If they are interested in the free trainer consult (yes, sure, book it, sign me up):
1. Do NOT call end_call on the same turn they said yes.
2. Do not save them in CRM and do not invent a booking time.
3. NEW caller (no CRM member name): ask ONE question — "Great — what name should I put this under?" — then wait.
4. After they give a name (or if you already have their first name from CRM): say "Perfect, someone from Total Bizz gym will reach out to schedule your free trainer consult. Thanks for your time — have a good one." Then call end_call.
5. Do not hang up before that thank-you line.
When they ask about class days or times, use the CLASS SCHEDULE section below — do not guess or invent times.
Do not invent a booking or say they are locked in for a visit unless staff confirmed it.
{_offerings_block(studio)}
"""

    return prompt + history
