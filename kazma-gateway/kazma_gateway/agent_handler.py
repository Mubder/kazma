"""Agent message handler — bridges IncomingMessage to the LangGraph supervisor.

Platform isolation contract:
    The Brain (LangGraph graph) NEVER sees platform-specific identifiers.
    chat_id, user_id, message_id, update_id, chat_type are stored in a
    SessionStore OUTSIDE the graph state and restored only when
    constructing the OutboundMessage for the return path.

    Graph state["_gateway"] contains ONLY:
        - thread_id:    stable session identifier
        - display_name: sender's display name (platform-agnostic)
        - platform:     "telegram" / "discord" (string, not an ID)

    Everything else lives in the SessionStore and is fetched back
    onto the OutboundMessage.context_metadata when replying.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from kazma_gateway.gateway import IncomingMessage, OutboundMessage, SessionStore

logger = logging.getLogger(__name__)

# Maximum number of entries retained in per-handler in-memory dicts.
# When exceeded the least-recently-used entry is evicted (LRU via
# OrderedDict).  This bounds memory usage for long-running gateways
# that see many distinct senders / threads.
_MAX_DICT_ENTRIES = 10_000

# Platform-specific keys that must NEVER enter graph state
_PLATFORM_KEYS = frozenset(
    {
        "chat_id",
        "user_id",
        "message_id",
        "update_id",
        "chat_type",
    }
)


# ══════════════════════════════════════════════════════════════════════════
# Standardized thread_id resolver
# ══════════════════════════════════════════════════════════════════════════


def _resolve_thread(msg: IncomingMessage) -> str:
    """Resolve or generate a stable thread_id for a message.

    Resolution order:
        1. Existing thread_id in context_metadata (from session store)
        2. Platform-prefixed deterministic ID from sender_id
        3. Fresh UUID4 (last resort)

    Args:
        msg: The incoming message.

    Returns:
        A stable thread_id string.
    """
    ctx = msg.context_metadata

    # 1. Already resolved (e.g. from a previous message in the session)
    if ctx.get("thread_id"):
        return ctx["thread_id"]

    # 2. Deterministic from sender_id (e.g. "telegram:12345" → "gw-telegram-12345")
    if msg.sender_id and ":" in msg.sender_id:
        platform, sender = msg.sender_id.split(":", 1)
        return f"gw-{platform}-{sender}"

    # 3. Fallback UUID
    return f"gw-{uuid.uuid4().hex[:12]}"


# ══════════════════════════════════════════════════════════════════════════
# In-memory fallback store (for when no persistent store is provided)
# ══════════════════════════════════════════════════════════════════════════


class _InMemoryStore(SessionStore):
    """Trivial in-memory store — no persistence, for testing/fallback.

    Tracks a monotonic timestamp per entry so that TTL-based eviction
    (``evict_older_than``) works the same way as the SQLite backend.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._timestamps: dict[str, float] = {}

    async def get(self, thread_id: str) -> dict[str, Any]:
        return dict(self._data.get(thread_id, {}))

    async def put(self, thread_id: str, context: dict[str, Any]) -> None:
        self._data[thread_id] = dict(context)
        self._timestamps[thread_id] = time.monotonic()

    async def delete(self, thread_id: str) -> None:
        self._data.pop(thread_id, None)
        self._timestamps.pop(thread_id, None)

    async def evict_older_than(self, seconds: float) -> int:
        """Remove entries whose last ``put`` is older than ``seconds`` ago."""
        cutoff = time.monotonic() - seconds
        stale = [tid for tid, ts in self._timestamps.items() if ts < cutoff]
        for tid in stale:
            self._data.pop(tid, None)
            self._timestamps.pop(tid, None)
        return len(stale)


# ══════════════════════════════════════════════════════════════════════════
# State builder — platform-agnostic
# ══════════════════════════════════════════════════════════════════════════


async def _build_initial_state(msg: IncomingMessage, store: SessionStore) -> dict[str, Any]:
    """Build a platform-agnostic graph state from an IncomingMessage.

    Side-effects:
        - Stores full context_metadata in SessionStore via store.put()
        - The graph state's _gateway block contains ZERO platform IDs

    Args:
        msg:   The incoming message from the gateway queue.
        store: SessionStore for persisting platform context.

    Returns:
        LangGraph-compatible initial state dict.
    """
    ctx = msg.context_metadata

    # Resolve thread_id using standardized resolver
    thread_id = _resolve_thread(msg)

    # Store full platform context in SessionStore (NEVER enters the graph)
    await store.put(thread_id, dict(ctx))

    # Build graph state with ONLY platform-agnostic fields
    try:
        from kazma_core.agent.state import initial_supervisor_state

        state = initial_supervisor_state(thread_id=thread_id)
    except ImportError:
        state = {"thread_id": thread_id, "messages": []}

    state["_gateway"] = {
        "thread_id": thread_id,
        "display_name": ctx.get("username") or "unknown",
        "platform": msg.platform,
    }

    # Attach the user message
    state["messages"] = [{"role": "user", "content": msg.text}]

    return state


# ══════════════════════════════════════════════════════════════════════════
# Swarm slash-command interceptor
# ══════════════════════════════════════════════════════════════════════════


