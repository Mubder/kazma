"""Swarm dispatch submodule — auto-routing, target configs, and dispatches to SwarmEngine."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from kazma_core.constants import (
    SWARM_DISPATCH_TIMEOUT_SECONDS,
    SWARM_TASK_PREVIEW_MAX_CHARS,
    TELEGRAM_MIN_CHAT_ID,
    TELEGRAM_MAX_CHAT_ID,
    VALID_OUTPUT_PLATFORMS,
)
from kazma_core.exceptions import sanitize_error
from kazma_gateway.gateway import IncomingMessage, OutboundMessage, SessionStore
from kazma_gateway.telegram_format import format_swarm_task_result, md_to_tg_html
from .store import _build_target_id
from .swarm_output import send_swarm_output

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
    match = re.search(r"\s*->\s*(\S+)\s*$", task)
    if not match:
        return task, None

    raw = match.group(1).strip()
    clean = task[: match.start()].rstrip()

    if ":" in raw:
        platform, _, chat_id_str = raw.partition(":")
        platform = platform.lower()
        
        # Validate platform
        if platform not in VALID_OUTPUT_PLATFORMS:
            logger.info(
                "[agent-handler] Invalid output platform %r in suffix; "
                "valid: %s; task left untouched", platform, VALID_OUTPUT_PLATFORMS
            )
            return task, None
        
        try:
            chat_id = int(chat_id_str)
        except ValueError:
            logger.info(
                "[agent-handler] Ignoring malformed output-target suffix %r "
                "(chat_id not an integer); task left untouched", raw,
            )
            return task, None  # malformed — leave prompt untouched
        
        # Validate chat_id for known platforms
        if platform == "telegram":
            if not (TELEGRAM_MIN_CHAT_ID <= chat_id <= TELEGRAM_MAX_CHAT_ID or chat_id > 0):
                logger.warning(
                    "[agent-handler] Telegram chat_id %d out of valid range "
                    "[%d, %d] or positive; task left untouched",
                    chat_id, TELEGRAM_MIN_CHAT_ID, TELEGRAM_MAX_CHAT_ID
                )
                return task, None
        
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
    *,
    is_html: bool = False,
) -> bool:
    """Send ``text`` to the configured output target (e.g. a Telegram group).

    Resolution order: per-dispatch ``override`` dict → ConfigStore entry.

    When ``is_html`` is True the text is already Telegram HTML (e.g. from
    :func:`format_swarm_task_result`) and must NOT be re-converted by the
    adapter — passing it through ``md_to_tg_html`` would double-escape it.

    Delegates to the platform-specific adapter in ``swarm_output``.

    Returns True if a message was sent (or attempted), False if no target.
    """
    return await send_swarm_output(manager, text, override, is_html=is_html)


def _output_target_is_origin(msg: IncomingMessage, override: dict[str, Any] | None) -> bool:
    """Return True if the resolved output target is the same chat the message
    originated from.

    Used to avoid sending the same swarm report twice (once via the direct
    reply and once via the output-target mirror) when the operator dispatches
    from the very group configured as the output target.
    """
    cfg = override if override is not None else _get_output_target_config()
    if not cfg or not isinstance(cfg, dict):
        return False
    target_platform = str(cfg.get("platform", "telegram")).lower()
    target_chat = str(cfg.get("chat_id", "")).strip()
    if not target_chat:
        return False
    if target_platform != msg.platform:
        return False
    origin_chat = str(msg.context_metadata.get("chat_id", "")).strip()
    return bool(origin_chat) and origin_chat == target_chat


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
        result = await asyncio.wait_for(
            engine.dispatch(swarm_task),
            timeout=SWARM_DISPATCH_TIMEOUT_SECONDS
        )

        # Format result for chat
        if result and result.aggregated_output:
            reply = result.aggregated_output
        elif result and result.worker_results:
            lines = []
            for wr in result.worker_results:
                status_icon = "✅" if wr.status == "success" else "❌"
                # Full worker output for complete reference.
                output = wr.output if wr.output else (wr.error or "no output")
                lines.append(f"{status_icon} **{wr.worker}**: {output}")
            reply = "\n\n".join(lines)
        elif result and result.error:
            reply = f"⚠️ Task failed: {result.error}"
        else:
            reply = "⚠️ No result returned from swarm."

        # Telegram gets rich quoted report formatting; other platforms use plain text.
        if msg.platform == "telegram" and result is not None:
            worker_rows = [wr.to_dict() for wr in getattr(result, "worker_results", [])]
            telegram_reply = format_swarm_task_result(
                task_id=getattr(result, "task_id", "") or "",
                status=getattr(result, "status", "") or "",
                aggregated_output=getattr(result, "aggregated_output", "") or "",
                error=getattr(result, "error", "") or "",
                duration=float(getattr(result, "duration_seconds", 0.0) or 0.0),
                tokens=int(getattr(result, "total_tokens", 0) or 0),
                worker_results=worker_rows,
            )
            await _send_swarm_reply(
                msg,
                store,
                manager,
                thread_id,
                telegram_reply,
                text_is_html=True,
            )
            # Mirror the SAME rich HTML report to the output target so the
            # Telegram group sees the formatted report, not raw markdown.
            # Skip when the output target IS the originating chat — otherwise
            # the operator gets the report twice.
            if not _output_target_is_origin(msg, target_override):
                await _maybe_send_to_output_target(
                    manager, telegram_reply, target_override, is_html=True
                )
        else:
            await _send_swarm_reply(msg, store, manager, thread_id, reply)
            if not _output_target_is_origin(msg, target_override):
                await _maybe_send_to_output_target(manager, reply, target_override)

    except asyncio.TimeoutError:
        logger.error("[agent-handler] Swarm dispatch timed out after %ds for thread %s",
                     SWARM_DISPATCH_TIMEOUT_SECONDS, thread_id)
        error_reply = "⚠️ Swarm task timed out after 5 minutes. The task may still be running in background."
        await _send_swarm_reply(msg, store, manager, thread_id, error_reply)
        if not _output_target_is_origin(msg, target_override):
            await _maybe_send_to_output_target(manager, error_reply, target_override)
    except Exception as exc:
        logger.exception("[agent-handler] Swarm dispatch failed for thread %s", thread_id)
        error_reply = sanitize_error(exc)
        await _send_swarm_reply(msg, store, manager, thread_id, error_reply)
        # Mirror the failure to the output target too, consistent with the success path.
        if not _output_target_is_origin(msg, target_override):
            await _maybe_send_to_output_target(manager, error_reply, target_override)

    return True


async def _send_swarm_reply(
    msg: IncomingMessage,
    store: SessionStore,
    manager: Any,
    thread_id: str,
    text: str,
    text_is_html: bool = False,
) -> None:
    """Send a reply through the gateway manager (platform-agnostic).
    
    For Telegram, applies markdown-to-HTML conversion so **bold** renders
    inside blockquotes instead of showing literal asterisks.
    """
    ctx = await store.get(thread_id)
    if not ctx:
        ctx = msg.context_metadata
    
    # Apply markdown-to-HTML conversion for Telegram platform.
    # If text_is_html=True, preserve trusted preformatted Telegram HTML.
    final_text = text
    if msg.platform == "telegram":
        final_text = text if text_is_html else md_to_tg_html(text)
        ctx.setdefault("parse_mode", "HTML")
    
    await manager.send(
        OutboundMessage(
            target_id=_build_target_id(msg.platform, ctx),
            text=final_text,
            context_metadata=ctx,
        )
    )
