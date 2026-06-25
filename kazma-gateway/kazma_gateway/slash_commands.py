"""Slash command router — resolves common commands without LLM calls.

Commands are matched by prefix in the dispatcher before the message
ever reaches the agent.  This keeps responses instant (<50ms) and
saves tokens.

Registered commands:
  /help     — list available commands
  /reset    — clear conversation history
  /status   — return gateway health overview
  /model    — show active model
  /memory   — toggle or report memory stats
  /cost     — show token spend for this session
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def is_slash_command(text: str) -> bool:
    """Check if a message is a slash command."""
    return text.startswith("/") and len(text) > 1


def resolve_slash_command(text: str, context: dict[str, Any] | None = None) -> str | None:
    """Resolve a slash command to a response string.

    Args:
        text: The raw message text (e.g. "/help", "/reset").
        context: Optional dict with session data (token_count, model, etc.).

    Returns:
        Response string if the command is recognised, None otherwise.
    """
    cmd = text.strip().lower().split()[0]
    ctx = context or {}

    if cmd == "/help":
        return _cmd_help()
    if cmd == "/reset":
        return _cmd_reset()
    if cmd == "/status":
        return _cmd_status(ctx)
    if cmd == "/model":
        return _cmd_model(ctx)
    if cmd == "/memory":
        return _cmd_memory(ctx)
    if cmd == "/cost":
        return _cmd_cost(ctx)

    return None  # not a recognised command → passed to LLM


def _cmd_help() -> str:
    return (
        "*Available commands:*\n\n"
        "• `/help` — Show this list\n"
        "• `/reset` — Clear conversation history\n"
        "• `/status` — Gateway health overview\n"
        "• `/model` — Show active model\n"
        "• `/memory` — Report memory usage\n"
        "• `/cost` — Token spend this session\n\n"
        "For anything else, just ask the agent directly!"
    )


def _cmd_reset() -> str:
    return "🔄 Conversation has been reset. Starting fresh."


def _cmd_status(ctx: dict[str, Any]) -> str:
    parts = ["*Gateway Status*"]
    if ctx.get("started"):
        parts.append("● Gateway: **running**")
    else:
        parts.append("○ Gateway: **stopped**")
    parts.append(f"• Adapters: `{ctx.get('adapters', '?')}`")
    parts.append(f"• Queue depth: `{ctx.get('queue_depth', '?')}`")
    parts.append(f"• Active threads: `{ctx.get('active_threads', '?')}`")
    return "\n".join(parts)


def _cmd_model(ctx: dict[str, Any]) -> str:
    model = ctx.get("model", "default")
    return f"🧠 Active model: **{model}**"


def _cmd_memory(ctx: dict[str, Any]) -> str:
    count = ctx.get("memory_count", "?")
    return f"💾 Memory: `{count}` stored facts."


def _cmd_cost(ctx: dict[str, Any]) -> str:
    tokens = ctx.get("total_tokens", 0)
    cost = ctx.get("total_cost", 0.0)
    return f"💰 Session cost: `${cost:.4f}` ({tokens} tokens)"
