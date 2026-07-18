"""Export session tool — Export the current conversation as JSON or Markdown.

Usage:
    from kazma_core.tools.export_session import export_session
    result = await export_session(format="json", messages=[...])

Session messages are passed explicitly via the ``messages`` parameter so
that concurrent sessions never corrupt each other's export data.  When
``export_session`` is invoked as an agent tool (the LLM does not pass
``messages``), the :func:`get_current_session_messages` accessor falls back
to a ``contextvars.ContextVar`` that the graph's tool-worker node sets per
invocation.  A ``ContextVar`` is the correct primitive for per-asyncio-task
data — each concurrent graph invocation sees its own value, unlike the old
module-global list which was shared across ALL sessions.
"""

from __future__ import annotations

import contextvars
import json
from datetime import UTC, datetime
from typing import Any

__all__ = ["export_session", "get_current_session_messages", "reset_current_session_messages", "set_current_session_messages"]

# ── Per-invocation session messages (async-safe) ──────────────────────
#
# This ContextVar holds the messages list for the *current* graph
# invocation.  It is set by the tool-worker node (see graph_builder.py)
# before executing any tool that needs session context.  Because it is a
# ContextVar, concurrent graph invocations (different thread_ids running
# in the same event loop) each see their own value — no cross-session
# leakage.
_current_session_messages: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "kazma_session_messages",
    default=None,
)


def set_current_session_messages(messages: list[dict[str, Any]] | None) -> contextvars.Token[list[dict[str, Any]] | None]:
    """Set the session messages for the current async context.

    Called by the graph's tool-worker node before executing tools so that
    tools registered without an explicit ``messages`` argument (e.g. when
    the LLM invokes ``export_session`` or ``context_info``) can access the
    correct per-invocation messages.

    Returns a token that should be passed to ``reset_current_session_messages``
    when the caller is done, restoring the previous value.
    """
    return _current_session_messages.set(messages)


def reset_current_session_messages(
    token: contextvars.Token[list[dict[str, Any]] | None],
) -> None:
    """Restore the session-messages ContextVar to its prior value."""
    _current_session_messages.reset(token)


def get_current_session_messages() -> list[dict[str, Any]]:
    """Return the session messages for the current async context.

    Returns an empty list if no session messages have been set in this
    context (e.g. when called outside the graph).
    """
    messages = _current_session_messages.get()
    return messages if messages is not None else []


async def export_session(
    format: str = "json",
    messages: list[dict[str, Any]] | None = None,
) -> str:
    """Export the current conversation session.

    Args:
        format: Output format — "json" or "markdown" (default "json").
        messages: Session messages to export.  When provided explicitly
            (the recommended path), only these messages are exported.  When
            omitted (e.g. when invoked as an agent tool by the LLM), the
            messages are read from the per-invocation ContextVar set by the
            tool-worker node.  This dual-entry design keeps concurrent
            sessions isolated while preserving the tool-call ergonomics.

    Returns:
        The session as a JSON string or Markdown document.
    """
    if messages is None:
        messages = get_current_session_messages()
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
