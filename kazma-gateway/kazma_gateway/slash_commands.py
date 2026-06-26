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
  /config       — interactive config wizard (show, model, personality, memory, tools, export)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ── Config path / cache ──────────────────────────────────────────────

_CONFIG_PATH: Path | None = None
_CONFIG_CACHE: dict[str, Any] | None = None
_CONFIG_CACHE_MTIME: float = 0.0


def _get_config_path() -> Path:
    global _CONFIG_PATH
    if _CONFIG_PATH is not None:
        return _CONFIG_PATH
    # Walk up from this file to find kazma.yaml in repo root
    p = Path(__file__).resolve().parent
    while p != p.parent:
        candidate = p / "kazma.yaml"
        if candidate.exists():
            _CONFIG_PATH = candidate
            return candidate
        p = p.parent
    raise FileNotFoundError("kazma.yaml not found")


def _load_config() -> dict[str, Any]:
    global _CONFIG_CACHE, _CONFIG_CACHE_MTIME
    path = _get_config_path()
    mtime = path.stat().st_mtime
    if _CONFIG_CACHE is not None and mtime == _CONFIG_CACHE_MTIME:
        return _CONFIG_CACHE
    with open(path, encoding="utf-8") as f:
        _CONFIG_CACHE = yaml.safe_load(f) or {}
    _CONFIG_CACHE_MTIME = mtime
    return _CONFIG_CACHE


def _save_config(config: dict[str, Any]) -> None:
    global _CONFIG_CACHE_MTIME
    path = _get_config_path()
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    _CONFIG_CACHE_MTIME = path.stat().st_mtime

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
    if cmd == "/config":
        return _cmd_config(text, ctx)

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
        "⚙️ *Config*\n"
        "• `/config show` — Display current configuration\n"
        "• `/config model <name>` — Switch model\n"
        "• `/config personality <name>` — Switch personality\n"
        "• `/config memory on|off` — Toggle memory\n"
        "• `/config tools list` — Show configured tools\n"
        "• `/config tools toggle <name>` — Enable/disable a tool\n"
        "• `/config export` — Export config as JSON\n\n"
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


# ── Config Wizard ──────────────────────────────────────────────────────


def _cmd_config(text: str, ctx: dict[str, Any]) -> str:
    """Handle /config sub-commands.

    Sub-commands:
        /config show                        — display current config table
        /config model <name>                — switch model
        /config personality <name>          — switch personality (delegates)
        /config memory on|off               — toggle memory
        /config tools list                  — show enabled tools
        /config tools toggle <name>         — enable/disable a tool
        /config export                      — export config as JSON
    """
    parts = text.strip().split()
    sub = parts[1].lower() if len(parts) > 1 else "show"

    if sub in ("show", ""):
        return _config_show(ctx)
    if sub == "model":
        return _config_model(parts, ctx)
    if sub == "personality":
        return _config_personality(parts)
    if sub == "memory":
        return _config_memory(parts)
    if sub == "tools":
        return _config_tools(parts, ctx)
    if sub == "export":
        return _config_export()
    return _config_usage()


def _config_show(ctx: dict[str, Any]) -> str:
    """Format current config as a table."""
    config = _load_config()
    model = _resolve_current_model(config, ctx)
    personality = _resolve_personality(config)
    memory_enabled = config.get("memory", {}).get("enabled", True)
    tools = _list_tool_names(config)

    lines = [
        "⚙️ *Current Configuration*",
        "",
        "```",
        f"{'Setting':<20} {'Value':<30}",
        f"{'───────':<20} {'─────':<30}",
        f"{'Model':<20} {model:<30}",
        f"{'Personality':<20} {personality:<30}",
        f"{'Memory':<20} {'enabled' if memory_enabled else 'disabled':<30}",
        f"{'Tools':<20} {', '.join(tools) if tools else '(none)':<30}",
        "```",
    ]
    return "\n".join(lines)


def _config_model(parts: list, ctx: dict[str, Any]) -> str:
    """Switch the active model."""
    if len(parts) < 3:
        current = _resolve_current_model(_load_config(), ctx)
        return f"🧠 Current model: **{current}**\n\nUsage: `/config model <name>`"

    model_name = parts[2].lower()
    # Persist to kazma.yaml
    try:
        config = _load_config()
        config.setdefault("models", {})["default"] = model_name
        config.setdefault("llm", {})["model"] = model_name
        _save_config(config)
        return f"✅ Switched to **{model_name}**.  Restart or reload for the change to take full effect."
    except Exception as exc:
        logger.warning("[slash] /config model save failed: %s", exc)
        return f"✅ Switched to **{model_name}** _(config write skipped: {exc})_"


def _config_personality(parts: list) -> str:
    """Delegate personality switching to kazma-core."""
    # Reconstruct the equivalent /personality command
    if len(parts) < 3:
        sub_text = "/personality"
    else:
        sub_text = f"/personality {parts[2]}"
    try:
        from kazma_core.tools.personality_cmd import handle_personality_command
        return handle_personality_command(sub_text)
    except ImportError:
        logger.info("[slash] kazma_core.tools.personality_cmd not available")
        return "🎭 Personality switching is handled by the agent. Try `/personality` directly."


