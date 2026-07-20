"""Slash command router — resolves common commands without LLM calls.
agent.  This keeps responses instant (<50ms) and saves tokens.

Registered commands:
  /help         — list available commands grouped by category
  /reset        — clear conversation history
  /status       — return gateway health overview
  /model        — show active model
  /memory       — report memory stats
  /cost         — show token spend for this session
  /replay       — time travel: list snapshots, replay, or compare
  /personality  — show, list, or switch agent personality (core tool)
  /context      — context window token usage report (core tool)
  /config       — interactive config wizard (show, model, personality, memory, tools, export)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

__all__ = [
    "is_slash_command",
    "resolve_slash_command",
]

# ── Config path / store ──────────────────────────────────────────────
#
# kazma.yaml is treated as a READ-ONLY bootstrap.  All runtime config
# mutations (slash commands, settings page, connector token updates) are
# routed through ``ConfigStore.set()`` which serializes every write with a
# ``threading.Lock`` and persists to the SQLite override DB.  This fixes
# the write-race that previously allowed concurrent slash commands to
# truncate or partially overwrite kazma.yaml (VAL-CRIT-006 / VAL-CRIT-007).

_CONFIG_PATH: Path | None = None


def _get_config_path() -> Path:
    """Locate the read-only ``kazma.yaml`` bootstrap file (cached)."""
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


def _get_config_store() -> Any:
    """Return the shared ``ConfigStore`` (locked SQLite settings store).

    Lazily imported to avoid a hard gateway -> core import at module load.
    Tests may monkeypatch this attribute (``slash_commands._get_config_store``)
    to inject an isolated store.
    """
    from kazma_core.config_store import get_config_store

    return get_config_store()


def _read_bootstrap_yaml() -> dict[str, Any]:
    """Read shipped ``kazma.yaml`` + optional ``kazma.local.yaml`` (no caching)."""
    try:
        from kazma_core.config_loader import load_merged_yaml

        path = _get_config_path()
        return load_merged_yaml(path)
    except Exception:
        path = _get_config_path()
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}


def _apply_overrides(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Deeply merge ``overrides`` (dotted-key -> value) into a copy of ``base``."""
    result: dict[str, Any] = {k: v for k, v in base.items()}
    for dotted_key, value in overrides.items():
        parts = dotted_key.split(".")
        target = result
        for part in parts[:-1]:
            existing = target.get(part)
            if not isinstance(existing, dict):
                existing = {}
                target[part] = existing
            target = existing
        target[parts[-1]] = value
    return result


def _load_config() -> dict[str, Any]:
    """Return the effective config: bootstrap YAML overridden by ConfigStore DB values.

    kazma.yaml is treated as read-only.  Runtime mutations made via
    ``_save_config`` (which delegates to ``ConfigStore.set``) are merged on
    top of the bootstrap so subsequent reads reflect the latest changes.
    """
    base = _read_bootstrap_yaml()
    try:
        store = _get_config_store()
    except Exception as exc:  # pragma: no cover - defensive: fall back to YAML
        logger.warning("[slash] ConfigStore unavailable, using YAML only: %s", exc)
        return base

    grouped = store.get_all()
    overrides: dict[str, Any] = {}
    for settings in grouped.values():
        overrides.update(settings)
    return _apply_overrides(base, overrides)