async def _try_swarm_command(
    msg: IncomingMessage,
    state: dict[str, Any],
    store: SessionStore,
    manager: Any,
    thread_id: str,
) -> bool:
    """Check if a message is a swarm command and handle it.

    Trigger patterns (case-insensitive):
        /swarm <worker> <task>               — dispatch to one worker
        /swarm pipeline|consult|fanout ...   — structured patterns
        /swarm broadcast <task>              — all workers
        /swarm status                        — show swarm status
        /swarm list                          — list workers
        /swarm <natural language task>       — auto-route via CapabilityRouter

        Also accepts bare mentions:
        "use the swarm to research X"
        "swarm: implement Y"
        "swarm analyze Z"

    Returns ``True`` if the message was handled (swarm intent detected),
    ``False`` to continue with normal graph processing.
    """
    text = (msg.text or "").strip()
    if not text:
        return False

    # ── Detect swarm intent ─────────────────────────────────────
    # Accept both "/swarm ..." and bare "swarm" mentions.
    is_slash = text.lower().startswith("/swarm")
    # Bare-word detection: only trigger on explicit intent patterns,
    # not just any message containing the word "swarm".
    # E.g. "use the swarm to X" → yes, "I saw a swarm of bees" → no.
    text_lower = text.lower()
    bare_swarm = False
    if not is_slash:
        import re
        # Only match specific command patterns, not arbitrary word usage
        bare_patterns = [
            r'(?:use|ask|tell)\s+(?:the\s+)?swarm\s+(?:to\s+)?',
            r'let\s+(?:the\s+)?swarm\s+',
            r'^swarm\s*:\s*',  # "swarm: task" (must be at start)
            r'^swarm\s+\S',    # "swarm <task>" (must be at start)
        ]
        for pat in bare_patterns:
            if re.match(pat, text_lower):
                bare_swarm = True
                break

    if not is_slash and not bare_swarm:
        return False

    # Lazy import — skip if swarm engine isn't available
    try:
        from kazma_core.swarm.engine import get_swarm_engine
        engine = get_swarm_engine()
    except Exception:
        return False  # swarm not initialized, fall through to graph

    # ── Extract the command body (everything after the trigger) ──
    if is_slash:
        # "/swarm ..." → take everything after "/swarm "
        parts = text.split(None, 2)  # ["/swarm", subcommand, rest]
        if len(parts) < 2:
            # Just "/swarm" with nothing else → show help
            await _send_swarm_reply(msg, store, manager, thread_id,
                "🐝 **Swarm Commands**\n\n"
                "/swarm `<worker>` `<task>` — dispatch to one worker\n"
                "/swarm pipeline `<w1,w2,...>` `<task>` — sequential pipeline\n"
                "/swarm consult `<w1,w2,...>` `<task>` — parallel consult\n"
                "/swarm fanout `<w1,w2,...>` `<task>` — parallel fan-out\n"
                "/swarm broadcast `<task>` — all workers\n"
                "/swarm `<task>` — auto-route to best workers\n"
                "/swarm status — show swarm status\n"
                "/swarm list — list workers\n"
                "/swarm config — show output routing config\n"
                "/swarm config group `<chat_id>` — route output to a Telegram group\n"
                "/swarm config clear — disable output routing\n\n"
                "Or just say: *use the swarm to <task>*\n\n"
                "Append `-> telegram:<chat_id>` to any task for one-off routing."
            )
            return True
        sub = parts[1].lower()
        task_body = parts[2] if len(parts) > 2 else ""
    else:
        # Bare mention: strip the trigger phrase, keep the rest as the task
        sub = ""
        task_body = _extract_swarm_task(text)

    # ── Known subcommands (only for /swarm prefix) ──────────────
    if is_slash:
        # /swarm status
        if sub == "status":
            try:
                worker_names = engine.worker_names
                status_lines = ["🐝 Swarm Status\n", f"Workers: {len(worker_names)}"]
                for name in worker_names:
                    w = engine.get_worker(name)
                    busy = " (busy)" if w and getattr(w, "busy", False) else ""
                    model = f" [{getattr(w, 'model', '?')}]" if w and getattr(w, "model", "") else ""
                    status_lines.append(f"  • {name}{busy}{model}")
                await _send_swarm_reply(msg, store, manager, thread_id, "\n".join(status_lines))
            except Exception as exc:
                await _send_swarm_reply(msg, store, manager, thread_id, f"⚠️ Status error: {exc}")
            return True

        # /swarm list
        if sub == "list":
            try:
                names = engine.worker_names
                if not names:
                    await _send_swarm_reply(msg, store, manager, thread_id, "No workers registered.")
                else:
                    lines = [f"🐝 Workers ({len(names)}):"]
                    for name in names:
                        w = engine.get_worker(name)
                        role = getattr(w, "role", "") or ""
                        model = getattr(w, "model", "") or ""
                        lines.append(f"  • {name}" + (f" ({role})" if role else "") + (f" [{model}]" if model else ""))
                    await _send_swarm_reply(msg, store, manager, thread_id, "\n".join(lines))
            except Exception as exc:
                await _send_swarm_reply(msg, store, manager, thread_id, f"⚠️ List error: {exc}")
            return True

        # /swarm config [group <chat_id> | clear]
        if sub == "config":
            return await _handle_swarm_config_command(
                msg, store, manager, thread_id, task_body,
            )

        # /swarm broadcast <prompt>
        if sub == "broadcast":
            prompt = task_body
            if not prompt:
                await _send_swarm_reply(msg, store, manager, thread_id, "⚠️ Usage: /swarm broadcast <prompt>")
                return True
            return await _dispatch_swarm_from_chat(
                msg, store, manager, thread_id, engine,
                workers=[], task=prompt, pattern="broadcast",
            )

        # /swarm pipeline|consult|fanout <workers> <prompt>
        if sub in ("pipeline", "consult", "fanout", "dispatch"):
            if not task_body:
                await _send_swarm_reply(msg, store, manager, thread_id,
                    f"⚠️ Usage: /swarm {sub} <workers> <prompt>")
                return True

            if sub == "dispatch":
                worker_parts = task_body.split(None, 1)
                if len(worker_parts) < 2:
                    await _send_swarm_reply(msg, store, manager, thread_id,
                        "⚠️ Usage: /swarm dispatch <worker> <prompt>")
                    return True
                return await _dispatch_swarm_from_chat(
                    msg, store, manager, thread_id, engine,
                    workers=[worker_parts[0]], task=worker_parts[1], pattern="dispatch",
                )

            split_idx = _find_worker_prompt_split(task_body)
            if split_idx is None:
                await _send_swarm_reply(msg, store, manager, thread_id,
                    f"⚠️ Usage: /swarm {sub} <worker1,worker2,...> <prompt>")
                return True

            workers_str = task_body[:split_idx].strip()
            prompt = task_body[split_idx:].strip()
            workers = [w.strip() for w in workers_str.split(",") if w.strip()]
            if not workers or not prompt:
                await _send_swarm_reply(msg, store, manager, thread_id,
                    f"⚠️ Usage: /swarm {sub} <worker1,worker2,...> <prompt>")
                return True

            return await _dispatch_swarm_from_chat(
                msg, store, manager, thread_id, engine,
                workers=workers, task=prompt, pattern=sub,
            )

    # ── Natural language auto-route ─────────────────────────────
    # If we reach here, the message triggered swarm intent but didn't
    # match any known subcommand. Treat the full body as a task and
    # auto-route to the best-matching workers via CapabilityRouter.
    if not task_body:
        task_body = text  # fallback: use the full message

    # Skip if the extracted task is too short to be meaningful
    if len(task_body.strip()) < 3:
        await _send_swarm_reply(msg, store, manager, thread_id,
            "🐝 What would you like the swarm to do?\n\n"
            "Examples:\n"
            "  /swarm research the latest AI trends\n"
            "  /swarm implement a dark mode toggle\n"
            "  use the swarm to analyze this code\n"
            "  /swarm broadcast summarize today's news"
        )
        return True

    return await _dispatch_auto_route(
        msg, store, manager, thread_id, engine, task_body,
    )


