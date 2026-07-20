"""Graph submodule — contains create_graph_handler which bridges messages with LangGraph."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from kazma_gateway.gateway import IncomingMessage, OutboundMessage, SessionStore
from kazma_gateway.telegram_format import md_to_tg_html
from .store import (
    _InMemoryStore,
    _resolve_thread,
    _build_target_id,
    _build_initial_state,
    _MAX_DICT_ENTRIES,
)
from .hitl import (
    _check_graph_interrupt,
    _build_approval_prompt,
    _handle_hitl_resume,
)
from .commands import (
    _try_ide_command,
    _try_model_command,
    _try_swarm_command,
    _build_slash_ctx,
)

logger = logging.getLogger(__name__)

__all__ = [
    "create_graph_handler",
]


def _prepare_tg_outbound(
    msg: IncomingMessage,
    text: str,
    ctx: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Prepare text + context for a Telegram-bound OutboundMessage.

    Converts Markdown to Telegram HTML and tags ``parse_mode`` so the adapter
    renders ``<b>/<i>/<code>`` instead of showing literal markers. Non-Telegram
    platforms get the text unchanged. The returned context is a shallow copy so
    the caller's ``ctx`` is not mutated.
    """
    if msg.platform != "telegram":
        return text, ctx
    out_ctx = dict(ctx)
    out_ctx["parse_mode"] = "HTML"
    return md_to_tg_html(text), out_ctx


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


def _sync_platform_session_to_web(thread_id: str, platform: str, metadata: dict[str, Any], messages: list) -> None:
    """Synchronize platform session messages to the Web UI's shared SessionManager."""
    try:
        from kazma_ui.session_manager import get_session_manager
        store = get_session_manager()
        session = store.get_or_create(thread_id)
        session.thread_id = thread_id
        session.messages = _convert_messages_to_dicts(messages)
        
        if not session.title:
            username = metadata.get("username") or metadata.get("display_name") or "user"
            session.title = f"Linked {platform.capitalize()} ({username})"
            
        store.put(session)
        logger.info("[agent-handler] Synchronized platform session %s to Web UI", thread_id)
    except Exception as exc:
        logger.debug("[agent-handler] Failed to sync session to Web UI: %s", exc)


