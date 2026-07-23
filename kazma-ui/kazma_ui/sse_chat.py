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
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from kazma_core.exceptions import sanitize_error

logger = logging.getLogger(__name__)

__all__ = ["create_sse_chat_router", "router"]

router = APIRouter(tags=["chat-sse"])


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
    from kazma_core.safety.hitl import set_current_thread_id, reset_current_thread_id

    tid = config.get("configurable", {}).get("thread_id") if config else None
    token = set_current_thread_id(tid) if tid else None

    total_tokens = 0
    total_cost = 0.0
    turn_start = time.monotonic()
    content_acc = ""  # accumulated assistant text for the done event
    _snapshot_info: dict[str, Any] | None = None  # last snapshot_id/iteration from graph state

    try:
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

                # ── on_chain_end at graph terminal: graph finished ─────
                # LangGraph 1.x emits the terminal on_chain_end with name
                # "LangGraph"; older versions (and some test mocks) use
                # "__end__".  Match both so the handler fires in production
                # and in unit tests.
                elif kind == "on_chain_end" and name in ("__end__", "LangGraph"):
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

                        # Time Travel: capture snapshot id/iteration if the
                        # graph stamped one (snapshot_recorder is wired).
                        _sid = output.get("snapshot_id")
                        if _sid:
                            _snapshot_info = {
                                "snapshot_id": _sid,
                                "iteration": output.get("snapshot_iteration", 0),
                                "model": output.get("last_model", ""),
                            }

                        # CRITICAL: LLMProvider uses custom httpx (not
                        # BaseChatModel), so on_chat_model_stream never fires.
                        # Surface final assistant text from graph state.
                        if not content_acc:
                            msg_content = _last_assistant_text(
                                output.get("messages") or []
                            )
                            if msg_content:
                                content_acc = msg_content
                                yield _sse_frame(
                                    "token",
                                    {"content": msg_content},
                                )

            # ── Post-stream: HITL + backfill assistant text ────────────
            # Custom LLM path never streams tokens. On HITL interrupt,
            # astream_events ends WITHOUT terminal on_chain_end — so we
            # must (1) detect interrupt, (2) pull any assistant prose from
            # checkpoint state, (3) never leave the UI with only "Thinking…".
            thread_id = (config.get("configurable") or {}).get("thread_id", "")
            interrupted = False
            snapshot = None
            try:
                snapshot = await graph.aget_state(config)
            except Exception as exc:
                logger.warning("[SSE] aget_state failed after stream: %s", exc)

            if snapshot is not None:
                # Backfill assistant text from checkpoint (interrupt or complete)
                if not content_acc:
                    try:
                        vals = getattr(snapshot, "values", None) or {}
                        msgs = vals.get("messages") if isinstance(vals, dict) else None
                        msg_content = _last_assistant_text(msgs or [])
                        if msg_content:
                            content_acc = msg_content
                            yield _sse_frame("token", {"content": msg_content})
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
                                yield _sse_frame(
                                    "approval_required",
                                    {
                                        "thread_id": thread_id,
                                        "tool": payload.get("tool", ""),
                                        "args": payload.get("args", {}),
                                        "tools": payload.get("tools") or [],
                                        "message": payload.get("message", ""),
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
                            yield _sse_frame("token", {"content": notice})
                except Exception as exc:
                    logger.warning("[SSE] interrupt scan failed: %s", exc, exc_info=True)

            # Never leave the chat blank after "Thinking…"
            if not content_acc and not interrupted:
                notice = (
                    "⚠️ No assistant text was returned for this turn "
                    "(model may have failed silently or only planned tools). "
                    "Please try again or check server logs."
                )
                content_acc = notice
                yield _sse_frame("token", {"content": notice})
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
                        for m in reversed(list((input_state or {}).get("messages") or [])):
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

            yield _sse_frame(
                "done",
                {
                    "tokens": total_tokens,
                    "cost": round(total_cost, 6),
                    "duration_ms": round(duration_ms, 0),
                    "interrupted": interrupted,
                    "empty": (not content_acc and not interrupted),
                },
            )

            # Time Travel: notify the UI a snapshot was captured (live
            # timeline growth). No-op if the replay panel isn't open.
            if _snapshot_info:
                yield _sse_frame("snapshot", _snapshot_info)

        except asyncio.CancelledError:
            logger.warning("SSE stream cancelled by client disconnect")
            yield _sse_frame("error", {"content": "Connection cancelled"})

        except Exception as exc:
            logger.error("SSE stream error: %s", exc, exc_info=True)
            yield _sse_frame("error", {"content": sanitize_error(exc)})
    finally:
        if token is not None:
            reset_current_thread_id(token)


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
        llm_provider: LLMProvider instance — reconfigured on provider switch.

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
        # Platform-linked seasons: session_id == thread_id always.
        if session_id.startswith("gw-"):
            if session.thread_id != session_id:
                session.thread_id = session_id
                _get_store().put(session)
        elif not session.thread_id:
            session.thread_id = str(uuid.uuid4())
            _get_store().put(session)
        return session, session.thread_id

    # ── Provider profile management ───────────────────────────────

    # Mutable provider profile (can be switched at runtime)
    _active_profile: dict[str, Any] = provider_profile or {}

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
        # Optional attachments uploaded via /api/chat/upload. Each entry is an
        # Attachment-shaped dict {id, kind, mime, filename, path}.
        raw_attachments = body.get("attachments") or []
        if not user_message and not raw_attachments:
            return StreamingResponse(
                iter([_sse_frame("error", {"content": "Empty message"})]),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        session_id = body.get("session_id") or str(uuid.uuid4())

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

        # ── Intercept YOLO command ─────────────────────────────────
        raw_msg = (body.get("message") or "").strip()
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
            session.messages.append({"role": "assistant", "content": confirmation})
            try:
                _get_store().put(session)
            except Exception:
                logger.exception("[SSE] failed to persist YOLO message")

            async def _yolo_generator() -> AsyncGenerator[str, None]:
                yield _sse_frame("token", {"content": confirmation})
                yield _sse_frame("done", {
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

        # ── Intercept /swarm <task> and /research <topic> ──────────
        # These dispatch directly through SwarmEngine — bypassing the LLM's
        # tool-call decision — so swarm research always works from chat.
        _lower = raw_msg.lower().strip()
        if _lower.startswith("/swarm ") or _lower.startswith("/research ") or _lower == "/swarm" or _lower == "/research":
            _is_research = _lower.startswith("/research")
            _task_text = raw_msg.split(maxsplit=1)[1].strip() if " " in raw_msg else ""
            if not _task_text:
                _usage = (
                    "🔍 *Usage:* `/research <topic>` — dispatches the swarm to research a topic.\n\n"
                    "Example: `/research latest hair transplant techniques`"
                ) if _is_research else (
                    "🐝 *Usage:* `/swarm <task>` — dispatches a task to the swarm.\n\n"
                    "Example: `/swarm analyze competitor pricing`"
                )

                async def _swarm_usage_gen() -> AsyncGenerator[str, None]:
                    yield _sse_frame("token", {"content": _usage})
                    yield _sse_frame("done", {"tokens": 1, "cost": 0.0, "duration_ms": 100})

                return StreamingResponse(
                    _swarm_usage_gen(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )

            try:
                import asyncio as _asyncio
                from kazma_core.swarm import SwarmTask, TaskType, get_swarm_engine

                _engine = get_swarm_engine()
                if _engine is None:
                    raise RuntimeError("Swarm engine not initialized")

                # Auto-register a researcher worker if none exist.
                if not _engine.worker_names:
                    from kazma_core.swarm.config import WorkerConfig, WorkerCapabilities
                    _profile = registry.get_active_profile() if registry else {}
                    _engine.add_worker(WorkerConfig(
                        name="researcher",
                        type="in_process",
                        model=_profile.get("model", ""),
                        provider=_profile.get("provider", ""),
                        role="researcher",
                        system_prompt="You are a Researcher. Use web_search, read_url, and crawl_site to research thoroughly.",
                        capabilities=WorkerCapabilities(
                            role="researcher", expertise=["research"],
                            tools=["web_search", "read_url", "crawl_site"],
                        ),
                    ))

                _worker = _engine.worker_names[0]
                _swarm_task = SwarmTask(
                    prompt=_task_text,
                    workers=[_worker],
                    type=TaskType.DISPATCH,
                    timeout=300.0,
                    metadata={"source": "chat", "kind": "research" if _is_research else "swarm"},
                )
                logger.info("[SSE] /swarm dispatch: task=%s worker=%s", _swarm_task.id, _worker)

                # Run dispatch in foreground (blocking) and stream the result.
                async def _swarm_dispatch_gen() -> AsyncGenerator[str, None]:
                    yield _sse_frame("token", {"content": f"🐝 Dispatching to swarm worker '{_worker}'...\n\n"})
                    try:
                        result = await _engine.dispatch(_swarm_task)
                        _output = ""
                        if result:
                            _output = (
                                result.aggregated_output
                                or result.synthesized_output
                                or (result.worker_results[0].output if result.worker_results else "")
                                or "(no output)"
                            )
                            _cost = getattr(result, "total_cost", 0.0)
                            _dur = getattr(result, "duration_seconds", 0.0)
                            _output = f"✅ Swarm task complete (cost: ${_cost:.4f}, duration: {_dur:.1f}s)\n\n{_output}"
                        else:
                            _output = "⚠️ Swarm task returned no result."
                    except Exception as exc:
                        _output = f"⚠️ Swarm task failed: {exc}"
                        logger.exception("[SSE] /swarm dispatch failed")
                    yield _sse_frame("token", {"content": _output})
                    yield _sse_frame("done", {"tokens": 1, "cost": 0.0, "duration_ms": 100})

                return StreamingResponse(
                    _swarm_dispatch_gen(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            except Exception as exc:
                logger.exception("[SSE] /swarm intercept failed")

                async def _swarm_err_gen() -> AsyncGenerator[str, None]:
                    yield _sse_frame("token", {"content": f"⚠️ Could not dispatch swarm: {exc}"})
                    yield _sse_frame("done", {"tokens": 1, "cost": 0.0, "duration_ms": 100})

                return StreamingResponse(
                    _swarm_err_gen(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )

        # ── Apply model from request body ──────────────────────────
        # The chat frontend sends the selected model so the server can
        # reconfigure the live LLM provider before streaming. We only
        # reconfigure the llm_provider for this request — we do NOT call
        # registry.set_active_model() to avoid mutating global state for
        # all users (any authenticated user could otherwise change the
        # model for everyone).
        requested_model = (body.get("model") or "").strip()
        if requested_model:
            _resolved_url = None
            _resolved_key = None
            if registry is not None:
                _owner = registry.find_provider_for_model(requested_model)
                if _owner:
                    _resolved_url = str(_owner.get("base_url", ""))
                    _resolved_key = str(_owner.get("api_key", ""))
                    logger.info(
                        "SSE chat: model %s routed to provider %s (%s)",
                        requested_model,
                        _owner.get("name", "?"),
                        _resolved_url,
                    )
            if llm_provider is not None:
                llm_provider.reconfigure(
                    base_url=_resolved_url,
                    model=requested_model,
                    api_key=_resolved_key,
                )
            elif _resolved_url or _resolved_key:
                logger.warning("SSE chat: llm_provider is None, cannot reconfigure to %s", _resolved_url)

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
        if cost_breaker and cost_breaker.should_halt():
            return StreamingResponse(
                iter([_sse_frame("error", {"content": "Session budget exceeded. Please restart."})]),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        if cost_breaker:
            cost_breaker.record_user_interaction()

        # ── Persist UI projection (display only) ───────────────────
        session.messages.append({"role": "user", "content": user_message})
        # CRITICAL: persist immediately so restarts keep the sidebar transcript.
        try:
            _get_store().put(session)
        except Exception:
            logger.exception("[SSE] failed to persist user message for session=%s", session_id)

        # ── LangGraph config with thread_id for checkpointing ──────
        graph_config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
            },
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

        try:
            from kazma_core.ide.env_context import build_env_context

            env_block = build_env_context()
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
                from kazma_gateway.gateway import Attachment
                from kazma_gateway.agent_handler.attachments import build_user_content
                from pathlib import Path as _Path

                atts: list[Attachment] = []
                for a in raw_attachments:
                    kind = a.get("kind", "file")
                    mime = a.get("mime", "application/octet-stream")
                    data = None
                    p = a.get("path")
                    if p:
                        try:
                            data = _Path(p).read_bytes()
                        except Exception:  # noqa: BLE001
                            data = None
                    atts.append(
                        Attachment(
                            kind=kind,
                            mime=mime,
                            filename=a.get("filename", ""),
                            data=data,
                            url=a.get("url"),
                        )
                    )
                multimodal_content = build_user_content(user_message or "", atts)
                # Replace the trailing user message content.
                for i in range(len(messages) - 1, -1, -1):
                    if isinstance(messages[i], dict) and messages[i].get("role") == "user":
                        messages[i]["content"] = multimodal_content
                        break
            except Exception:  # noqa: BLE001 — never block a turn on media
                logger.debug("[SSE] attachment content build failed", exc_info=True)

        # ── Build SupervisorState for the graph ────────────────────
        from kazma_core.agent.state import initial_supervisor_state

        input_state = initial_supervisor_state(thread_id=thread_id)
        input_state["messages"] = messages

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
                    graph=current_graph,
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

                # Store assistant response in session history + persist to disk.
                if content_acc:
                    session.messages.append(
                        {
                            "role": "assistant",
                            "content": content_acc,
                        }
                    )
                try:
                    _get_store().put(session)
                except Exception:
                    logger.exception(
                        "[SSE] failed to persist turn for session=%s", session_id
                    )

            except asyncio.CancelledError:
                logger.warning("SSE generator cancelled for session=%s", session_id)
                # Still flush whatever we have so a dropped connection doesn't
                # lose the user message (and partial assistant text).
                try:
                    if content_acc:
                        session.messages.append(
                            {"role": "assistant", "content": content_acc}
                        )
                    _get_store().put(session)
                except Exception:
                    pass
                yield _sse_frame("error", {"content": "Connection closed"})

            except Exception as exc:
                logger.error("SSE generator error: %s", exc, exc_info=True)
                try:
                    _get_store().put(session)
                except Exception:
                    pass
                yield _sse_frame("error", {"content": sanitize_error(exc)})

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
        """List all active chat sessions (shared store)."""
        return [
            s.to_summary()
            for s in _get_store().list_all()
        ]

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

    @r.get("/api/chat/sessions/archived")
    async def list_archived_sessions() -> list[dict[str, Any]]:
        """List archived chat sessions (for the archive view)."""
        try:
            return [
                s.to_summary()
                for s in _get_store().list_all(include_archived=True)
                if s.archived
            ]
        except Exception:
            return []

    @r.get("/api/chat/sessions/{session_id}/messages")
    async def get_session_messages(session_id: str) -> list[dict[str, Any]]:
        """Return the message history for a chat session (shared store).

        Cross-platform seasons: if the UI projection is empty but a
        LangGraph checkpointer has history for this thread (e.g. Telegram
        session opened in Web), hydrate from the checkpointer and persist
        back to SessionManager so takeover is seamless.
        """
        session = _get_store().get(session_id)
        if not session:
            # Platform seasons may not be in store yet — create shell
            if session_id.startswith("gw-"):
                session = _get_store().get_or_create(session_id)
                session.thread_id = session_id
            else:
                return []

        messages = list(session.messages or [])
        if not messages:
            # Hydrate from checkpointer (source of truth for agent seasons)
            try:
                live = _get_graph()
                tid = session.thread_id or (
                    session_id if session_id.startswith("gw-") else ""
                )
                if live and tid and getattr(live, "checkpointer", None):
                    from kazma_core.agent.turn_input import load_checkpoint_messages

                    prior = await load_checkpoint_messages(
                        live,
                        {"configurable": {"thread_id": tid, "checkpoint_ns": ""}},
                    )
                    ui = [
                        {"role": m.get("role", "user"), "content": m.get("content", "")}
                        for m in prior
                        if isinstance(m, dict)
                        and m.get("role") in ("user", "assistant", "system")
                        and (m.get("content") or "").strip()
                    ]
                    if ui:
                        session.messages = ui
                        if session_id.startswith("gw-"):
                            session.thread_id = session_id
                        _get_store().put(session)
                        messages = ui
            except Exception:
                logger.debug(
                    "[SSE] checkpointer hydrate failed for %s",
                    session_id,
                    exc_info=True,
                )

        return [
            {
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            }
            for msg in messages
            if msg.get("role") in ("user", "assistant", "system")
        ]

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

    @r.get("/api/providers")
    async def list_providers_endpoint() -> list[dict[str, str]]:
        """Return the list of known provider presets."""
        from kazma_core.providers import list_providers
        return list_providers()

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

        if registry is not None:
            result = registry.set_active_provider(
                provider=body.get("provider", ""),
                base_url=body.get("base_url", ""),
                model=body.get("model", ""),
                api_key=body.get("api_key", ""),
            )
            # Also reconfigure the live llm_provider if passed
            if llm_provider is not None:
                llm_provider.reconfigure(
                    base_url=result.get("base_url", ""),
                    model=result.get("model", ""),
                    api_key=result.get("api_key", ""),
                )
            return {**result, "status": "ok"}

        # Fallback: old behavior
        from kazma_core.url_utils import get_dummy_api_key, normalize_model_name, normalize_provider_url

        prov = body.get("provider", "").lower().strip()
        raw_url = body.get("base_url", "")
        raw_model = body.get("model", "")
        raw_key = body.get("api_key", "")

        # Use preset base_url if a known built-in provider
        from kazma_core.providers import PROVIDER_PRESETS
        if prov in PROVIDER_PRESETS and not raw_url:
            url = PROVIDER_PRESETS[prov]["base_url"]
        elif prov in ("ollama",):
            url = "http://127.0.0.1:11434/v1"
        elif prov in ("lm-studio", "lm_studio", "lmstudio"):
            url = normalize_provider_url(raw_url or "http://localhost:1234/v1")
        elif prov in ("custom", "openai"):
            url = normalize_provider_url(raw_url)
        else:
            url = normalize_provider_url(raw_url) if raw_url else ""

        # Normalize model name
        model = normalize_model_name(raw_model, url)

        # Resolve API key
        api_key = get_dummy_api_key(url, raw_key)

        _active_profile.clear()
        _active_profile.update(
            {
                "provider": prov,
                "base_url": url,
                "model": model,
                "api_key": api_key,
            }
        )

        # Reconfigure the graph's LLM provider at runtime
        if llm_provider is not None:
            llm_provider.reconfigure(base_url=url, model=model, api_key=api_key)

        logger.info(
            "Provider switched: %s model=%s base_url=%s",
            prov,
            model,
            url,
        )

        return {
            "provider": prov,
            "base_url": url,
            "model": model,
            "status": "ok",
        }

    return r