def _extract_swarm_task(text: str) -> str:
    """Extract the task description from a bare 'swarm' mention.

    Strips common filler phrases:
        "use the swarm to <task>"  → "<task>"
        "swarm: <task>"            → "<task>"
        "swarm <task>"             → "<task>"
        "use swarm to <task>"      → "<task>"
        "ask the swarm to <task>"  → "<task>"
        "tell the swarm to <task>" → "<task>"
        "let the swarm <task>"     → "<task>"
    """
    import re

    # Normalize: try various patterns (case-insensitive)
    patterns = [
        r'(?:use|ask|tell)\s+(?:the\s+)?swarm\s+(?:to\s+)?(.+)',
        r'let\s+(?:the\s+)?swarm\s+(.+)',
        r'swarm\s*:\s*(.+)',  # "swarm: task"
        r'swarm\s+(.+)',      # "swarm task"
    ]

    for pattern in patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            extracted = match.group(1).strip()
            # Strip leading "to " if present (e.g. "use the swarm to research X")
            if extracted.lower().startswith("to "):
                extracted = extracted[3:].strip()
            return extracted

    return ""


async def _handle_swarm_config_command(
    msg: IncomingMessage,
    store: SessionStore,
    manager: Any,
    thread_id: str,
    body: str,
) -> bool:
    """Handle ``/swarm config`` subcommands for output routing.

    Forms:
        /swarm config                       — show current config
        /swarm config group <chat_id>       — set Telegram group chat_id (enables routing)
        /swarm config disable               — disable routing (keep chat_id)
        /swarm config clear                 — clear output target entirely
    """
    parts = body.split(None, 1)
    action = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    try:
        from kazma_core.config_store import get_config_store
        cs = get_config_store()
    except ImportError:
        await _send_swarm_reply(msg, store, manager, thread_id,
            "⚠️ Config store unavailable.")
        return True

    key = "swarm.output_target"

    # ── /swarm config group <chat_id> ─────────────────────────────
    if action == "group":
        if not arg:
            await _send_swarm_reply(msg, store, manager, thread_id,
                "⚠️ Usage: /swarm config group <chat_id>\n\n"
                "Tip: group chat IDs are negative, e.g. -1001234567890")
            return True
        try:
            chat_id = int(arg)
        except ValueError:
            await _send_swarm_reply(msg, store, manager, thread_id,
                f"⚠️ Invalid chat_id: `{arg}`. It must be an integer "
                "(group IDs are negative, e.g. -1001234567890).")
            return True
        cs.set(key, {
            "platform": "telegram",
            "chat_id": chat_id,
            "enabled": True,
        }, category="swarm")
        await _send_swarm_reply(msg, store, manager, thread_id,
            f"✅ Output routing enabled.\n"
            f"Swarm results will also be sent to Telegram group `{chat_id}`.\n\n"
            "Make sure the bot is a member of that group.")
        return True

    # ── /swarm config disable ─────────────────────────────────────
    if action == "disable":
        existing = cs.get(key, None)
        if isinstance(existing, dict):
            existing["enabled"] = False
            cs.set(key, existing, category="swarm")
        await _send_swarm_reply(msg, store, manager, thread_id,
            "✅ Output routing disabled (config retained).")
        return True

    # ── /swarm config clear ───────────────────────────────────────
    if action == "clear":
        cs.delete(key)
        await _send_swarm_reply(msg, store, manager, thread_id,
            "✅ Output routing cleared.")
        return True

    # ── /swarm config (show current) ──────────────────────────────
    current = cs.get(key, None)
    if not isinstance(current, dict) or not current.get("chat_id"):
        await _send_swarm_reply(msg, store, manager, thread_id,
            "🐝 **Output Routing**\n\n"
            "Status: *not configured*\n\n"
            "To route swarm output to a Telegram group:\n"
            "  /swarm config group -1001234567890\n\n"
            "The bot must be added to the group first.")
        return True

    status = "✅ enabled" if current.get("enabled") else "⏸ disabled"
    await _send_swarm_reply(msg, store, manager, thread_id,
        f"🐝 **Output Routing**\n\n"
        f"Status: {status}\n"
        f"Platform: `{current.get('platform', 'telegram')}`\n"
        f"Group chat_id: `{current.get('chat_id')}`\n\n"
        "Commands:\n"
        "  /swarm config group <chat_id> — change target\n"
        "  /swarm config disable — turn off\n"
        "  /swarm config clear — remove config")
    return True


async def _dispatch_auto_route(
    msg: IncomingMessage,
    store: SessionStore,
    manager: Any,
    thread_id: str,
    engine: Any,
    task: str,
) -> bool:
    """Auto-route a natural-language task to the best-matching workers.

    Uses the CapabilityRouter to find workers whose expertise matches
    the task. Falls back to broadcast if no specific match is found.
    """
    # Build available workers list for the router
    available: list[dict[str, Any]] = []
    for name in engine.worker_names:
        w = engine.get_worker(name)
        if w is None:
            continue
        available.append({
            "name": name,
            "capabilities": getattr(w, "capabilities", None),
        })

    if not available:
        await _send_swarm_reply(msg, store, manager, thread_id,
            "⚠️ No workers registered. Add workers in the Swarm panel first.")
        return True

    # Try capability routing
    routed_workers: list[str] = []
    try:
        from kazma_core.swarm.router import CapabilityRouter
        from kazma_core.swarm.task import SwarmTask, TaskType

        router = CapabilityRouter()
        temp_task = SwarmTask(
            id="auto-route-temp",
            type=TaskType.DISPATCH,
            prompt=task,
            workers=["auto"],
        )
        routed_workers = router.route(temp_task, available)
    except Exception as exc:
        logger.warning("[agent-handler] Auto-route failed: %s", exc)

    if not routed_workers:
        # Fallback: broadcast to all workers
        routed_workers = [w["name"] for w in available]
        logger.info("[agent-handler] Auto-route: no match, broadcasting to %d workers", len(routed_workers))
        pattern = "broadcast"
    else:
        logger.info("[agent-handler] Auto-route: matched workers %s", routed_workers)
        pattern = "fanout" if len(routed_workers) > 1 else "dispatch"

    # Confirm to the user what's happening
    worker_list = ", ".join(routed_workers)
    await _send_swarm_reply(msg, store, manager, thread_id,
        f"🐝 Dispatching to: **{worker_list}** ({pattern})\n\nTask: {task[:200]}"
    )

    return await _dispatch_swarm_from_chat(
        msg, store, manager, thread_id, engine,
        workers=routed_workers if pattern != "broadcast" else [],
        task=task, pattern=pattern,
    )


