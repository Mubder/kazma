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
    provider_profile: dict[str, Any] | None = None,
    llm_provider: Any = None,
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
    from kazma_ui.chat import ChatSession
    from kazma_ui.chat import _sessions as _ws_sessions

    r = APIRouter(tags=["chat-sse"])

    # In-memory session → thread_id mapping (persists across requests)
    _sessions: dict[str, dict[str, Any]] = {}

    def _sync_to_shared_store(session_id: str, session: dict[str, Any]) -> None:
        """Mirror an SSE session into the shared WebSocket session store.

        The chat.py router's GET endpoints (/api/chat/sessions and
        /api/chat/sessions/{id}/messages) are registered before the SSE
        router and therefore take precedence in route matching. By syncing
        SSE-created sessions into the shared store, the session list and
        message-history endpoints serve data from both transports.
        """
        ws = _ws_sessions.get(session_id)
        if ws is None:
            ws = ChatSession(session_id=session_id)
            _ws_sessions[session_id] = ws
        ws.messages = list(session.get("messages", []))
        ws.total_cost = session.get("total_cost", 0.0)
        ws.total_tokens = session.get("total_tokens", 0)

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
        _sync_to_shared_store(session_id, session)

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
                    _sync_to_shared_store(session_id, session)

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
        _ws_sessions.pop(session_id, None)
        return {"status": "ok"}

    @r.get("/api/chat/sessions/{session_id}/messages")
    async def get_session_messages(session_id: str) -> list[dict[str, Any]]:
        """Return the message history for an SSE chat session.

        Each message dict has ``role`` ("user" | "assistant") and ``content``.
        Returns an empty list if the session does not exist (e.g. it was
        created on a different transport or has already been deleted).
        """
        session = _sessions.get(session_id)
        if not session:
            return []
        # Return only role/content pairs so we don't leak internal keys
        return [
            {
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            }
            for msg in session.get("messages", [])
        ]

    # ── Provider profile management ───────────────────────────────

    # Mutable provider profile (can be switched at runtime)
    _active_profile: dict[str, Any] = provider_profile or {}

    @r.get("/api/provider/active")
    async def get_active_provider() -> dict[str, Any]:
        """Return the currently active provider profile.

        Returns:
            {"provider": "ollama", "base_url": "...", "model": "...", "api_key": "..."}
        """
        if not _active_profile:
            return {"provider": "none", "base_url": "", "model": "", "api_key": ""}
        # Don't expose real API keys
        safe = {**_active_profile}
        if safe.get("api_key") and not safe["api_key"].startswith(("sk-lm", "sk-lit", "ollama", "not-needed")):
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
        from kazma_core.url_utils import get_dummy_api_key, normalize_model_name, normalize_provider_url

        try:
            body = await request.json()
        except Exception:
            return {"error": "Invalid JSON"}

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