def _config_memory(parts: list) -> str:
    """Toggle memory on/off."""
    if len(parts) < 3 or parts[2].lower() not in ("on", "off"):
        config = _load_config()
        state = "enabled" if config.get("memory", {}).get("enabled", True) else "disabled"
        return f"💾 Memory is currently **{state}**.\n\nUsage: `/config memory on` or `/config memory off`"

    toggle = parts[2].lower()
    try:
        config = _load_config()
        config.setdefault("memory", {})["enabled"] = (toggle == "on")
        _save_config(config)
        return f"💾 Memory **{toggle.upper()}**.  Restart for the change to take full effect."
    except Exception as exc:
        logger.warning("[slash] /config memory save failed: %s", exc)
        return f"💾 Memory **{toggle.upper()}** _(config write skipped: {exc})_"


def _config_tools(parts: list, ctx: dict[str, Any]) -> str:
    """Handle /config tools sub-commands."""
    if len(parts) < 3:
        return _config_usage()

    action = parts[2].lower()
    config = _load_config()

    if action == "list":
        tools = _list_tool_names(config)
        if not tools:
            return "🔧 No tools configured.\n\nAdd tools to `mcp.servers` in kazma.yaml."
        lines = ["🔧 *Configured Tools:*", ""]
        for t in tools:
            line = f"• `{t}`"
            if _tool_is_disabled(config, t):
                line += " _(disabled)_"
            lines.append(line)
        return "\n".join(lines)

    if action == "toggle" and len(parts) >= 4:
        tool_name = parts[3].lower()
        tools = _list_tool_names(config)
        if tool_name not in [t.lower() for t in tools]:
            available = ", ".join(tools) if tools else "(none)"
            return f"❌ Unknown tool: `{tool_name}`\n\nAvailable: {available}"
        try:
            was_enabled = not _tool_is_disabled(config, tool_name)
            _toggle_tool(config, tool_name, not was_enabled)
            _save_config(config)
            new_state = "enabled" if not was_enabled else "disabled"
            return f"🔧 Tool `{tool_name}` **{new_state}**."
        except Exception as exc:
            logger.warning("[slash] /config tools toggle save failed: %s", exc)
            return f"🔧 Tool `{tool_name}` toggled _(config write skipped: {exc})_"

    return "Usage: `/config tools list` or `/config tools toggle <name>`"


def _config_export() -> str:
    """Export current config as JSON."""
    try:
        config = _load_config()
        # Redact sensitive keys
        safe = dict(config)
        if "llm" in safe and "api_key" in safe["llm"]:
            safe["llm"]["api_key"] = "***REDACTED***"
        for conn in safe.get("connectors", {}).values():
            if isinstance(conn, dict) and "token" in conn:
                conn["token"] = "***REDACTED***"
        return f"```json\n{json.dumps(safe, indent=2, ensure_ascii=False)}\n```"
    except Exception as exc:
        logger.warning("[slash] /config export failed: %s", exc)
        return f"⚠️ Could not export config: {exc}"


def _config_usage() -> str:
    return (
        "⚙️ *Config Wizard — available sub-commands:*\n\n"
        "• `/config show` — Display current configuration\n"
        "• `/config model <name>` — Switch model\n"
        "• `/config personality <name>` — Switch personality\n"
        "• `/config memory on|off` — Toggle memory\n"
        "• `/config tools list` — Show configured tools\n"
        "• `/config tools toggle <name>` — Enable/disable a tool\n"
        "• `/config export` — Export config as JSON"
    )


# ── Config helpers ────────────────────────────────────────────────────


def _resolve_current_model(config: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Resolve the current model from ctx or config."""
    return ctx.get("model") or config.get("llm", {}).get("model") or config.get("models", {}).get("default", "unknown")


def _resolve_personality(config: dict[str, Any]) -> str:
    """Resolve the current personality name."""
    try:
        from kazma_core.personalities import get_current_personality
        return get_current_personality(config=config).name
    except ImportError:
        return config.get("agent", {}).get("personality", "default")


def _list_tool_names(config: dict[str, Any]) -> list[str]:
    """List tool/server names from MCP config."""
    servers = config.get("mcp", {}).get("servers", [])
    if not servers:
        return []
    names: list[str] = []
    for s in servers:
        if isinstance(s, dict):
            name = s.get("name", "unnamed")
            names.append(name)
    return names


def _tool_is_disabled(config: dict[str, Any], tool_name: str) -> bool:
    """Check if a tool is explicitly disabled."""
    disabled: list[str] = config.get("mcp", {}).get("disabled_servers", [])
    return tool_name.lower() in [d.lower() for d in disabled]


def _toggle_tool(config: dict[str, Any], tool_name: str, enable: bool) -> None:
    """Toggle a tool in the disabled_servers list."""
    disabled: list[str] = config.setdefault("mcp", {}).setdefault("disabled_servers", [])
    if enable:
        # Remove from disabled list
        config["mcp"]["disabled_servers"] = [d for d in disabled if d.lower() != tool_name.lower()]
    else:
        # Add to disabled list if not present
        if tool_name.lower() not in [d.lower() for d in disabled]:
            disabled.append(tool_name)