def _find_worker_prompt_split(text: str) -> int | None:
    """Find the split point between comma-separated worker names and the prompt.

    Worker names may contain forward slashes (e.g. "meta/llama-3.1-8b-instruct")
    but not spaces. The first space after the last comma or the first space
    if no comma exists marks the start of the prompt.

    Returns the character index of the prompt start, or None if ambiguous.
    """
    if "," in text:
        last_comma = text.rfind(",")
        rest_after_comma = text[last_comma + 1:].lstrip()
        if rest_after_comma:
            # Find first space in the segment after the last comma
            space_in_segment = rest_after_comma.find(" ")
            if space_in_segment != -1:
                actual_idx = last_comma + 1 + (len(text[last_comma + 1:]) - len(rest_after_comma)) + space_in_segment
                return actual_idx + 1
        return None
    else:
        # No comma — first space splits worker name from prompt
        space = text.find(" ")
        if space != -1:
            return space + 1
        return None


def _get_output_target_config() -> dict[str, Any] | None:
    """Read the configured swarm output target from ConfigStore.

    Returns a dict like ``{"platform": "telegram", "chat_id": -100…,
    "enabled": true}`` or ``None`` if not configured / not enabled.
    """
    try:
        from kazma_core.config_store import get_config_store
    except ImportError:
        return None
    try:
        cs = get_config_store()
        target = cs.get("swarm.output_target", None)
        if not isinstance(target, dict):
            return None
        if not target.get("enabled", False):
            return None
        if not target.get("chat_id"):
            return None
        target.setdefault("platform", "telegram")
        return target
    except Exception:
        logger.debug("[agent-handler] Failed reading swarm.output_target", exc_info=True)
        return None


def _parse_output_target_suffix(task: str) -> tuple[str, dict[str, Any] | None]:
    """Detect a trailing ``-> telegram:<chat_id>`` routing suffix.

    Supports one inline override form:
        "<task> -> telegram:-1001234567890" — explicit platform:chat_id

    Returns ``(clean_task, override_target_or_None)``. ``override_target`` is a
    dict shaped like the ConfigStore entry when the suffix is a concrete
    ``platform:chat_id``, or ``None`` when no override was present.
    """
    import re

    match = re.search(r"\s*->\s*(\S+)\s*$", task)
    if not match:
        return task, None

    raw = match.group(1).strip()
    clean = task[: match.start()].rstrip()

    if ":" in raw:
        platform, _, chat_id_str = raw.partition(":")
        try:
            chat_id = int(chat_id_str)
        except ValueError:
            logger.info(
                "[agent-handler] Ignoring malformed output-target suffix %r "
                "(chat_id not an integer); task left untouched", raw,
            )
            return task, None  # malformed — leave prompt untouched
        return clean, {"platform": platform, "chat_id": chat_id, "enabled": True}

    # Unrecognized suffix format (e.g. "@GroupName") — log so the user
    # knows the override was seen but not applied, then leave prompt untouched.
    logger.info(
        "[agent-handler] Unrecognized output-target suffix %r "
        "(expected platform:chat_id, e.g. telegram:-100123); task left untouched",
        raw,
    )
    return task, None


async def _maybe_send_to_output_target(
    manager: Any,
    text: str,
    override: dict[str, Any] | None = None,
) -> bool:
    """Send ``text`` to the configured output target (e.g. a Telegram group).

    Resolution order: per-dispatch ``override`` dict → ConfigStore entry.

    Two delivery modes:
        1. **Dedicated bot**: if ``bot_token`` is set in the config, sends
           directly via the Telegram Bot API using that token. This is the
           "separate swarm bot" mode — output goes to a DM with a different
           bot, avoiding group-membership requirements.
        2. **Gateway adapter**: falls back to ``manager.send()`` which routes
           through the gateway's primary adapter (e.g. @KazmaAIBot). This is
           the original group-routing mode.

    Sends the *same* output that went to the originating chat. Errors are
    logged but never raised — group routing is best-effort.

    Returns True if a message was sent (or attempted), False if no target.
    """
    target = override if isinstance(override, dict) and override.get("chat_id") else _get_output_target_config()
    if not target:
        return False

    platform = target.get("platform", "telegram")
    chat_id = target.get("chat_id")
    bot_token = target.get("bot_token", "")

    # ── Mode 1: Dedicated swarm bot (direct Telegram API) ──────────
    if bot_token and platform == "telegram":
        try:
            import httpx

            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0)
            ) as client:
                # Telegram messages are limited to 4096 chars.
                for i in range(0, len(text), 4096):
                    chunk = text[i:i + 4096]
                    resp = await client.post(
                        f"https://api.telegram.org/bot{bot_token}/sendMessage",
                        json={"chat_id": chat_id, "text": chunk},
                    )
                    if not resp.json().get("ok"):
                        logger.warning(
                            "[agent-handler] Swarm bot send failed: %s",
                            resp.json().get("description", "unknown"),
                        )
            logger.info(
                "[agent-handler] Swarm output routed via dedicated bot to %s",
                chat_id,
            )
            return True
        except Exception:
            logger.warning(
                "[agent-handler] Failed routing swarm output via dedicated bot to %s",
                chat_id, exc_info=True,
            )
            return False

    # ── Mode 2: Gateway adapter (original group routing) ───────────
    try:
        await manager.send(OutboundMessage(
            target_id=f"{platform}:{chat_id}",
            text=text,
            context_metadata={"chat_id": chat_id},
        ))
        logger.info(
            "[agent-handler] Swarm output routed to %s:%s",
            platform, chat_id,
        )
        return True
    except Exception:
        logger.warning(
            "[agent-handler] Failed routing swarm output to %s:%s",
            platform, chat_id, exc_info=True,
        )
        return False


