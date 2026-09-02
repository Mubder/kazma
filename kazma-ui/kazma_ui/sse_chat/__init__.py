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
from collections.abc import AsyncGenerator, Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from kazma_core.exceptions import sanitize_error
from kazma_core.shutdown import is_shutting_down

from kazma_ui.rate_limit import rate_limit

logger = logging.getLogger(__name__)

# Layers extracted from this module when it was split (audit O5).
from kazma_ui.sse_chat._helpers import (  # noqa: F401
    _convert_messages_to_dicts,
    _extract_hitl_payload,
    _is_cloud_url,
    _last_assistant_text,
    _message_text,
    _module_graph,
    _module_graph_holder,
    _module_store,
    _user_facing_reply,
)
from kazma_ui.sse_chat._persistence import (  # noqa: F401
    _checkpoint_backfill_unanswered,
    _persist_detached_reply,
    _persist_instant_turn,
    _snapshot_paused,
)
from kazma_ui.sse_chat._streaming import (  # noqa: F401
    _drive_graph_to_journal,
    _frame_from_journaled,
    _journal_fast_path,
    _sse_attach_stream,
    _stream_langgraph_events,
    is_thread_paused,
    mark_thread_paused,
    mark_thread_unpaused,
    stamp_hitl_part_state,
)

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
# Re-exported for backward compatibility: callers still do
# `from kazma_ui.sse_chat import ...` for names that moved into the
# submodules below, or that are now only used by them (audit O5).
from kazma_ui.active_turns import (  # noqa: F401
    DETACHED_TTL_S,
    active_turns,
    cancel_turn,
    get_active_turn,
    is_turn_running,
    mark_turn_orphaned,
    pump_is_stalled,
    reap_stale_turn,
    register_turn,
    unregister_turn,
)
from kazma_ui.delivery import get_turn_broker, is_replayable  # noqa: F401

_active_turns = active_turns  # type: ignore[name-defined]

# T1: strong references to detached-pump watchdog tasks so CPython never
# GCs one while its pump is still running.


# ══════════════════════════════════════════════════════════════════════════
# SSE frame helper (imported from shared utility)
# ══════════════════════════════════════════════════════════════════════════

from kazma_ui.sse_utils import sse_frame as _sse_frame

# Graph accessor for module-level helpers: populated by the router factory
# with its live ``_get_graph`` closure at creation time.
# _module_graph_holder now lives in _helpers (imported below).














# ══════════════════════════════════════════════════════════════════════════
# LangGraph event → SSE mapping
# ══════════════════════════════════════════════════════════════════════════




# ══════════════════════════════════════════════════════════════════════════
# Turn Delivery V2 — SSE cursor attach (replay + live reattach)
# ══════════════════════════════════════════════════════════════════════════

#: Idle tick budget for the attach stream: ~10s per tick, 30 ticks ≈ 5 min
#: of TOTAL silence (no events AND no running turn) before closing.

#: Journal frame types that terminate an attached stream.








