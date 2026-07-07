"""Swarm dispatch submodule — auto-routing, target configs, and dispatches to SwarmEngine."""

from __future__ import annotations

import logging
from typing import Any

from kazma_gateway.gateway import IncomingMessage, OutboundMessage, SessionStore
from .store import _build_target_id

logger = logging.getLogger(__name__)


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
        from kazma_core.swarm import UnifiedRouter
        from kazma_core.swarm.task import SwarmTask, TaskType

        router = UnifiedRouter()
        temp_task = SwarmTask(
            id="auto-route-temp",
            type=TaskType.DISPATCH,
            prompt=task,
            workers=["auto"],
        )
        routed_workers = await router.route(temp_task, available)
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
           the original group-routing mode. If ``manager`` is None or doesn't
           have the necessary adapters, tries to fall back to the primary bot token
           and send directly as well.

    Sends the *same* output that went to the originating chat. Errors are
    logged but never raised — group routing is best-effort.

    Returns True if a message was sent (or attempted), False if no target.
    """
    target = override if isinstance(override, dict) and override.get("chat_id") else _get_output_target_config()
    if not target:
        return False

    platform = target.get("platform", "telegram")
    chat_id = target.get("chat_id")
    explicit_bot_token = target.get("bot_token", "")

    # Helper function to do Mode 1 direct Telegram send
    async def try_mode1_direct_send(token: str) -> bool:
        if platform != "telegram" or not token:
            return False
        try:
            import httpx
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0)
            ) as client:
                all_chunks_ok = True
                for i in range(0, len(text), 4096):
                    chunk = text[i:i + 4096]
                    chunk_ok = False
                    payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"}
                    resp = await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json=payload,
                    )
                    resp_json = resp.json()
                    if resp_json.get("ok"):
                        chunk_ok = True
                    else:
                        desc = resp_json.get("description", "")
                        logger.warning(
                            "[agent-handler] Swarm bot Markdown send failed: %s. Retrying in plain text...",
                            desc or "unknown",
                        )
                        payload_plain = {"chat_id": chat_id, "text": chunk}
                        resp_plain = await client.post(
                            f"https://api.telegram.org/bot{token}/sendMessage",
                            json=payload_plain,
                        )
                        resp_plain_json = resp_plain.json()
                        if resp_plain_json.get("ok"):
                            chunk_ok = True
                        else:
                            logger.warning(
                                "[agent-handler] Swarm bot fallback plain send failed: %s",
                                resp_plain_json.get("description", "unknown"),
                            )
                    
                    if not chunk_ok:
                        all_chunks_ok = False
                        break
                
                if all_chunks_ok:
                    logger.info(
                        "[agent-handler] Swarm output routed via direct bot to %s",
                        chat_id,
                    )
                    return True
                return False
        except Exception:
            logger.warning(
                "[agent-handler] Failed routing swarm output via direct bot to %s",
                chat_id, exc_info=True,
            )
            return False

    # Helper function to do Mode 2 Gateway adapter send
    async def try_mode2_gateway_send() -> bool:
        if manager is None:
            return False
        try:
            await manager.send(OutboundMessage(
                target_id=f"{platform}:{chat_id}",
                text=text,
                context_metadata={"chat_id": chat_id},
            ))
            logger.info(
                "[agent-handler] Swarm output routed to %s:%s via gateway manager",
                platform, chat_id,
            )
            return True
        except Exception:
            logger.warning(
                "[agent-handler] Failed routing swarm output to %s:%s via gateway manager",
                platform, chat_id, exc_info=True,
            )
            return False

    # Helper function to resolve primary bot token as fallback
    def resolve_primary_token() -> str:
        if platform != "telegram":
            return ""
        try:
            from kazma_core.config_store import get_config_store
            import os
            cs = get_config_store()
            token = ""
            if cs:
                token = cs.get("connectors.telegram.token", "")
            if not token:
                token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            return token
        except Exception:
            return ""

    # Execute resolution logic:
    if explicit_bot_token:
        # Scenario A: User explicitly configured a separate dedicated swarm bot token
        if await try_mode1_direct_send(explicit_bot_token):
            return True
        # If dedicated direct send fails, fall back to gateway manager if available
        if await try_mode2_gateway_send():
            return True
        return False
    else:
        # Scenario B: User wants standard gateway adapter routing
        if await try_mode2_gateway_send():
            return True
        # If manager is None or failed, resolve the primary token and fallback to direct send
        primary_token = resolve_primary_token()
        if primary_token:
            if await try_mode1_direct_send(primary_token):
                return True
        return False

    logger.warning(
        "[agent-handler] Failed routing swarm output to %s:%s: GatewayManager not available.",
        platform, chat_id,
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
