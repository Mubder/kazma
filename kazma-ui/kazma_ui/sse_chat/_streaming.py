"""LangGraph event streaming and SSE attach/resume.

Extracted from the former 3,099-line ``kazma_ui/sse_chat.py``
(audit O5). Bodies are unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter
from kazma_core.exceptions import sanitize_error
from kazma_core.shutdown import is_shutting_down

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════
# Turn Delivery V2 — SSE cursor attach (replay + live reattach)
# ══════════════════════════════════════════════════════════════════════════

#: Idle tick budget for the attach stream: ~10s per tick, 30 ticks ≈ 5 min
#: of TOTAL silence (no events AND no running turn) before closing.
_ATTACH_IDLE_TIMEOUT_S = 10.0
_ATTACH_MAX_IDLE_TICKS = 30

#: Journal frame types that terminate an attached stream.
_SSE_ATTACH_TERMINAL = frozenset({"done", "turn_complete", "stream_end"})


from kazma_ui.sse_chat._helpers import (
    _extract_hitl_payload,
    _last_assistant_text,
    _user_facing_reply,
)
from kazma_ui.sse_chat._persistence import (
    _persist_detached_reply,
    _persist_turn_reply,
)

__all__: list[str] = []

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
    is_turn_running,
    mark_turn_orphaned,
    pump_is_stalled,
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


async def _stream_langgraph_events(
    graph: Any,
    input_state: dict[str, Any] | Any,
    config: dict[str, Any],
    *,
    thread_id: str = "",
    session_id: str = "",
    reply_turn_id: str = "",
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

    **Terminal persistence lives here, not in the callers.** Every endpoint
    that streams a graph turn (``/api/chat/stream``, ``/api/approve``,
    hard-steer resume, and anything added later) reaches the same terminal
    block, so the durable write cannot be forgotten by a new caller — which
    is exactly how ``/api/approve`` shipped delivering answers it never
    saved (live incident 2026-08-28). The write goes through
    :mod:`kazma_ui.reply_sink` keyed on *reply_turn_id*, and happens BEFORE
    the ``done`` frame so a refresh the instant the answer appears cannot
    beat the store.

    Args:
        graph: Compiled LangGraph app (must support astream_events/ainvoke).
        input_state: The SupervisorState dict, or a Command for HITL resume.
        config: LangGraph config dict (thread_id, checkpoint_ns, etc.).
        thread_id: Delivery/journal thread (defaults to the config value).
        session_id: Session store id; without it no durable write happens.
        reply_turn_id: Identity of the reply row this turn owns. A HITL
            resume passes the id of the turn it is continuing so the pause
            narration and the final answer share one bubble.

    Yields:
        SSE-formatted strings.
    """
    from kazma_core.observability.correlation import (
        bind_turn_id,
        current_turn_id,
        new_turn_id,
        reset_turn_id,
    )
    from kazma_core.safety.hitl import reset_current_thread_id, set_current_thread_id

    # Turn correlation id — binds once per streamed turn so every log line,
    # the terminal done payload, and the stored reply row carry the same
    # identifier. The reply turn id wins when the caller supplied one (HITL
    # resume continuing an earlier turn), so logs follow the turn across the
    # approval pause instead of splitting into two unrelated ids.
    _turn_token = bind_turn_id(reply_turn_id or current_turn_id() or new_turn_id())

    tid = config.get("configurable", {}).get("thread_id") if config else None
    token = set_current_thread_id(tid) if tid else None

    total_tokens = 0
    total_cost = 0.0
    turn_start = time.monotonic()
    content_acc = ""  # accumulated assistant text for the done event
    # True between an on_chat_model_start and its first streamed token — used
    # to insert a paragraph break BETWEEN LLM invocations of one turn.
    _first_token_of_model_call = False
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
                # Detach the resume the same way the streaming path detaches
                # its pump. Previously ``ainvoke`` was awaited inline on the
                # request task, so closing the tab after clicking Approve
                # cancelled the graph mid-tool — an approved action could be
                # half-executed and its answer lost. Now the graph owns its
                # own task: a disconnect cancels only the delivery, and the
                # done-callback persists the result for the next page load.
                _resume_loop = asyncio.get_running_loop()
                _resume_task = asyncio.create_task(graph.ainvoke(input_state, config))
                register_turn(thread_id, _resume_task)

                def _on_resume_done(t: asyncio.Task) -> None:
                    unregister_turn(thread_id, t)
                    if not session_id or not reply_turn_id:
                        return
                    _resume_loop.call_soon_threadsafe(
                        lambda: _resume_loop.create_task(
                            _persist_detached_reply(
                                graph, config, session_id, thread_id,
                                streamed_text=content_acc,
                                reply_turn_id=reply_turn_id,
                            )
                        )
                    )

                _resume_task.add_done_callback(_on_resume_done)
                # shield: cancelling this generator (client left) must not
                # cancel the graph run it detached.
                await asyncio.shield(_resume_task)
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
                        if not pump_is_stalled(thread_id, _progress["last"]):
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
                            _persist_detached_reply(
                                graph, config, session_id, thread_id,
                                streamed_text=content_acc,
                                interrupted=interrupted,
                                reply_turn_id=reply_turn_id,
                            )
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
                        except TimeoutError:
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
                                    if _first_token_of_model_call:
                                        _first_token_of_model_call = False
                                        if content_acc and not content_acc.endswith(("\n", " ", "\t")):
                                            # Multi-iteration turns stream one
                                            # narration per LLM invocation; the
                                            # model frequently drops the
                                            # trailing newline before a tool
                                            # call, gluing "…(both TLDs):"
                                            # straight into "Batch 1/4: …" in
                                            # the live bubble (2026-08-27).
                                            # Emit a paragraph break BETWEEN
                                            # invocations only — mid-word
                                            # chunks belong to ONE invocation
                                            # and never hit this branch.
                                            sep = "\n\n"
                                            content_acc += sep
                                            yield await emit_j("token", {"content": sep})
                                    content_acc += token_text
                                    yield await emit_j("token", {"content": token_text})

                        # ── on_chat_model_start: a new LLM invocation ────────
                        elif kind == "on_chat_model_start":
                            _first_token_of_model_call = True

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
                try:
                    from kazma_core.observability.ops_alerts import alert

                    alert(
                        "turn.empty",
                        "A turn finished without producing any reply.",
                        f"thread={thread_id[:12]} tokens={total_tokens}. The "
                        f"user saw a recovery notice instead of an answer.",
                        severity="warn",
                    )
                except Exception:
                    pass

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
            # ── Durable write, BEFORE the client is told the turn is over ──
            # Ordering matters: a user who refreshes the instant the answer
            # paints must find it in the store. Emitting `done` first left a
            # window where the browser reloaded into a transcript that did
            # not yet contain the reply it had just rendered.
            _persist_turn_reply(
                session_id,
                reply_turn_id,
                content_acc,
                interrupted=interrupted,
                thread_id=thread_id,
                model=_done_model,
                tokens=total_tokens,
                cost=total_cost,
            )
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
            # A crashed turn still owes the user a durable record of whatever
            # it managed to say; without this the transcript reloads blank.
            _persist_turn_reply(
                session_id,
                reply_turn_id,
                content_acc,
                interrupted=interrupted,
                thread_id=thread_id,
                allow_shrink=False,
            )
            yield await emit_j("error", {"content": sanitize_error(exc)})
    finally:
        if token is not None:
            reset_current_thread_id(token)
        reset_turn_id(_turn_token)

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
            except TimeoutError:
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