async def _dispatch_swarm_from_chat(
    msg: IncomingMessage,
    store: SessionStore,
    manager: Any,
    thread_id: str,
    engine: Any,
    workers: list[str],
    task: str,
    pattern: str,
) -> bool:
    """Dispatch a swarm task from a chat message and send the result back."""
    from kazma_core.swarm.task import SwarmTask, TaskType

    # Map pattern names to TaskType
    type_map = {
        "dispatch": TaskType.DISPATCH,
        "pipeline": TaskType.PIPELINE,
        "consult": TaskType.CONSULT,
        "fanout": TaskType.FAN_OUT,
        "broadcast": TaskType.BROADCAST,
    }
    task_type = type_map.get(pattern, TaskType.DISPATCH)

    # ── Phase 5: parse inline "-> @group" / "-> telegram:<id>" suffix ──
    task, target_override = _parse_output_target_suffix(task)

    # Tag with source platform metadata
    ctx = msg.context_metadata
    metadata = {
        "source_platform": msg.platform,
        "source_chat_id": ctx.get("chat_id", ""),
        "source_thread_id": thread_id,
        "source_user": ctx.get("username", ""),
    }

    swarm_task = SwarmTask(
        prompt=task,
        workers=workers,
        type=task_type,
        metadata=metadata,
    )

    # Send "dispatching" notification
    worker_label = ", ".join(workers) if workers else "all workers"
    await _send_swarm_reply(msg, store, manager, thread_id,
        f"🐝 Dispatching to {worker_label} ({pattern})...")

    # Dispatch synchronously (the engine handles concurrency internally)
    try:
        result = await engine.dispatch(swarm_task)

        # Format result for chat
        if result and result.aggregated_output:
            reply = result.aggregated_output
        elif result and result.worker_results:
            lines = []
            for wr in result.worker_results:
                status_icon = "✅" if wr.status == "success" else "❌"
                output = (wr.output or "")[:500] if wr.output else wr.error or "no output"
                lines.append(f"{status_icon} **{wr.worker}**: {output}")
            reply = "\n\n".join(lines)
        elif result and result.error:
            reply = f"⚠️ Task failed: {result.error}"
        else:
            reply = "⚠️ No result returned from swarm."

        # Send to the originating chat…
        await _send_swarm_reply(msg, store, manager, thread_id, reply)
        # …and mirror the same output to the configured Telegram group.
        await _maybe_send_to_output_target(manager, reply, target_override)

    except Exception as exc:
        logger.exception("[agent-handler] Swarm dispatch failed for thread %s", thread_id)
        error_reply = f"⚠️ Swarm dispatch failed: {exc}"
        await _send_swarm_reply(msg, store, manager, thread_id, error_reply)
        # Mirror the failure to the group too, consistent with the success path.
        await _maybe_send_to_output_target(manager, error_reply, target_override)

    return True


async def _send_swarm_reply(
    msg: IncomingMessage,
    store: SessionStore,
    manager: Any,
    thread_id: str,
    text: str,
) -> None:
    """Send a reply through the gateway manager (platform-agnostic)."""
    ctx = await store.get(thread_id)
    if not ctx:
        ctx = msg.context_metadata
    await manager.send(
        OutboundMessage(
            target_id=_build_target_id(msg.platform, ctx),
            text=text,
            context_metadata=ctx,
        )
    )


# ══════════════════════════════════════════════════════════════════════════
# Interactive model selector (/models with inline keyboards)
# ══════════════════════════════════════════════════════════════════════════


def _get_visible_providers() -> list[dict[str, Any]]:
    """Return providers that have selected (visible) models."""
    try:
        from kazma_core.model_registry import get_model_registry
        reg = get_model_registry()
        result: list[dict[str, Any]] = []
        for p in reg.list_providers():
            name = p.get("name", "")
            display = p.get("display_name", name)
            enabled = p.get("enabled", True)
            if not enabled or not name:
                continue
            models = reg.get_visible_models(name)
            if models:
                result.append({"name": name, "display_name": display, "models": models})
        return result
    except Exception as exc:
        logger.warning("[agent-handler] Failed to get providers: %s", exc)
        return []


async def _try_model_command(
    msg: IncomingMessage,
    store: SessionStore,
    manager: Any,
    thread_id: str,
) -> bool:
    """Handle /models, /_models_provider, /_models_select commands.

    Flow:
        /models                → show provider buttons
        /_models_provider <p>  → show model buttons for provider
        /_models_select <m>    → switch active model, confirm

    Returns True if handled.
    """
    text = (msg.text or "").strip()
    if not text.startswith("/"):
        return False

    cmd = text.split(None, 1)[0].lower()

    if cmd not in ("/models", "/model", "/_models_provider", "/_models_select"):
        return False

    ctx = await store.get(thread_id)
    if not ctx:
        ctx = msg.context_metadata

    # ── /models: Show provider keyboard ───────────────────────────
    if cmd in ("/models", "/model"):
        providers = _get_visible_providers()
        if not providers:
            await _send_model_reply(msg, store, manager, thread_id,
                "No providers with models configured. "
                "Use the Web UI Settings to add providers and select models.")
            return True

        # Build inline keyboard for Telegram
        if msg.platform == "telegram":
            try:
                from kazma_gateway.adapters.telegram import TelegramAdapter
                keyboard = TelegramAdapter.build_provider_keyboard(providers)
                reply_ctx = dict(ctx)
                reply_ctx["reply_markup"] = keyboard
                model_lines = "\n".join(
                    f"  • {p['display_name']} ({len(p['models'])} models)"
                    for p in providers
                )
                await manager.send(OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=f"Select a provider:\n\n{model_lines}",
                    context_metadata=reply_ctx,
                ))
                return True
            except Exception:
                pass  # fall through to text

        # Text fallback (non-Telegram or keyboard build failed)
        lines = ["Available providers:\n"]
        for p in providers:
            lines.append(f"  {p['display_name']} — {len(p['models'])} models")
            for m in p["models"][:5]:
                active = " *(active)*" if _is_active_model(m) else ""
                lines.append(f"    {m}{active}")
        lines.append("\nUse `/config model <model_name>` to switch.")
        await _send_model_reply(msg, store, manager, thread_id, "\n".join(lines))
        return True

    # ── /_models_provider: Show model buttons ─────────────────────
    if cmd == "/_models_provider":
        provider_name = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
        if not provider_name:
            return True

        models = _get_provider_models(provider_name)
        if not models:
            await _send_model_reply(msg, store, manager, thread_id,
                f"No models found for provider '{provider_name}'.")
            return True

        if msg.platform == "telegram":
            try:
                from kazma_gateway.adapters.telegram import TelegramAdapter
                keyboard = TelegramAdapter.build_model_keyboard(provider_name, models)
                reply_ctx = dict(ctx)
                reply_ctx["reply_markup"] = keyboard
                model_lines = "\n".join(f"  • {m}" for m in models)
                await manager.send(OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=f"Select a model from {provider_name}:\n\n{model_lines}",
                    context_metadata=reply_ctx,
                ))
                return True
            except Exception as exc:
                logger.debug("Interactive model selection failed: %s", exc)

        lines = [f"Models for {provider_name}:\n"]
        for m in models:
            active = " *(active)*" if _is_active_model(m) else ""
            lines.append(f"  {m}{active}")
        await _send_model_reply(msg, store, manager, thread_id, "\n".join(lines))
        return True

    # ── /_models_select: Switch active model ──────────────────────
    if cmd == "/_models_select":
        model_id = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
        if not model_id:
            return True

        try:
            from kazma_core.model_registry import get_model_registry
            reg = get_model_registry()
            reg.set_active_model(model_id)
            await _send_model_reply(msg, store, manager, thread_id,
                f"✅ Switched to **{model_id}** (provider: {reg._active_provider})")
        except Exception as exc:
            await _send_model_reply(msg, store, manager, thread_id,
                f"⚠️ Failed to switch model: {exc}")
        return True

    return False


