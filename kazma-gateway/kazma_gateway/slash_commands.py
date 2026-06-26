"""Slash command router — resolves common commands without LLM calls.

Commands are matched by prefix in the dispatcher before the message
ever reaches the agent.  This keeps responses instant (<50ms) and
saves tokens.

Registered commands:
  /help         — list available commands grouped by category
  /reset        — clear conversation history
  /status       — return gateway health overview
  /model        — show active model
  /memory       — report memory stats
  /cost         — show token spend for this session
  /undo         — remove the last agent response from chat
  /edit         — edit the last agent response with corrected text
  /replay       — time travel: list snapshots, replay, or compare
  /personality  — show, list, or switch agent personality (core tool)
  /context      — context window token usage report (core tool)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Re-exported for dispatcher.py
CMD_UNDO = "/undo"
CMD_EDIT = "/edit"

# Lazy-loaded ReplayEngine — may not be available yet
_ReplayEngine = None
_replay_import_attempted = False


def _get_replay_engine():
    """Try to import ReplayEngine from kazma_core.time_travel.

    Returns the class if available, None otherwise.  Caches the result
    so the import is attempted only once per process.
    """
    global _ReplayEngine, _replay_import_attempted
    if _replay_import_attempted:
        return _ReplayEngine
    _replay_import_attempted = True
    try:
        from kazma_core.time_travel import ReplayEngine  # type: ignore[import-untyped]
        _ReplayEngine = ReplayEngine
    except ImportError:
        logger.info("[slash] kazma_core.time_travel not available — /replay will show fallback")
        _ReplayEngine = None
    return _ReplayEngine


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
    if cmd == "/replay":
        return _cmd_replay(text, ctx)

    return None  # not a recognised command → passed to LLM


def _cmd_help() -> str:
    return (
        "*Available commands:*\n\n"
        "🔄 *Session*\n"
        "• `/reset` — Clear conversation history\n"
        "• `/undo` — Remove last agent response\n"
        "• `/edit <text>` — Correct last agent response\n"
        "• `/replay list` — Show available snapshots\n"
        "• `/replay <iteration>` — Replay from iteration\n"
        "• `/replay compare <a> <b>` — Compare two runs\n"
        "• `/replay clear` — Clear snapshots for this thread\n\n"
        "🔧 *Tools*\n"
        "• `/personality` — Show current personality\n"
        "• `/personality list` — List all available personalities\n"
        "• `/personality <name>` — Switch personality\n"
        "• `/context` — Show context window usage\n\n"
        "ℹ️ *Info*\n"
        "• `/help` — Show this list\n"
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


# ── Replay / Time Travel ────────────────────────────────────────────


def _cmd_replay(text: str, ctx: dict[str, Any]) -> str:
    """Handle /replay commands for time-travel debugging.

    Sub-commands:
        /replay list               — show available snapshots
        /replay <iteration>        — replay from that iteration
        /replay compare <a> <b>    — compare two replay runs
        /replay clear              — clear snapshots for current thread

    Gracefully falls back if kazma_core.time_travel is not yet available.
    """
    engine_cls = _get_replay_engine()
    if engine_cls is None:
        return "⏳ Time travel not yet available."

    parts = text.strip().split()
    # parts[0] is "/replay"
    sub = parts[1] if len(parts) > 1 else ""

    thread_id = ctx.get("thread_id", "default")

    if sub == "list" or sub == "":
        return _replay_list(engine_cls, thread_id)
    if sub == "compare":
        return _replay_compare(engine_cls, parts, thread_id)
    if sub == "clear":
        return _replay_clear(engine_cls, thread_id)
    # Otherwise treat as an iteration number
    return _replay_iteration(engine_cls, sub, thread_id)


def _replay_list(engine_cls, thread_id: str) -> str:
    """List available snapshots for a thread."""
    try:
        engine = engine_cls(thread_id=thread_id)
        snapshots = engine.list_snapshots()
    except Exception as exc:
        logger.warning("[slash] /replay list failed: %s", exc)
        return f"⚠️ Could not list snapshots: {exc}"

    if not snapshots:
        return "📭 No snapshots available for this thread."

    lines = ["🕰️ *Available snapshots:*\n"]
    for snap in snapshots:
        it = snap.get("iteration", "?")
        ts = snap.get("timestamp", "?")
        desc = snap.get("description", "")
        entry = f"• Iteration `{it}` — {ts}"
        if desc:
            entry += f" — {desc}"
        lines.append(entry)
    return "\n".join(lines)


def _replay_iteration(engine_cls, iteration_str: str, thread_id: str) -> str:
    """Replay from a specific iteration."""
    try:
        iteration = int(iteration_str)
    except (ValueError, TypeError):
        return f"⚠️ Invalid iteration: `{iteration_str}`. Use a number (e.g. `/replay 3`)."

    try:
        engine = engine_cls(thread_id=thread_id)
        result = engine.replay(iteration)
    except Exception as exc:
        logger.warning("[slash] /replay iteration %s failed: %s", iteration, exc)
        return f"⚠️ Could not replay iteration `{iteration}`: {exc}"

    if result is None:
        return f"📭 No snapshot found for iteration `{iteration}`."

    return f"🕰️ *Replay from iteration {iteration}:*\n\n{result}"


def _replay_compare(engine_cls, parts: list, thread_id: str) -> str:
    """Compare two replay runs."""
    if len(parts) < 4:
        return "⚠️ Usage: `/replay compare <a> <b>` — provide two iteration numbers."

    try:
        iter_a = int(parts[2])
        iter_b = int(parts[3])
    except (ValueError, TypeError):
        return "⚠️ Both iterations must be numbers (e.g. `/replay compare 1 3`)."

    try:
        engine = engine_cls(thread_id=thread_id)
        result = engine.compare(iter_a, iter_b)
    except Exception as exc:
        logger.warning("[slash] /replay compare %s vs %s failed: %s", iter_a, iter_b, exc)
        return f"⚠️ Could not compare iterations `{iter_a}` and `{iter_b}`: {exc}"

    if result is None:
        return f"📭 Could not compare iterations `{iter_a}` and `{iter_b}` — snapshots may be missing."

    return f"🕰️ *Comparison: iteration {iter_a} vs {iter_b}:*\n\n{result}"


def _replay_clear(engine_cls, thread_id: str) -> str:
    """Clear all snapshots for a thread."""
    try:
        engine = engine_cls(thread_id=thread_id)
        count = engine.clear_snapshots()
    except Exception as exc:
        logger.warning("[slash] /replay clear failed: %s", exc)
        return f"⚠️ Could not clear snapshots: {exc}"

    return f"🗑️ Cleared {count} snapshot(s) for this thread."
