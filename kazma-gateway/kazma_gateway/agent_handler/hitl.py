"""HITL submodule — graph interrupt detection and approval resume handlers."""

from __future__ import annotations

import logging
from typing import Any

from kazma_gateway.gateway import IncomingMessage, OutboundMessage, SessionStore
from .store import _build_target_id

logger = logging.getLogger(__name__)

__all__: list[str] = []


async def _stale_approval_message(
    graph: Any,
    config: dict[str, Any],
    thread_id: str,
    *,
    action: str,
    approved: bool,
) -> str | None:
    """Human message for a late/duplicate approve, or None to stay silent.

    If we recently completed a resume for this thread, silence the second tap.
    If the graph already finished with an assistant answer, say so honestly —
    never claim "Nothing was executed" when work already completed.
    """
    import time

    try:
        from kazma_core.config_store import get_config_store

        last = get_config_store().get(f"hitl.last_resume.{thread_id}")
        if isinstance(last, dict):
            at = float(last.get("at") or 0)
            if at and (time.time() - at) < 90:
                # Duplicate within 90s of a successful resume — no spam.
                return None
    except Exception:
        pass

    # Graph finished (no next nodes) — likely already approved and completed.
    try:
        snapshot = await graph.aget_state(config)
        next_nodes = getattr(snapshot, "next", None) or ()
        vals = getattr(snapshot, "values", None) or {}
        msgs = vals.get("messages") or []
        has_assistant = any(
            isinstance(m, dict)
            and m.get("role") in ("assistant", "ai")
            and str(m.get("content") or "").strip()
            and not m.get("tool_calls")
            for m in msgs[-6:]
        )
        if not next_nodes and has_assistant and approved:
            return (
                "✅ Already handled — this approval was applied earlier and the "
                "turn finished. You can ignore this button (or send a new request)."
            )
        if not next_nodes and not approved:
            return (
                "ℹ️ No pending approval on this chat (already resolved or expired)."
            )
    except Exception:
        logger.debug("[HITL] stale-message state probe failed", exc_info=True)

    return (
        "⚠️ This approval card is no longer active "
        "(already resolved, or a newer turn replaced it).\n"
        "If you still need the action, send your request again."
    )


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
            if payload is None and isinstance(intr, dict):
                payload = intr.get("value", intr)
            if isinstance(payload, (list, tuple)) and payload:
                payload = payload[0]
            if isinstance(payload, dict) and payload.get("type") == "hitl_approval":
                return payload
            # Fallback: tool/args shape without type tag
            if isinstance(payload, dict) and (
                "tool" in payload or "args" in payload or "tools" in payload
            ):
                return {
                    "type": "hitl_approval",
                    "tool": payload.get("tool", "unknown"),
                    "args": payload.get("args", payload.get("arguments", {})),
                    "tools": payload.get("tools") or [],
                    "message": payload.get("message", ""),
                }
    return None


