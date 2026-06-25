"""Export session tool — Export the current conversation as JSON or Markdown.

Usage:
    from kazma_core.tools.export_session import export_session
    result = await export_session(format="json")
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

# Global session messages reference — set by the graph/sse_chat at runtime.
_session_messages: list[dict[str, Any]] = []


def set_session_messages(messages: list[dict[str, Any]]) -> None:
    """Set the current session messages (called by the graph or SSE handler)."""
    global _session_messages
    _session_messages = messages


def get_session_messages() -> list[dict[str, Any]]:
    """Get the current session messages."""
    return _session_messages


async def export_session(format: str = "json") -> str:
    """Export the current conversation session.

    Args:
        format: Output format — "json" or "markdown" (default "json").

    Returns:
        The session as a JSON string or Markdown document.
    """
    messages = get_session_messages()
    if not messages:
        return "Error: No session messages available to export."

    fmt = format.strip().lower()

    if fmt == "json":
        return _export_json(messages)
    elif fmt in ("markdown", "md"):
        return _export_markdown(messages)
    else:
        return f"Error: Unknown format '{format}'. Use 'json' or 'markdown'."


def _export_json(messages: list[dict[str, Any]]) -> str:
    """Export session as formatted JSON."""
    export = {
        "exported_at": datetime.now(UTC).isoformat(),
        "message_count": len(messages),
        "messages": messages,
    }
    return json.dumps(export, ensure_ascii=False, indent=2)


def _export_markdown(messages: list[dict[str, Any]]) -> str:
    """Export session as a readable Markdown document."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        "# Session Export",
        f"**Exported:** {now}",
        f"**Messages:** {len(messages)}",
        "",
        "---",
        "",
    ]

    for i, msg in enumerate(messages, 1):
        role = msg.get("role", "unknown").capitalize()
        content = msg.get("content", "")
        name = msg.get("name", "")

        if role == "System":
            continue  # skip system messages in export

        header = f"## {i}. {role}"
        if name:
            header += f" ({name})"
        lines.append(header)

        if content:
            lines.append(content)
        else:
            lines.append("*(no content)*")

        # Include tool calls if present
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            lines.append("\n**Tool calls:**")
            for tc in tool_calls:
                fn = tc.get("function", {})
                lines.append(f"- `{fn.get('name', '?')}({fn.get('arguments', '')})`")

        lines.append("")

    return "\n".join(lines)