def _get_provider_models(provider_name: str) -> list[str]:
    """Return visible models for a provider."""
    try:
        from kazma_core.model_registry import get_model_registry
        reg = get_model_registry()
        return reg.get_visible_models(provider_name)
    except Exception:
        return []


def _is_active_model(model_id: str) -> bool:
    """Check if a model is the active model."""
    try:
        from kazma_core.model_registry import get_model_registry
        reg = get_model_registry()
        return reg._active_model == model_id
    except Exception:
        return False


async def _send_model_reply(
    msg: IncomingMessage,
    store: SessionStore,
    manager: Any,
    thread_id: str,
    text: str,
) -> None:
    """Send a model command reply through the gateway."""
    ctx = await store.get(thread_id)
    if not ctx:
        ctx = msg.context_metadata
    await manager.send(OutboundMessage(
        target_id=_build_target_id(msg.platform, ctx),
        text=text,
        context_metadata=ctx,
    ))


async def _build_slash_ctx(
    thread_id: str,
    msg: IncomingMessage,
    state: dict[str, Any],
    store: SessionStore,
) -> dict[str, Any]:
    """Build rich context for slash commands with real data."""
    ctx: dict[str, Any] = {
        "thread_id": thread_id,
        "platform": msg.platform,
    }

    # Active model
    try:
        from kazma_core.model_registry import get_model_registry
        reg = get_model_registry()
        ctx["model"] = reg._active_model or "default"
    except Exception:
        ctx["model"] = "default"

    # Token / cost data from checkpoint state
    try:
        messages = state.get("messages", [])
        ctx["token_count"] = sum(
            len(str(m.get("content", ""))) // 4
            for m in messages
            if isinstance(m, dict)
        )
    except Exception:
        ctx["token_count"] = 0

    # Memory count
    try:
        from kazma_core.agent_runner import get_agent
        agent = get_agent()
        if agent and agent.memory:
            ctx["memory_count"] = len(agent.memory)
    except Exception as exc:
        logger.debug("Failed to get agent memory count: %s", exc)

    # Cost data from cost breaker
    ctx["total_tokens"] = 0
    ctx["total_cost"] = 0.0

    # Gateway status
    ctx["started"] = True
    ctx["adapters"] = msg.platform
    ctx["queue_depth"] = 0
    ctx["active_threads"] = 1

    return ctx