def _build_approval_prompt(
    payload: dict[str, Any],
    thread_id: str,
    *,
    platform: str = "telegram",
) -> dict[str, Any]:
    """Build the approval prompt text + platform-specific interactive controls.

    All platforms share the ``hitl:approve|deny:<id>`` action vocabulary
    (Telegram keyboards, Discord components, Slack Block Kit).
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
        f"Reply: hitl approve {thread_id}\n"
        f"   or: hitl deny {thread_id}"
    )
    markup = None
    plat = (platform or "telegram").lower()
    try:
        if plat == "telegram":
            from kazma_gateway.adapters.telegram import TelegramAdapter

            markup = TelegramAdapter.build_approval_keyboard(thread_id)
        elif plat == "discord":
            from kazma_gateway.adapters.discord import DiscordAdapter

            markup = DiscordAdapter.build_approval_keyboard(thread_id)
        elif plat == "slack":
            from kazma_gateway.adapters.slack import SlackAdapter

            markup = SlackAdapter.build_approval_keyboard(thread_id)
    except Exception as exc:
        logger.debug(
            "Approval keyboard build failed for platform=%s: %s",
            plat,
            exc,
            exc_info=True,
        )
    return {"text": text, "markup": markup, "platform": plat}


async def _handle_hitl_resume(
    msg: IncomingMessage,
    graph: Any,
    config: dict[str, Any],
    thread_id: str,
    store: SessionStore,
    manager: Any,
    lock_getter: Any = None,
) -> bool:
    """Process a ``hitl approve|deny <thread_id>`` message.

    The leading ``/`` is optional — platforms that block slash-commands
    (Slack) use the bare ``hitl`` prefix.

    Resumes the paused graph with ``Command(resume=...)`` and sends the
    resulting assistant reply back to the platform.

    ``lock_getter`` is the handler's per-thread lock factory. The resume
    MUST serialize against regular turns on the same thread: a concurrent
    ``graph.ainvoke()`` would interleave checkpoint writes and corrupt the
    message history.

    Stale cards: on langgraph >= 1.x, a new user message on a paused thread
    silently discards the pending interrupt, and a later ``Command(resume=)``
    is a silent no-op that replays old state. We therefore verify a pending
    ``hitl_approval`` interrupt actually exists before resuming, and tell
    the user when their card has expired instead of pretending to approve.

    Returns True if the message was handled (always, for hitl commands).
    """
    parts = msg.text.strip().split()
    # Expected: [hitl|/hitl] <action> <thread_id>
    if len(parts) < 2:
        return False

    cmd = parts[0].lower().lstrip("/")
    if cmd != "hitl":
        return False

    action = parts[1].lower()
    approved = action in ("approve", "yes", "y", "allow", "approve_task")
    is_task_grant = action == "approve_task"
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
    # Fail-closed: if the target session is missing, deny cross-thread
    # approvals rather than skipping the check.
    if target_thread != thread_id:
        target_ctx = await store.get(target_thread)
        if not target_ctx:
            logger.warning(
                "[HITL] Authz denied: cross-thread approve for missing session %s by %s",
                target_thread, msg.sender_id,
            )
            await manager.send(
                OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text="⚠️ Cannot approve: target session not found.",
                    context_metadata=ctx,
                )
            )
            return True
        original_sender = (target_ctx.get("sender_id") or "").strip()
        current_sender = (msg.sender_id or "").strip()
        # Fail-closed (audit M6): empty owner or mismatch → deny
        if not original_sender or not current_sender or original_sender != current_sender:
            logger.warning(
                "[HITL] Authz denied: %s tried to approve thread %s owned by %s",
                current_sender or "(empty)",
                target_thread,
                original_sender or "(empty)",
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
        import contextlib

        from langgraph.types import Command

        lock = await lock_getter(target_thread) if lock_getter is not None else None
        async with (lock if lock is not None else contextlib.AsyncExitStack()):
            # ── Stale-card guard ────────────────────────────────────
            # Verify the graph is actually paused on a hitl_approval
            # interrupt. If not (a newer message superseded the card, or
            # it was already resolved), Command(resume=...) would be a
            # silent no-op replaying old state — the "approve did nothing"
            # bug. Fail loudly instead.
            pending = await _check_graph_interrupt(graph, resume_config)
            if pending is None:
                # Stale/duplicate callback after a successful resume is common
                # (double-tap, Telegram retries). Prefer a calm message — and
                # suppress spam within a short debounce window.
                soft = await _stale_approval_message(
                    graph, resume_config, target_thread, action=action, approved=approved
                )
                logger.info(
                    "[HITL] Stale approval ignored: thread=%s action=%s "
                    "(no pending interrupt) soft=%s",
                    target_thread,
                    action,
                    soft is not None,
                )
                if soft:
                    await manager.send(
                        OutboundMessage(
                            target_id=_build_target_id(msg.platform, ctx),
                            text=soft,
                            context_metadata=ctx,
                        )
                    )
                return True

            logger.info(
                "[HITL] Resume: thread=%s approved=%s action=%s",
                target_thread, approved, action,
            )
            # Mark successful resume so a late second callback stays quiet.
            try:
                from kazma_core.config_store import get_config_store
                import time as _time

                get_config_store().set(
                    f"hitl.last_resume.{target_thread}",
                    {
                        "at": _time.time(),
                        "action": action,
                        "approved": approved,
                    },
                    category="safety",
                )
            except Exception:
                pass

            # ── Task grant: auto-approve all danger tools until next message ──
            # When the user clicks "Approve for task", grant ALL danger tools
            # for this thread so subsequent turns skip the approval card
            # entirely. The grant auto-clears on the next user message (not
            # a callback) or after the TTL (default 10 min).
            if is_task_grant and approved:
                try:
                    from kazma_core.safety.task_grants import grant_task

                    actor = (msg.sender_id or msg.user_id or "unknown")
                    grant_task(target_thread, actor=actor)
                    logger.info(
                        "[HITL] Task grant activated for thread=%s actor=%s",
                        target_thread, actor,
                    )
                except Exception as exc:
                    logger.warning("[HITL] Task grant failed: %s — continuing with single approve", exc)

            # Phase 3/§4.3: semantic interrupts need {tcid: option_id}; security
            # needs {approved: bool}. The existing approve/deny buttons map to
            # "best option" / "cancel" for semantic via build_resume_value.
            from kazma_core.safety.commitment.resume import build_resume_value, is_semantic_kind

            if is_semantic_kind(pending):
                _resume_val = build_resume_value(pending, approved)
            else:
                _resume_val = {"approved": approved, "reason": action,
                               "scope": "task" if is_task_grant else "once"}
            result_state = await graph.ainvoke(
                Command(resume=_resume_val),
                resume_config,
            )

            # Re-surface a chained interrupt (a second danger tool paused in
            # the same resumed turn). Without this the user sees "Approved —
            # continuing." while the graph is actually still paused on
            # another tool, so the agent appears to "do nothing".
            chained = await _check_graph_interrupt(graph, resume_config)

        from .graph import _prepare_tg_outbound

        # Extract the assistant's response from the resumed turn.
        assistant_text = ""
        messages = result_state.get("messages", []) if isinstance(result_state, dict) else []
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role") == "assistant" and m.get("content"):
                assistant_text = m["content"]
                break

        # If there is assistant text, send it first
        if assistant_text:
            tg_text, tg_ctx = _prepare_tg_outbound(msg, assistant_text, ctx)
            await manager.send(
                OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=tg_text,
                    context_metadata=tg_ctx,
                )
            )

        if chained is not None:
            prompt = _build_approval_prompt(
                chained, target_thread, platform=msg.platform
            )
            prompt_text, send_ctx = _prepare_tg_outbound(msg, prompt["text"], ctx)
            if prompt.get("markup"):
                if msg.platform == "telegram":
                    send_ctx["reply_markup"] = prompt["markup"]
                elif msg.platform == "discord":
                    send_ctx["components"] = prompt["markup"]
                elif msg.platform == "slack":
                    send_ctx["blocks"] = prompt["markup"]
            await manager.send(
                OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=prompt_text,
                    context_metadata=send_ctx,
                )
            )
            return True

        if not assistant_text:
            fallback_text = "✅ Approved — continuing." if approved else "🚫 Denied."
            await manager.send(
                OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=fallback_text,
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
