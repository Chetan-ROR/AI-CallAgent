from __future__ import annotations

from typing import Any

# Toggle keys stored on agent.tools in Rails (not all are OpenAI function calls).
CRM_TOOL_KEYS: frozenset[str] = frozenset(
    {
        "crm_member",
        "crm_class_schedule",
        "crm_pricing",
    }
)

OPENAI_FUNCTION_TOOL_KEYS: frozenset[str] = frozenset(
    {"end_call", "book_guest_pass", "book_class_visit"}
)

AGENT_TOOL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "key": "end_call",
        "kind": "realtime",
        "label": "End call",
        "description": "Let the agent hang up after a short goodbye.",
        "default_enabled": True,
    },
    {
        "key": "book_guest_pass",
        "kind": "realtime",
        "label": "7 Day Guest Pass",
        "description": "Offer and book only the 7 Day Guest Pass. Creates a member first when the caller is new. No payment.",
        "default_enabled": False,
    },
    {
        "key": "book_class_visit",
        "kind": "realtime",
        "label": "Book class visit",
        "description": "Book the caller into a scheduled class after they confirm the class and time. Requires a Mindbody member with a pack that covers that class.",
        "default_enabled": False,
    },
    {
        "key": "crm_member",
        "kind": "crm",
        "label": "Member profile",
        "description": "Load member name, membership, and visit history from CRM into the prompt.",
        "default_enabled": False,
    },
    {
        "key": "crm_class_schedule",
        "kind": "crm",
        "label": "Class schedule",
        "description": "Load studio class days and times from CRM into the prompt.",
        "default_enabled": False,
    },
    {
        "key": "crm_pricing",
        "kind": "crm",
        "label": "Pricing & plans",
        "description": "Load membership plans and pricing options from CRM into the prompt.",
        "default_enabled": False,
    },
)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, "", 0, "0", "false", "False"):
        return False
    return True


def agent_tools_map(agent: dict | None) -> dict[str, Any]:
    raw = (agent or {}).get("tools")
    return raw if isinstance(raw, dict) else {}


def agent_tool_enabled(agent: dict | None, key: str, *, default: bool = False) -> bool:
    tools = agent_tools_map(agent)
    if not tools or key not in tools:
        return default
    return _truthy(tools.get(key))


def end_call_enabled(agent: dict | None) -> bool:
    """Saved agents honor the flag. Agents with no tools map keep end_call on."""
    if not agent_tools_map(agent):
        return True
    return agent_tool_enabled(agent, "end_call", default=False)


def agent_needs_crm_fetch(agent: dict | None) -> bool:
    """Fetch LLC member/studio when CRM tools OR guest-pass booking needs ownership check."""
    if any(agent_tool_enabled(agent, key, default=False) for key in CRM_TOOL_KEYS):
        return True
    if agent_tool_enabled(agent, "book_guest_pass", default=False):
        return True
    return agent_tool_enabled(agent, "book_class_visit", default=False)


def member_has_active_guest_pass(member: dict | None) -> bool:
    """True when Rails lookup marked an active 7 Day Guest Pass on this member."""
    if not isinstance(member, dict) or not member:
        return False
    if member.get("has_active_guest_pass") is True:
        return True
    return str(member.get("guest_pass_status") or "").strip().lower() == "active"
