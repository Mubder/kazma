"""Slash command router — resolves common commands without LLM calls.
agent.  This keeps responses instant (<50ms) and saves tokens.

Resolved here (every other command is handled by the gateway's handler
before this resolver runs, or returns None from it):
  /help         — list available commands grouped by category
  /status       — the gateway: each chat app's connection, the queue, messages in progress
  /memory       — report memory stats; /memory off|on — keep this chat out of memory
  /cost         — this chat's model calls: tokens and cost, from the per-call ledger
  /replay       — time travel: list snapshots, replay, or compare
  /personality  — show, list, or switch agent personality (core tool)
  /context      — how much of the model's window this chat fills (core tool)
  /config       — interactive config wizard (show, model, personality, memory, tools, export)

What a command reports comes from ``agent_handler.commands._build_slash_ctx``,
which reads each fact from where it lives; ``tests/test_gateway_slash_facts.py``
fails on a key a command reads that nothing fills, or fills with a constant.
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
    "BOT_MENU_COMMANDS",
    "changes_global_config",
    "is_slash_command",
    "resolve_slash_command",
    "switch_model_from_chat",
]

# Telegram setMyCommands menu (and any other mouth that shows a "/" picker).
# Telegram only lists what we register here — handlers may accept more
# aliases, but if a command is missing from this list it will not appear
# next to the chat input. The menu and _cmd_help list the same commands
# (tests/test_slash_help_and_menu.py).
# Constraints: command 1–32 [a-z0-9_]; description 3–256 chars; max 100.
BOT_MENU_COMMANDS: list[dict[str, str]] = [
    {"command": "help", "description": "Show available commands"},
    {"command": "sessions", "description": "List every season (Web + Telegram + Discord + Slack)"},
    {"command": "seasons", "description": "List every season (alias of /sessions)"},
    {"command": "session", "description": "Switch onto a season (#, id, or name)"},
    {"command": "season", "description": "Switch onto a season (alias of /session)"},
    {"command": "switch", "description": "Take over a season (same as /session)"},
    {"command": "new", "description": "Create a brand new session/season"},
    {"command": "reset", "description": "Clear conversation history"},
    {"command": "compact", "description": "Manually trigger context compaction"},
    {"command": "research", "description": "Deep research via the same agent"},
    {"command": "swarm", "description": "Swarm orchestration"},
    {"command": "ide", "description": "IDE: files, git, coding skills"},
    {
        "command": "skill",
        "description": "Agent Skills: list / install / activate (agentskills.io)",
    },
    {"command": "documents", "description": "Document Intelligence: list / read / search"},
    {"command": "docs", "description": "Documents (alias of /documents)"},
    {"command": "kb", "description": "Knowledge library: list / crawl / search"},
    {"command": "long", "description": "Long-task mode on/off (deep audits)"},
    {"command": "mission", "description": "Mission-length budget for this chat"},
    {"command": "yolo", "description": "Toggle session YOLO safety bypass"},
    {"command": "unrestricted", "description": "Mission budget + YOLO for this chat"},
    {"command": "plan", "description": "Plan mode: inspect and propose before acting"},
    {"command": "steer", "description": "Add context to the running task"},
    {"command": "abort", "description": "Stop and abandon the running task"},
    {"command": "replay", "description": "Time travel snapshots"},
    {"command": "fork", "description": "Fork from a snapshot into a new thread"},
    {"command": "undo", "description": "Undo last response"},
    {"command": "edit", "description": "Edit last response"},
    {"command": "config", "description": "Configuration wizard"},
    {"command": "personality", "description": "Agent personality"},
    {"command": "model", "description": "Show / switch active model"},
    {"command": "models", "description": "Show / switch active model (alias of /model)"},
    {"command": "context", "description": "Context window usage"},
    {"command": "status", "description": "Gateway health overview"},
    {"command": "memory", "description": "Report memory usage"},
    {"command": "cost", "description": "Tokens and cost of this chat"},
    {"command": "hitl", "description": "Approve or deny a pending HITL tool"},
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

    kazma.yaml is treated as read-only here. What ``/config`` changes is
    written one key at a time to the settings store (never the whole
    merged configuration back) and read here on top of the bootstrap.
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


# Lazy-loaded time-travel components — may not be available if time_travel
# module is absent. Cached so the import is attempted only once per process.
_replay_recorder = None
_replay_engine = None
_replay_import_attempted = False


def _get_replay_components():
    """Try to import SnapshotRecorder + ReplayEngine from kazma_core.time_travel.

    Returns ``(recorder, engine)`` instances if available, ``(None, None)``
    otherwise. Caches the result so the import is attempted only once.
    """
    global _replay_recorder, _replay_engine, _replay_import_attempted
    if _replay_import_attempted:
        return _replay_recorder, _replay_engine
    _replay_import_attempted = True
    try:
        from kazma_core.time_travel import ReplayEngine, create_recorder
        _replay_recorder = create_recorder()
        _replay_engine = ReplayEngine(_replay_recorder)
    except ImportError:
        logger.info("[slash] kazma_core.time_travel not available — /replay will show fallback")
        _replay_recorder = None
        _replay_engine = None
    return _replay_recorder, _replay_engine


def is_slash_command(text: str) -> bool:
    """Check if a message is a slash command."""
    return text.startswith("/") and len(text) > 1


def changes_global_config(text: str) -> bool:
    """Whether a command changes what every user and platform gets, and so
    needs a gateway admin (audit H-8; ``allowlists.is_gateway_admin``): with
    an empty allowlist anyone in a chat may talk to Kazma, and none of them
    may install or remove code, change the model or the configuration.

    ``/config model``, ``memory`` and ``tools`` are gated whole, as before.
    Until 2026-10-01 the rest of the class was open to any chat member: the
    personality switch (every reply, web chats included), ``/skill
    uninstall``, switching or cloning the active workspace (``/ide repo``) and
    the swarm's output routing (``/swarm config``). Showing and listing stay
    open. ``graph.py`` asks this before every slash command; the ``/model``
    menu's pick (``/_models_select``) is handled before that and checks the
    same admin rule itself.
    """
    parts = (text or "").strip().split()
    head = parts[0].lower().split("@", 1)[0] if parts else ""
    sub = parts[1].lower() if len(parts) > 1 else ""
    arg = parts[2].lower() if len(parts) > 2 else ""
    if head == "/config":
        return sub in ("model", "memory", "tools") or (sub == "personality" and bool(arg))
    if head == "/personality":
        return bool(sub) and sub != "list"
    if head == "/skill":
        return sub in ("install", "add", "uninstall", "remove", "rm")
    if head == "/ide":
        return sub == "repo" and (arg in ("switch", "clone") or "/" in arg)
    if head == "/swarm":
        return sub == "config" and arg in ("group", "clear")
    return head == "/_models_select"


def resolve_slash_command(text: str, context: dict[str, Any] | None = None) -> str | None:
    """Resolve a slash command to a response string.

    Args:
        text: The raw message text (e.g. "/help", "/reset").
        context: Optional dict with session data (token_count, model, etc.).

    Returns:
        Response string if the command is recognised, None otherwise.
    """
    cmd = text.strip().lower().split()[0]
    if "@" in cmd:
        cmd = cmd.split("@", 1)[0]
    ctx = context or {}

    if cmd == "/help":
        return _cmd_help()
    if cmd == "/reset":
        return None  # Handled by agent_handler directly (clears state)
    if cmd == "/new":
        return None  # Handled by session_commands (mint a season)
    if cmd in ("/sessions", "/seasons", "/session", "/season", "/switch"):
        return None  # Handled by session_commands (list / take-over)
    if cmd == "/compact":
        return None  # Handled by agent_handler directly (manual context compaction)
    if cmd == "/status":
        return _cmd_status(ctx)
    if cmd in ("/model", "/models"):
        return None  # Handled by agent_handler directly (interactive selector)
    if cmd == "/memory":
        return _cmd_memory(text, ctx)
    if cmd == "/cost":
        return _cmd_cost(ctx)
    if cmd == "/replay":
        return _cmd_replay(text, ctx)
    if cmd == "/config":
        return _cmd_config(text, ctx)
    if cmd == "/personality":
        return _cmd_config(f"/config {text}", ctx)
    if cmd == "/context":
        return _cmd_context(text, ctx)

    # NOTE: /undo, /edit, /fork, /replay <n>, /steer, /steer!, and /abort
    # are intentionally NOT handled here. They target a running turn or
    # mutate LangGraph checkpoint state and are resolved by the graph
    # handler in agent_handler/graph.py (_handle_undo / _handle_edit /
    # _handle_replay / _handle_fork, and the /steer+/abort intercepts)
    # before this resolver runs. Returning None lets them fall through to
    # the graph on live platforms, and to the LLM otherwise.

    return None  # not a recognised command → passed to LLM


def _cmd_help() -> str:
    return (
        "*Available commands:*\n\n"
        "🔄 *Session*\n"
        "• `/sessions` (`/seasons`) — List every season (Web + Telegram + Discord + Slack)\n"
        "• `/session 2` (`/season`, `/switch`) — Continue that season here (take over)\n"
        "• `/session new [name]` — Start a fresh season (`/new` still works)\n"
        "• `/research deep <topic>` — deep research via the same agent\n"
        "• `/swarm <task>` — dispatch workers via the same agent\n"
        "• `/reset` — Clear the conversation history and start fresh\n"
        "• `/compact` — Manually trigger context window compaction\n"
        "• `/undo` — Remove the last reply from the conversation\n"
        "• `/edit <text>` — Replace the last reply with your corrected text\n"
        "• `/replay list` — Show available snapshots\n"
        "• `/replay <iteration>` — Restore from iteration (rewinds in-place)\n"
        "• `/replay compare <a> <b>` — Compare two snapshots\n"
        "• `/replay clear` — Clear snapshots for this thread\n"
        "• `/fork <iteration>` — Fork from iteration into a new thread\n\n"
        "🧭 *Running task*\n"
        "• `/steer <text>` — Add context to the running task (applies next step)\n"
        "• `/steer! <text>` — Pause the task, inject a requirement, then resume\n"
        "• `/abort` — Stop and abandon the running task (won't continue unless re-asked)\n"
        "• `/hitl approve` · `/hitl deny` — Answer a pending approval "
        "(`/hitl approve_task` — every approval of this task)\n\n"
        "⚡ *Capacity & YOLO*\n"
        "• `/long on` · `/long mission` (`/mission`) — raise tool-round budget (HITL stays on)\n"
        "• `/yolo` — skip danger-tool approvals (does **not** raise the budget)\n"
        "• `/long yolo` — research budget **and** YOLO\n"
        "• `/unrestricted` — mission + YOLO (full power this chat)\n"
        "• `/long off` · `/yolo off` · `/unrestricted off`\n\n"
        "📋 *Plan mode*\n"
        "• `/plan on` · `/plan <task>` — inspect and propose (write/exec blocked)\n"
        "• `/plan go` · **Proceed** — approve and execute (HITL still on)\n"
        "• `/plan off` · `/plan status`\n\n"
        "🔧 *Tools*\n"
        "• `/personality` — Show current personality\n"
        "• `/personality list` — List all available personalities\n"
        "• `/personality <name>` — Switch personality\n"
        "• `/context` — Show context window usage\n"
        "• `/ide` — Workspace files, git and coding skills (`/ide help`)\n"
        "• `/kb` — Knowledge libraries: list, crawl, search (`/kb help`)\n"
        "• `/skill list` — List installed Agent Skills\n"
        "• `/skill install <owner/repo>` — Install from GitHub (agentskills.io)\n"
        "• `/skill activate <name>` — Arm a skill for this chat\n"
        "• `/skill deactivate` — Clear the active skill\n"
        "• `/skill uninstall <name>` — Remove an Agent Skill\n\n"
        "📄 *Documents*\n"
        "• `/documents list` (`/docs`) — List processed documents\n"
        "• `/documents status <id>` — Durable job state\n"
        "• `/documents read <id>` — Read a ready document\n"
        "• `/documents search <library> <query>` — Search indexed docs\n\n"
        "⚙️ *Config*\n"
        "• `/config show` — Display current configuration\n"
        "• `/config model <name>` — Switch model\n"
        "• `/config personality <name>` — Switch personality\n"
        "• `/config memory on|off` — Toggle memory\n"
        "• `/config tools list` — Show the MCP servers\n"
        "• `/config tools toggle <name>` — Turn an MCP server on or off (from the next start)\n"
        "• `/config export` — Export config as JSON\n\n"
        "ℹ️ *Info*\n"
        "• `/help` — Show this list\n"
        "• `/status` — Gateway health overview\n"
        "• `/model` (`/models`) — Show and switch the model\n"
        "• `/memory` — Report memory usage; `/memory off` / `/memory on` — keep this chat out of memory, or let it back in\n"
        "• `/cost` — Tokens and cost of this chat\n\n"
        "For anything else, just ask the agent directly!"
    )


def _cmd_context(text: str, ctx: dict[str, Any]) -> str:
    """``/context``: how much of the model's window this chat's saved
    conversation fills -- the ``context_info`` tool's report
    (``kazma_core.tools.context_cmd``); ``/context details`` adds the
    per-role breakdown. Until 2026-10-01 it measured the ``/context`` message
    itself against a fixed 128,000."""
    history = ctx.get("history")
    if not isinstance(history, list):
        return "📊 The size of this conversation could not be read."
    from kazma_core.tools.context_cmd import context_report

    parts = (text or "").strip().lower().split()
    detailed = len(parts) > 1 and parts[1] in ("details", "detail", "detailed")
    return context_report(history, detailed=detailed)


def _cmd_status(ctx: dict[str, Any]) -> str:
    """``/status``: the gateway as it is -- each chat app's line is what its
    connection says (``GatewayManager.connection_report``), then the queue
    and the other messages being handled. What could not be read shows as
    ``?``. Until 2026-10-01 these were constants: running, the asking
    platform as the only adapter, queue 0, one thread."""
    parts = ["*Gateway Status*"]
    started = ctx.get("started")
    if started is None:
        parts.append("? Gateway: **unknown**")
    elif started:
        parts.append("● Gateway: **running**")
    else:
        parts.append("○ Gateway: **stopped**")
    adapters = ctx.get("adapters")
    if not isinstance(adapters, list):
        parts.append("• Chat apps: `?`")
    elif not adapters:
        parts.append("• Chat apps: none configured")
    for row in adapters if isinstance(adapters, list) else []:
        line = f"• {row.get('name') or '?'}: `{row.get('state') or '?'}`"
        detail = str(row.get("detail") or "").strip()
        if detail:
            line += f" — {detail[:160]}"
        parts.append(line)
    parts.append(f"• Queue depth: `{ctx.get('queue_depth', '?')}`")
    parts.append(f"• Messages in progress: `{ctx.get('in_progress', '?')}`")
    return "\n".join(parts)


def _cmd_memory(text: str, ctx: dict[str, Any]) -> str:
    """``/memory``: the facts held and whether Kazma remembers this chat.
    ``/memory off`` keeps the chat out of memory and forgets what it left;
    ``/memory on`` lets new messages back in (plan U1,
    ``kazma_core/memory/forget.py``). The resolver runs in a thread, so the
    ledger is read and written here directly."""
    from kazma_core.memory.forget import chat_remembered, forget_chat, set_chat_remembered

    parts = (text or "").strip().split()
    sub = parts[1].lower() if len(parts) > 1 else ""
    thread = str(ctx.get("thread_id") or "")
    tenant = str(ctx.get("memory_tenant") or "")
    if not tenant:
        # Whose memory this is could not be read. Falling back to "default"
        # would write the forget ledger under another tenant.
        return "💾 Memory could not be reached, so nothing was read or changed."
    if sub in ("off", "on"):
        if not thread:
            return "💾 There is no chat here to set memory for."
        if sub == "off":
            set_chat_remembered(thread, False, tenant_id=tenant)
            n = int(forget_chat(thread, tenant_id=tenant).get("forgotten") or 0)
            forgot = f" Forgot {n} memor{'y' if n == 1 else 'ies'} from it." if n else ""
            return f"🔕 Kazma won't remember this chat.{forgot} `/memory on` lets new messages back in."
        set_chat_remembered(thread, True, tenant_id=tenant)
        return "💾 Kazma remembers this chat again. What it forgot stays forgotten."
    count = ctx.get("memory_count", "?")
    here = ""
    if thread:
        here = (
            " This chat is remembered (`/memory off` to stop)."
            if chat_remembered(thread, tenant_id=tenant)
            else " This chat is not remembered (`/memory on` to start again)."
        )
    return f"💾 Memory: `{count}` stored facts.{here}"


def _cmd_cost(ctx: dict[str, Any]) -> str:
    """``/cost``: this chat's model calls, summed from the per-call ledger
    (``llm_calls.db``). Until 2026-10-01 it said ``$0.0000`` whatever the
    chat had spent: the gateway set both numbers to zero."""
    if ctx.get("cost_tracking") is False:
        return (
            "💰 Cost tracking is off (`agent.nonstop.ledger.enabled`), so this "
            "chat's model calls are not recorded."
        )
    tokens, cost, calls = ctx.get("total_tokens"), ctx.get("total_cost"), ctx.get("total_calls")
    if not isinstance(tokens, int) or not isinstance(cost, (int, float)) or not isinstance(calls, int):
        return "💰 Session cost: unknown (the cost ledger could not be read)."
    text = (
        f"💰 Session cost: `${cost:.4f}` ({tokens:,} tokens, "
        f"{calls:,} model call{'' if calls == 1 else 's'})"
    )
    if tokens and not cost:
        text += " — no price is recorded for this chat's models"
    return text


# ── Replay / Time Travel ────────────────────────────────────────────


def _cmd_replay(text: str, ctx: dict[str, Any]) -> str | None:
    """Handle /replay commands for time-travel debugging.

    Sub-commands:
        /replay list               — show available snapshots
        /replay <iteration>        — restore from that iteration (graph handler)
        /replay compare <a> <b>    — compare two snapshots
        /replay clear              — clear snapshots for current thread

    Returns None for ``/replay <iteration>`` so it falls through to the graph
    handler (``_handle_replay``) which can call ``graph.aupdate_state``.
    """
    recorder, engine = _get_replay_components()
    if recorder is None or engine is None:
        return "⏳ Time travel not yet available."

    parts = text.strip().split()
    # parts[0] is "/replay"
    sub = parts[1] if len(parts) > 1 else ""

    thread_id = ctx.get("thread_id", "default")

    if sub == "list" or sub == "":
        return _replay_list(recorder, thread_id)
    if sub == "compare":
        return _replay_compare(engine, parts, thread_id)
    if sub == "clear":
        return _replay_clear(recorder, thread_id)
    # Numeric iteration → fall through to the graph handler for restore.
    # Validate it's a number so we can give immediate feedback if not.
    try:
        int(sub)
    except (ValueError, TypeError):
        return f"⚠️ Unknown /replay sub-command: `{sub}`. Use `list`, `compare`, `clear`, or a number."
    return None  # graph handler (_handle_replay) does the restore


def _replay_list(recorder, thread_id: str) -> str:
    """List available snapshots for a thread."""
    try:
        snapshots = recorder.list_snapshots(thread_id)
    except Exception as exc:
        logger.warning("[slash] /replay list failed: %s", exc)
        return f"⚠️ Could not list snapshots: {exc}"

    if not snapshots:
        return "📭 No snapshots available for this thread."

    lines = ["🕰️ *Available snapshots:*\n"]
    for snap in snapshots:
        it = snap.iteration
        ts = snap.timestamp
        model = snap.model_used or "—"
        lines.append(f"• Iteration `{it}` — {ts} — {model}")
    return "\n".join(lines)


def _replay_compare(engine, parts: list, thread_id: str) -> str:
    """Compare two snapshots via ReplayEngine.compare_replays()."""
    if len(parts) < 4:
        return "⚠️ Usage: `/replay compare <a> <b>` — provide two iteration numbers."

    try:
        iter_a = int(parts[2])
        iter_b = int(parts[3])
    except (ValueError, TypeError):
        return "⚠️ Both iterations must be numbers (e.g. `/replay compare 1 3`)."

    try:
        state_a = engine.replay_from(thread_id, iter_a)
        state_b = engine.replay_from(thread_id, iter_b)
        if state_a is None or state_b is None:
            return f"📭 Could not load snapshots for iterations `{iter_a}`/`{iter_b}`."
        diff = engine.compare_replays(state_a, state_b)
    except Exception as exc:
        logger.warning("[slash] /replay compare %s vs %s failed: %s", iter_a, iter_b, exc)
        return f"⚠️ Could not compare iterations `{iter_a}` and `{iter_b}`: {exc}"

    return _format_compare_diff(iter_a, iter_b, diff)


def _format_compare_diff(iter_a: int, iter_b: int, diff: dict[str, Any]) -> str:
    """Render a compare_replays() diff dict as a Markdown table."""
    def _arrow(delta: int | float) -> str:
        if delta > 0:
            return f"+{delta}"
        return str(delta)

    lines = [f"🕰️ *Comparison: iteration {iter_a} vs {iter_b}:*\n"]
    lines.append("| Metric | Iter {} | Iter {} | Delta |".format(iter_a, iter_b))
    lines.append("|---|---|---|---|")
    lines.append("| Messages | {} | {} | {} |".format(
        diff["original_message_count"], diff["replayed_message_count"],
        _arrow(diff["message_count_delta"])))
    lines.append("| Iteration | {} | {} | {} |".format(
        diff["original_iteration"], diff["replayed_iteration"],
        _arrow(diff["iteration_delta"])))
    lines.append("| Model | {} | {} | {} |".format(
        diff["original_model"], diff["replayed_model"],
        "changed" if diff["model_changed"] else "same"))
    lines.append("| Cost (USD) | {:.4f} | {:.4f} | {} |".format(
        diff["original_cost_usd"], diff["replayed_cost_usd"],
        _arrow(diff["cost_delta_usd"])))
    lines.append("| Tool calls | {} | {} | {} |".format(
        diff["original_tool_calls"], diff["replayed_tool_calls"],
        _arrow(diff["tool_calls_delta"])))
    lines.append("| Next node | {} | {} | {} |".format(
        diff["original_next_node"], diff["replayed_next_node"],
        "changed" if diff["routing_changed"] else "same"))
    if diff["identical"]:
        lines.append("\n✅ States are **identical**.")
    return "\n".join(lines)


def _replay_clear(recorder, thread_id: str) -> str:
    """Clear all snapshots for a thread."""
    try:
        count = recorder.clear_snapshots(thread_id)
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
    from kazma_core.memory.config import memory_enabled as _memory_enabled

    config = _load_config()
    model = _resolve_current_model(config, ctx)
    personality = _resolve_personality(config)
    memory_enabled = _memory_enabled()
    tools = [str(s["name"]) for s in _mcp_servers()]

    lines = [
        "⚙️ *Current Configuration*",
        "",
        "```",
        f"{'Setting':<20} {'Value':<30}",
        f"{'───────':<20} {'─────':<30}",
        f"{'Model':<20} {model:<30}",
        f"{'Personality':<20} {personality:<30}",
        f"{'Memory':<20} {'enabled' if memory_enabled else 'disabled':<30}",
        f"{'MCP servers':<20} {', '.join(tools) if tools else '(none)':<30}",
        "```",
    ]
    return "\n".join(lines)


def _config_model(parts: list, ctx: dict[str, Any]) -> str:
    """Show or switch the active model (``switch_model_from_chat``)."""
    if len(parts) < 3:
        current = _resolve_current_model(_load_config(), ctx)
        return f"🧠 Current model: **{current}**\n\nUsage: `/config model <name>`"
    return switch_model_from_chat(parts[2])  # model names are case-sensitive


def switch_model_from_chat(model_id: str) -> str:
    """Switch the process's model the one way, and say what happened.

    ``switch_active_model`` sets the model AND its provider and rebinds the
    live agent. The ``/model`` menu and ``/config model`` both come here.
    Until 2026-10-01 ``/config model`` wrote ``llm.model`` alone -- a legacy
    key the registry reads only when no profile is active, so the running
    agent kept its model -- and said "Switched" even when the write failed.
    Blocking (settings writes, a graph recompile): call it from a thread.
    """
    from kazma_core.agent_runner import KazmaAgent
    from kazma_core.runtime.model_switch import switch_active_model
    from kazma_core.service_container import get_container

    container = get_container()
    agent = container.get(KazmaAgent) if container.has(KazmaAgent) else None
    result = switch_active_model(model_id, agent=agent)
    if result.ok:
        return f"✅ Switched to **{result.model}** (provider: {result.provider or '—'})"
    return f"⚠️ Failed to switch model: {result.error or result.error_code or 'unknown'}"


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
    """Turn memory on or off: ``memory.enabled``, the one key it changes
    (read live by ``kazma_core.memory.config``). It used to save the whole
    merged configuration back, every setting, to change this one."""
    from kazma_core.memory.config import memory_enabled

    if len(parts) < 3 or parts[2].lower() not in ("on", "off"):
        state = "enabled" if memory_enabled() else "disabled"
        return f"💾 Memory is currently **{state}**.\n\nUsage: `/config memory on` or `/config memory off`"

    toggle = parts[2].lower()
    try:
        _get_config_store().set("memory.enabled", toggle == "on", "memory")
    except Exception as exc:
        logger.warning("[slash] /config memory could not be saved: %s", exc)
        return f"⚠️ Memory was not changed: {exc}"
    return f"💾 Memory **{toggle.upper()}**."


def _mcp_servers() -> list[dict[str, Any]]:
    """The MCP servers as Settings -> MCP lists them (settings over kazma.yaml)."""
    from kazma_core.mcp_servers_store import list_mcp_servers

    return [s for s in list_mcp_servers() if isinstance(s, dict) and s.get("name")]


def _config_tools(parts: list, ctx: dict[str, Any]) -> str:
    """``/config tools list|toggle``: the MCP servers, switched the way
    Settings -> MCP switches them (``set_mcp_server_enabled``). The toggle
    used to write ``mcp.disabled_servers``, which nothing reads, and report
    the server disabled."""
    if len(parts) < 3:
        return _config_usage()

    action = parts[2].lower()
    servers = _mcp_servers()

    if action == "list":
        if not servers:
            return "🔧 No MCP servers are configured.\n\nAdd one in the Web UI under Settings → MCP."
        lines = ["🔧 *MCP servers:*", ""]
        for server in servers:
            line = f"• `{server['name']}`"
            if not server.get("enabled", True):
                line += " _(disabled)_"
            lines.append(line)
        return "\n".join(lines)

    if action == "toggle" and len(parts) >= 4:
        wanted = parts[3].lower()
        match = next((s for s in servers if str(s["name"]).lower() == wanted), None)
        if match is None:
            available = ", ".join(str(s["name"]) for s in servers) or "(none)"
            return f"❌ Unknown MCP server: `{parts[3]}`\n\nAvailable: {available}"
        from kazma_core.mcp_servers_store import set_mcp_server_enabled

        name, enable = str(match["name"]), not match.get("enabled", True)
        try:
            set_mcp_server_enabled(name, enable)
        except Exception as exc:
            logger.warning("[slash] /config tools toggle could not be saved: %s", exc)
            return f"⚠️ `{name}` was not changed: {exc}"
        state = "enabled" if enable else "disabled"
        return f"🔧 MCP server `{name}` **{state}**. It applies when Kazma next starts."

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
    """Recursively redact sensitive keys in a nested dict/list.

    Leaves keep their value with any URL password masked: a key rule cannot
    see the password inside a DSN or a token URL.
    """
    if isinstance(obj, dict):
        return {
            k: ("***REDACTED***" if k.lower() in _REDACT_KEYS and v else _redact_secrets(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_secrets(item) for item in obj]
    from kazma_core.security.url_credentials import mask_url_credentials_deep

    return mask_url_credentials_deep(obj)


def _config_usage() -> str:
    return (
        "⚙️ *Config Wizard — available sub-commands:*\n\n"
        "• `/config show` — Display current configuration\n"
        "• `/config model <name>` — Switch model\n"
        "• `/config personality <name>` — Switch personality\n"
        "• `/config memory on|off` — Toggle memory\n"
        "• `/config tools list` — Show the MCP servers\n"
        "• `/config tools toggle <name>` — Turn an MCP server on or off (from the next start)\n"
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

