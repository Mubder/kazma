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
  /undo     — remove the last agent response from chat
  /edit     — edit the last agent response with corrected text
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Re-exported for dispatcher.py
CMD_UNDO = "/undo"
CMD_EDIT = "/edit"


def is_slash_command(text: str) -> bool:
    """Check if a message is a slash command."""
    return text.startswith("/") and len(text) > 1


def resolve_slash_command(text: str, context: dict[str, Any] | None = None) -> str | None:
    """Resolve a slash command to a response string.

    Args:
        text: The raw message text (e.g. "/help", "/reset").
        context: Optional dict with session data (token_count, model, etc.).
                 For /undo and /edit, must contain:
                   - _dispatcher: MessageDispatcher instance
                   - _chat_id:    chat-scoped key (str)

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
    if cmd == CMD_UNDO:
        return _cmd_undo(ctx)
    if cmd == CMD_EDIT:
        return _cmd_edit(text, ctx)

    return None  # not a recognised command → passed to LLM


def _cmd_help() -> str:
    return (
        "*Available commands:*\n\n"
        "• `/help` — Show this list\n"
        "• `/reset` — Clear conversation history\n"
        "• `/status` — Gateway health overview\n"
        "• `/model` — Show active model\n"
        "• `/memory` — Report memory usage\n"
        "• `/cost` — Token spend this session\n"
        "• `/undo` — Remove last agent response\n"
        "• `/edit <text>` — Correct last agent response\n\n"
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


# ── Undo / Edit ───────────────────────────────────────────────────────


def _cmd_undo(ctx: dict[str, Any]) -> str:
    """Remove the last agent response from the chat.

    Pops the last exchange from the dispatcher's message tracker.
    The caller (dispatcher) is responsible for issuing the platform-level
    deleteMessage call if the adapter supports it.

    Args:
        ctx: Must contain ``_dispatcher`` (MessageDispatcher) and
             ``_chat_id`` (str).

    Returns:
        Confirmation or error message.
    """
    dispatcher = ctx.get("_dispatcher")
    chat_id = ctx.get("_chat_id", "")

    if dispatcher is None:
        logger.warning("[slash] /undo called without _dispatcher in context")
        return "⚠️ Undo not available right now."

    pair = dispatcher.undo_last(chat_id)
    if pair is None:
        return "📭 Nothing to undo — no recent responses."

    user_msg_id, bot_tracking_id = pair
    logger.info(
        "[slash] /undo popped exchange: user_msg=%s bot_track=%s (chat=%s)",
        user_msg_id,
        bot_tracking_id,
        chat_id,
    )
    return "🔄 Last response removed."


def _cmd_edit(text: str, ctx: dict[str, Any]) -> str:
    """Edit the last agent response.

    Usage: /edit <corrected text>

    Pops the last bot response and replaces it with the new text.
    The caller (dispatcher) is responsible for issuing the platform-level
    editMessageText call if the adapter supports it.

    Args:
        text:    The full command text (e.g. "/edit fixed response here").
        ctx:     Must contain ``_dispatcher`` (MessageDispatcher) and
                 ``_chat_id`` (str).

    Returns:
        Confirmation with the new text, or error message.
    """
    dispatcher = ctx.get("_dispatcher")
    chat_id = ctx.get("_chat_id", "")

    if dispatcher is None:
        logger.warning("[slash] /edit called without _dispatcher in context")
        return "⚠️ Edit not available right now."

    # Extract the new text after "/edit "
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return "✏️ Usage: `/edit <corrected text>` — provide the new text."

    new_text = parts[1].strip()

    pair = dispatcher.undo_last(chat_id)
    if pair is None:
        return "📭 Nothing to edit — no recent responses."

    user_msg_id, bot_tracking_id = pair
    logger.info(
        "[slash] /edit popped exchange: user_msg=%s bot_track=%s → new_text=%.80s (chat=%s)",
        user_msg_id,
        bot_tracking_id,
        new_text,
        chat_id,
    )
    return f"✏️ Last response edited to:\n\n{new_text}"