def create_graph_handler(
    graph: Any,
    manager: Any,  # GatewayManager (avoid circular import)
    system_prompt: str = "",
    cost_breaker: Any = None,
    store: SessionStore | None = None,
) -> Callable[[IncomingMessage], Awaitable[None]]:
    """Create an async handler that processes messages through LangGraph.

    Args:
        graph:          Compiled LangGraph supervisor graph.
        manager:        GatewayManager instance (for send() routing).
        system_prompt:  System prompt for the agent.
        cost_breaker:   Optional CostCircuitBreaker for budget control.
        store:          SessionStore for platform context persistence.
                        Falls back to in-memory store if not provided.

    Returns:
        Async handler function compatible with manager.on_message().
    """
    # Use provided store or fall back to in-memory
    _store = store or _InMemoryStore()

    # Per-sender session tracking (sender_id → thread_id).
    # Guarded by _sessions_lock because concurrent handler invocations for
    # different senders read/write this shared dict.
    #
    # Bounded LRU: evicts the least-recently-used sender when the store
    # exceeds _MAX_DICT_ENTRIES (default 10 000).
    _sessions: OrderedDict[str, str] = OrderedDict()
    _sessions_lock = asyncio.Lock()

    # Per-thread_id serialization lock. Two concurrent messages for the same
    # thread_id must not interleave graph.ainvoke() / checkpoint writes, or
    # the LangGraph state and SQLite checkpoint will corrupt. Each distinct
    # thread_id gets its own asyncio.Lock so unrelated threads stay parallel.
    #
    # Bounded LRU: evicts the least-recently-used lock when the store
    # exceeds _MAX_DICT_ENTRIES (default 10 000).
    _thread_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
    _thread_locks_lock = asyncio.Lock()

    # Session TTL: entries survive agent replies (for crash-recovery routing)
    # and are evicted lazily by this many seconds of inactivity.
    _session_ttl_seconds = 300  # 5 minutes

    async def _get_thread_lock(thread_id: str) -> asyncio.Lock:
        """Return (creating if needed) the serialization lock for a thread_id.

        Uses LRU ordering: existing entries are moved to the end
        (most-recently-used) and the oldest entry is evicted when the
        bound is exceeded.
        """
        async with _thread_locks_lock:
            lock = _thread_locks.get(thread_id)
            if lock is not None:
                _thread_locks.move_to_end(thread_id)
                return lock
            lock = asyncio.Lock()
            _thread_locks[thread_id] = lock
            # Evict oldest entries, but skip any that are currently held
            while len(_thread_locks) > _MAX_DICT_ENTRIES:
                # Find the oldest non-held lock to evict
                evicted = False
                for key in list(_thread_locks.keys()):
                    if not _thread_locks[key].locked():
                        _thread_locks.pop(key)
                        evicted = True
                        break
                if not evicted:
                    # All locks are held — keep growing rather than
                    # breaking mutual exclusion
                    break
            return lock

    async def handler(msg: IncomingMessage) -> None:
        """Process a single IncomingMessage through the agent graph."""
        sender = msg.sender_id

        # Resolve thread_id using standardized resolver (synchronized)
        async with _sessions_lock:
            if sender in _sessions:
                # LRU: mark as most-recently-used.
                _sessions.move_to_end(sender)
                thread_id = _sessions[sender]
            else:
                _sessions[sender] = _resolve_thread(msg)
                while len(_sessions) > _MAX_DICT_ENTRIES:
                    _sessions.popitem(last=False)
                thread_id = _sessions[sender]

        # Inject the resolved thread_id into context_metadata
        # so _build_initial_state can pick it up
        msg.context_metadata["thread_id"] = thread_id

        # Cost breaker gate
        if cost_breaker and cost_breaker.should_halt():
            # Restore platform context for the reply
            ctx = await _store.get(thread_id)
            if not ctx:
                ctx = msg.context_metadata
            await manager.send(
                OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text="⚠️ ميزانية الجلسة انتهت. (Budget exceeded)",
                    context_metadata=ctx,
                )
            )
            return

        if cost_breaker:
            cost_breaker.record_user_interaction()

        # ── Build platform-agnostic state ──────────────────────────
        state = await _build_initial_state(msg, _store)

        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

        # ── Interactive model selector (/models, /_models_provider, /_models_select) ──
        model_handled = await _try_model_command(msg, _store, manager, thread_id)
        if model_handled:
            return

        # ── HITL approval (/hitl approve|deny <thread_id>) ──────────
        # Resumes a graph paused at interrupt(). Synthetic messages are
        # generated by the Telegram callback handler's hitl: vocabulary.
        if msg.text and msg.text.strip().lower().startswith("/hitl "):
            hitl_handled = await _handle_hitl_resume(
                msg, graph, config, thread_id, _store, manager,
            )
            if hitl_handled:
                return

        # ── /reset: Clear conversation for this thread ──────────────
        # Send confirmation and skip graph so the next message starts fresh.
        if msg.text and msg.text.strip().lower() == "/reset":
            ctx_reset = await _store.get(thread_id)
            if not ctx_reset:
                ctx_reset = msg.context_metadata
            await manager.send(OutboundMessage(
                target_id=_build_target_id(msg.platform, ctx_reset),
                text="🔄 Conversation cleared. Starting fresh.",
                context_metadata=ctx_reset,
            ))
            logger.info("[agent-handler] /reset for thread=%s", thread_id)
            return

        # ── Slash-command intercept (/model, /help, /reset, etc.) ──
        # Resolve common commands without an LLM call. This keeps
        # responses instant and saves tokens.
        try:
            from kazma_gateway.slash_commands import is_slash_command, resolve_slash_command

            if is_slash_command(msg.text):
                # Build context for the command resolver with real data
                slash_ctx = await _build_slash_ctx(thread_id, msg, state, _store)

                reply = resolve_slash_command(msg.text, context=slash_ctx)
                if reply is not None:
                    # Command was recognised — send the response and skip graph
                    ctx = await _store.get(thread_id)
                    if not ctx:
                        ctx = msg.context_metadata
                    await manager.send(
                        OutboundMessage(
                            target_id=_build_target_id(msg.platform, ctx),
                            text=reply,
                            context_metadata=ctx,
                        )
                    )
                    logger.info(
                        "[agent-handler] Slash command resolved (cmd=%s, thread=%s)",
                        msg.text.strip().split()[0] if msg.text else "?",
                        thread_id,
                    )
                    return
        except ImportError:
            pass  # slash_commands module not available

        # ── Swarm slash-command intercept ──────────────────────────
        # If the message starts with /swarm, dispatch to the swarm engine
        # instead of the single-agent graph.
        swarm_handled = await _try_swarm_command(
            msg, state, _store, manager, thread_id,
        )
        if swarm_handled:
            return  # swarm dispatched, skip graph

        # ── Serialize per thread_id ────────────────────────────────
        # Two concurrent messages for the same thread_id must NOT interleave
        # graph.ainvoke() calls, or LangGraph checkpoints and messages will
        # corrupt. Different thread_ids use different locks and stay parallel.
        thread_lock = await _get_thread_lock(thread_id)

        async with thread_lock:
            # ── Invoke graph ───────────────────────────────────────
            start = time.monotonic()
            try:
                result_state = await graph.ainvoke(state, config)
                duration_ms = (time.monotonic() - start) * 1000

                # ── HITL: detect interrupt() pause ──────────────────
                # When tool_worker_node calls interrupt() for a danger
                # tool, ainvoke returns a partial state and the graph is
                # paused at the checkpoint. Surface an approval prompt so
                # the user can resume via /hitl approve {thread_id}.
                hitl_payload = await _check_graph_interrupt(graph, config)
                if hitl_payload is not None:
                    ctx = await _store.get(thread_id)
                    if not ctx:
                        ctx = msg.context_metadata
                    prompt = _build_approval_prompt(hitl_payload, thread_id)
                    # reply_markup travels inside context_metadata — the
                    # Telegram adapter reads it from there.
                    send_ctx = dict(ctx)
                    if prompt.get("markup"):
                        send_ctx["reply_markup"] = prompt["markup"]
                    await manager.send(
                        OutboundMessage(
                            target_id=_build_target_id(msg.platform, ctx),
                            text=prompt["text"],
                            context_metadata=send_ctx,
                        )
                    )
                    logger.info(
                        "[agent-handler] HITL interrupt surfaced: thread=%s tool=%s",
                        thread_id, hitl_payload.get("tool"),
                    )
                    return  # graph paused; resume on /hitl response

                messages = result_state.get("messages", [])
                assistant_text = ""
                for m in reversed(messages):
                    if isinstance(m, dict) and m.get("role") == "assistant" and m.get("content"):
                        assistant_text = m["content"]
                        break

                if not assistant_text:
                    assistant_text = "(No response generated)"

                logger.info(
                    "[agent-handler] Graph completed in %.0fms (thread=%s, platform=%s)",
                    duration_ms,
                    thread_id,
                    msg.platform,
                )

                # ── Restore platform IDs from SessionStore ─────────
                # The entry is intentionally NOT deleted here. It must persist
                # so crash-recovery routing can rehydrate the platform context
                # (chat_id, user_id) on the next inbound message. Stale
                # entries are evicted lazily by TTL below.
                ctx = await _store.get(thread_id)

                await manager.send(
                    OutboundMessage(
                        target_id=_build_target_id(msg.platform, ctx),
                        text=assistant_text,
                        context_metadata=ctx,
                    )
                )

            except Exception:
                logger.exception("[agent-handler] Graph invocation failed for %s", sender)
                # Use msg.context_metadata directly instead of re-accessing
                # the store (which may be the source of the original exception)
                ctx = msg.context_metadata
                await manager.send(
                    OutboundMessage(
                        target_id=_build_target_id(msg.platform, ctx),
                        text="⚠️ حدث خطأ أثناء معالجة رسالتك. (Processing error)",
                        context_metadata=ctx,
                    )
                )

        # ── Lazy TTL eviction ──────────────────────────────────────
        # Opportunistically prune sessions that have been inactive longer than
        # the TTL. This bounds the store size over time without deleting live
        # entries that crash recovery still needs.
        try:
            await _store.evict_older_than(_session_ttl_seconds)
        except Exception:
            logger.debug("[agent-handler] TTL eviction skipped (store may not support it)")

    # ── Register telegram backend with core's send_message dispatcher ──
    try:
        from kazma_core.tools.send_message import register_message_backend

        async def _telegram_backend_handler(target_id: str, text: str) -> str:
            ctx = await _store.get(target_id)
            if not ctx:
                ctx = {"thread_id": target_id}
            outbound = OutboundMessage(target_id=target_id, text=text, context_metadata=ctx)
            await manager.send(outbound)
            return f"sent:{target_id}"

        register_message_backend("telegram", _telegram_backend_handler)
    except ImportError:
        logger.debug("[agent-handler] kazma_core not available — backend registration skipped")

    return handler


