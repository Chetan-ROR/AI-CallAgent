import json


async def dispatch_tool(name: str, arguments: dict, stream_info: dict) -> dict:
    if name == "end_call":
        if not stream_info.get("call_sid"):
            return {"success": False, "error": "Call SID not available"}
        return {
            "success": True,
            "hangup_pending": True,
            "say_to_user": (
                "Say ONE short goodbye (one or two sentences max): thank them by first name "
                "if you know it, say someone from Total Bizz gym will reach out about the "
                "free trainer consult, and say have a good one. Do not ask another question."
            ),
        }

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