# ══════════════════════════════════════════════════════════════════════════
# POST /api/chat/stream
# ══════════════════════════════════════════════════════════════════════════




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
            _usage = (
                "Usage: `/research deep <topic>` — runs through "
                "the same agent as chat (tools + HITL)."
            )
            _persist_instant_turn(session, thread_id, raw_msg, _usage)

            async def _research_gen() -> AsyncGenerator[str, None]:
                yield await _journal_fast_path(thread_id, "token", {"content": _usage})
                yield await _journal_fast_path(thread_id, "done", {"tokens": 0, "cost": 0.0, "duration_ms": 0})

            return StreamingResponse(
                _research_gen(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        from kazma_ui.sse_chat._capacity import intercept_capacity_fast_path

        _cap_resp, _cap_rewrite = intercept_capacity_fast_path(
            session=session,
            thread_id=thread_id,
            session_id=session_id,
            raw_msg=raw_msg,
        )
        if _cap_resp is not None:
            return _cap_resp
        if _cap_rewrite:
            raw_msg = _cap_rewrite
            user_message = _cap_rewrite

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
                        
                        from kazma_ui.turn_runtime import invoke_turn as _invoke_compact

                        result_state = await _invoke_compact(
                            live_graph,
                            current_values,
                            config,
                            session_id=getattr(session, "session_id", "") or "",
                            thread_id=thread_id,
                            persist=False,
                        )
                        
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

            # /compact rewrites the transcript from the compacted checkpoint
            # and used to stream its confirmation without storing it — the
            # user reloaded into a compacted history with no sign of why.
            _persist_instant_turn(session, thread_id, raw_msg, confirmation)

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
            _swarm_usage = (
                "🐝 `/swarm <task>` goes through the same agent as chat "
                "(tools + HITL). Example: `/swarm analyze competitor pricing`\n"
                "`/swarm status` and `/swarm list` still answer instantly."
            )
            _persist_instant_turn(session, thread_id, raw_msg, _swarm_usage)

            async def _swarm_usage_gen() -> AsyncGenerator[str, None]:
                yield _sse_frame("token", {"content": _swarm_usage})
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
        from datetime import UTC
        from datetime import datetime as _dt

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
        from kazma_core.agent.long_task import consume_long_task_turn
        from kazma_core.agent.turn_input import build_turn_messages

        # Consume a long_task turn-budget at the START of each new user message.
        consume_long_task_turn(thread_id)

        current_graph = _get_graph()
        # If user sent a new message while HITL is waiting, auto-deny so
        # tool chains close cleanly (no silent supersede / amnesia).
        try:
            # A new stream is a new turn. Preserve the interrupt only when
            # a live unanswered gate is still pending (the client should
            # have steered). After /abort the gate is settled and the
            # leftover checkpoint interrupt must be denied so the prompt
            # is not swallowed as /steer.
            _hitl_now = "idle"
            try:
                from kazma_ui.hitl_status import hitl_thread_status as _hitl_status

                _hitl_now = await _hitl_status(thread_id, graph=current_graph)
            except Exception:
                _hitl_now = "idle"
            cancelled = await cancel_pending_hitl(
                current_graph,
                graph_config,
                reason="superseded by new user message",
                auto_deny=_hitl_now != "pending",
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
        # Web has no inbound telegram chat_id. Stamp operator Telegram on
        # `_gateway` so schedule_task captures a deliverable target (the
        # gateway path already writes this block; SSE used not to).
        try:
            from kazma_core.tools.send_message import web_gateway_block

            input_state["_gateway"] = web_gateway_block(thread_id)
        except Exception:
            logger.debug("[SSE] web gateway stamp skipped", exc_info=True)
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

        # ── Reply identity for this turn ───────────────────────────
        # One id owns the reply row from here until the turn produces its
        # final answer — across a HITL pause and its separate approve
        # request. Every writer for this turn upserts on it.
        from kazma_ui.reply_sink import open_reply_turn as _open_reply_turn
        from kazma_ui.turn_runtime import persist_reply as _persist_reply

        _reply_turn = _open_reply_turn(thread_id)

        # ── Stream the response ────────────────────────────────────
        async def _event_generator() -> AsyncGenerator[str, None]:
            content_acc = ""
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

            def _persist_now(*, final: str | None = None, open_turn: bool = True) -> None:
                """Flush in-progress text into this turn's reply row.

                Turn-keyed upsert: repeated calls update the same row, so it
                no longer matters whether this ran before the detached
                callback did. The old version appended on first call and
                overwrote ``messages[-1]`` after — and since it only fired
                on ``len(content_acc) % 50 == 0``, a single 125-char
                backfilled chunk skipped it entirely and let two other
                writers each append their own row (2026-08-28 duplicates).
                """
                from kazma_ui.turn_document import activity_of, parts_from_stream, text_of

                streamed = str(content_acc or "")
                hop = final if final is not None else ""
                parts = parts_from_stream(
                    streamed=streamed,
                    final=hop or streamed,
                    activity=activity_log or None,
                )
                body = text_of(parts) or hop or streamed
                _persist_reply(
                    session_id,
                    _reply_turn,
                    body,
                    interrupted=open_turn,
                    pending=not str(body or "").strip(),
                    activity=activity_of(parts) or activity_log or None,
                    parts=parts,
                    streamed_text=streamed,
                    model=_turn_model or None,
                    thread_id=thread_id,
                )

            # Per-turn usage captured from the done/turn_complete frame so the
            # final persist can stamp it on the assistant message (reload stats).
            turn_usage: dict[str, Any] = {}
            # Chars already flushed to the store by _persist_now.
            _flushed_at = [0]

            try:
                # CQRS: the graph runs in a shielded background task that
                # journals only. This HTTP body is a journal subscriber —
                # a 70s MCP call or a dropped TCP cannot kill the turn.
                #
                # A NEW prompt subscribes at the CURRENT journal head, not 0:
                # the journal is per-THREAD and retains the previous turn's
                # frames, so replaying from 0 re-delivered the old reply's
                # token frames into the fresh turn — the two replies crossed
                # bubbles in chat ("my answer appeared above the previous
                # reply", 2026-09-02). Frames the drive emits after this
                # capture carry seq > head and arrive via resume(head) ∪ the
                # live queue, deduplicated by seq inside _sse_attach_stream.
                _journal_head = get_turn_broker().head_seq(thread_id)
                _drive = asyncio.create_task(
                    _drive_graph_to_journal(
                        current_graph,
                        input_state,
                        graph_config,
                        thread_id=thread_id,
                        session_id=session_id,
                        reply_turn_id=_reply_turn,
                    )
                )
                register_turn(thread_id, _drive)

                def _on_drive_done(t: asyncio.Task, tid: str = thread_id) -> None:
                    unregister_turn(tid, t)

                _drive.add_done_callback(_on_drive_done)

                async for frame in _sse_attach_stream(thread_id, session_id, _journal_head):
                    parsed = _parse_frame(frame)
                    if parsed is None:
                        yield frame
                        continue
                    ev_type, data = parsed
                    if ev_type in ("status_update", "status") and str(
                        data.get("status") or ""
                    ) == "resync":
                        yield frame
                        return

                    # Accumulate content + record CoT activity
                    if ev_type == "token":
                        token_text = str(data.get("content", "") or "")
                        content_acc += token_text
                        temp_assistant_msg["content"] += token_text

                        # Flush every ~50 chars of GROWTH. The old
                        # ``len(content_acc) % 50 == 0`` test only fired when
                        # the running length landed exactly on a multiple of
                        # 50, so a reply delivered as one backfilled chunk
                        # (125 chars → 125 % 50 == 25) never flushed at all.
                        if len(content_acc) - _flushed_at[0] >= 50:
                            _flushed_at[0] = len(content_acc)
                            _persist_now()
                    elif ev_type in ("tool_call", "tool_result", "status_update"):
                        _record_activity(ev_type, data)
                    elif ev_type in ("done", "turn_complete"):
                        if data.get("tokens") is not None:
                            turn_usage["tokens"] = data.get("tokens")
                        if data.get("cost") is not None:
                            turn_usage["cost"] = data.get("cost")
                        # A turn that paused for approval is NOT finished —
                        # its row must stay open so the approve request can
                        # find it after a restart.
                        if data.get("interrupted"):
                            turn_usage["interrupted"] = True
                        # Terminal frame is SoT for the *text* part. Keep the
                        # token buffer as streamed working notes so a shorter
                        # final hop cannot erase them.
                        done_text = str(data.get("content") or "")
                        if done_text.strip():
                            temp_assistant_msg["content"] = done_text.strip()

                    yield frame

                # The streamer already wrote the reply before emitting `done`.
                # This upsert stamps parts + CoT activity + usage onto the
                # SAME row. Streamed notes stay in ``reasoning``; the hop
                # in ``text``.
                done_body = str(temp_assistant_msg.get("content") or "") or content_acc
                if done_body or activity_log:
                    from kazma_ui.turn_document import activity_of, parts_from_stream, text_of

                    parts = parts_from_stream(
                        streamed=content_acc,
                        final=done_body,
                        activity=activity_log or None,
                    )
                    _persist_reply(
                        session_id,
                        _reply_turn,
                        text_of(parts) or done_body,
                        interrupted=bool(turn_usage.get("interrupted")),
                        activity=activity_of(parts) or activity_log or None,
                        parts=parts,
                        streamed_text=content_acc,
                        model=_turn_model or None,
                        tokens=turn_usage.get("tokens"),
                        cost=turn_usage.get("cost"),
                        thread_id=thread_id,
                    )

            except asyncio.CancelledError:
                logger.warning("SSE generator cancelled for session=%s (client refresh/tab switch?)", session_id)
                # Flush whatever partial content we have so the user's question
                # isn't left without an answer on reload. The turn stays OPEN:
                # the pump is detached and still running, and its callback
                # writes the real final answer into this same row.
                #
                # The previous version walked backwards for "the last
                # assistant message anywhere" and overwrote it — on a turn
                # that had not yet written a row of its own, that target was
                # the PREVIOUS turn's answer, silently replacing good history
                # with a fragment.
                _persist_now(open_turn=True)
                raise

            except Exception as exc:
                logger.error("SSE generator error: %s", exc, exc_info=True)
                _persist_now(open_turn=False)
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
        paused = bool(thread_id) and is_thread_paused(thread_id)
        hitl: dict[str, Any] | None = None
        if thread_id:
            try:
                from kazma_ui.hitl_status import (
                    hitl_thread_status,
                    persisted_hitl_for_thread,
                )

                gate = "idle"
                try:
                    gate = await hitl_thread_status(thread_id, graph=_get_graph())
                except Exception:
                    gate = "idle"
                part = persisted_hitl_for_thread(thread_id)
                payload: dict[str, Any] = {}
                if isinstance(part, dict):
                    raw_payload = part.get("payload")
                    payload = raw_payload if isinstance(raw_payload, dict) else {}
                    hitl = {
                        "state": str(part.get("state") or "pending"),
                        "tool": str(part.get("tool") or payload.get("tool") or ""),
                        "interrupt_id": str(
                            part.get("interrupt_id")
                            or payload.get("interrupt_id")
                            or ""
                        ),
                        "gate": gate,
                    }
                elif gate != "idle":
                    hitl = {
                        "state": gate,
                        "tool": "",
                        "interrupt_id": "",
                        "gate": gate,
                    }
            except Exception:
                hitl = None

        # ── Gate registry (P2): the live-gates list rides along so the
        # client renders decision truth (one entry per gate — a second
        # pending question is naturally visible next to the claimed first).
        # ``gates_authoritative`` tells the client the registry answered:
        # only then may it treat the LIST (including an empty list) as
        # truth. A read failure must not look like "no live gates".
        gates: list[dict[str, Any]] = []
        gates_authoritative = False
        if thread_id:
            try:
                from kazma_ui.hitl_gate_bridge import registry_on

                if registry_on():
                    from kazma_core.safety.hitl_gates import live_gates_async

                    gates = [
                        {
                            "gate_id": g.gate_id,
                            "state": g.state,
                            "tool": g.tool,
                            "kind": g.kind,
                            "decision": g.decision,
                            "message": g.message,
                            "payload": g.payload(),
                        }
                        for g in await live_gates_async(thread_id)
                    ]
                    gates_authoritative = True
            except Exception:
                gates = []
                gates_authoritative = False

        # In-memory `_paused_threads` dies on restart. A durable pending
        # gate (or hitl_thread_status=pending) is still a live question.
        if not paused:
            if any(str(g.get("state") or "") == "pending" for g in gates):
                paused = True
            elif isinstance(hitl, dict) and str(hitl.get("gate") or "") == "pending":
                paused = True

        return {
            "session_id": session_id,
            "thread_id": thread_id,
            "generating": bool(is_running),
            "paused": bool(paused),
            "hitl": hitl,
            "gates": gates,
            "gates_authoritative": gates_authoritative,
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

                    _extras: dict[str, dict[str, Any]] = {}
                    for m in messages:
                        if not isinstance(m, dict) or (m.get("role") or "").lower() != "assistant":
                            continue
                        key = str(m.get("content") or "").strip()
                        extra = {
                            k: m[k]
                            for k in ("turn_id", "parts", "activity", "open", "pending", "model", "ts")
                            if m.get(k) is not None
                        }
                        if key and extra:
                            _extras[key] = extra
                    ui = []
                    for m in prior:
                        if not _ui_ok(m):
                            continue
                        item: dict[str, Any] = {
                            "role": m.get("role"),
                            "content": str(m.get("content") or "").strip(),
                        }
                        extra = _extras.get(item["content"])
                        if extra:
                            item.update(extra)
                        ui.append(item)
                    if ui:
                        # An in-flight turn's row lives only here — the
                        # checkpoint has no reply for it yet — and carries the
                        # turn_id its resume needs. Hydrating over it would
                        # drop the narration and split the answer in two.
                        _in_flight = [
                            m
                            for m in messages
                            if isinstance(m, dict)
                            and m.get("role") == "assistant"
                            and (m.get("open") or m.get("pending"))
                        ]
                        # The checkpointer is the source of truth for gw-*
                        # sessions. Replace if the checkpoint has more content
                        # (e.g. it has assistant replies the cached row lacks),
                        # or if the cache was empty.
                        if len(ui) >= len(messages):
                            session.messages = ui + _in_flight
                        else:
                            # Merge: add any checkpoint messages missing from
                            # cache. Keyed on FULL text — an 80-char prefix
                            # collapsed distinct replies sharing an opening.
                            existing_keys = {
                                (m.get("role"), str(m.get("content") or ""))
                                for m in messages if isinstance(m, dict)
                            }
                            for m in ui:
                                key = (m.get("role"), str(m.get("content") or ""))
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

        def _coalesce_assistant_runs(
            rows: list[dict[str, Any]],
        ) -> list[dict[str, Any]]:
            """Collapse related consecutive assistant snapshots to one row.

            A single user turn should project as one assistant row. If a
            delivery drift left a chain of consecutive assistant snapshots
            whose content is a growing prefix, return only the newest/richest
            snapshot so clients do not render a fake "many answers" ladder.
            Distinct assistant replies (no prefix relation) are preserved.
            """
            out: list[dict[str, Any]] = []
            for row in rows:
                role = str((row or {}).get("role") or "").lower()
                if role != "assistant":
                    out.append(row)
                    continue
                if not out or str((out[-1] or {}).get("role") or "").lower() != "assistant":
                    out.append(row)
                    continue

                prev = out[-1]
                p = str(prev.get("content") or "").strip()
                c = str(row.get("content") or "").strip()
                same_turn = bool(
                    prev.get("turn_id")
                    and row.get("turn_id")
                    and str(prev.get("turn_id")) == str(row.get("turn_id"))
                )
                related = (
                    same_turn
                    or not p
                    or not c
                    or c.startswith(p)
                    or p.startswith(c)
                )
                if not related:
                    out.append(row)
                    continue

                merged = dict(prev)
                merged.update(row)
                if not c and p:
                    merged["content"] = prev.get("content", "")
                prev_parts = prev.get("parts") if isinstance(prev.get("parts"), list) else []
                cur_parts = row.get("parts") if isinstance(row.get("parts"), list) else []
                if len(prev_parts) > len(cur_parts):
                    merged["parts"] = prev_parts
                prev_act = (
                    prev.get("activity") if isinstance(prev.get("activity"), list) else []
                )
                cur_act = (
                    row.get("activity") if isinstance(row.get("activity"), list) else []
                )
                if len(prev_act) > len(cur_act):
                    merged["activity"] = prev_act
                if not row.get("turn_id") and prev.get("turn_id"):
                    merged["turn_id"] = prev.get("turn_id")
                out[-1] = merged
            return out

        payload: list[dict[str, Any]] = []
        for msg in messages:
            if not _visible(msg):
                continue
            item: dict[str, Any] = {
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            }
            if msg.get("pending"):
                item["pending"] = True
            if msg.get("open"):
                item["open"] = True
            if msg.get("ts"):
                item["ts"] = msg["ts"]
            if msg.get("model"):
                item["model"] = msg["model"]
            if msg.get("turn_id"):
                item["turn_id"] = msg["turn_id"]
            parts = msg.get("parts") if isinstance(msg.get("parts"), list) else None
            if parts:
                item["parts"] = parts
            activity = msg.get("activity")
            if not (isinstance(activity, list) and activity) and parts:
                from kazma_ui.turn_document import activity_of

                activity = activity_of(parts)
            if isinstance(activity, list) and activity:
                item["activity"] = activity
            if (item.get("role") or "").lower() == "assistant":
                from kazma_ui.turn_document import hydrate_message

                item = hydrate_message(item)
            payload.append(item)
        payload = _coalesce_assistant_runs(payload)
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
                from kazma_core.security.ssrf import validate_url
                from kazma_core.url_utils import normalize_provider_url

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
          and resume. Returns JSON immediately (same contract as
          ``POST /api/approve``); tokens arrive on the journal attach. If the
          turn is finalizing and the pause isn't reached within ~12s, demotes
          to soft.

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

        # Resume into the journal — same JSON command as /api/approve.
        # A second graph SSE here is the dual-tail class (client never
        # consumed the steer body; cancelling it raced the shielded invoke).
        logger.info("[SSE] hard steer resuming thread=%s", thread_id[:12])
        from kazma_core.safety.commitment.resume import build_resume_command
        from kazma_ui.active_turns import register_turn
        from kazma_ui.reply_sink import resolve_reply_turn as _resolve_steer_turn
        from kazma_ui.sse_chat._streaming import (
            _drive_graph_to_journal,
            mark_thread_unpaused,
        )

        resume_input = build_resume_command(action="apply")
        _steer_turn = _resolve_steer_turn(thread_id, session_id)
        _steer_task = asyncio.create_task(
            _drive_graph_to_journal(
                graph_inst,
                resume_input,
                config,
                thread_id=thread_id,
                session_id=session_id,
                reply_turn_id=_steer_turn,
            )
        )
        register_turn(thread_id, _steer_task)
        mark_thread_unpaused(thread_id)
        return {
            "ok": True,
            "mode": "hard",
            "thread_id": thread_id,
            "turn_id": _steer_turn,
            "running": True,
        }

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
            try:
                from kazma_ui.hitl_gate_bridge import abort_thread_hitl

                await abort_thread_hitl(thread_id, session_id=session_id)
            except Exception:
                logger.debug("[SSE] abort HITL release skipped", exc_info=True)
            logger.info(
                "[SSE] Abort thread=%s cancelled=%s marker_written=True",
                thread_id[:12], cancelled,
            )
            return {"ok": True, "cancelled": cancelled}
        except Exception as exc:  # noqa: BLE001 — never fail the HTTP call
            logger.warning("[SSE] Abort marker write failed thread=%s: %s", thread_id[:12], exc)
            return {"ok": cancelled, "cancelled": cancelled, "warning": str(exc)}

    return r