def _build_target_id(platform: str, ctx: dict[str, Any]) -> str:
    """Build a platform-prefixed target ID from context_metadata.

    Args:
        platform: "telegram", "discord", etc.
        ctx: The restored context_metadata (may be empty on error).

    Returns:
        e.g. "telegram:12345" or "telegram:unknown"
    """
    # Telegram uses chat_id
    chat_id = ctx.get("chat_id")
    if chat_id is not None:
        return f"{platform}:{chat_id}"

    # Discord would use channel_id, etc.
    return f"{platform}:unknown"


# ══════════════════════════════════════════════════════════════════════════
# HITL helpers — graph interrupt detection + approval resume
# ══════════════════════════════════════════════════════════════════════════


async def _check_graph_interrupt(graph: Any, config: dict[str, Any]) -> dict[str, Any] | None:
    """Return the hitl_approval interrupt payload if the graph is paused, else None.

    After ``graph.ainvoke()`` returns, an interrupt() in tool_worker_node
    leaves the graph paused at a checkpoint. This inspects the snapshot
    for a pending ``hitl_approval`` task and returns its payload.
    """
    try:
        snapshot = await graph.aget_state(config)
    except Exception as exc:
        logger.debug("[HITL] aget_state unavailable: %s", exc)
        return None
    if not getattr(snapshot, "next", None):
        return None  # graph completed normally
    for task in getattr(snapshot, "tasks", []) or []:
        for intr in getattr(task, "interrupts", []) or []:
            payload = getattr(intr, "value", None)
            if isinstance(payload, dict) and payload.get("type") == "hitl_approval":
                return payload
    return None


def _build_approval_prompt(payload: dict[str, Any], thread_id: str) -> dict[str, Any]:
    """Build the approval prompt text + optional inline keyboard.

    For Telegram the keyboard uses the ``hitl:`` vocabulary so the
    existing callback handler produces a synthetic ``/hitl`` message.
    Other platforms get a plain-text instruction with the thread_id.
    """
    tool = payload.get("tool", "unknown")
    args = payload.get("args", {})
    args_str = str(args)
    if len(args_str) > 300:
        args_str = args_str[:300] + "…"
    text = (
        f"⚠️ Approval required\n"
        f"Tool: {tool}\n"
        f"Args: {args_str}\n\n"
        f"Reply: /hitl approve {thread_id}\n"
        f"   or: /hitl deny {thread_id}"
    )
    markup = None
    try:
        from kazma_gateway.adapters.telegram import TelegramAdapter

        markup = TelegramAdapter.build_approval_keyboard(thread_id)
    except Exception:
        pass  # non-Telegram platforms — plain text fallback
    return {"text": text, "markup": markup}


async def _handle_hitl_resume(
    msg: IncomingMessage,
    graph: Any,
    config: dict[str, Any],
    thread_id: str,
    store: SessionStore,
    manager: Any,
) -> bool:
    """Process a ``/hitl approve|deny <thread_id>`` message.

    Resumes the paused graph with ``Command(resume=...)`` and sends the
    resulting assistant reply back to the platform.

    Returns True if the message was handled (always, for /hitl).
    """
    parts = msg.text.strip().split()
    # Expected: /hitl <action> <thread_id>
    if len(parts) < 2:
        return False

    action = parts[1].lower()
    approved = action in ("approve", "yes", "y", "allow")
    # The target thread_id defaults to the current sender's thread but can
    # be overridden by the third argument (for cross-thread approvals).
    target_thread = parts[2] if len(parts) >= 3 else thread_id
    resume_config = {"configurable": {"thread_id": target_thread, "checkpoint_ns": ""}}

    ctx = await store.get(thread_id)
    if not ctx:
        ctx = msg.context_metadata

    # Authorization: verify that the requester is the same user who
    # initiated the paused task. Look up the target thread's context
    # and compare sender_id. This prevents any user from approving
    # another user's paused danger-tool execution.
    if target_thread != thread_id:
        target_ctx = await store.get(target_thread)
        if target_ctx:
            original_sender = target_ctx.get("sender_id", "")
            current_sender = msg.sender_id
            if original_sender and original_sender != current_sender:
                logger.warning(
                    "[HITL] Authz denied: %s tried to approve thread %s owned by %s",
                    current_sender, target_thread, original_sender,
                )
                await manager.send(
                    OutboundMessage(
                        target_id=_build_target_id(msg.platform, ctx),
                        text="⚠️ You are not authorized to approve this task.",
                        context_metadata=ctx,
                    )
                )
                return True

    try:
        from langgraph.types import Command

        logger.info(
            "[HITL] Resume: thread=%s approved=%s action=%s",
            target_thread, approved, action,
        )
        result_state = await graph.ainvoke(
            Command(resume={"approved": approved, "reason": action}),
            resume_config,
        )

        # Extract the assistant's response from the resumed turn.
        assistant_text = ""
        messages = result_state.get("messages", []) if isinstance(result_state, dict) else []
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role") == "assistant" and m.get("content"):
                assistant_text = m["content"]
                break
        if not assistant_text:
            assistant_text = "✅ Approved — continuing." if approved else "🚫 Denied."

        await manager.send(
            OutboundMessage(
                target_id=_build_target_id(msg.platform, ctx),
                text=assistant_text,
                context_metadata=ctx,
            )
        )
    except Exception:
        logger.exception("[HITL] Resume failed for thread=%s", target_thread)
        await manager.send(
            OutboundMessage(
                target_id=_build_target_id(msg.platform, ctx),
                text="⚠️ HITL resume failed — no paused task found for that session.",
                context_metadata=ctx,
            )
        )

    return True