def create_graph_handler(
    graph: Any,
    manager: Any,  # GatewayManager (avoid circular import)
    system_prompt: str = "",
    cost_breaker: Any = None,
    store: SessionStore | None = None,
) -> Callable[[IncomingMessage], Awaitable[None]]:
    """Create an async handler that processes messages through LangGraph.

    Args:
        graph:          Compiled LangGraph supervisor graph.
        manager:        GatewayManager instance (for send() routing).
        system_prompt:  System prompt for the agent.
        cost_breaker:   Optional CostCircuitBreaker for budget control.
        store:          SessionStore for platform context persistence.
                        Falls back to in-memory store if not provided.

    Returns:
        Async handler function compatible with manager.on_message().
    """
    # Use provided store or fall back to in-memory
    _store = store or _InMemoryStore()

    # Per-sender session tracking (sender_id → thread_id).
    # Guarded by _sessions_lock because concurrent handler invocations for
    # different senders read/write this shared dict.
    #
    # Bounded LRU: evicts the least-recently-used sender when the store
    # exceeds _MAX_DICT_ENTRIES (default 10 000).
    _sessions: OrderedDict[str, str] = OrderedDict()
    _sessions_lock = asyncio.Lock()

    # Per-thread_id serialization lock. Two concurrent messages for the same
    # thread_id must not interleave graph.ainvoke() / checkpoint writes, or
    # the LangGraph state and SQLite checkpoint will corrupt. Each distinct
    # thread_id gets its own asyncio.Lock so unrelated threads stay parallel.
    #
    # Bounded LRU: evicts the least-recently-used lock when the store
    # exceeds _MAX_DICT_ENTRIES (default 10 000).
    _thread_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
    _thread_locks_lock = asyncio.Lock()

    # Session TTL: entries survive agent replies (for crash-recovery routing)
    # and are evicted lazily by this many seconds of inactivity.
    _session_ttl_seconds = 300  # 5 minutes

    async def _get_thread_lock(thread_id: str) -> asyncio.Lock:
        """Return (creating if needed) the serialization lock for a thread_id.

        Uses LRU ordering: existing entries are moved to the end
        (most-recently-used) and the oldest entry is evicted when the
        bound is exceeded.
        """
        async with _thread_locks_lock:
            lock = _thread_locks.get(thread_id)
            if lock is not None:
                _thread_locks.move_to_end(thread_id)
                return lock
            lock = asyncio.Lock()
            _thread_locks[thread_id] = lock
            # Evict oldest entries, but skip any that are currently held
            while len(_thread_locks) > _MAX_DICT_ENTRIES:
                # Find the oldest non-held lock to evict
                evicted = False
                for key in list(_thread_locks.keys()):
                    if not _thread_locks[key].locked():
                        _thread_locks.pop(key)
                        evicted = True
                        break
                if not evicted:
                    # All locks are held — keep growing rather than
                    # breaking mutual exclusion
                    break
            return lock

    async def handler(msg: IncomingMessage) -> None:
        """Process a single IncomingMessage through the agent graph."""
        sender = msg.sender_id

        # Resolve thread_id using standardized resolver (synchronized)
        async with _sessions_lock:
            if sender in _sessions:
                # LRU: mark as most-recently-used.
                _sessions.move_to_end(sender)
                thread_id = _sessions[sender]
            else:
                _sessions[sender] = _resolve_thread(msg)
                while len(_sessions) > _MAX_DICT_ENTRIES:
                    _sessions.popitem(last=False)
                thread_id = _sessions[sender]

        # Inject the resolved thread_id into context_metadata
        # so _build_initial_state can pick it up
        msg.context_metadata["thread_id"] = thread_id

        # Cost breaker gate
        if cost_breaker and cost_breaker.should_halt():
            # Restore platform context for the reply
            ctx = await _store.get(thread_id)
            if not ctx:
                ctx = msg.context_metadata
            budget_text, budget_ctx = _prepare_tg_outbound(
                msg, "⚠️ ميزانية الجلسة انتهت. (Budget exceeded)", ctx
            )
            await manager.send(
                OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=budget_text,
                    context_metadata=budget_ctx,
                )
            )
            return

        if cost_breaker:
            cost_breaker.record_user_interaction()

        # ── Build platform-agnostic state ──────────────────────────
        state = await _build_initial_state(msg, _store)

        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

        # ── Interactive model selector (/models, /_models_provider, /_models_select) ──
        model_handled = await _try_model_command(msg, _store, manager, thread_id)
        if model_handled:
            return

        # ── HITL approval (hitl approve|deny <thread_id>) ──────────
        # Resumes a graph paused at interrupt(). Synthetic messages are
        # generated by the Telegram callback handler's hitl: vocabulary.
        # Leading "/" is optional — Slack blocks slash-commands so the
        # approval prompt uses "hitl" without the prefix.
        if msg.text:
            lower_text = msg.text.strip().lower()
            if lower_text.startswith("/hitl ") or lower_text.startswith("hitl "):
                hitl_handled = await _handle_hitl_resume(
                    msg, graph, config, thread_id, _store, manager,
                )
                if hitl_handled:
                    return

        # ── /new: Create a brand new session/season ───────────────
        if msg.text and msg.text.strip().lower() == "/new":
            import uuid
            new_thread_id = f"gw-{msg.platform}-{sender.replace(':', '_')}-{uuid.uuid4().hex[:8]}"
            
            # Persist mapping in ConfigStore
            try:
                from kazma_core.config_store import get_config_store
                cs = get_config_store()
                cs.set(f"active_thread.{sender}", new_thread_id)
            except Exception as exc:
                logger.error("[agent-handler] Failed to persist active thread mapping: %s", exc)
                
            # Update in-memory session cache
            async with _sessions_lock:
                _sessions[sender] = new_thread_id
                
            # Initialize empty linked session in Web UI's SessionManager
            try:
                from kazma_ui.session_manager import get_session_manager, ChatSession
                web_store = get_session_manager()
                username = msg.context_metadata.get("username") or msg.context_metadata.get("display_name") or "user"
                web_session = ChatSession(
                    session_id=new_thread_id,
                    thread_id=new_thread_id,
                    title=f"Linked {msg.platform.capitalize()} ({username})",
                    messages=[]
                )
                web_store.put(web_session)
            except Exception as exc:
                logger.debug("[agent-handler] Failed to create empty Web UI session: %s", exc)
                
            reply_msg = (
                f"🆕 Created a brand new season/session!\n\n"
                f"All your future messages here will be kept in a separate thread.\n"
                f"You can view or continue it in the Web UI as: **Linked {msg.platform.capitalize()} ({username})**"
            )
            ctx = msg.context_metadata
            out_text, out_ctx = _prepare_tg_outbound(msg, reply_msg, ctx)
            await manager.send(OutboundMessage(
                target_id=_build_target_id(msg.platform, ctx),
                text=out_text,
                context_metadata=out_ctx,
            ))
            logger.info("[agent-handler] /new for thread=%s (new_thread=%s)", thread_id, new_thread_id)
            return

        # ── /reset: Clear conversation checkpoints and settings ───
        if msg.text and msg.text.strip().lower() == "/reset":
            # 1. Delete checkpoints
            if hasattr(graph, "checkpointer") and graph.checkpointer:
                try:
                    await graph.checkpointer.adelete_thread(thread_id)
                except Exception as exc:
                    logger.error("[agent-handler] Failed to delete checkpoints on /reset: %s", exc)
            
            # 2. Delete ConfigStore active mapping
            try:
                from kazma_core.config_store import get_config_store
                cs = get_config_store()
                cs.delete(f"active_thread.{sender}")
            except Exception as exc:
                logger.debug("[agent-handler] ConfigStore delete active_thread failed: %s", exc)
                
            # 3. Clear in-memory session cache
            async with _sessions_lock:
                _sessions.pop(sender, None)
                
            # 4. Delete from Web UI SessionManager
            try:
                from kazma_ui.session_manager import get_session_manager
                web_store = get_session_manager()
                for sess in web_store.list_all(include_archived=True):
                    if sess.thread_id == thread_id or sess.session_id == thread_id:
                        sess.messages = []
                        sess.title = ""
                        web_store.put(sess)
                        break
            except Exception as exc:
                logger.debug("[agent-handler] Web UI session clear failed on /reset: %s", exc)
                
            # 5. Delete from platform session store
            try:
                await _store.delete(thread_id)
            except Exception as exc:
                logger.debug("[agent-handler] _store.delete failed: %s", exc)

            reply_msg = "🔄 Conversation cleared and reset to default. Starting fresh!"
            ctx = msg.context_metadata
            out_text, out_ctx = _prepare_tg_outbound(msg, reply_msg, ctx)
            await manager.send(OutboundMessage(
                target_id=_build_target_id(msg.platform, ctx),
                text=out_text,
                context_metadata=out_ctx,
            ))
            logger.info("[agent-handler] /reset completed for thread=%s", thread_id)
            return

        # ── /compact: Force manually triggered context compaction ─
        if msg.text and msg.text.strip().lower() == "/compact":
            try:
                state_obj = await graph.aget_state(config)
                if state_obj and state_obj.values:
                    current_values = dict(state_obj.values)
                    current_values["needs_compaction"] = True
                    await graph.ainvoke(current_values, config)
                    reply_msg = "🗜️ Context compaction completed successfully! Your conversation history has been summarized and compressed."
                else:
                    reply_msg = "🗜️ No conversation history found to compact yet."
            except Exception as exc:
                logger.error("[agent-handler] /compact failed for thread=%s: %s", thread_id, exc)
                reply_msg = "⚠️ Failed to compact context. (Compaction error)"

            ctx = await _store.get(thread_id) or msg.context_metadata
            out_text, out_ctx = _prepare_tg_outbound(msg, reply_msg, ctx)
            await manager.send(OutboundMessage(
                target_id=_build_target_id(msg.platform, ctx),
                text=out_text,
                context_metadata=out_ctx,
            ))
            logger.info("[agent-handler] /compact completed for thread=%s", thread_id)
            return

        # ── /yolo: Toggle session YOLO safety bypass ───────────────
        if msg.text and msg.text.strip().lower() in ("/yolo", "/yolo on", "/yolo off"):
            from kazma_core.config_store import get_config_store

            cs = get_config_store()
            is_off = msg.text.strip().lower() == "/yolo off"
            if is_off:
                cs.delete(f"yolo.{thread_id}")
                reply_msg = "🛡️ Mode YOLO deactivated. Safety gates are active again."
            else:
                cs.set(f"yolo.{thread_id}", True)
                reply_msg = "🚀 Mode YOLO activated! All tools in this session will execute automatically without requesting your approval. Run free!"

            ctx = await _store.get(thread_id) or msg.context_metadata
            out_text, out_ctx = _prepare_tg_outbound(msg, reply_msg, ctx)
            await manager.send(OutboundMessage(
                target_id=_build_target_id(msg.platform, ctx),
                text=out_text,
                context_metadata=out_ctx,
            ))
            logger.info("[agent-handler] /yolo for thread=%s is_off=%s", thread_id, is_off)
            return

        # ── /undo: Remove last assistant response ──────────────────
        if msg.text and msg.text.strip().lower() == "/undo":
            undo_result = await _handle_undo(thread_id, config)
            ctx = await _store.get(thread_id) or msg.context_metadata
            undo_text, undo_ctx = _prepare_tg_outbound(msg, undo_result, ctx)
            await manager.send(OutboundMessage(
                target_id=_build_target_id(msg.platform, ctx),
                text=undo_text,
                context_metadata=undo_ctx,
            ))
            return

        # ── /edit: Correct last assistant response ─────────────────
        # None = not an /edit command; "" = bare /edit (show usage)
        edit_match = _extract_edit_command(msg.text)
        if edit_match is not None:
            corrected_text, edit_result = await _handle_edit(thread_id, config, edit_match)
            ctx = await _store.get(thread_id) or msg.context_metadata
            edit_text, edit_ctx = _prepare_tg_outbound(msg, edit_result, ctx)
            await manager.send(OutboundMessage(
                target_id=_build_target_id(msg.platform, ctx),
                text=edit_text,
                context_metadata=edit_ctx,
            ))
            return

        # ── Slash-command intercept (/model, /help, /reset, etc.) ──
        # Resolve common commands without an LLM call. This keeps
        # responses instant and saves tokens.
        try:
            from kazma_gateway.slash_commands import is_slash_command, resolve_slash_command

            if is_slash_command(msg.text):
                # Build context for the command resolver with real data
                slash_ctx = await _build_slash_ctx(thread_id, msg, state, _store)

                reply = resolve_slash_command(msg.text, context=slash_ctx)
                if reply is not None:
                    # Command was recognised — send the response and skip graph
                    ctx = await _store.get(thread_id)
                    if not ctx:
                        ctx = msg.context_metadata
                    await manager.send(
                        OutboundMessage(
                            target_id=_build_target_id(msg.platform, ctx),
                            text=reply,
                            context_metadata=ctx,
                        )
                    )
                    logger.info(
                        "[agent-handler] Slash command resolved (cmd=%s, thread=%s)",
                        msg.text.strip().split()[0] if msg.text else "?",
                        thread_id,
                    )
                    return
        except ImportError:
            pass  # slash_commands module not available

        # ── Swarm slash-command intercept ──────────────────────────
        # If the message starts with /swarm, dispatch to the swarm engine
        # instead of the single-agent graph.
        swarm_handled = await _try_swarm_command(
            msg, state, _store, manager, thread_id,
        )
        if swarm_handled:
            return  # swarm dispatched, skip graph

        # ── IDE slash-command intercept ─────────────────────────────
        # /ide ... drives the transport-neutral IdeService. Placed after the
        # swarm intercept so /swarm keeps precedence; both skip the graph.
        ide_handled = await _try_ide_command(
            msg, _store, manager, thread_id,
        )
        if ide_handled:
            return  # IDE command handled, skip graph

        # ── Majlis cultural fast-path ─────────────────────────────
        # Detect pure greetings/farewells before invoking the LLM.
        # Instant (< 50ms), zero token cost, culturally aware.
        try:
            from kazma_core.pacing import detect_intent, Intent, get_greeting_response
            from kazma_core.cultural_context import CulturalContext

            intent = detect_intent(msg.text)
            if intent in (Intent.GREETING, Intent.FAREWELL):
                cc = CulturalContext()
                if intent == Intent.GREETING:
                    greeting = get_greeting_response(
                        dialect="kw",
                        is_ramadan=cc.state.is_ramadan,
                        is_eid=cc.state.is_eid,
                    )
                    reply_text = greeting
                else:
                    # Farewell
                    reply_text = "في أمان الله 👋"

                ctx = msg.context_metadata
                tg_text, tg_ctx = _prepare_tg_outbound(msg, reply_text, ctx)
                await manager.send(
                    OutboundMessage(
                        target_id=_build_target_id(msg.platform, ctx),
                        text=tg_text,
                        context_metadata=tg_ctx,
                    )
                )
                logger.info("[agent-handler] Majlis fast-path: %s (thread=%s)", intent.name, thread_id)
                return  # skip LLM — instant cultural greeting/farewell
        except Exception as exc:
            logger.debug("[agent-handler] Majlis fast-path unavailable: %s", exc)

        # ── Serialize per thread_id ────────────────────────────────
        # Two concurrent messages for the same thread_id must NOT interleave
        # graph.ainvoke() calls, or LangGraph checkpoints and messages will
        # corrupt. Different thread_ids use different locks and stay parallel.
        thread_lock = await _get_thread_lock(thread_id)

        async with thread_lock:
            # ── Invoke graph ───────────────────────────────────────
            start = time.monotonic()
            try:
                result_state = await graph.ainvoke(state, config)
                duration_ms = (time.monotonic() - start) * 1000

                # ── HITL: detect interrupt() pause ──────────────────
                # When tool_worker_node calls interrupt() for a danger
                # tool, ainvoke returns a partial state and the graph is
                # paused at the checkpoint. Surface an approval prompt so
                # the user can resume via /hitl approve {thread_id}.
                hitl_payload = await _check_graph_interrupt(graph, config)
                if hitl_payload is not None:
                    ctx = await _store.get(thread_id)
                    if not ctx:
                        ctx = msg.context_metadata
                    prompt = _build_approval_prompt(hitl_payload, thread_id)
                    # reply_markup travels inside context_metadata — the
                    # Telegram adapter reads it from there.
                    prompt_text, send_ctx = _prepare_tg_outbound(msg, prompt["text"], ctx)
                    if prompt.get("markup"):
                        send_ctx["reply_markup"] = prompt["markup"]
                    await manager.send(
                        OutboundMessage(
                            target_id=_build_target_id(msg.platform, ctx),
                            text=prompt_text,
                            context_metadata=send_ctx,
                        )
                    )
                    logger.info(
                        "[agent-handler] HITL interrupt surfaced: thread=%s tool=%s",
                        thread_id, hitl_payload.get("tool"),
                    )
                    return  # graph paused; resume on /hitl response

                messages = result_state.get("messages", [])
                assistant_text = ""
                for m in reversed(messages):
                    if not isinstance(m, dict) or m.get("role") != "assistant":
                        continue
                    content = m.get("content")
                    if content and str(content).strip():
                        assistant_text = str(content).strip()
                        break

                if not assistant_text:
                    # Last resort: check if there's an assistant message
                    # with tool_calls but empty content — the LLM routed
                    # through tools but never produced a final text answer.
                    # Give a helpful fallback instead of "(No response generated)".
                    has_tool_msgs = any(
                        isinstance(m, dict)
                        and m.get("role") == "assistant"
                        and m.get("tool_calls")
                        for m in messages
                    )
                    if has_tool_msgs:
                        assistant_text = (
                            "I looked into that but couldn't formulate a clear response. "
                            "Could you rephrase your question?"
                        )
                    else:
                        assistant_text = "(No response generated)"

                # ── Majlis tone adaptation ──────────────────────────
                # Wrap the LLM's response with cultural tone based on
                # current cultural context (Ramadan warm, Eid celebratory,
                # formal business, general polite).
                try:
                    from kazma_core.tone_adapter import ToneAdapter
                    from kazma_core.cultural_context import CulturalContext

                    _cc = CulturalContext()
                    _ta = ToneAdapter()
                    _profile = _ta.select_profile(
                        formality=_ta.determine_formality_from_text(msg.text),
                        dialect="kw",
                        is_ramadan=_cc.state.is_ramadan,
                        is_eid=_cc.state.is_eid,
                        is_national_day=_cc.state.is_national_day,
                    )
                    assistant_text = _ta.adapt_response(assistant_text, profile=_profile, dialect="kw")
                except Exception as exc:
                    logger.debug("[agent-handler] Tone adaptation skipped: %s", exc)

                logger.info(
                    "[agent-handler] Graph completed in %.0fms (thread=%s, platform=%s)",
                    duration_ms,
                    thread_id,
                    msg.platform,
                )

                _sync_platform_session_to_web(
                    thread_id,
                    msg.platform,
                    msg.context_metadata,
                    result_state.get("messages", []),
                )

                # ── Restore platform IDs from SessionStore ─────────
                # The entry is intentionally NOT deleted here. It must persist
                # so crash-recovery routing can rehydrate the platform context
                # (chat_id, user_id) on the next inbound message. Stale
                # entries are evicted lazily by TTL below.
                ctx = await _store.get(thread_id)

                # Convert Markdown → Telegram HTML so bold/code/etc. render
                # instead of showing literal ** markers (which legacy Markdown
                # parse_mode would 400-reject and strip to plain text).
                tg_text, tg_ctx = _prepare_tg_outbound(msg, assistant_text, ctx)
                await manager.send(
                    OutboundMessage(
                        target_id=_build_target_id(msg.platform, ctx),
                        text=tg_text,
                        context_metadata=tg_ctx,
                    )
                )

            except Exception:
                logger.exception("[agent-handler] Graph invocation failed for %s", sender)
                # Use msg.context_metadata directly instead of re-accessing
                # the store (which may be the source of the original exception)
                ctx = msg.context_metadata
                err_text, err_ctx = _prepare_tg_outbound(
                    msg, "⚠️ حدث خطأ أثناء معالجة رسالتك. (Processing error)", ctx
                )
                await manager.send(
                    OutboundMessage(
                        target_id=_build_target_id(msg.platform, ctx),
                        text=err_text,
                        context_metadata=err_ctx,
                    )
                )

        # ── Lazy TTL eviction ──────────────────────────────────────
        # Opportunistically prune sessions that have been inactive longer than
        # the TTL. This bounds the store size over time without deleting live
        # entries that crash recovery still needs.
        try:
            await _store.evict_older_than(_session_ttl_seconds)
        except Exception:
            logger.debug("[agent-handler] TTL eviction skipped (store may not support it)", exc_info=True)

    # ── /undo and /edit helpers (LangGraph aget_state / aupdate_state) ──

    async def _handle_undo(thread_id: str, config: dict[str, Any]) -> str:
        """Remove the last assistant turn (and trailing tool msgs) from checkpoint."""
        try:
            snap = await graph.aget_state(config)
            if snap is None or not getattr(snap, "values", None):
                return "↩️ No conversation history to undo."

            messages = list(snap.values.get("messages") or [])
            if not messages:
                return "↩️ No messages in conversation."

            # Drop trailing assistant message; also drop tool results that
            # immediately precede it (same turn) so the graph is consistent.
            removed = False
            i = len(messages) - 1
            while i >= 0:
                role = messages[i].get("role") if isinstance(messages[i], dict) else None
                if role == "assistant":
                    messages.pop(i)
                    removed = True
                    # strip tool messages belonging to this turn (before assistant)
                    j = i - 1
                    while j >= 0:
                        r = messages[j].get("role") if isinstance(messages[j], dict) else None
                        if r == "tool":
                            messages.pop(j)
                            j -= 1
                            continue
                        # also drop the assistant tool_calls message that
                        # triggered tools (role=assistant with tool_calls)
                        if r == "assistant" and messages[j].get("tool_calls"):
                            messages.pop(j)
                        break
                    break
                i -= 1

            if not removed:
                return "↩️ No assistant response to undo."

            await graph.aupdate_state(config, {"messages": messages})
            logger.info("[agent-handler] /undo thread=%s msgs_left=%d", thread_id, len(messages))
            return "✅ Removed last assistant response. You can continue the conversation."
        except Exception as exc:
            logger.warning("[agent-handler] /undo failed: %s", exc, exc_info=True)
            return f"⚠️ Could not undo: {exc}"

    def _extract_edit_command(text: str | None) -> str | None:
        """Extract corrected text from ``/edit …``, or empty string if bare ``/edit``."""
        if not text:
            return None
        stripped = text.strip()
        if not stripped.lower().startswith("/edit"):
            return None
        # Bare "/edit" → empty string (show usage); "/edit foo" → "foo"
        parts = stripped.split(maxsplit=1)
        if len(parts) < 2:
            return ""
        return parts[1]

    async def _handle_edit(
        thread_id: str, config: dict[str, Any], corrected_text: str
    ) -> tuple[str, str]:
        """Replace the last assistant response with *corrected_text*.

        Returns ``(corrected_text, status_message)``.
        """
        if not (corrected_text or "").strip():
            return (
                corrected_text,
                "✏️ *Usage:* `/edit <corrected text>`\n\n"
                "Replaces the last assistant message in conversation history.",
            )
        try:
            snap = await graph.aget_state(config)
            if snap is None or not getattr(snap, "values", None):
                return corrected_text, "✏️ No conversation history to edit."

            messages = list(snap.values.get("messages") or [])
            if not messages:
                return corrected_text, "✏️ No messages in conversation."

            for i in range(len(messages) - 1, -1, -1):
                msg_i = messages[i]
                if isinstance(msg_i, dict) and msg_i.get("role") == "assistant":
                    # Keep tool_calls-only intermediate assistants out of "last reply"
                    if msg_i.get("tool_calls") and not (msg_i.get("content") or "").strip():
                        continue
                    messages[i] = {**msg_i, "role": "assistant", "content": corrected_text}
                    # Drop tool_calls if we are replacing with plain text
                    messages[i].pop("tool_calls", None)
                    await graph.aupdate_state(config, {"messages": messages})
                    logger.info(
                        "[agent-handler] /edit thread=%s len=%d",
                        thread_id, len(corrected_text),
                    )
                    return (
                        corrected_text,
                        "✅ Replaced last response. You can continue the conversation.",
                    )

            messages.append({"role": "assistant", "content": corrected_text})
            await graph.aupdate_state(config, {"messages": messages})
            return corrected_text, "✅ Added corrected text as new message."
        except Exception as exc:
            logger.warning("[agent-handler] /edit failed: %s", exc, exc_info=True)
            return corrected_text, f"⚠️ Could not edit: {exc}"

    # ── Register telegram backend with core's send_message dispatcher ──
    try:
        from kazma_core.tools.send_message import register_message_backend

        async def _telegram_backend_handler(target_id: str, text: str) -> str:
            ctx = await _store.get(target_id)
            if not ctx:
                ctx = {"thread_id": target_id}
            # target_id is prefixed "telegram:..." — convert markdown to HTML
            # so worker output renders instead of showing literal markers.
            if str(target_id).startswith("telegram:"):
                out_ctx = dict(ctx)
                out_ctx["parse_mode"] = "HTML"
                out_text: str = md_to_tg_html(text)
            else:
                out_ctx, out_text = ctx, text
            outbound = OutboundMessage(
                target_id=target_id, text=out_text, context_metadata=out_ctx
            )
            await manager.send(outbound)
            return f"sent:{target_id}"

        register_message_backend("telegram", _telegram_backend_handler)
    except ImportError:
        logger.debug("[agent-handler] kazma_core not available — backend registration skipped")

    return handler
