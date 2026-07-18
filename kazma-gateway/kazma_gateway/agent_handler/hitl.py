"""HITL submodule — graph interrupt detection and approval resume handlers."""

from __future__ import annotations

import logging
from typing import Any

from kazma_gateway.gateway import IncomingMessage, OutboundMessage, SessionStore
from .store import _build_target_id

logger = logging.getLogger(__name__)

__all__: list[str] = []


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
        f"Reply: hitl approve {thread_id}\n"
        f"   or: hitl deny {thread_id}"
    )
    markup = None
    try:
        from kazma_gateway.adapters.telegram import TelegramAdapter

        markup = TelegramAdapter.build_approval_keyboard(thread_id)
    except Exception as exc:
        logger.debug("TelegramAdapter approval keyboard build skipped or failed: %s", exc, exc_info=True)
        # non-Telegram platforms — plain text fallback
    return {"text": text, "markup": markup}


async def _handle_hitl_resume(
    msg: IncomingMessage,
    graph: Any,
    config: dict[str, Any],
    thread_id: str,
    store: SessionStore,
    manager: Any,
) -> bool:
    """Process a ``hitl approve|deny <thread_id>`` message.

    The leading ``/`` is optional — platforms that block slash-commands
    (Slack) use the bare ``hitl`` prefix.

    Resumes the paused graph with ``Command(resume=...)`` and sends the
    resulting assistant reply back to the platform.

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
