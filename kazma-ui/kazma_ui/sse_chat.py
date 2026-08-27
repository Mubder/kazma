"""SSE Chat Router — Bridges LangGraph astream_events to HTMX/Alpine frontend.

Provides POST /api/chat/stream which:
  1. Receives a user message + optional session_id.
  2. Feeds it through the compiled Supervisor graph.
  3. Streams LangGraph events as SSE text/event-stream frames.

Event contract (matches what the Alpine.js frontend expects):
  event: token       data: {"content": "..."}               — LLM streaming chunk
  event: tool_call   data: {"tool_name": "...", "inputs": "..."}  — tool starting
  event: tool_result data: {"tool_name": "...", "result": "..."}  — tool finished
  event: done        data: {"tokens": N, "cost": 0.xxxx}    — turn complete
  event: error       data: {"content": "..."}                — fatal error
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any, Callable

from fastapi import APIRouter, Depends, Request
from kazma_ui.rate_limit import rate_limit
from fastapi.responses import StreamingResponse

from kazma_core.exceptions import sanitize_error
from kazma_core.shutdown import is_shutting_down

logger = logging.getLogger(__name__)

__all__ = ["create_sse_chat_router", "router"]

router = APIRouter(tags=["chat-sse"])


# ══════════════════════════════════════════════════════════════════════════
# Detached turn registry — keeps graph tasks alive across client disconnects
# ══════════════════════════════════════════════════════════════════════════
# Strong-reference map: thread_id → running graph pump task. Prevents CPython
# from garbage-collecting the task when the SSE generator is cancelled by a
# client disconnect (refresh / tab switch). The task runs to completion;
# the checkpointer + done_callback persist the result so the client finds
# it on reload.
#
# The registry is SHARED with the WebSocket transport (kazma_ui.active_turns)
# so a WS turn is visible to the SSE duplicate-turn guard and to the session
# status endpoint — otherwise a refresh could start a second concurrent
# graph run on the same thread/checkpointer.  ``_active_turns`` stays as a
# back-compat alias to the shared dict.
from kazma_ui.active_turns import (
    DETACHED_TTL_S,
    active_turns,
    cancel_turn,
    get_active_turn,
    get_orphan_stamp,
    is_turn_running,
    mark_turn_orphaned,
    reap_stale_turn,
    register_turn,
    unregister_turn,
)
from kazma_ui.delivery import get_turn_broker, is_replayable

_active_turns = active_turns  # type: ignore[name-defined]

# T1: strong references to detached-pump watchdog tasks so CPython never
# GCs one while its pump is still running.
_watchdog_tasks: set[asyncio.Task] = set()


# ══════════════════════════════════════════════════════════════════════════
# SSE frame helper (imported from shared utility)
# ══════════════════════════════════════════════════════════════════════════

from kazma_ui.sse_utils import sse_frame as _sse_frame


def _convert_messages_to_dicts(langgraph_messages) -> list[dict[str, Any]]:
    dicts = []
    for m in langgraph_messages:
        role = "user"
        content = ""
        if isinstance(m, dict):
            role = m.get("role") or "user"
            content = m.get("content") or ""
        else:
            cls_name = m.__class__.__name__
            if cls_name == "AIMessage":
                role = "assistant"
            elif cls_name == "SystemMessage":
                role = "system"
            else:
                role = "user"
            content = getattr(m, "content", "")
        
        if role in ("system", "user", "assistant") and content:
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in content
                )
            dicts.append({"role": role, "content": str(content).strip()})
    return dicts


def _message_text(m: Any) -> str:
    """Extract plain assistant text from a dict or LangChain message object."""
    if m is None:
        return ""

    if isinstance(m, dict):
        role = (m.get("role") or m.get("type") or "").lower()
        if role in ("user", "system", "tool", "human"):
            return ""
        # assistant / ai / empty role with tool_calls
        if role and role not in ("assistant", "ai") and not m.get("tool_calls"):
            return ""
        text = m.get("content")
    else:
        cls = m.__class__.__name__
        role_attr = (getattr(m, "type", None) or getattr(m, "role", None) or "").lower()
        if cls not in ("AIMessage", "AIMessageChunk") and role_attr not in (
            "ai",
            "assistant",
            "",
        ):
            return ""
        text = getattr(m, "content", None)

    if isinstance(text, list):
        parts: list[str] = []
        for block in text:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            else:
                t = getattr(block, "text", None)
                if t:
                    parts.append(str(t))
        text = "".join(parts)
    if text is None:
        return ""
    return str(text).strip()


def _extract_hitl_payload(intr: Any) -> dict[str, Any] | None:
    """Normalize LangGraph interrupt objects into a hitl payload dict."""
    value = getattr(intr, "value", None)
    if value is None and isinstance(intr, dict):
        value = intr.get("value", intr)
    # Some versions wrap the value in a 1-tuple / list
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    if not isinstance(value, dict):
        return None
    if value.get("type") == "hitl_approval":
        return value
    # Fallback: tool/args shape without type tag (still show a card)
    if "tool" in value or "args" in value or "tools" in value:
        return {
            "type": "hitl_approval",
            "tool": value.get("tool", "unknown"),
            "args": value.get("args", value.get("arguments", {})),
            "tools": value.get("tools") or [],
            "message": value.get("message", ""),
        }
    return None


def _last_assistant_text(messages: list[Any] | None) -> str:
    """Return the last non-empty assistant text from a message list."""
    if not messages:
        return ""
    for m in reversed(list(messages)):
        text = _message_text(m)
        if text:
            return text
    return ""


def _user_facing_reply(*parts: str) -> str:
    """Best user-facing assistant payload (plan fence un-glued). Never raises."""
    try:
        from kazma_core.agent.plan_fence import pick_user_facing_text

        return pick_user_facing_text(*parts)
    except Exception:
        logger.debug("[SSE] plan_fence pick failed", exc_info=True)
        for p in parts:
            if p and str(p).strip():
                return str(p).strip()
        return ""


def _module_store():
    """Session-store accessor for module-level helpers.

    ``_get_store`` is a closure inside the router factory; the extracted
    detached-persist/backfill helpers live at module scope and use the
    same singleton through this alias.
    """
    from kazma_ui.session_manager import get_session_manager

    return get_session_manager()


# Graph accessor for module-level helpers: populated by the router factory
# with its live ``_get_graph`` closure at creation time.
_module_graph_holder: dict[str, Any] = {"getter": None}


def _module_graph() -> Any:
    getter = _module_graph_holder.get("getter")
    if getter is None:
        return None
    try:
        return getter()
    except Exception:
        return None


async def _persist_detached_reply(
    graph: Any, config: dict, session_id: str, thread_id: str
) -> None:
    """Persist a detached turn's final reply into the session store.

    Extracted from the pump done-callback (live incident 2026-08-21) so the
    persistence contract is testable:

    * The reply is APPENDED after a trailing USER message. The old
      ``has_asst`` logic overwrote the PREVIOUS turn's assistant reply in
      multi-turn sessions — it would have replaced the prior answer with
      the new one and lost history.
    * Failures log at WARNING (they were debug-swallowed, leaving the user
      waiting hours for a reply that existed in the checkpoint while the
      log showed nothing).
    """
    try:
        snap = await graph.aget_state(config)
        asst = ""
        if snap and snap.values:
            msgs = snap.values.get("messages") or []
            asst = _user_facing_reply(_last_assistant_text(msgs))
        with _module_store().transact(session_id) as sess:
            if asst:
                trailing = next(
                    (
                        m
                        for m in reversed(sess.messages)
                        if isinstance(m, dict)
                        and m.get("role") in ("user", "assistant")
                        and str(m.get("content") or "").strip()
                    ),
                    None,
                )
                if trailing is not None and trailing.get("role") == "user":
                    # Normal detached completion: the user bubble is the last
                    # entry — append the reply after it. Stamp ``ts`` like the
                    # inline persist — mixed shapes (ts-less rows from one
                    # writer, ts from the other) showed up as duplicate
                    # consecutive replies after restarts (2026-08-26).
                    from datetime import UTC as _UTC
                    from datetime import datetime as _dtmod

                    sess.messages.append({
                        "role": "assistant",
                        "content": asst,
                        "ts": _dtmod.now(_UTC).isoformat(),
                    })
                else:
                    # No trailing user turn (e.g. a pending bubble from an
                    # incremental persist) — replace the last assistant /
                    # pending message as before.
                    for m in reversed(sess.messages):
                        if isinstance(m, dict) and m.get("role") == "assistant":
                            m["content"] = asst
                            m.pop("pending", None)
                            break
            else:
                # The turn completed WITHOUT producing any assistant text.
                # Never leave the pending bubble stuck — resolve it with a
                # recovery notice so returning users see an explanation.
                for m in reversed(sess.messages):
                    if isinstance(m, dict) and m.get("role") == "assistant" and m.pop(
                        "pending", False
                    ):
                        m["content"] = (
                            "⚠️ Your previous turn finished without producing "
                            "a reply (the model may have failed silently). "
                            "Please try again."
                        )
                        break
        logger.info(
            "[SSE] Detached turn completed for thread=%s — response persisted (%d chars)",
            thread_id[:12],
            len(asst),
        )
    except Exception:
        logger.warning(
            "[SSE] Detached turn persist FAILED for thread=%s session=%s "
            "(reply is still in the checkpoint; the session was NOT updated)",
            thread_id[:12],
            session_id[:12],
            exc_info=True,
        )


async def _checkpoint_backfill_unanswered(session: Any) -> list[dict]:
    """Surface a checkpointed reply for a session whose last message is the
    user's (live incident 2026-08-21 recovery).

    When the detached-pump persist fails (or the process dies mid-turn),
    the completed reply still lives in the thread's checkpoint. On session
    load, if the transcript ends with an unanswered user message AND the
    checkpoint state ends with an assistant reply, append it — and heal
    the stored session so the fix outlives the reload.
    Returns the (possibly extended) message list.
    """
    messages = list(getattr(session, "messages", None) or [])
    last = next(
        (
            m
            for m in reversed(messages)
            if isinstance(m, dict) and str(m.get("content") or "").strip()
        ),
        None,
    )
    if not isinstance(last, dict) or (last.get("role") or "").lower() != "user":
        return messages
    try:
        live = _module_graph()
        tid = getattr(session, "thread_id", "") or ""
        if not live or not tid or not getattr(live, "checkpointer", None):
            return messages
        snap = await live.aget_state(
            {"configurable": {"thread_id": tid, "checkpoint_ns": ""}}
        )
        vals = (snap.values or {}) if snap else {}
        cp_msgs = [m for m in (vals.get("messages") or []) if isinstance(m, dict)]
        cp_last = next(
            (
                m
                for m in reversed(cp_msgs)
                if str(m.get("content") or "").strip()
            ),
            None,
        )
        asst = _user_facing_reply(_last_assistant_text(vals.get("messages") or []))
        cp_last_role = ""
        if isinstance(cp_last, dict):
            cp_last_role = (cp_last.get("role") or cp_last.get("type") or "").lower()
        if not asst or cp_last_role not in ("assistant", "ai"):
            return messages
        # Already present? (idempotent heal)
        if any(
            isinstance(m, dict)
            and m.get("role") == "assistant"
            and str(m.get("content") or "") == asst
            for m in messages
        ):
            return messages
        messages = messages + [{"role": "assistant", "content": asst}]
        with _module_store().transact(session.session_id) as sess:
            trailing = next(
                (
                    m
                    for m in reversed(sess.messages)
                    if isinstance(m, dict) and str(m.get("content") or "").strip()
                ),
                None,
            )
            already = any(
                isinstance(m, dict)
                and m.get("role") == "assistant"
                and str(m.get("content") or "") == asst
                for m in sess.messages
            )
            if trailing is not None and trailing.get("role") == "user" and not already:
                sess.messages.append({"role": "assistant", "content": asst})
                logger.info(
                    "[SSE] Backfilled unanswered turn from checkpoint for "
                    "session=%s (%d chars)",
                    session.session_id[:12],
                    len(asst),
                )
        return messages
    except Exception:
        logger.debug("[SSE] unanswered-turn checkpoint backfill skipped", exc_info=True)
        return messages


# ══════════════════════════════════════════════════════════════════════════
# LangGraph event → SSE mapping
# ══════════════════════════════════════════════════════════════════════════


async def _stream_langgraph_events(
    graph: Any,
    input_state: dict[str, Any] | Any,
    config: dict[str, Any],
    *,
    thread_id: str = "",
    session_id: str = "",
) -> AsyncGenerator[str, None]:
    """Consume LangGraph astream_events and yield SSE frames.

    This is the core generator that maps LangGraph v2 event types to the
    SSE contract expected by the Alpine.js frontend.

    LangGraph v2 event names we care about:
      on_chat_model_stream  → token
      on_tool_start         → tool_call
      on_tool_end           → tool_result
      on_chain_end          → done (only from the respond node)

    When *input_state* is a ``langgraph.types.Command`` (HITL resume path),
    ``graph.ainvoke()`` is used instead of ``astream_events`` because the
    custom LLMProvider does not emit ``on_chat_model_stream`` events, causing
    ``astream_events`` to hang without producing any frames.  The post-stream
    backfill logic still runs and surfaces assistant text from the checkpoint.

    Args:
        graph: Compiled LangGraph app (must support astream_events/ainvoke).
        input_state: The SupervisorState dict, or a Command for HITL resume.
        config: LangGraph config dict (thread_id, checkpoint_ns, etc.).

    Yields:
        SSE-formatted strings.
    """
    from kazma_core.safety.hitl import set_current_thread_id, reset_current_thread_id
    from kazma_core.observability.correlation import (
        bind_turn_id,
        current_turn_id,
        new_turn_id,
        reset_turn_id,
    )

    # Turn correlation id — binds once per streamed turn so every log line
    # and the terminal done payload carry the same identifier.
    _turn_token = bind_turn_id(current_turn_id() or new_turn_id())

    tid = config.get("configurable", {}).get("thread_id") if config else None
    token = set_current_thread_id(tid) if tid else None

    total_tokens = 0
    total_cost = 0.0
    turn_start = time.monotonic()
    content_acc = ""  # accumulated assistant text for the done event
    interrupted = False
    thread_id = tid or ""
    _snapshot_info: dict[str, Any] | None = None  # last snapshot_id/iteration from graph state

    # ── Turn Delivery V2: journaled emit ──────────────────────────────
    # Every client-visible frame of this turn is appended to the per-thread
    # journal (monotonic seq) BEFORE it is yielded, so a reconnecting client
    # presenting its cursor replays exactly what it missed — including the
    # full window while it was disconnected (the pump survives; see the
    # detached-pump block below). The frame carries both an SSE ``id:`` line
    # and a ``seq`` key in the JSON payload.
    _broker = get_turn_broker()

    async def emit_j(event: str, data: dict[str, Any]) -> str:
        if not thread_id:
            return _sse_frame(event, data)
        stamped = await _broker.emit(thread_id, {"type": event, "data": data})
        seq = int(stamped.get("seq") or 0)
        payload = dict(stamped.get("data") or {})
        payload["seq"] = seq
        return _sse_frame(event, payload, id=seq)

    try:
        try:
            # ── Detect Command input (HITL resume path) ─────────────────
            # When resuming from a HITL interrupt, input_state is a
            # langgraph.types.Command object (not a state dict).  In that
            # case astream_events(version="v2") hangs without producing any
            # frames because Kazma's custom LLMProvider does not emit
            # on_chat_model_stream events (see comment below).  The async-for
            # loop never exhausts, so the post-stream backfill logic that
            # reads graph.aget_state() is never reached — the SSE stream stays
            # open with a fake "Thinking…" spinner.
            #
            # Fix: for Command inputs, use graph.ainvoke() which completes
            # synchronously (stopping at any new interrupt), then fall through
            # to the existing post-stream logic that backfills assistant text
            # and detects chained HITL interrupts from the checkpoint.
            from langgraph.types import Command as _Command

            # Initialize BEFORE the if/else so the HITL resume path doesn't hit
            # UnboundLocalError at the post-stream `if stream_error:` checks
            # (the resume branch previously never bound this name).
            stream_error: str | None = None

            _is_resume = isinstance(input_state, _Command)

            if _is_resume:
                # Guard against double-resume race (e.g. user double-clicks
                # YOLO / Approve ~2s apart): if a turn is already running on
                # this thread, reject the second resume so two graphs don't
                # race on the same checkpoint and corrupt the delivery path.
                if is_turn_running(thread_id):
                    logger.warning(
                        "[SSE] Rejecting duplicate resume — turn already "
                        "running for thread=%s", thread_id,
                    )
                    yield await emit_j("error", {
                        "content": "This conversation is already processing. "
                                   "Please wait for the current turn to finish."
                    })
                    return
                logger.debug(
                    "[SSE] HITL resume path — using ainvoke() for thread=%s",
                    thread_id,
                )
                register_turn(thread_id, asyncio.current_task())
                try:
                    await graph.ainvoke(input_state, config)
                finally:
                    unregister_turn(thread_id)
            else:
                # Wrap astream_events with a keepalive generator so long LLM
                # processing (e.g. DeepSeek 30-40s on 150K-token contexts)
                # doesn't cause the SSE connection to time out silently.
                # Yields a ":keepalive" SSE comment every 10s when no events
                # arrive, keeping the HTTP connection alive.
                _event_queue: asyncio.Queue = asyncio.Queue(maxsize=2048)
                # Token deltas from invoke_llm_chat land here as synthetic
                # on_chat_model_stream events (custom LLM is not a BaseChatModel).
                from kazma_core.llm_stream import register_delta_queue, unregister_delta_queue

                register_delta_queue(thread_id, _event_queue)
                _stream_done = False
                # T1 watchdog: last-progress clock written by the pump on every
                # queue put; read by _pump_watchdog to detect a stalled pump.
                _progress = {"last": time.monotonic()}
                # T5: capture the live loop at registration time — the pump
                # done_callback may fire from any thread, and
                # asyncio.get_event_loop() is not safe there.
                _turn_loop = asyncio.get_running_loop()

                async def _pump_events():
                    nonlocal _stream_done
                    try:
                        async for ev in graph.astream_events(input_state, config=config, version="v2"):
                            # Bounded queue: drop advisory stream events when
                            # the consumer is gone/slow rather than grow the
                            # queue unbounded for up to DETACHED_TTL_S. The
                            # final state is backfilled from the checkpoint
                            # anyway (audit finding).
                            try:
                                _event_queue.put_nowait(ev)
                            except asyncio.QueueFull:
                                pass
                            _progress["last"] = time.monotonic()
                    except Exception as exc:
                        await _event_queue.put(exc)
                    finally:
                        _stream_done = True
                        await _event_queue.put(None)  # sentinel

                pump_task = asyncio.create_task(_pump_events())

                # ── T1: detached-pump watchdog ────────────────────────────
                # If the client is gone AND the stream has produced no events
                # for DETACHED_TTL_S (measured from the later of the disconnect
                # stamp and the last event), the pump is stuck (the documented
                # astream_events hang) — cancel it so the done_callback
                # persists whatever the checkpointer already wrote and the
                # thread frees up for a new turn. Progress-based: a background
                # turn still emitting events is left alone (refresh-safe).
                async def _pump_watchdog() -> None:
                    while not pump_task.done():
                        await asyncio.sleep(5.0)
                        stamp = get_orphan_stamp(thread_id)
                        if stamp is None:
                            continue
                        idle_since = max(stamp, _progress["last"])
                        if time.monotonic() - idle_since < DETACHED_TTL_S:
                            continue
                        logger.warning(
                            "[SSE] Reaping stalled detached pump for thread=%s "
                            "(no events for %.0fs)",
                            thread_id[:12], DETACHED_TTL_S,
                        )
                        pump_task.cancel()
                        return

                _watchdog_task = asyncio.create_task(_pump_watchdog())
                _watchdog_tasks.add(_watchdog_task)
                _watchdog_task.add_done_callback(_watchdog_tasks.discard)

                # ── Detach: register the pump task so it survives client
                # disconnects. The done_callback persists the final response
                # to the session store so loadSession finds it on reload.
                # This is the strong-reference pattern from self_improvement.py.
                register_turn(thread_id, pump_task)

                def _on_pump_done(t: asyncio.Task) -> None:
                    unregister_turn(thread_id, t)
                    # Persist the final response to the session store.
                    if not session_id:
                        return

                    # T5: schedule on the captured turn loop from whichever
                    # thread fired the callback (get_event_loop() would raise
                    # RuntimeError off-loop and drop the persist silently).
                    _turn_loop.call_soon_threadsafe(
                        lambda: _turn_loop.create_task(
                            _persist_detached_reply(graph, config, session_id, thread_id)
                        )
                    )

                pump_task.add_done_callback(_on_pump_done)

                stream_error: str | None = None
                _stream_completed = False

                try:
                    while True:
                        if is_shutting_down():
                            # Server is stopping — close the stream now so
                            # uvicorn's graceful shutdown completes inside its
                            # timeout instead of hard-cancelling us (which logs
                            # a noisy CancelledError traceback).
                            break
                        try:
                            event = await asyncio.wait_for(_event_queue.get(), timeout=10.0)
                        except asyncio.TimeoutError:
                            # No event in 10s — send keepalive to hold the
                            # connection open during long LLM processing.
                            yield ": keepalive\n\n"
                            continue
                        if event is None:
                            _stream_completed = True
                            break  # stream finished
                        if isinstance(event, Exception):
                            # Sanitize before it reaches the client — raw
                            # exceptions can leak stack traces / API bodies.
                            stream_error = sanitize_error(event)
                            logger.warning("[SSE] astream_events error: %s", event)
                            break

                        # ── Process the event inline (can't yield from in async) ──
                        kind = event.get("event", "")
                        data = event.get("data", {})
                        name = event.get("name", "")

                        # ── on_chat_model_stream: LLM token delta ──────────────
                        if kind == "on_chat_model_stream":
                            chunk = data.get("chunk")
                            if chunk is not None:
                                token_text = ""
                                if hasattr(chunk, "content"):
                                    token_text = chunk.content or ""
                                elif isinstance(chunk, dict):
                                    token_text = chunk.get("content", "")

                                if token_text:
                                    content_acc += token_text
                                    yield await emit_j("token", {"content": token_text})

                        # ── on_chat_model_end: LLM finished — extract usage ────
                        elif kind == "on_chat_model_end":
                            output = data.get("output", {})
                            if hasattr(output, "usage_metadata"):
                                usage = output.usage_metadata or {}
                                total_tokens = usage.get("total_tokens", total_tokens)
                            elif isinstance(output, dict):
                                usage = output.get("usage", {})
                                total_tokens = usage.get("total_tokens", total_tokens)
                                meta = output.get("response_metadata", {})
                                if "cost" in meta:
                                    total_cost += meta["cost"]

                        # ── on_tool_start: tool execution beginning ────────────
                        elif kind == "on_tool_start":
                            inputs = data.get("input", {})
                            if isinstance(inputs, dict) and "input" in inputs:
                                inputs = inputs["input"]
                            yield await emit_j(
                                "tool_call",
                                {
                                    "tool_name": name,
                                    "inputs": json.dumps(inputs, ensure_ascii=False)[:2000]
                                    if isinstance(inputs, dict)
                                    else str(inputs)[:2000],
                                },
                            )

                        # ── on_tool_end: tool execution finished ───────────────
                        elif kind == "on_tool_end":
                            output = data.get("output", "")
                            if hasattr(output, "content"):
                                output = output.content
                            elif isinstance(output, dict):
                                output = output.get("content", json.dumps(output, ensure_ascii=False))
                            yield await emit_j(
                                "tool_result",
                                {
                                    "tool_name": name,
                                    "result": str(output)[:5000],
                                },
                            )

                        # ── supervisor node end: memory explain panel ──────────
                        elif kind == "on_chain_end" and (
                            name in ("supervisor", "Supervisor", "_supervisor")
                            or "supervisor" in str(name).lower()
                        ):
                            output = data.get("output", {})
                            if isinstance(output, dict) and output.get("memory_explain"):
                                yield await emit_j(
                                    "memory_explain", output["memory_explain"]
                                )

                        # ── on_chain_end at graph terminal: graph finished ─────
                        elif kind == "on_chain_end" and name in ("__end__", "LangGraph"):
                            # Emit synthesizing status so the client keeps the
                            # thinking indicator alive until the backfilled text
                            # arrives — prevents the "Done 0s" silence gap.
                            yield await emit_j("status_update", {
                                "status": "synthesizing",
                                "active_node": "Respond",
                            })
                            # Extract final state if available
                            output = data.get("output", {})
                            if isinstance(output, dict):
                                # Pull cost/tokens from the final state
                                final_cost = output.get("last_cost_usd", total_cost)
                                final_tokens = output.get("last_tokens", total_tokens)
                                if final_cost:
                                    total_cost = final_cost
                                if final_tokens:
                                    total_tokens = final_tokens

                                # Time Travel: capture snapshot id/iteration
                                _sid = output.get("snapshot_id")
                                if _sid:
                                    _snapshot_info = {
                                        "snapshot_id": _sid,
                                        "iteration": output.get("snapshot_iteration", 0),
                                        "model": output.get("last_model", ""),
                                    }
                                # Late explain if only present on terminal state
                                if output.get("memory_explain"):
                                    yield await emit_j(
                                        "memory_explain", output["memory_explain"]
                                    )

                                # CRITICAL: LLMProvider uses custom httpx (not
                                # BaseChatModel), so on_chat_model_stream never fires.
                                # Surface final assistant text from graph state.
                                if not content_acc:
                                    msg_content = _last_assistant_text(
                                        output.get("messages") or []
                                    )
                                    if msg_content:
                                        content_acc = msg_content
                                        yield await emit_j(
                                            "token",
                                            {"content": msg_content},
                                        )
                                        await asyncio.sleep(0.1)
                finally:
                    unregister_delta_queue(thread_id)
                    # DETACHED: do NOT cancel pump_task. The graph keeps running
                    # in the background after the client disconnects. The
                    # done_callback (_on_pump_done) persists the result when
                    # the turn completes. The SSE generator just stops reading
                    # from the queue — the pump fills it and events are dropped
                    # harmlessly if nobody is consuming.
                    if not _stream_completed:
                        # The client went away (Starlette closes/cancels this
                        # generator) — stamp the disconnect time so the WS
                        # duplicate-turn guard can reap the pump task once the
                        # turn has been abandoned past DETACHED_TTL_S.
                        mark_turn_orphaned(thread_id)
                    pass

            # Stream crashed mid-turn (or failed immediately) — surface a
            # sanitized error frame BEFORE any fallback text so the client
            # shows the failure instead of a silent blank turn. The raw
            # exception never reaches the client.
            if stream_error:
                yield await emit_j("error", {"content": stream_error})

            # ── Post-stream: HITL + backfill assistant text ────────────
            # Tokens now stream via synthetic on_chat_model_stream (llm_stream).
            # Backfill still runs when no deltas arrived (HITL resume ainvoke,
            # kill-switch, or a provider that fell back to blocking chat).
            # On HITL interrupt, astream_events ends WITHOUT terminal
            # on_chain_end — so we still (1) detect interrupt, (2) pull any
            # assistant prose from checkpoint state, (3) never leave the UI
            # with only "Thinking…".
            thread_id = (config.get("configurable") or {}).get("thread_id", "")
            interrupted = False
            snapshot = None
            try:
                snapshot = await graph.aget_state(config)
            except Exception as exc:
                logger.warning("[SSE] aget_state failed after stream: %s", exc)

            if snapshot is not None:
                # Checkpoint last-hop vs streamed concat: the token stream
                # glues hop-0 ```plan onto the final answer (````Saved.``).
                # done.content is SoT — pick the best user-facing payload.
                try:
                    vals = getattr(snapshot, "values", None) or {}
                    msgs = vals.get("messages") if isinstance(vals, dict) else None
                    ckpt_text = _last_assistant_text(msgs or [])
                    chosen = _user_facing_reply(ckpt_text, content_acc)
                    if chosen and chosen != content_acc:
                        # Do NOT re-yield as tokens when we already streamed —
                        # the client applies done.content as a replace paint.
                        if not content_acc:
                            yield await emit_j("token", {"content": chosen})
                            await asyncio.sleep(0.1)
                        content_acc = chosen
                    elif not content_acc and ckpt_text:
                        content_acc = ckpt_text
                        yield await emit_j("token", {"content": ckpt_text})
                        await asyncio.sleep(0.1)
                except Exception:
                    logger.debug("[SSE] post-stream text backfill failed", exc_info=True)

                # HITL interrupt detection (strict type OR tool/args fallback)
                try:
                    next_nodes = getattr(snapshot, "next", None) or ()
                    if next_nodes:
                        for task in getattr(snapshot, "tasks", []) or []:
                            for intr in getattr(task, "interrupts", []) or []:
                                payload = _extract_hitl_payload(intr)
                                if not payload:
                                    continue
                                interrupted = True
                                yield await emit_j(
                                    "approval_required",
                                    {
                                        "thread_id": thread_id,
                                        "kind": payload.get("kind", "security"),
                                        "tool": payload.get("tool", ""),
                                        "args": payload.get("args", {}),
                                        "tools": payload.get("tools") or [],
                                        "items": payload.get("items") or [],
                                        "message": payload.get("message", ""),
                                        "yolo_allowed": payload.get(
                                            "yolo_allowed", True
                                        ),
                                    },
                                )
                                logger.info(
                                    "[SSE] HITL interrupt: thread=%s tool=%s — awaiting approval",
                                    thread_id,
                                    payload.get("tool"),
                                )
                                break
                            if interrupted:
                                break
                        # Paused mid-graph but no parseable HITL payload
                        if not interrupted and not content_acc:
                            logger.warning(
                                "[SSE] Graph paused (next=%s) without HITL payload "
                                "thread=%s — emitting recovery notice",
                                list(next_nodes),
                                thread_id,
                            )
                            notice = (
                                "⚠️ The agent paused mid-turn (no approval card could be "
                                "built). Try again, or open **Dashboard → Pending Approvals**. "
                                f"Thread: `{thread_id}`"
                            )
                            content_acc = notice
                            yield await emit_j("token", {"content": notice})
                except Exception as exc:
                    logger.warning("[SSE] interrupt scan failed: %s", exc, exc_info=True)

            # Never leave the chat blank after "Thinking…"
            # (skipped when the stream itself crashed — the error frame above
            # already told the client the turn failed; don't stack a second
            # generic notice on top of it)
            if not content_acc and not interrupted and not stream_error:
                notice = (
                    "⚠️ No assistant text was returned for this turn "
                    "(model may have failed silently or only planned tools). "
                    "Please try again or check server logs."
                )
                content_acc = notice
                yield await emit_j("token", {"content": notice})
                logger.warning(
                    "[SSE] Empty turn with no HITL — thread=%s tokens=%s",
                    thread_id,
                    total_tokens,
                )

            # ── Turn complete ──────────────────────────────────────────
            duration_ms = (time.monotonic() - turn_start) * 1000
            logger.info(
                "SSE turn complete: tokens=%d cost=$%.4f duration=%.0fms content_len=%d interrupted=%s",
                total_tokens,
                total_cost,
                duration_ms,
                len(content_acc),
                interrupted,
            )
            # Kazma-wide SI: learn from completed turns (skip HITL pauses).
            # Background — never delay the done frame.
            if not interrupted:
                try:
                    from kazma_core.skills.self_improvement import (
                        schedule_chat_self_improvement,
                    )

                    empty = not content_acc
                    looks_error = content_acc.strip().startswith("⚠️") or content_acc.strip().startswith(
                        "Error"
                    )
                    # Recover user text from input_state when available
                    umsg = ""
                    try:
                        _state_for_msgs = input_state if isinstance(input_state, dict) else {}
                        for m in reversed(list((_state_for_msgs or {}).get("messages") or [])):
                            if isinstance(m, dict) and m.get("role") == "user":
                                umsg = str(m.get("content") or "")
                                break
                    except Exception:
                        pass
                    schedule_chat_self_improvement(
                        user_message=umsg or "(chat turn)",
                        success=(not empty and not looks_error),
                        error="" if not looks_error else content_acc[:400],
                        output_snippet=content_acc[:600],
                    )
                except Exception:
                    logger.debug("[SSE] chat self-improvement schedule skipped", exc_info=True)

            _done_model = ""
            try:
                from kazma_core.model_registry import get_model_registry

                _done_model = str(
                    get_model_registry().get_active_profile().get("model") or ""
                )
            except Exception:
                pass
            # Enriched done + turn_complete (content+model) for reliable delivery
            # Cumulative session totals mirror the WS turn_complete keys so both
            # transports render identical badge values (AC#3) and a page refresh
            # restores correct totals instead of the last turn's numbers.
            sess_tokens, sess_cost = int(total_tokens or 0), round(float(total_cost or 0.0), 6)
            if session_id:
                try:
                    from kazma_ui.session_manager import get_session_manager as _gsm

                    sess_tokens, sess_cost = _gsm().add_usage(
                        session_id, int(total_tokens or 0), float(total_cost or 0.0)
                    )
                except Exception:
                    logger.debug("[SSE] add_usage skipped for %s", session_id, exc_info=True)
            _done_payload = {
                "tokens": total_tokens,
                "cost": round(total_cost, 6),
                "duration_ms": round(duration_ms, 0),
                "interrupted": interrupted,
                "empty": (not content_acc and not interrupted),
                "content": content_acc or "",
                "model": _done_model,
                "turn_id": current_turn_id(),
                "session_tokens": sess_tokens,
                "session_cost": round(float(sess_cost or 0.0), 6),
            }
            yield await emit_j("done", _done_payload)
            yield await emit_j("turn_complete", _done_payload)
            logger.info(
                "SSE turn_complete: model=%s tokens=%d content_len=%d interrupted=%s",
                _done_model or "?",
                total_tokens,
                len(content_acc or ""),
                interrupted,
            )

            # Time Travel: notify the UI a snapshot was captured (live
            # timeline growth). No-op if the replay panel isn't open.
            if _snapshot_info:
                yield await emit_j("snapshot", _snapshot_info)

        except asyncio.CancelledError:
            logger.info("SSE stream cancelled by client disconnect (thread=%s)", thread_id)
            raise

        except Exception as exc:
            logger.error("SSE stream error: %s", exc, exc_info=True)
            # Layer 2 of the "agent stopped talking" defense: if the stream
            # errored WITHOUT producing any assistant text (the classic
            # "_No response received_" symptom), emit a recoverable notice
            # BEFORE the raw error. The bare error frame used to leave the
            # chat showing only a blank "K" bubble — the user thought the
            # agent died. Now they get an explanation + the error inline.
            if not content_acc and not interrupted:
                recovery = (
                    "⚠️ The agent hit an error before producing a reply. "
                    "This is usually transient — please try again, or rephrase.\n\n"
                    f"Detail: {sanitize_error(exc)}\n"
                    f"Thread: `{thread_id}`"
                )
                content_acc = recovery
                yield await emit_j("token", {"content": recovery})
                logger.warning(
                    "[SSE] Empty turn recovered on exception — thread=%s tokens=%s",
                    thread_id, total_tokens,
                )
            yield await emit_j("error", {"content": sanitize_error(exc)})
    finally:
        if token is not None:
            reset_current_thread_id(token)
        reset_turn_id(_turn_token)


# ══════════════════════════════════════════════════════════════════════════
# Turn Delivery V2 — SSE cursor attach (replay + live reattach)
# ══════════════════════════════════════════════════════════════════════════

#: Idle tick budget for the attach stream: ~10s per tick, 30 ticks ≈ 5 min
#: of TOTAL silence (no events AND no running turn) before closing.
_ATTACH_IDLE_TIMEOUT_S = 10.0
_ATTACH_MAX_IDLE_TICKS = 30

#: Journal frame types that terminate an attached stream.
_SSE_ATTACH_TERMINAL = frozenset({"done", "turn_complete", "stream_end"})


def _frame_from_journaled(frame: dict[str, Any]) -> str:
    """Render a journal entry as an id:-lined SSE frame."""
    data = dict(frame.get("data") or {})
    seq = frame.get("seq")
    data["seq"] = seq
    return _sse_frame(str(frame.get("type") or "message"), data, id=seq)


async def _journal_fast_path(thread_id: str, event: str, data: dict[str, Any]) -> str:
    """Journal a slash-command confirmation and return its SSE frame.

    Closes the multi-tab parity gap: /research, /long, /yolo, /reset,
    /compact confirmations were direct-to-response only, so a second bound
    tab never saw them live. Replay of these frames is filtered at attach
    time (_REPLAY_SKIP_TYPES / capacity flag) — they are transcript-
    persisted already; re-painting them into a reconnecting open turn was
    the 2026-08-16 duplicated-MISSION-ON incident class.
    """
    if thread_id:
        try:
            stamped = await get_turn_broker().emit(
                thread_id, {"type": event, "data": data}
            )
            return _frame_from_journaled(stamped)
        except Exception:
            logger.debug("[SSE] fast-path journal failed event=%s", event, exc_info=True)
    return _sse_frame(event, data)


async def _sse_attach_stream(
    thread_id: str,
    session_id: str,
    after_seq: int,
) -> AsyncGenerator[str, None]:
    """Reattach a reconnecting SSE client to a live (or finished) turn.

    The swarm-bus ordering discipline: subscribe FIRST so nothing emitted
    between replay and live streaming can be lost, then replay the journal
    strictly after the client's cursor, then drain the queue. Frames that
    land in the queue during the replay window are deduplicated by seq.

    ``gap=True`` means the cursor predates journal retention — we signal
    ``resync`` and close rather than serve a silent partial history; the
    client rebuilds from SessionStore (durable truth) instead.
    """
    broker = get_turn_broker()
    queue = broker.subscribe(thread_id)
    last_yielded = int(after_seq or 0)
    try:
        frames, gap, head = broker.resume(thread_id, after_seq)
        running = is_turn_running(thread_id)
        yield _sse_frame(
            "resumed",
            {
                "from": last_yielded,
                "to": head,
                "count": len(frames),
                "gap": bool(gap),
                "running": bool(running),
                "session_id": session_id,
                "thread_id": thread_id,
            },
        )
        if gap:
            yield _sse_frame("status_update", {"status": "resync", "seq": head})
            return
        for frame in frames:
            if is_replayable(frame):
                yield _frame_from_journaled(frame)
                last_yielded = max(last_yielded, int(frame.get("seq") or 0))
        if not running:
            # Nothing live to attach to — replay covered everything missed.
            return

        idle_ticks = 0
        while True:
            try:
                frame = await asyncio.wait_for(
                    queue.get(), timeout=_ATTACH_IDLE_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                idle_ticks += 1
                yield ": keepalive\n\n"
                still_running = is_turn_running(thread_id)
                if not still_running and queue.empty():
                    return
                if idle_ticks >= _ATTACH_MAX_IDLE_TICKS and not still_running:
                    return
                continue
            idle_ticks = 0
            seq = int(frame.get("seq") or 0)
            if seq <= last_yielded:
                continue  # emitted during the replay window — already served
            last_yielded = seq
            yield _frame_from_journaled(frame)
            if str(frame.get("type")) in _SSE_ATTACH_TERMINAL:
                return
    finally:
        broker.unsubscribe(thread_id, queue)


# ══════════════════════════════════════════════════════════════════════════
# POST /api/chat/stream
# ══════════════════════════════════════════════════════════════════════════


def _is_cloud_url(base_url: str) -> bool:
    """Return True if *base_url* points to a real cloud LLM API.

    Local endpoints (localhost, 127.0.0.1, 0.0.0.0) and known local
    services (Ollama port 11434, LM Studio port 1234, LiteLLM port 4000)
    do NOT require a real API key and are excluded.
    """
    if not base_url:
        return False
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").lower()
    port = parsed.port

    # Local addresses never need a real API key
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0"):
        return False
    # Known local-service ports
    if port in (11434, 1234, 4000):
        return False
    return True


def create_sse_chat_router(
    graph: Any = None,
    graph_holder: dict[str, Any] | None = None,  # preferred: mutable holder updated after startup recompile with checkpointer + HITL
    graph_getter: Callable[[], Any] | None = None,  # dynamic provider for live checkpointed graph
    checkpointer: Any = None,  # deprecated, kept for API compatibility
    system_prompt: str = "",
    cost_breaker: Any = None,
    authority: Any = None,
    tracer: Any = None,
    provider_profile: dict[str, Any] | None = None,
    llm_provider: Any = None,
    llm_provider_getter: Callable[[], Any] | None = None,
    agent_getter: Callable[[], Any] | None = None,
    registry: Any = None,
) -> APIRouter:
    """Create the SSE chat router wired to the compiled Supervisor graph.

    This factory receives all dependencies at construction time so the
    endpoint itself is a thin, testable coroutine.

    Args:
        graph: Compiled LangGraph app (from build_supervisor_graph).
        checkpointer: AsyncSqliteSaver for thread_id persistence.
        system_prompt: System prompt to prepend on first message.
        cost_breaker: CostCircuitBreaker instance.
        authority: ContextAuthority for 80% compaction.
        tracer: KazmaTracer for observability.
        provider_profile: Active provider config dict with keys:
            - provider: str ("ollama", "lm-studio", "custom", "openai")
            - base_url: str (normalized)
            - model: str (normalized)
            - api_key: str (real or dummy)
        llm_provider: LLMProvider instance snapshot (prefer *llm_provider_getter*).
        llm_provider_getter: Live LLM resolver (``lambda: agent.llm``) so model
            switches never reconfigure an orphaned mount-time client.
        agent_getter: Optional ``lambda: agent`` for model-switch rebind.

    Returns:
        APIRouter with POST /api/chat/stream registered.
    """
    from kazma_ui.session_manager import get_session_manager

    # Shared, process-wide session store (same instance used by chat.py).
    # A session created via the SSE transport is therefore visible to the
    # WebSocket session-list / message-history endpoints and vice versa.
    # See VAL-UX-007 for the contract this satisfies.
    def _get_store():
        return get_session_manager()

    def _get_graph() -> Any:
        """Resolve current graph from mutable holder, dynamic getter, or fallback.
        Ensures /api/chat/stream uses the live, checkpointed, HITL-wired graph.
        """
        if graph_getter:
            try:
                g = graph_getter()
                if g:
                    return g
            except Exception as exc:
                logger.debug("[SSE] graph_getter failed: %s", exc)
        if graph_holder and graph_holder.get("graph"):
            return graph_holder.get("graph")
        return graph

    # Module-level helpers (detached persist / unanswered backfill) share
    # this live resolver (live incident 2026-08-21).
    _module_graph_holder["getter"] = _get_graph

    def _get_llm() -> Any:
        """Resolve the live LLM client (never a stale mount-time snapshot)."""
        if llm_provider_getter is not None:
            try:
                live = llm_provider_getter()
                if live is not None:
                    return live
            except Exception as exc:
                logger.debug("[SSE] llm_provider_getter failed: %s", exc)
        return llm_provider

    def _get_agent() -> Any:
        if agent_getter is not None:
            try:
                return agent_getter()
            except Exception as exc:
                logger.debug("[SSE] agent_getter failed: %s", exc)
        return None

    r = APIRouter(tags=["chat-sse"])

    def _resolve_session(session_id: str) -> tuple[Any, str]:
        """Return (ChatSession, thread_id) for ``session_id``.

        Creates the ChatSession in the shared store on first use so the
        WebSocket transport can see it immediately.

        Cross-platform continuity: platform sessions use ids like
        ``gw-telegram-…``. Those ids **are** the LangGraph thread_id, so
        Web and Telegram share one checkpointer season.
        """
        session = _get_store().get_or_create(session_id)
        # One id everywhere: Web session_id == LangGraph thread_id, same as
        # gw-* platform seasons. Existing rows keep a previously stored
        # thread_id so we never orphan a checkpointer chain.
        if session_id.startswith("gw-"):
            if session.thread_id != session_id:
                session.thread_id = session_id
                _get_store().put(session)
        elif not session.thread_id:
            session.thread_id = session_id
            _get_store().put(session)
        return session, session.thread_id

    # ── Provider profile management ───────────────────────────────

    # Mutable provider profile (can be switched at runtime)
    _active_profile: dict[str, Any] = provider_profile or {}

    @r.post("/api/chat/stream", dependencies=[Depends(rate_limit("chat", 30))])
    async def chat_stream(request: Request) -> StreamingResponse:
        """Stream a chat turn as Server-Sent Events.

        Request body (JSON):
            message: str       — user input (required)
            session_id: str    — session ID (optional, auto-generated)

        Returns:
            StreamingResponse with Content-Type text/event-stream.
        """
        # ── Parse request ──────────────────────────────────────────
        try:
            body = await request.json()
        except Exception:
            return StreamingResponse(
                iter([_sse_frame("error", {"content": "Invalid JSON body"})]),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",  # nginx passthrough
                },
            )

        user_message = (body.get("message") or "").strip()
        # Optional attachments uploaded via /api/chat/upload. The upload ID,
        # not a client filesystem path, is the server-side byte reference.
        raw_attachments = body.get("attachments") or []
        # Turn Delivery V2: a reconnecting client presents its cursor
        # (``last_event_id``, SSE-spec name; ``last_seq`` accepted as alias).
        # A request carrying one is an ATTACH, not a new prompt — it must
        # pass the empty-message gate and never start a second graph run.
        _attach_seq: int | None = None
        _attach_raw = body.get("last_event_id", body.get("last_seq"))
        if _attach_raw is not None:
            try:
                _attach_seq = int(_attach_raw)
            except Exception:
                _attach_seq = None
        if not user_message and not raw_attachments and _attach_seq is None:
            return StreamingResponse(
                iter([_sse_frame("error", {"content": "Empty message"})]),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        session_id = body.get("session_id") or str(uuid.uuid4())
        workspace_id = str(body.get("workspace_id") or "").strip()
        _ws_token = None
        _model_token = None
        if workspace_id:
            try:
                from kazma_core.ide.workspace_scope import pin_workspace

                _ws_token = pin_workspace(workspace_id)
            except Exception:
                logger.debug("[SSE] workspace pin skipped", exc_info=True)

        # ── Optional IDE context (Phase: IDE chat box) ───────────────
        # When the IDE chat sends the currently-open file as context, we
        # prepend it as a clearly-delimited preamble so the agent knows
        # what the user is looking at, separate from their question. This
        # is backward-compatible: the field is absent for the main /chat
        # page, so behavior there is unchanged.
        ide_context = (body.get("context") or "").strip()
        if ide_context:
            user_message = f"{ide_context}\n\n--- User message ---\n{user_message}"

        # ── Resolve session and thread_id (shared store) ───────────
        session, thread_id = _resolve_session(session_id)

        # ── Turn Delivery V2: cursor attach (replay + live reattach) ──
        # Serves the missed window of a RUNNING turn (pump survives client
        # disconnects) or replays a finished one — without touching the
        # checkpointer. This is SSE parity with the WS live-socket rebind.
        if _attach_seq is not None:
            return StreamingResponse(
                _sse_attach_stream(thread_id, session_id, _attach_seq),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",  # nginx passthrough
                    "Connection": "keep-alive",
                },
            )

        try:
            from kazma_core.sessions.directory import stamp_last_platform

            stamp_last_platform(thread_id, "web")
        except Exception:
            pass

        # ── Intercept YOLO command ─────────────────────────────────
        raw_msg = (body.get("message") or "").strip()
        try:
            from kazma_core.agent.slash_turns import rewrite_work_slash

            _rw = rewrite_work_slash(raw_msg)
            if _rw:
                raw_msg = _rw
                user_message = _rw
        except Exception:
            logger.debug("[SSE] slash rewrite skipped", exc_info=True)
        # Bare /research with no topic — usage only. Work slashes fall through
        # to the supervisor (same brain as Telegram / TUI).
        if raw_msg.lower().startswith("/research"):
            async def _research_gen() -> AsyncGenerator[str, None]:
                yield await _journal_fast_path(thread_id, "token", {
                    "content": (
                        "Usage: `/research deep <topic>` — runs through "
                        "the same agent as chat (tools + HITL)."
                    )
                })
                yield await _journal_fast_path(thread_id, "done", {"tokens": 0, "cost": 0.0, "duration_ms": 0})

            return StreamingResponse(
                _research_gen(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        from kazma_core.agent.capacity_commands import (
            apply_capacity_command,
            is_capacity_command,
        )

        if is_capacity_command(raw_msg, require_slash=True):
            _cap = apply_capacity_command(
                thread_id, raw_msg, actor=f"web:{session_id[:12]}",
            )
            session.messages.append({"role": "user", "content": raw_msg})
            session.messages.append({
                "role": "assistant",
                "content": _cap.reply,
                "kind": "capacity",
            })
            try:
                _get_store().put(session)
            except Exception:
                logger.exception("[SSE] failed to persist /long message")

            async def _long_generator() -> AsyncGenerator[str, None]:
                yield await _journal_fast_path(thread_id, "capacity", {
                    "long_active": _cap.long_active,
                    "yolo_active": _cap.yolo_active,
                    "action": _cap.action,
                    "reply": _cap.reply,
                })
                yield await _journal_fast_path(thread_id, "done", {
                    "tokens": 1, "cost": 0.0, "duration_ms": 100,
                })

            return StreamingResponse(
                _long_generator(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        from kazma_core.agent.plan_mode import apply_plan_command, is_plan_command

        if is_plan_command(raw_msg, require_slash=True):
            _pl = apply_plan_command(
                thread_id, raw_msg, actor=f"web:{session_id[:12]}",
            )
            if _pl.rewrite_user_text:
                raw_msg = _pl.rewrite_user_text
                user_message = _pl.rewrite_user_text
            elif _pl.handled:
                session.messages.append({"role": "user", "content": raw_msg})
                session.messages.append({
                    "role": "assistant",
                    "content": _pl.reply,
                    "kind": "capacity",
                })
                try:
                    _get_store().put(session)
                except Exception:
                    logger.exception("[SSE] failed to persist /plan message")

                async def _plan_generator() -> AsyncGenerator[str, None]:
                    yield await _journal_fast_path(thread_id, "capacity", {
                        "plan_active": _pl.plan_active,
                        "action": _pl.action,
                        "reply": _pl.reply,
                    })
                    yield await _journal_fast_path(thread_id, "done", {
                        "tokens": 1, "cost": 0.0, "duration_ms": 50,
                    })

                return StreamingResponse(
                    _plan_generator(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )

        if raw_msg.lower() in ("/yolo", "/yolo on", "/yolo off", "/yolo status"):
            from kazma_core.safety.yolo import (
                YoloDisabledError,
                disable_yolo,
                enable_yolo,
                yolo_allowed,
                yolo_status,
            )

            cmd = raw_msg.lower().strip()
            if cmd == "/yolo status":
                st = yolo_status(thread_id)
                grant_note = ""
                try:
                    from kazma_core.safety.hitl_grants import list_grants

                    grants = list_grants(thread_id)
                    if grants:
                        names = ", ".join(g["tool"] for g in grants)
                        grant_note = f"\nPer-tool grants active: `{names}`"
                except Exception:
                    pass
                if st.get("active"):
                    rem = st.get("remaining_seconds")
                    ttl_note = (
                        f"Expires in ~{rem // 60}m." if rem is not None
                        else "No auto-expiry."
                    )
                    confirmation = (
                        f"🚀 YOLO is **ON** for this session. {ttl_note}\n"
                        f"Disable: `/yolo off`{grant_note}"
                    )
                else:
                    prod_note = ""
                    if not yolo_allowed():
                        prod_note = (
                            "\nProduction mode blocks YOLO "
                            "(set `KAZMA_ALLOW_YOLO=1` to opt in)."
                        )
                    confirmation = (
                        "🛡️ YOLO is **OFF**. HITL approvals are required for danger tools."
                        f"{grant_note}{prod_note}\n"
                        "Tip: on an approval card use **Allow tool (session)** to stop "
                        "repeat prompts for one tool without full YOLO."
                    )
            elif cmd == "/yolo off":
                disable_yolo(thread_id, actor=f"web:{session_id[:12]}")
                confirmation = (
                    "🛡️ YOLO deactivated. Safety gates and tool grants are cleared."
                )
            else:
                try:
                    st = enable_yolo(thread_id, actor=f"web:{session_id[:12]}")
                    rem = st.get("remaining_seconds")
                    ttl_note = (
                        f"Auto-expires in ~{rem // 60} minutes "
                        f"(set KAZMA_YOLO_TTL_SECONDS to change; 0 = no expiry)."
                        if rem is not None
                        else "No auto-expiry (KAZMA_YOLO_TTL_SECONDS=0)."
                    )
                    confirmation = (
                        "🚀 **YOLO ON** for this session only.\n"
                        "All danger tools run **without** approval until you `/yolo off` "
                        f"or TTL ends.\n{ttl_note}\n"
                        "⚠️ Use only when you fully trust this session."
                    )
                except YoloDisabledError as yde:
                    confirmation = f"🛡️ {yde}"

            session.messages.append({"role": "user", "content": raw_msg})
            session.messages.append({
                "role": "assistant",
                "content": confirmation,
                "kind": "capacity",
            })
            try:
                _get_store().put(session)
            except Exception:
                logger.exception("[SSE] failed to persist YOLO message")

            async def _yolo_generator() -> AsyncGenerator[str, None]:
                yield await _journal_fast_path(thread_id, "capacity", {"action": "yolo", "reply": confirmation})
                yield await _journal_fast_path(thread_id, "done", {
                    "tokens": 1,
                    "cost": 0.0,
                    "duration_ms": 100,
                })

            return StreamingResponse(
                _yolo_generator(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        # ── Intercept RESET command ────────────────────────────────
        if raw_msg.lower() == "/reset":
            live_graph = _get_graph()
            if live_graph and hasattr(live_graph, "checkpointer") and live_graph.checkpointer:
                try:
                    await live_graph.checkpointer.adelete_thread(thread_id)
                except Exception as exc:
                    logger.debug("[SSE] failed to delete thread checkpoints on /reset: %s", exc)
            
            session.messages = []
            session.title = ""
            try:
                _get_store().put(session)
            except Exception:
                logger.exception("[SSE] failed to persist /reset")

            confirmation = "🔄 Conversation cleared. Starting fresh."

            async def _reset_generator() -> AsyncGenerator[str, None]:
                yield _sse_frame("token", {"content": confirmation})
                yield _sse_frame("done", {
                    "tokens": 1,
                    "cost": 0.0,
                    "duration_ms": 100,
                })

            return StreamingResponse(
                _reset_generator(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        # ── Intercept COMPACT command ──────────────────────────────
        if raw_msg.lower() == "/compact":
            live_graph = _get_graph()
            if live_graph:
                try:
                    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                    state_obj = await live_graph.aget_state(config)
                    if state_obj and state_obj.values:
                        current_values = dict(state_obj.values)
                        current_values["needs_compaction"] = True
                        
                        result_state = await live_graph.ainvoke(current_values, config)
                        
                        session.messages = _convert_messages_to_dicts(result_state.get("messages", []))
                        _get_store().put(session)
                        
                        confirmation = "🗜️ Context compaction completed successfully! Your conversation history has been summarized and compressed."
                    else:
                        confirmation = "🗜️ No conversation history found to compact yet."
                except Exception as exc:
                    logger.error("[SSE] failed to compact context: %s", exc)
                    confirmation = "⚠️ Failed to compact context. (Compaction error)"
            else:
                confirmation = "⚠️ Live graph not loaded."

            async def _compact_generator() -> AsyncGenerator[str, None]:
                yield _sse_frame("token", {"content": confirmation})
                yield _sse_frame("done", {
                    "tokens": 1,
                    "cost": 0.0,
                    "duration_ms": 100,
                })

            return StreamingResponse(
                _compact_generator(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        # Work /swarm and /research are rewritten above and fall through
        # to the supervisor. Bare `/swarm` is usage only.
        if raw_msg.lower().strip() in ("/swarm", "/swarm help"):
            async def _swarm_usage_gen() -> AsyncGenerator[str, None]:
                yield _sse_frame("token", {
                    "content": (
                        "🐝 `/swarm <task>` goes through the same agent as chat "
                        "(tools + HITL). Example: `/swarm analyze competitor pricing`\n"
                        "`/swarm status` and `/swarm list` still answer instantly."
                    )
                })
                yield _sse_frame("done", {"tokens": 1, "cost": 0.0, "duration_ms": 100})

            return StreamingResponse(
                _swarm_usage_gen(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        # ── Per-turn model pin (does NOT mutate the process-wide registry)
        requested_model = (body.get("model") or "").strip()
        if not requested_model:
            try:
                for _m in reversed(session.messages or []):
                    if isinstance(_m, dict) and _m.get("role") == "assistant" and _m.get("model"):
                        requested_model = str(_m.get("model") or "").strip()
                        if requested_model:
                            break
            except Exception:
                pass
        if requested_model:
            try:
                from kazma_core.runtime.turn_model import pin_turn_model

                _model_token = pin_turn_model(requested_model)
                logger.info("SSE chat: turn-model pin=%s (not process-wide)", requested_model)
            except Exception as exc:
                logger.warning("SSE chat: turn-model pin failed: %s", exc)

        # Live LLM for key validation (getter, not mount snapshot).
        llm_provider = _get_llm()
        if requested_model:
            try:
                from kazma_core.model_registry import get_model_registry

                llm_provider = get_model_registry().get_client(requested_model)
            except Exception:
                pass

        # ── Pre-stream API key validation (Bug 4 fix) ───────────────
        # If the provider is a real cloud API but the API key is the
        # placeholder "not-needed" (meaning the user never configured a
        # real key), return an immediate, helpful error frame instead of
        # silently failing with a 401 deep in the graph.
        _cur_key = (
            getattr(llm_provider, "config", None) and getattr(llm_provider.config, "api_key", "")
        ) or _active_profile.get("api_key", "")
        _cur_url = (
            getattr(llm_provider, "config", None) and getattr(llm_provider.config, "base_url", "")
        ) or _active_profile.get("base_url", "")
        if _cur_key in ("not-needed", "", None) and _is_cloud_url(_cur_url):
            _help_msg = (
                "⚠️ No API key configured for "
                f"{_cur_url}. "
                "Please go to Settings > Models, enter your API key, "
                "and click Save before chatting."
            )
            return StreamingResponse(
                iter([_sse_frame("error", {"content": _help_msg})]),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        # ── Cost breaker gate ──────────────────────────────────────
        # Record the interaction FIRST — record_user_interaction() un-halts
        # a tripped breaker and refreshes the budget by design. With the gate
        # first, a tripped breaker rejected every message before the reset
        # could run — a permanent lockout until process restart (audit
        # 2026-08-26; same fix as the gateway handler).
        if cost_breaker:
            cost_breaker.record_user_interaction()
        if cost_breaker and cost_breaker.should_halt():
            return StreamingResponse(
                iter([_sse_frame("error", {"content": "Session budget exceeded. Please restart."})]),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        # ── In-flight turn: a NEW user message supersedes; it does not wait.
        # Cursor attach (`last_event_id`) returned earlier so a refresh can
        # rejoin the running pump. A follow-up prompt is the user moving on —
        # the old "still being processed" reject locked the composer until
        # they pressed Stop. Cancel + await the old pump so two graphs never
        # interleave on the same checkpointer (same sequential rule as T2 reap).
        _detached_turn = get_active_turn(thread_id)
        if _detached_turn is not None and not _detached_turn.done():
            _stale = reap_stale_turn(thread_id)
            if _stale is not None and _stale is _detached_turn:
                logger.info(
                    "[SSE] Reaping stale detached turn for thread=%s",
                    thread_id[:12],
                )
                _stale.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await _stale
        if is_turn_running(thread_id):
            logger.info(
                "[SSE] Superseding in-flight turn for thread=%s (new user message)",
                thread_id[:12],
            )
            _old = cancel_turn(thread_id)
            if _old is not None:
                with contextlib.suppress(
                    asyncio.CancelledError, asyncio.TimeoutError, Exception
                ):
                    await asyncio.wait_for(_old, timeout=15.0)

        # ── Persist UI projection (display only) ───────────────────
        from datetime import UTC, datetime as _dt

        _ts = _dt.now(UTC).isoformat()
        session.messages.append({"role": "user", "content": user_message, "ts": _ts})
        # CRITICAL: persist immediately so restarts keep the sidebar transcript.
        try:
            _get_store().put(session)
        except Exception:
            logger.exception("[SSE] failed to persist user message for session=%s", session_id)

        # ── LangGraph config with thread_id for checkpointing ──────
        try:
            from kazma_core.agent.long_task import resolve_turn_budgets

            _sse_recursion = int(resolve_turn_budgets(thread_id)["recursion_limit"])
        except Exception:
            _sse_recursion = 100
        graph_config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
            },
            "recursion_limit": _sse_recursion,
        }

        # ── Build agent messages from CHECKPOINTER (source of truth) ─
        # SessionManager is UI-only. Feeding text-only session history into
        # ainvoke was overwriting checkpoint tool chains (no add_messages
        # reducer) → post-HITL / multi-turn amnesia. Mirror the gateway path.
        system_msgs: list[dict[str, Any]] = []
        if system_prompt:
            system_msgs.append({"role": "system", "content": system_prompt})

        # Kazma-wide self-improvement Soul (fresh every turn so new deltas apply
        # without rebuilding the cached streaming graph). The deltas are wrapped
        # in an untrusted data fence — the model must treat them as observation
        # context, never as instructions to obey (prompt-injection defense).
        try:
            from kazma_core.safety.prompt_fence import format_untrusted_block
            from kazma_core.skills.self_improvement import get_agent_evolution_block

            evo = get_agent_evolution_block("supervisor")
            if evo:
                system_msgs.append(
                    {
                        "role": "system",
                        "content": format_untrusted_block(evo, source="self_improvement"),
                    }
                )
        except Exception:
            logger.debug("[sse_chat] agent evolution inject skipped", exc_info=True)

        # Knowledge Library auto-inject (Phase 2). For libraries with
        # ``auto_inject=1``, fold the top-k chunks for this user message
        # into the prompt — fenced as untrusted data so a malicious doc
        # page can't smuggle instructions (AGENTS.md §11).  Kill switch
        # ``KAZMA_KB_AUTO_INJECT=0`` is checked live inside the getter.
        try:
            from kazma_core.safety.prompt_fence import format_untrusted_block
            from kazma_core.stores.knowledge_index import (
                get_knowledge_auto_inject_block,
            )

            kb_block = await get_knowledge_auto_inject_block(user_message)
            if kb_block:
                system_msgs.append(
                    {
                        "role": "system",
                        "content": format_untrusted_block(kb_block, source="knowledge"),
                    }
                )
        except Exception:
            logger.debug("[sse_chat] knowledge auto-inject skipped", exc_info=True)

        try:
            from kazma_core.ide.env_context import build_env_context

            # Async facade — offloads blocking git probes off the event loop.
            env_block = await build_env_context()
            if env_block:
                system_msgs.append({"role": "system", "content": env_block})
        except Exception:
            logger.debug("[sse_chat] per-turn env_context refresh skipped", exc_info=True)

        try:
            from kazma_core.language_lock import language_lock_message

            lock = language_lock_message(user_message)
            if lock:
                system_msgs.append({"role": "system", "content": lock})
        except Exception:
            logger.debug("[sse_chat] language lock skipped", exc_info=True)

        from kazma_core.agent.hitl_supersede import cancel_pending_hitl
        from kazma_core.agent.turn_input import build_turn_messages
        from kazma_core.agent.long_task import consume_long_task_turn

        # Consume a long_task turn-budget at the START of each new user message.
        consume_long_task_turn(thread_id)

        current_graph = _get_graph()
        # If user sent a new message while HITL is waiting, auto-deny so
        # tool chains close cleanly (no silent supersede / amnesia).
        try:
            cancelled = await cancel_pending_hitl(
                current_graph,
                graph_config,
                reason="superseded by new user message",
            )
            if cancelled:
                logger.info(
                    "[SSE] cancelled pending HITL before new turn thread=%s",
                    thread_id[:16],
                )
        except Exception:
            logger.debug("[SSE] HITL supersede cancel skipped", exc_info=True)

        messages = await build_turn_messages(
            current_graph,
            graph_config,
            user_text=user_message,
            system_messages=system_msgs,
            fallback_history=session.messages[:-1],  # exclude the user line we just added
        )

        # If attachments were uploaded, replace the last user message's text
        # content with the multimodal version (inline images / persisted docs).
        # This mirrors the gateway path (agent_handler/attachments.py) so both
        # transports produce identical OpenAI-compatible content.
        if raw_attachments and messages:
            try:
                from kazma_gateway.agent_handler.attachments import build_user_content
                from kazma_ui.chat_attachments import attachments_from_client_payload

                atts = attachments_from_client_payload(raw_attachments)
                # to_thread: build_user_content persists + parses documents
                # synchronously — keep that disk work off the event loop.
                multimodal_content = await asyncio.to_thread(
                    build_user_content, user_message or "", atts
                )
                # Replace the trailing user message content.
                for i in range(len(messages) - 1, -1, -1):
                    if isinstance(messages[i], dict) and messages[i].get("role") == "user":
                        messages[i]["content"] = multimodal_content
                        break
            except Exception:  # noqa: BLE001 — never block a turn on media
                logger.debug("[SSE] attachment content build failed", exc_info=True)

        # ── Build SupervisorState for the graph ────────────────────
        from kazma_core.agent.state import initial_supervisor_state
        from kazma_core.memory.config import resolve_tenant_id

        # SaaS: bind memory tenant to authenticated principal when present
        _auth_uid = ""
        try:
            from kazma_core.tenant_context import get_current_tenant_id

            _ctx = (get_current_tenant_id() or "").strip()
            if _ctx and _ctx != "default":
                # principal tenant already set by middleware — resolve_tenant_id
                # will prefer ContextVar in per_user mode
                pass
            # Optional user claim for per_user platform:user keys
            principal = getattr(request.state, "principal", None) or getattr(
                request.state, "user", None
            )
            if isinstance(principal, dict):
                _auth_uid = str(
                    principal.get("user_id")
                    or principal.get("sub")
                    or principal.get("id")
                    or ""
                )
        except Exception:
            pass

        input_state = initial_supervisor_state(
            thread_id=thread_id,
            tenant_id=resolve_tenant_id(
                "web", "", session_id, auth_user_id=_auth_uid
            ),
        )
        input_state["messages"] = messages
        # Transport-level working-memory pin (before supervisor loop)
        try:
            from kazma_core.agent.turn_input import build_turn_working_memory

            _wm = build_turn_working_memory(
                user_message,
                messages=messages,
                client_attachments=list(raw_attachments or []),
            )
            input_state.update(_wm)
            if _wm.get("hard_constraints"):
                logger.info(
                    "[SSE] Pinned working memory constraints=%s attachments=%d",
                    _wm.get("hard_constraints"),
                    len(_wm.get("active_attachments") or []),
                )
        except Exception:
            logger.debug("[SSE] working-memory pin skipped", exc_info=True)

        # ── Trace the request ──────────────────────────────────────
        if tracer:
            tracer.trace_state_transition(
                from_state="idle",
                to_state="streaming",
                checkpoint_id=thread_id[:12],
            )

        logger.info(
            "SSE chat: session=%s thread=%s msg_len=%d",
            session_id,
            thread_id[:12],
            len(user_message),
        )

        # ── Stream the response ────────────────────────────────────
        async def _event_generator() -> AsyncGenerator[str, None]:
            content_acc = ""
            # Track if we've started persisting assistant messages
            assistant_message_started = False
            # Stamp active model on the assistant bubble for reload / meta.
            _turn_model = requested_model
            if not _turn_model:
                try:
                    from kazma_core.runtime.turn_model import current_turn_model

                    _turn_model = current_turn_model() or ""
                except Exception:
                    _turn_model = ""
            if not _turn_model:
                try:
                    from kazma_core.model_registry import get_model_registry

                    _turn_model = str(
                        get_model_registry().get_active_profile().get("model") or ""
                    )
                except Exception:
                    _turn_model = ""
            # Temporary message dict for incremental persistence. Carries
            # ``ts`` from creation so every append path (incremental, final,
            # detached) produces the same row shape — mixed shapes surfaced
            # as ts-less duplicate rows after restarts (2026-08-26).
            from datetime import UTC as _UTCc
            from datetime import datetime as _dtc

            temp_assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": "",
                "ts": _dtc.now(_UTCc).isoformat(),
            }
            if _turn_model:
                temp_assistant_msg["model"] = _turn_model
            # CoT / activity log for this turn (tools + status), persisted with
            # the assistant message so reloads / session switches restore the
            # workbench panel instead of showing a blank transcript.
            activity_log: list[dict[str, Any]] = []

            def _parse_frame(frame: str) -> tuple[str, dict[str, Any]] | None:
                """Split an SSE frame into (event_type, data) or None.

                Line-scoped field parsing per the SSE spec: ``id:`` /
                ``retry:`` lines may legally precede or follow ``event:``
                (Turn Delivery V2 prepends ``id: <seq>``), so the first
                line is not assumed to be the event field.
                """
                try:
                    ev_type = ""
                    data: dict[str, Any] = {}
                    for line in frame.split("\n"):
                        if line.startswith("event: "):
                            ev_type = line[len("event: "):].strip()
                        elif line.startswith("data: "):
                            data = json.loads(line[len("data: "):])
                    if not ev_type:
                        return None
                    return ev_type, data
                except (json.JSONDecodeError, ValueError):
                    return None

            def _record_activity(ev_type: str, data: dict[str, Any]) -> None:
                """Append a workbench row for a tool/status frame (deduped)."""
                try:
                    if ev_type == "tool_call":
                        activity_log.append({
                            "kind": "tool",
                            "title": str(data.get("tool_name") or "tool"),
                            "detail": str(data.get("inputs") or ""),
                            "state": "running",
                            "ts": _dt.now(UTC).isoformat(),
                        })
                    elif ev_type == "tool_result":
                        activity_log.append({
                            "kind": "tool",
                            "title": str(data.get("tool_name") or "tool"),
                            "detail": str(data.get("result") or ""),
                            "state": "done",
                            "ts": _dt.now(UTC).isoformat(),
                        })
                    elif ev_type == "status_update":
                        status = str(data.get("status") or "").strip()
                        # Only persist meaningful progress states; skip the
                        # synthesizing heartbeat (cosmetic, noisy on reload).
                        if status and status != "synthesizing":
                            activity_log.append({
                                "kind": "status",
                                "title": status,
                                "state": "running",
                                "ts": _dt.now(UTC).isoformat(),
                            })
                except Exception:
                    logger.debug("[SSE] activity capture failed", exc_info=True)

            def _attach_activity(msg: dict[str, Any]) -> None:
                if activity_log:
                    msg["activity"] = list(activity_log)
                if _turn_model and not msg.get("model"):
                    msg["model"] = _turn_model

            def _persist_now() -> None:
                """Persist the current temp_assistant_msg to the store.

                T4: runs under the per-session mutation lock so this live
                persist never interleaves with the detached done_callback
                persist (or /reset) on the same ChatSession.
                """
                nonlocal assistant_message_started
                try:
                    _attach_activity(temp_assistant_msg)
                    with _get_store().transact(session_id) as sess:
                        if not assistant_message_started:
                            sess.messages.append(temp_assistant_msg.copy())
                            assistant_message_started = True
                        else:
                            if sess.messages:
                                sess.messages[-1]["content"] = temp_assistant_msg["content"]
                                _attach_activity(sess.messages[-1])
                                if _turn_model:
                                    sess.messages[-1]["model"] = _turn_model
                except Exception:
                    logger.debug(
                        "[SSE] failed to persist assistant message for session=%s",
                        session_id,
                    )

            # Per-turn usage captured from the done/turn_complete frame so the
            # final persist can stamp it on the assistant message (reload stats).
            turn_usage: dict[str, Any] = {}

            try:
                async for frame in _stream_langgraph_events(
                    graph=current_graph,
                    input_state=input_state,
                    config=graph_config,
                    thread_id=thread_id,
                    session_id=session_id,
                ):
                    parsed = _parse_frame(frame)
                    if parsed is None:
                        yield frame
                        continue
                    ev_type, data = parsed

                    # Accumulate content + record CoT activity
                    if ev_type == "token":
                        token_text = str(data.get("content", "") or "")
                        content_acc += token_text
                        temp_assistant_msg["content"] += token_text

                        # Persist incrementally every few tokens to prevent data loss.
                        # _persist_now performs the append/update under the
                        # per-session mutation lock (T4) — no inline mutation.
                        if len(content_acc) % 50 == 0:
                            _persist_now()
                    elif ev_type in ("tool_call", "tool_result", "status_update"):
                        _record_activity(ev_type, data)
                    elif ev_type in ("done", "turn_complete"):
                        if data.get("tokens") is not None:
                            turn_usage["tokens"] = data.get("tokens")
                        if data.get("cost") is not None:
                            turn_usage["cost"] = data.get("cost")
                        # Terminal frame is SoT — replace glued token concat
                        # with the un-glued checkpoint/normalized payload.
                        done_text = str(data.get("content") or "")
                        if done_text.strip():
                            content_acc = done_text.strip()
                            temp_assistant_msg["content"] = content_acc

                    yield frame

                # Store final assistant response in session history + persist to disk.
                if content_acc:
                    _ats = _dt.now(UTC).isoformat()
                    if assistant_message_started and session.messages:
                        if session.messages[-1].get("role") == "assistant":
                            session.messages[-1]["content"] = content_acc
                            _attach_activity(session.messages[-1])
                            session.messages[-1].setdefault("ts", _ats)
                            if "tokens" in turn_usage:
                                session.messages[-1]["tokens"] = int(turn_usage["tokens"] or 0)
                            if "cost" in turn_usage:
                                session.messages[-1]["cost"] = round(
                                    float(turn_usage["cost"] or 0.0), 6
                                )
                    else:
                        final_msg: dict[str, Any] = {
                            "role": "assistant",
                            "content": content_acc,
                            "ts": _ats,
                        }
                        _attach_activity(final_msg)
                        if "tokens" in turn_usage:
                            final_msg["tokens"] = int(turn_usage["tokens"] or 0)
                        if "cost" in turn_usage:
                            final_msg["cost"] = round(float(turn_usage["cost"] or 0.0), 6)
                        session.messages.append(final_msg)
                try:
                    _get_store().put(session)
                except Exception:
                    logger.exception(
                        "[SSE] failed to persist turn for session=%s", session_id
                    )

            except asyncio.CancelledError:
                logger.warning("SSE generator cancelled for session=%s (client refresh/tab switch?)", session_id)
                # Flush whatever partial content we have so the user's question
                # isn't left without an answer on reload.
                try:
                    if content_acc:
                        content_acc = _user_facing_reply(content_acc) or content_acc
                        has_assistant = any(
                            msg.get("role") == "assistant" for msg in session.messages
                        )
                        if not has_assistant:
                            session.messages.append(
                                {"role": "assistant", "content": content_acc}
                            )
                        else:
                            for msg in reversed(session.messages):
                                if msg.get("role") == "assistant":
                                    msg["content"] = content_acc
                                    break
                    else:
                        # No content yet — the LLM was still processing when
                        # the client disconnected. Mark the turn as in-progress
                        # so loadSession can detect it and show a "still
                        # processing" indicator instead of a blank response.
                        has_assistant = any(
                            msg.get("role") == "assistant" for msg in session.messages
                        )
                        if not has_assistant:
                            session.messages.append(
                                {"role": "assistant", "content": "", "pending": True}
                            )
                    _get_store().put(session)
                except Exception:
                    pass
                yield _sse_frame("error", {"content": "Connection closed"})

            except Exception as exc:
                logger.error("SSE generator error: %s", exc, exc_info=True)
                try:
                    if content_acc:
                        # Ensure assistant message is persisted even on error
                        has_assistant = any(
                            msg.get("role") == "assistant" for msg in session.messages
                        )
                        if not has_assistant:
                            session.messages.append(
                                {"role": "assistant", "content": content_acc}
                            )
                        else:
                            for msg in reversed(session.messages):
                                if msg.get("role") == "assistant":
                                    msg["content"] = content_acc
                                    break
                    _get_store().put(session)
                except Exception:
                    pass
                yield _sse_frame("error", {"content": sanitize_error(exc)})

        async def _guarded_events() -> AsyncGenerator[str, None]:
            try:
                async for frame in _event_generator():
                    yield frame
            finally:
                if _ws_token is not None:
                    try:
                        from kazma_core.ide.workspace_scope import reset_workspace

                        reset_workspace(_ws_token)
                    except Exception:
                        pass
                if _model_token is not None:
                    try:
                        from kazma_core.runtime.turn_model import reset_turn_model

                        reset_turn_model(_model_token)
                    except Exception:
                        pass

        return StreamingResponse(
            _guarded_events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @r.get("/api/chat/sessions")
    async def list_sessions() -> list[dict[str, Any]]:
        """List all active chat sessions (shared store)."""
        try:
            from kazma_core.sessions.directory import enrich_summary

            return [enrich_summary(s.to_summary()) for s in _get_store().list_all()]
        except Exception:
            return [s.to_summary() for s in _get_store().list_all()]

    @r.get("/api/chat/sessions/{session_id}/status")
    async def get_session_status(session_id: str) -> dict[str, Any]:
        """Return whether a background turn is running for this session.

        Used by the frontend on page load / tab focus to detect that the
        agent is still generating a response after a refresh or tab switch.
        """
        session = _get_store().get(session_id)
        thread_id = ""
        if session:
            thread_id = session.thread_id or session_id

        # Check if a detached turn is running for this thread (SSE or WS)
        is_running = thread_id and is_turn_running(thread_id)

        return {
            "session_id": session_id,
            "thread_id": thread_id,
            "generating": bool(is_running),
        }

    @r.delete("/api/chat/sessions/{session_id}")
    async def delete_session(session_id: str) -> dict[str, str]:
        """Delete a chat session and its associated checkpoint data."""
        try:
            store = _get_store()
            session = store.get(session_id)
            thread_id = session.thread_id if session else ""
            store.delete(session_id)

            if thread_id:
                try:
                    from kazma_ui import dashboard as _dash
                    cm = _dash._checkpoint_manager
                    if cm and hasattr(cm, "adelete_thread"):
                        await cm.adelete_thread(thread_id)
                except Exception as exc:
                    logger.debug("Checkpoint cleanup for %s failed: %s", thread_id, exc)
            return {"status": "ok"}
        except Exception as exc:
            logger.error("delete_session failed: %s", exc)
            return {"status": "error", "error": "Internal error"}

    @r.patch("/api/chat/sessions/{session_id}")
    async def rename_session(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Rename a chat session (set a custom title)."""
        try:
            title = str(body.get("title") or "").strip()
            if not title:
                return {"status": "error", "error": "Title cannot be empty"}
            session = _get_store().rename(session_id, title)
            if session is None:
                return {"status": "error", "error": "Session not found"}
            return {"status": "ok", "title": session.title}
        except Exception as exc:
            logger.error("rename_session failed: %s", exc)
            return {"status": "error", "error": "Internal error"}

    @r.post("/api/chat/sessions/{session_id}/archive")
    async def archive_session(session_id: str) -> dict[str, Any]:
        """Archive a chat session (hide from sidebar without deleting)."""
        try:
            session = _get_store().set_archived(session_id, True)
            if session is None:
                return {"status": "error", "error": "Session not found"}
            return {"status": "ok", "archived": True}
        except Exception as exc:
            logger.error("archive_session failed: %s", exc)
            return {"status": "error", "error": "Internal error"}

    @r.post("/api/chat/sessions/{session_id}/unarchive")
    async def unarchive_session(session_id: str) -> dict[str, Any]:
        """Restore an archived chat session back to the sidebar."""
        try:
            session = _get_store().set_archived(session_id, False)
            if session is None:
                return {"status": "error", "error": "Session not found"}
            return {"status": "ok", "archived": False}
        except Exception as exc:
            logger.error("unarchive_session failed: %s", exc)
            return {"status": "error", "error": "Internal error"}

    @r.post("/api/chat/sessions/{session_id}/pin")
    async def pin_session(session_id: str) -> dict[str, Any]:
        """Pin a chat session (stays at the top of the sidebar)."""
        try:
            session = _get_store().set_pinned(session_id, True)
            if session is None:
                return {"status": "error", "error": "Session not found"}
            return {"status": "ok", "pinned": True}
        except Exception as exc:
            logger.error("pin_session failed: %s", exc)
            return {"status": "error", "error": "Internal error"}

    @r.post("/api/chat/sessions/{session_id}/unpin")
    async def unpin_session(session_id: str) -> dict[str, Any]:
        """Unpin a chat session (back to normal updated_at ordering)."""
        try:
            session = _get_store().set_pinned(session_id, False)
            if session is None:
                return {"status": "error", "error": "Session not found"}
            return {"status": "ok", "pinned": False}
        except Exception as exc:
            logger.error("unpin_session failed: %s", exc)
            return {"status": "error", "error": "Internal error"}

    @r.get("/api/chat/sessions/archived")
    async def list_archived_sessions() -> list[dict[str, Any]]:
        """List archived chat sessions (for the archive view)."""
        try:
            from kazma_core.sessions.directory import enrich_summary

            return [
                enrich_summary(s.to_summary())
                for s in _get_store().list_all(include_archived=True)
                if s.archived
            ]
        except Exception:
            return [
                s.to_summary()
                for s in _get_store().list_all(include_archived=True)
                if s.archived
            ]

    @r.get("/api/chat/sessions/{session_id}/messages")
    async def get_session_messages(
        session_id: str, stats: bool = False
    ) -> Any:
        """Return the current tenant's message history for a chat session.

        Default response is the legacy bare ``list[message]`` (old clients +
        tests depend on it). Pass ``?stats=1`` to get an envelope
        ``{"session_id", "messages", "total_tokens", "total_cost"}`` — the
        cumulative usage totals power the header cost/token badges after a
        page refresh (``ChatSession.total_*`` are incremented per turn via
        ``SessionManager.add_usage``).
        """
        if session_id.startswith("gw-"):
            _get_store()._refresh_from_db(session_id)

        session = _get_store().get(session_id)
        if not session:
            return []

        # Unanswered-turn backfill (live incident 2026-08-21): if the last
        # message is the user's, the detached-pump persist may have failed
        # after the graph completed — the checkpoint still holds the reply.
        messages = await _checkpoint_backfill_unanswered(session)
        # Hydrate from checkpointer when:
        # 1. messages is completely empty (original behavior), OR
        # 2. there are messages but NO assistant replies (partial sync from
        #    gateway — the end-of-turn sync didn't reach this process's
        #    in-memory cache). The checkpointer is the source of truth for
        #    gw-* platform sessions.
        has_assistant = any(
            (m.get("role") or "").lower() == "assistant"
            and str(m.get("content") or "").strip()
            for m in messages
            if isinstance(m, dict)
        )
        if not messages or (not has_assistant and session_id.startswith("gw-")):
            # Hydrate from checkpointer (source of truth for agent seasons)
            try:
                live = _get_graph()
                tid = session.thread_id or (
                    session_id if session_id.startswith("gw-") else ""
                )
                # Twin sidebar rows (take-over) must not copy this thread's
                # transcript onto a different session_id.
                if tid:
                    owner = _get_store().get(tid) or _get_store().get_by_thread_id(tid)
                    if owner is not None and owner.session_id != session.session_id:
                        tid = ""
                if live and tid and getattr(live, "checkpointer", None):
                    from kazma_core.agent.turn_input import load_checkpoint_messages

                    prior = await load_checkpoint_messages(
                        live,
                        {"configurable": {"thread_id": tid, "checkpoint_ns": ""}},
                    )
                    def _ui_ok(m: dict) -> bool:
                        if not isinstance(m, dict):
                            return False
                        role = (m.get("role") or "").lower()
                        if role not in ("user", "assistant"):
                            return False
                        c = str(m.get("content") or "").strip()
                        if not c:
                            return False
                        # Drop prompt injects if they were ever mis-tagged as user
                        if "<kazma:data" in c and "untrusted" in c:
                            return False
                        if "[SelfImprovement]" in c and "BEGIN OBSERVATION" in c:
                            return False
                        return True

                    ui = [
                        {"role": m.get("role"), "content": str(m.get("content") or "").strip()}
                        for m in prior
                        if _ui_ok(m)
                    ]
                    if ui:
                        # The checkpointer is the source of truth for gw-*
                        # sessions. Replace if the checkpoint has more content
                        # (e.g. it has assistant replies the cached row lacks),
                        # or if the cache was empty.
                        if len(ui) >= len(messages):
                            session.messages = ui
                        else:
                            # Merge: add any checkpoint messages missing from cache
                            existing_keys = {
                                (m.get("role"), str(m.get("content") or "")[:80])
                                for m in messages if isinstance(m, dict)
                            }
                            for m in ui:
                                key = (m.get("role"), str(m.get("content") or "")[:80])
                                if key not in existing_keys:
                                    messages.append(m)
                            session.messages = messages
                        if session_id.startswith("gw-"):
                            session.thread_id = session_id
                        _get_store().put(session)
                        messages = session.messages
            except Exception:
                logger.debug(
                    "[SSE] checkpointer hydrate failed for %s",
                    session_id,
                    exc_info=True,
                )

        def _visible(msg: dict) -> bool:
            role = (msg.get("role") or "").lower()
            if role not in ("user", "assistant"):
                return False
            c = str(msg.get("content") or "")
            if "<kazma:data" in c and "untrusted" in c:
                return False
            if "[SelfImprovement]" in c and "BEGIN OBSERVATION" in c:
                return False
            return True

        payload: list[dict[str, Any]] = [
            {
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
                **({"pending": True} if msg.get("pending") else {}),
                **({"ts": msg["ts"]} if msg.get("ts") else {}),
                **(
                    {"activity": msg["activity"]}
                    if isinstance(msg.get("activity"), list) and msg["activity"]
                    else {}
                ),
            }
            for msg in messages
            if _visible(msg)
        ]
        if stats:
            return {
                "session_id": session.session_id or session_id,
                "messages": payload,
                "total_tokens": int(session.total_tokens or 0),
                "total_cost": round(float(session.total_cost or 0.0), 6),
            }
        return payload

    # ── Provider profile management (continued) ───────────────────

    @r.get("/api/provider/active")
    async def get_active_provider() -> dict[str, Any]:
        """Return the currently active provider profile.

        Returns:
            {"provider": "ollama", "base_url": "...", "model": "...", "api_key": "..."}
        """
        if registry is not None:
            return registry.get_active_profile()
        # Fallback to local profile
        if not _active_profile:
            return {"provider": "none", "base_url": "", "model": "", "api_key": ""}
        # Don't expose real API keys — always mask
        safe = {**_active_profile}
        if safe.get("api_key"):
            safe["api_key"] = "***"
        return safe

    # NOTE: there is intentionally NO `GET /api/providers` here — the
    # providers router (providers.py, mounted before this one) owns that
    # path with the full masked-config shape the sidebar consumes. The
    # preset-list variant that used to live here was shadowed dead weight
    # (deep-audit 2026-08-19, finding #16). `/api/provider/active` below
    # remains unique to this router.

    @r.post("/api/provider/switch")
    async def switch_provider(request: Request) -> dict[str, Any]:
        """Switch the active provider profile at runtime.

        Request body:
            {"provider": "openai", "model": "gpt-4o-mini", "api_key": "sk-..."}
            {"provider": "lm-studio", "base_url": "http://localhost:1234/v1", "model": "local-model"}
            {"provider": "custom", "base_url": "http://my-server:8080/v1", "model": "gpt-4o", "api_key": "sk-..."}

        Returns:
            The normalized provider profile.
        """
        try:
            body = await request.json()
        except Exception:
            return {"error": "Invalid JSON"}

        # SSRF validation on user-supplied base_url.
        # Provider switch is user-initiated configuration of their own LLM
        # endpoint (often local: Ollama, LM Studio), so we allow private
        # addresses and normalize scheme-less URLs before validating.
        _raw_url = body.get("base_url", "")
        if _raw_url:
            try:
                from kazma_core.url_utils import normalize_provider_url
                from kazma_core.security.ssrf import validate_url

                validate_url(normalize_provider_url(_raw_url), allow_private=True)
            except Exception as exc:
                return {"error": f"URL validation failed: {exc}"}

        # Single switch pipeline: registry + agent.sync + graph recompile.
        # Never feed a masked "***" api_key into reconfigure (SwitchResult /
        # get_active_profile mask keys for display only).
        raw_key = body.get("api_key", "") or ""
        if str(raw_key).strip() in ("***", "••••", "****"):
            raw_key = ""

        try:
            from kazma_core.runtime.model_switch import switch_active_provider

            sw = switch_active_provider(
                provider=body.get("provider", ""),
                base_url=body.get("base_url", "") or "",
                model=body.get("model", "") or "",
                api_key=raw_key,
                agent=_get_agent(),
                registry=registry,
            )
            out = sw.to_dict()
            # Surface unmasked profile fields for the settings UI (key still
            # masked in get_active_profile-style responses).
            if registry is not None and sw.ok:
                try:
                    prof = registry.get_active_profile()
                    out["base_url"] = prof.get("base_url", "")
                    out["api_key"] = "***" if prof.get("api_key") else ""
                except Exception:
                    pass
            return out
        except Exception as exc:
            logger.warning("Provider switch failed: %s", exc)
            return {"error": str(exc), "status": "error", "ok": False}

    # ── Stop: cancel the running turn (Stop button) ──────────────────
    @r.post("/api/chat/stop")
    async def stop_chat_turn(request: Request) -> dict[str, Any]:
        """Cancel the in-flight turn for a session (user pressed Stop).

        Body: ``{"session_id": ...}`` (or ``{"thread_id": ...}``). Resolves
        the LangGraph thread from the session, atomically unregisters and
        cancels the pump task. The pump's done_callback persists whatever
        the checkpointer already wrote — a Stop never corrupts the
        transcript. Returns ``{"cancelled": bool}``.
        """
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        session_id = str(payload.get("session_id") or "")
        thread_id = str(payload.get("thread_id") or "")
        if not thread_id and session_id:
            try:
                sess = _get_store().get(session_id)
                thread_id = (sess.thread_id if sess else "") or session_id
            except Exception:
                thread_id = session_id
        task = cancel_turn(thread_id) if thread_id else None
        logger.info(
            "[SSE] Stop requested for thread=%s -> %s",
            thread_id[:12] if thread_id else "?", "cancelled" if task else "no active turn",
        )
        return {"cancelled": task is not None}

    @r.get("/api/chat/capacity")
    async def chat_capacity(session_id: str = "", thread_id: str = "") -> dict[str, Any]:
        """Live budget + YOLO snapshot for the composer capacity bar."""
        tid = (thread_id or "").strip()
        sid = (session_id or "").strip()
        if not tid and sid:
            try:
                sess = _get_store().get(sid)
                tid = (sess.thread_id if sess else "") or sid
            except Exception:
                tid = sid
        if not tid:
            return {"ok": False, "reason": "missing_session"}
        from kazma_core.agent.capacity_commands import snapshot_capacity

        snap = snapshot_capacity(tid)
        snap["ok"] = True
        snap["session_id"] = sid
        return snap

    # ── Steer: inject info into a RUNNING turn (/steer, /steer!) ──────
    @r.post("/api/chat/steer")
    async def steer_chat_turn(request: Request) -> Any:
        """Soft/hard-steer an in-progress turn without cancelling it.

        Body: ``{"session_id": ..., "thread_id"?: ..., "text": "...",
        "mode": "soft"|"hard"}``.

        * **soft** (default): buffer the note; the supervisor drains it on the
          next iteration and folds it into the LLM call. Returns ``{ok: true}``.
        * **hard**: pause the running turn at the next supervisor entry via a
          LangGraph ``interrupt()``, inject the note as a first-class message,
          and resume. Returns a streaming response (like ``/api/approve``) so
          the continued turn streams back to the client. If the turn is
          finalizing and the pause isn't reached within ~12s, demotes to soft.

        Requires an active turn on the thread; otherwise returns
        ``{ok: false, reason: "no_active_task"}``.
        """
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        session_id = str(payload.get("session_id") or "")
        thread_id = str(payload.get("thread_id") or "")
        if not thread_id and session_id:
            try:
                sess = _get_store().get(session_id)
                thread_id = (sess.thread_id if sess else "") or session_id
            except Exception:
                thread_id = session_id
        text = str(payload.get("text") or "").strip()
        mode = str(payload.get("mode") or "soft").strip().lower()
        if mode not in ("soft", "hard"):
            mode = "soft"
        if not thread_id or not text:
            return {"ok": False, "reason": "missing_session_or_text"}

        graph_inst = _get_graph()
        _paused = False
        if not is_turn_running(thread_id):
            # HITL / hard-steer interrupt: the prompt stream has finished but
            # the task is still the user's — allow steer instead of "no task".
            try:
                from kazma_core.agent.long_task import resolve_turn_budgets

                _rec = int(resolve_turn_budgets(thread_id)["recursion_limit"])
                _cfg = {
                    "configurable": {"thread_id": thread_id, "checkpoint_ns": ""},
                    "recursion_limit": _rec,
                }
                _snap = await graph_inst.aget_state(_cfg)
                _paused = bool(getattr(_snap, "next", None))
            except Exception:
                _paused = False
            if not _paused:
                return {"ok": False, "reason": "no_active_task"}

        from kazma_core.agent.steer import (
            clear_all_steers,
            is_hard_steer_interrupt,
            push_hard_steer,
            push_soft_steer,
        )

        # Durable transcript so the user can see / edit the steer after refresh.
        try:
            if session_id:
                _sess = _get_store().get(session_id)
                if _sess is not None:
                    _label = "/steer! " if mode == "hard" else "/steer "
                    _sess.messages.append({
                        "role": "user",
                        "content": _label + text,
                    })
                    _get_store().put(_sess)
        except Exception:
            logger.debug("[SSE] persist steer transcript failed", exc_info=True)

        if mode == "soft" or (_paused and not is_turn_running(thread_id)):
            push_soft_steer(thread_id, text)
            logger.info(
                "[SSE] soft steer queued thread=%s paused=%s demoted=%s",
                thread_id[:12], _paused, mode == "hard",
            )
            return {
                "ok": True,
                "mode": "soft",
                "demoted": mode == "hard",
            }

        # ── hard steer: queue, wait for the interrupt, then resume ──
        push_hard_steer(thread_id, text)
        graph_inst = _get_graph()
        try:
            from kazma_core.agent.long_task import resolve_turn_budgets

            _rec = int(resolve_turn_budgets(thread_id)["recursion_limit"])
        except Exception:
            _rec = 100
        config = {
            "configurable": {"thread_id": thread_id, "checkpoint_ns": ""},
            "recursion_limit": _rec,
        }
        # Poll up to ~12s for the supervisor to reach the hard_steer interrupt.
        # The running turn's pump exhausts when interrupt() fires, so
        # is_turn_running flips to False; the authoritative signal is the
        # hard_steer payload on aget_state().
        paused_text: str | None = None
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            try:
                snap = await graph_inst.aget_state(config)
            except Exception as exc:  # noqa: BLE001 — poll must not crash
                logger.debug("[SSE] steer poll aget_state failed: %s", exc)
                break
            paused_text = is_hard_steer_interrupt(snap)
            if paused_text is not None:
                break
            # Turn finished without pausing (ran to END) → give up cleanly.
            if not getattr(snap, "next", None):
                break
            await asyncio.sleep(0.2)

        if paused_text is None:
            # Never reached the pause (finalizing / long tool) — demote to
            # soft so the note isn't lost, and tell the client honestly.
            clear_all_steers(thread_id)
            push_soft_steer(thread_id, text)
            logger.info("[SSE] hard steer demoted to soft thread=%s", thread_id[:12])
            return {"ok": True, "mode": "soft", "demoted": True, "reason": "finalizing"}

        # Resume: drive the graph forward. The supervisor pops the steer,
        # injects it as a first-class message, and continues. Stream the
        # continued turn to the client (same shape as /api/approve).
        logger.info("[SSE] hard steer resuming thread=%s", thread_id[:12])
        from kazma_core.safety.commitment.resume import build_resume_command

        resume_input = build_resume_command(action="apply")
        return StreamingResponse(
            _stream_langgraph_events(
                graph_inst, resume_input, config,
                thread_id=thread_id, session_id=session_id,
            ),
            media_type="text/event-stream",
        )

    # ── Abort: cancel + abandon the running task (/abort) ────────────
    @r.post("/api/chat/abort")
    async def abort_chat_turn(request: Request) -> dict[str, Any]:
        """Cancel the in-flight turn AND mark it abandoned.

        Unlike ``/api/chat/stop`` (which only cancels the pump and leaves
        ``task_status="in_progress"`` so the model resumes on the next
        "continue"), abort writes a terminal abandonment marker into the
        checkpoint (``task_status="abandoned"``, ``auto_continue=False``) so
        the model will NOT auto-continue — it only re-engages if the user
        explicitly re-asks.

        Body: ``{"session_id": ...}`` (or ``{"thread_id": ...}``).
        """
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        session_id = str(payload.get("session_id") or "")
        thread_id = str(payload.get("thread_id") or "")
        if not thread_id and session_id:
            try:
                sess = _get_store().get(session_id)
                thread_id = (sess.thread_id if sess else "") or session_id
            except Exception:
                thread_id = session_id
        if not thread_id:
            return {"ok": False, "reason": "missing_session"}

        from kazma_core.agent.steer import abort_marker, clear_all_steers

        # Drop any pending steers first so a stray drain/resume can't fire.
        clear_all_steers(thread_id)
        # Cancel the running pump (no-op if no turn is running).
        cancelled = cancel_turn(thread_id) is not None

        graph_inst = _get_graph()
        config = {
            "configurable": {"thread_id": thread_id, "checkpoint_ns": ""},
        }
        try:
            snap = await graph_inst.aget_state(config)
            msgs = list((snap.values if snap and snap.values else {}).get("messages") or [])
            msgs.append({"role": "system", "content": abort_marker()})
            await graph_inst.aupdate_state(config, {
                "messages": msgs,
                "task_status": "abandoned",
                "auto_continue": False,
            })
            logger.info(
                "[SSE] Abort thread=%s cancelled=%s marker_written=True",
                thread_id[:12], cancelled,
            )
            return {"ok": True, "cancelled": cancelled}
        except Exception as exc:  # noqa: BLE001 — never fail the HTTP call
            logger.warning("[SSE] Abort marker write failed thread=%s: %s", thread_id[:12], exc)
            return {"ok": cancelled, "cancelled": cancelled, "warning": str(exc)}

    return r
