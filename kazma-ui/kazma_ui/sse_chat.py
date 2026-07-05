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
# SSE frame helper (imported from shared utility)
# ══════════════════════════════════════════════════════════════════════════

from kazma_ui.sse_utils import sse_frame as _sse_frame


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

                    # ── Extract final assistant message from terminal state ──
                    # CRITICAL FIX (Issue 3): LLMProvider uses custom httpx
                    # calls (not LangChain BaseChatModel), so
                    # on_chat_model_stream events never fire.  The response
                    # exists in output["messages"][-1] but was never emitted.
                    # Guard with 'if not content_acc' to prevent duplicates
                    # if real streaming is ever added.
                    if not content_acc:
                        messages_list = output.get("messages", [])
                        if isinstance(messages_list, list) and messages_list:
                            last_msg = messages_list[-1]
                            if isinstance(last_msg, dict):
                                msg_content = last_msg.get("content", "")
                                if (
                                    last_msg.get("role") == "assistant"
                                    and msg_content
                                ):
                                    content_acc = msg_content
                                    yield _sse_frame(
                                        "token",
                                        {"content": msg_content},
                                    )

        # ── HITL: detect interrupt() pause ─────────────────────────
        # When tool_worker_node calls interrupt() for a danger tool, the
        # graph checkpoint pauses and astream_events ends WITHOUT the
        # terminal on_chain_end. Check the graph state for pending
        # interrupts and surface them so the frontend can prompt for
        # approval (POST /api/approve/{thread_id}).
        thread_id = (config.get("configurable") or {}).get("thread_id", "")
        interrupted = False
        try:
            snapshot = await graph.aget_state(config)
            if snapshot and getattr(snapshot, "next", None):
                # Graph still has work to do — check for interrupt tasks.
                for task in getattr(snapshot, "tasks", []) or []:
                    interrupts = getattr(task, "interrupts", []) or []
                    for intr in interrupts:
                        payload = getattr(intr, "value", None)
                        if isinstance(payload, dict) and payload.get("type") == "hitl_approval":
                            interrupted = True
                            yield _sse_frame(
                                "approval_required",
                                {
                                    "thread_id": thread_id,
                                    "tool": payload.get("tool", ""),
                                    "args": payload.get("args", {}),
                                    "message": payload.get("message", ""),
                                },
                            )
                            logger.info(
                                "[SSE] HITL interrupt: thread=%s tool=%s — awaiting approval",
                                thread_id,
                                payload.get("tool"),
                            )
        except Exception as exc:
            # aget_state may be unavailable (no checkpointer). Don't fail
            # the whole turn over interrupt detection — just log.
            logger.debug("[SSE] Could not check interrupt state: %s", exc)

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

    except Exception as exc:
        logger.error("SSE stream error: %s", exc, exc_info=True)
        yield _sse_frame("error", {"content": str(exc)})


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
    graph: Any,
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
    _store = get_session_manager()

    r = APIRouter(tags=["chat-sse"])

    # SSE-only mapping: session_id -> thread_id used for LangGraph
    # checkpointing.  This is orthogonal to the shared message-history
    # store and therefore lives here rather than in SessionManager.
    # Bounded: capped at 10 000 entries (matching SessionManager's LRU).
    _thread_ids: dict[str, str] = {}
    _THREAD_IDS_MAX = 10_000

    def _resolve_session(session_id: str) -> tuple[Any, str]:
        """Return (ChatSession, thread_id) for ``session_id``.

        Creates the ChatSession in the shared store on first use so the
        WebSocket transport can see it immediately.
        """
        session = _store.get_or_create(session_id)
        thread_id = _thread_ids.get(session_id)
        if thread_id is None:
            thread_id = str(uuid.uuid4())
            _thread_ids[session_id] = thread_id
            # Enforce bound: remove oldest entries when exceeding the cap
            if len(_thread_ids) > _THREAD_IDS_MAX:
                oldest = next(iter(_thread_ids))
                _thread_ids.pop(oldest, None)
        return session, thread_id

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
        if not user_message:
            return StreamingResponse(
                iter([_sse_frame("error", {"content": "Empty message"})]),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        session_id = body.get("session_id") or str(uuid.uuid4())

        # ── Resolve session and thread_id (shared store) ───────────
        session, thread_id = _resolve_session(session_id)

        # ── Apply model from request body (Bug 2/4 fix) ─────────────
        # The chat frontend sends the selected model so the server can
        # reconfigure the live LLM provider before streaming. This
        # closes the gap where settings.js saved the model but it never
        # reached the graph.
        requested_model = (body.get("model") or "").strip()
        if requested_model:
            if registry is not None:
                registry.set_active_model(requested_model)
            # Re-resolve the provider that owns this model so the
            # LLM client points at the correct base_url + api_key.
            _resolved_url = None
            _resolved_key = None
            if registry is not None:
                _owner = registry.find_provider_for_model(requested_model)
                if _owner:
                    _resolved_url = str(_owner.get("base_url", ""))
                    _resolved_key = str(_owner.get("api_key", ""))
                    registry.set_active_provider(_owner.get("name", ""))
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

        # ── Build conversation messages ────────────────────────────
        session.messages.append({"role": "user", "content": user_message})

        messages: list[dict[str, Any]] = []
        has_system = any(m.get("role") == "system" for m in session.messages)
        if not has_system and system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(
            {k: v for k, v in m.items() if k in ("role", "content")} for m in session.messages
        )

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
                    session.messages.append(
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
        """List all active chat sessions (shared store)."""
        return [
            s.to_summary()
            for s in _store.list_all()
        ]

    @r.delete("/api/chat/sessions/{session_id}")
    async def delete_session(session_id: str) -> dict[str, str]:
        """Delete a chat session (shared store)."""
        _store.delete(session_id)
        _thread_ids.pop(session_id, None)
        return {"status": "ok"}

    @r.get("/api/chat/sessions/{session_id}/messages")
    async def get_session_messages(session_id: str) -> list[dict[str, Any]]:
        """Return the message history for a chat session (shared store).

        Each message dict has ``role`` ("user" | "assistant") and ``content``.
        Returns an empty list if the session does not exist (e.g. it was
        created on a different transport or has already been deleted).
        """
        session = _store.get(session_id)
        if not session:
            return []
        # Return only role/content pairs so we don't leak internal keys
        return [
            {
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            }
            for msg in session.messages
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
