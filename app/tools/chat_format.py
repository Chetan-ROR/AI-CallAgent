"""Chat Completions tool format helpers."""

from __future__ import annotations

from typing import Any


def to_chat_completion_tools(realtime_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Realtime flat function tools to Chat Completions shape."""
    out: list[dict[str, Any]] = []
    for tool in realtime_tools or []:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            out.append(tool)
            continue
        name = tool.get("name")
        if not name:
            continue
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description") or "",
                    "parameters": tool.get("parameters")
                    or {"type": "object", "properties": {}, "required": []},
                },
            }
        )
    return out