def _save_config(config: dict[str, Any]) -> None:
    """Persist ``config`` through the locked ``ConfigStore``.

    Walks the (possibly nested) dict and writes all leaf scalars to the
    SQLite override DB **atomically** via ``batch_set()``. kazma.yaml is
    never opened for writing at runtime.
    """
    store = _get_config_store()
    items: list[tuple[str, Any, str]] = []

    def _flatten(data: dict[str, Any], prefix: str = "") -> None:
        for key, value in data.items():
            full_key = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                _flatten(value, full_key)
            else:
                category = prefix.split(".")[0] if prefix else "general"
                items.append((full_key, value, category))

    _flatten(config)
    store.batch_set(items)

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

    Returns:
        Response string if the command is recognised, None otherwise.
    """
    cmd = text.strip().lower().split()[0]
    ctx = context or {}

    if cmd == "/help":
        return _cmd_help()
    if cmd == "/reset":
        return None  # Handled by agent_handler directly (clears state)
    if cmd == "/new":
        return None  # Handled by agent_handler directly (creates new session)
    if cmd == "/compact":
        return None  # Handled by agent_handler directly (manual context compaction)
    if cmd == "/status":
        return _cmd_status(ctx)
    if cmd in ("/model", "/models"):
        return None  # Handled by agent_handler directly (interactive selector)
    if cmd == "/memory":
        return _cmd_memory(ctx)
    if cmd == "/cost":
        return _cmd_cost(ctx)
    if cmd == "/replay":
        return _cmd_replay(text, ctx)
    if cmd == "/config":
        return _cmd_config(text, ctx)
    if cmd == "/personality":
        return _cmd_config(f"/config {text}", ctx)
    if cmd == "/context":
        return _cmd_context(ctx)
    if cmd == "/undo":
        return _cmd_undo()
    if cmd == "/edit":
        return _cmd_edit(text)

    return None  # not a recognised command → passed to LLM


def _cmd_help() -> str:
    return (
        "*Available commands:*\n\n"
        "🔄 *Session*\n"
        "• `/new` — Create a brand new session/season\n"
        "• `/reset` — Clear conversation history and starting fresh\n"
        "• `/compact` — Manually trigger context window compaction\n"
        "• `/replay list` — Show available snapshots\n"
        "• `/replay <iteration>` — Replay from iteration\n"
        "• `/replay compare <a> <b>` — Compare two runs\n"
        "• `/replay clear` — Clear snapshots for this thread\n\n"
        "🔧 *Tools*\n"
        "• `/personality` — Show current personality\n"
        "• `/personality list` — List all available personalities\n"
        "• `/personality <name>` — Switch personality\n"
        "• `/context` — Show context window usage\n"
        "• `/skill list` — List installed Agent Skills\n"
        "• `/skill install <owner/repo>` — Install from GitHub (agentskills.io)\n"
        "• `/skill activate <name>` — Arm a skill for this chat\n"
        "• `/skill deactivate` — Clear the active skill\n"
        "• `/skill uninstall <name>` — Remove an Agent Skill\n\n"
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


def _cmd_context(ctx: dict[str, Any]) -> str:
    """Show context window token usage."""
    token_count = ctx.get("token_count", 0)
    max_tokens = ctx.get("max_tokens", 128000)
    model = ctx.get("model", "unknown")
    pct = (token_count / max_tokens * 100) if max_tokens else 0
    bar_len = 20
    filled = int(bar_len * pct / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    return (
        f"📊 *Context Window*\n\n"
        f"Model: `{model}`\n"
        f"Tokens: `{token_count:,}` / `{max_tokens:,}` ({pct:.1f}%)\n"
        f"[{bar}]\n\n"
        f"Compaction triggers at 80% usage."
    )


def _cmd_undo() -> str:
    """Fallback when /undo is not handled by agent_handler (no graph)."""
    return (
        "↩️ *Undo* is handled by the live agent session.\n\n"
        "Send `/undo` on Telegram/Discord/Slack while chatting with the "
        "agent — it removes the last assistant turn from checkpoint state.\n\n"
        "If you see this message, the graph handler is not wired for this "
        "channel. Use `/reset` or start a new session instead."
    )


def _cmd_edit(text: str) -> str:
    """Fallback when /edit is not handled by agent_handler (no graph)."""
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return (
            "✏️ *Usage:* `/edit <corrected text>`\n\n"
            "On live chat platforms this replaces the last assistant "
            "message in the conversation checkpoint."
        )
    return (
        "✏️ *Edit* is handled by the live agent session.\n\n"
        f"Correction received (not applied here): {parts[1][:200]}\n\n"
        "Send `/edit <text>` in Telegram/Discord/Slack while chatting "
        "with the agent so the graph handler can update the checkpoint."
    )


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

    model_name = parts[2]  # Preserve case — model names are case-sensitive
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
        # Deep-redact sensitive keys at all nesting levels
        safe = _redact_secrets(config)
        return f"```json\n{json.dumps(safe, indent=2, ensure_ascii=False)}\n```"
    except Exception as exc:
        logger.warning("[slash] /config export failed: %s", exc)
        return f"⚠️ Could not export config: {exc}"


_REDACT_KEYS = {"api_key", "token", "secret", "password", "stt_api_key", "bot_token", "app_token"}


def _redact_secrets(obj: Any) -> Any:
    """Recursively redact sensitive keys in a nested dict/list."""
    if isinstance(obj, dict):
        return {
            k: ("***REDACTED***" if k.lower() in _REDACT_KEYS and v else _redact_secrets(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_secrets(item) for item in obj]
    return obj


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
