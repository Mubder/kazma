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
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat-sse"])


# ══════════════════════════════════════════════════════════════════════════
# SSE frame helpers
# ══════════════════════════════════════════════════════════════════════════


def _sse_frame(event: str, data: str | dict | list) -> str:
    """Format a single SSE frame.

    Args:
        event: The event type (token, tool_call, tool_result, done, error).
        data: Payload — dict/list is JSON-serialized, str is used as-is.

    Returns:
        Formatted SSE string: ``event: <type>\\ndata: <json>\\n\\n``
    """
    if isinstance(data, (dict, list)):
        data = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


# ══════════════════════════════════════════════════════════════════════════
# LangGraph event → SSE mapping
# ══════════════════════════════════════════════════════════════════════════


async def _stream_langgraph_events(
    graph: Any,
    input_state: dict[str, Any],
    config: dict[str, Any],
) -> AsyncGenerator[str, None]:
    """Consume LangGraph astream_events and yield SSE frames.

    This is the core generator that maps LangGraph v2 event types to the
    SSE contract expected by the Alpine.js frontend.

    LangGraph v2 event names we care about:
      on_chat_model_stream  → token
      on_tool_start         → tool_call
      on_tool_end           → tool_result
      on_chain_end          → done (only from the respond node)

    Args:
        graph: Compiled LangGraph app (must support astream_events).
        input_state: The SupervisorState dict to feed into the graph.
        config: LangGraph config dict (thread_id, checkpoint_ns, etc.).

    Yields:
        SSE-formatted strings.
    """
    total_tokens = 0
    total_cost = 0.0
    turn_start = time.monotonic()
    content_acc = ""  # accumulated assistant text for the done event
    error_yielded = False

    try:
        async for event in graph.astream_events(input_state, config=config, version="v2"):
            kind = event.get("event", "")
            data = event.get("data", {})
            name = event.get("name", "")

            # ── on_chat_model_stream: LLM token delta ──────────────
            if kind == "on_chat_model_stream":
                chunk = data.get("chunk")
                if chunk is not None:
                    # chunk is an AIMessageChunk — extract content
                    token_text = ""
                    if hasattr(chunk, "content"):
                        token_text = chunk.content or ""
                    elif isinstance(chunk, dict):
                        token_text = chunk.get("content", "")

                    if token_text:
                        content_acc += token_text
                        yield _sse_frame("token", {"content": token_text})

            # ── on_chat_model_end: LLM finished — extract usage ────
            elif kind == "on_chat_model_end":
                output = data.get("output", {})
                if hasattr(output, "usage_metadata"):
                    usage = output.usage_metadata or {}
                    total_tokens = usage.get("total_tokens", total_tokens)
                elif isinstance(output, dict):
                    usage = output.get("usage", {})
                    total_tokens = usage.get("total_tokens", total_tokens)
                    # Some providers put cost in response_metadata
                    meta = output.get("response_metadata", {})
                    if "cost" in meta:
                        total_cost += meta["cost"]

            # ── on_tool_start: tool execution beginning ────────────
            elif kind == "on_tool_start":
                inputs = data.get("input", {})
                # data.input can be the raw args dict or nested
                if isinstance(inputs, dict) and "input" in inputs:
                    inputs = inputs["input"]
                yield _sse_frame(
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
                yield _sse_frame(
                    "tool_result",
                    {
                        "tool_name": name,
                        "result": str(output)[:5000],
                    },
                )

            # ── on_chain_end at __end__: graph finished ────────────
            elif kind == "on_chain_end" and name == "__end__":
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

        # ── Turn complete ──────────────────────────────────────────
        duration_ms = (time.monotonic() - turn_start) * 1000
        logger.info(
            "SSE turn complete: tokens=%d cost=$%.4f duration=%.0fms content_len=%d",
            total_tokens,
            total_cost,
            duration_ms,
            len(content_acc),
        )
        yield _sse_frame(
            "done",
            {
                "tokens": total_tokens,
                "cost": round(total_cost, 6),
                "duration_ms": round(duration_ms, 0),
            },
        )

    except asyncio.CancelledError:
        logger.warning("SSE stream cancelled by client disconnect")
        yield _sse_frame("error", {"content": "Connection cancelled"})
        error_yielded = True

    except Exception as exc:
        logger.error("SSE stream error: %s", exc, exc_info=True)
        yield _sse_frame("error", {"content": str(exc)})
        error_yielded = True


# ══════════════════════════════════════════════════════════════════════════
# POST /api/chat/stream
# ══════════════════════════════════════════════════════════════════════════


def create_sse_chat_router(
    graph: Any,
    checkpointer: Any,
    system_prompt: str = "",
    cost_breaker: Any = None,
    authority: Any = None,
    tracer: Any = None,
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

    Returns:
        APIRouter with POST /api/chat/stream registered.
    """
    r = APIRouter(tags=["chat-sse"])

    # In-memory session → thread_id mapping (persists across requests)
    _sessions: dict[str, dict[str, Any]] = {}

    @r.post("/api/chat/stream")
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
        if not user_message:
            return StreamingResponse(
                iter([_sse_frame("error", {"content": "Empty message"})]),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        session_id = body.get("session_id") or str(uuid.uuid4())

        # ── Resolve thread_id for this session ─────────────────────
        if session_id not in _sessions:
            _sessions[session_id] = {
                "thread_id": str(uuid.uuid4()),
                "messages": [],
                "total_cost": 0.0,
                "total_tokens": 0,
            }
        session = _sessions[session_id]
        thread_id = session["thread_id"]

        # ── Cost breaker gate ──────────────────────────────────────
        if cost_breaker and cost_breaker.should_halt():
            return StreamingResponse(
                iter([_sse_frame("error", {"content": "⚠️ ميزانية الجلسة انتهت. أعد التشغيل. (Budget exceeded)"})]),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        if cost_breaker:
            cost_breaker.record_user_interaction()

        # ── Build conversation messages ────────────────────────────
        session["messages"].append({"role": "user", "content": user_message})

        messages: list[dict[str, Any]] = []
        has_system = any(m.get("role") == "system" for m in session["messages"])
        if not has_system and system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(session["messages"])

        # ── Build SupervisorState for the graph ────────────────────
        from kazma_core.agent.state import initial_supervisor_state

        input_state = initial_supervisor_state(thread_id=thread_id)
        input_state["messages"] = messages

        # ── LangGraph config with thread_id for checkpointing ──────
        graph_config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
            },
        }

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

            try:
                async for frame in _stream_langgraph_events(
                    graph=graph,
                    input_state=input_state,
                    config=graph_config,
                ):
                    # Accumulate content for session history
                    if frame.startswith("event: token\n"):
                        try:
                            data = json.loads(frame.split("data: ", 1)[1].split("\n\n")[0])
                            content_acc += data.get("content", "")
                        except (json.JSONDecodeError, IndexError):
                            pass

                    yield frame

                # Store assistant response in session history
                if content_acc:
                    session["messages"].append(
                        {
                            "role": "assistant",
                            "content": content_acc,
                        }
                    )

            except asyncio.CancelledError:
                logger.warning("SSE generator cancelled for session=%s", session_id)
                yield _sse_frame("error", {"content": "Connection closed"})

            except Exception as exc:
                logger.error("SSE generator error: %s", exc, exc_info=True)
                yield _sse_frame("error", {"content": str(exc)})

        return StreamingResponse(
            _event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @r.get("/api/chat/sessions")
    async def list_sessions() -> list[dict[str, Any]]:
        """List all active SSE chat sessions."""
        return [
            {
                "session_id": sid,
                "message_count": len(s["messages"]),
                "total_cost": s["total_cost"],
                "total_tokens": s["total_tokens"],
            }
            for sid, s in _sessions.items()
        ]

    @r.delete("/api/chat/sessions/{session_id}")
    async def delete_session(session_id: str) -> dict[str, str]:
        """Delete an SSE chat session."""
        _sessions.pop(session_id, None)
        return {"status": "ok"}

    return r
