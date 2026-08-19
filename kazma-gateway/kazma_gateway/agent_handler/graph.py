"""Graph submodule — contains create_graph_handler which bridges messages with LangGraph."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from kazma_gateway.gateway import Attachment, IncomingMessage, OutboundMessage, SessionStore
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
    apply_hitl_approval_markup,
)
from .commands import (
    _try_documents_command,
    _try_ide_command,
    _try_kb_command,
    _try_model_command,
    _try_research_command,
    _try_skill_command,
    _try_swarm_command,
    _build_slash_ctx,
)
from .session_commands import try_session_command

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


def _is_internal_prompt_inject(content: str) -> bool:
    """True for system injects that must never appear as chat bubbles."""
    c = (content or "").strip()
    if not c:
        return True
    # Fenced observation blocks (Soul / knowledge / memory)
    if "<kazma:data" in c and "untrusted" in c:
        return True
    if c.startswith("CONTINUITY:") or c.startswith("LATEST USER MESSAGE"):
        return True
    if c.startswith("MEMORY STORE TASK:") or c.startswith("MEMORY GRAPH CLEANUP"):
        return True
    if "[SelfImprovement]" in c and "BEGIN OBSERVATION" in c:
        return True
    return False


def _convert_messages_to_dicts(langgraph_messages) -> list[dict[str, Any]]:
    """Project graph messages to a Web UI transcript (user/assistant only).

    System injects (self-improvement Soul, knowledge, priority notes) stay in
    the checkpointer for the model but must not surface as "You" bubbles when
    a Telegram season is opened in the Web UI.
    """
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
            elif cls_name == "HumanMessage":
                role = "user"
            elif cls_name == "ToolMessage":
                role = "tool"
            else:
                role = "user"
            content = getattr(m, "content", "")

        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in content
            )
        text = str(content or "").strip()
        if not text:
            continue
        # Never put system/tool injects in the human-readable transcript
        if role in ("system", "tool"):
            continue
        if role not in ("user", "assistant"):
            continue
        if _is_internal_prompt_inject(text):
            continue
        dicts.append({"role": role, "content": text})
    return dicts


def _sync_platform_session_to_web(thread_id: str, platform: str, metadata: dict[str, Any], messages: list) -> None:
    """Synchronize platform session to Web UI for seamless season takeover.

    The web session_id **is** the gateway thread_id (``gw-telegram-…``), so
    opening that season in the chat sidebar continues the same LangGraph
    checkpointer used by Telegram/Discord/Slack.
    """
    try:
        from kazma_core.sessions.directory import canonical_web_session
        from kazma_ui.session_manager import get_session_manager
        store = get_session_manager()
        session = canonical_web_session(thread_id) or store.get_or_create(thread_id)
        session.thread_id = thread_id
        converted = _convert_messages_to_dicts(messages)
        # Prefer richer checkpoint-derived history when available; never wipe
        # a longer UI transcript with an empty convert.
        if converted and len(converted) >= len(session.messages):
            session.messages = converted
        elif converted and not session.messages:
            session.messages = converted
        elif converted:
            # Merge: keep UI rows, append new tail from platform if missing
            existing_keys = {
                (m.get("role"), (m.get("content") or "")[:80])
                for m in session.messages
            }
            for m in converted:
                key = (m.get("role"), (m.get("content") or "")[:80])
                if key not in existing_keys:
                    session.messages.append(m)
                    existing_keys.add(key)

        username = metadata.get("username") or metadata.get("display_name") or "user"
        plat = (platform or "chat").capitalize()
        if not session.title or session.title.startswith("Linked "):
            session.title = f"{plat} · {username}"
        store.put(session)
        try:
            from kazma_core.sessions.directory import stamp_last_platform

            stamp_last_platform(thread_id, platform)
        except Exception:
            pass
        logger.info(
            "[agent-handler] Synced platform season %s → web (platform=%s msgs=%d)",
            thread_id,
            platform,
            len(session.messages),
        )
    except Exception as exc:
        logger.debug("[agent-handler] Failed to sync session to Web UI: %s", exc)


def _clean_prior_messages(prior: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repair incomplete tool-call chains in the loaded checkpoint history.

    A checkpoint saved mid-interrupt (graph paused at HITL gate) contains an
    assistant message with ``tool_calls`` but no corresponding ``tool``
    messages.  Sending such a sequence to the LLM provider raises HTTP 400:
    ``"An assistant message with 'toolcalls' must be followed by tool messages"``.

    Dangling chains can also sit MID-history: before full sanitization, a 400
    error turn was committed on top of the broken chain, poisoning the thread
    permanently (tail-only cleaning never reached it). Prefer the core
    full-history sanitizer; fall back to tail-only cleaning if kazma_core is
    unavailable.
    """
    try:
        from kazma_core.agent.graph_builder import sanitize_tool_chains

        return sanitize_tool_chains(prior)
    except ImportError:
        pass

    # Fallback: walk backwards from the end and remove trailing assistant
    # tool-call messages that lack their tool responses.
    result = list(prior)
    while result:
        last = result[-1]
        role = last.get("role")
        # If last message is an assistant with tool_calls, check if all
        # tool_call_ids have matching tool responses in the NEXT message(s).
        if role == "assistant" and last.get("tool_calls"):
            tc_ids = {tc.get("id") for tc in last["tool_calls"]}
            # Check next messages (they would follow this assistant msg)
            # But since this is the LAST message, there ARE no next messages.
            # This means tool responses are missing — drop this message.
            result.pop()
            continue
        # If last message is a tool response, verify the preceding assistant
        # message's tool_calls are all accounted for.
        if role == "tool":
            tool_call_id = last.get("tool_call_id")
            # Walk backwards to find the preceding assistant tool_calls message
            # and verify all its tool_call_ids have responses.
            idx = len(result) - 2
            pending_ids: set[str] = set()
            while idx >= 0:
                m = result[idx]
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    pending_ids = {tc.get("id") for tc in m["tool_calls"]}
                    break
                idx -= 1
            if pending_ids:
                # Collect all tool_call_ids that have responses AFTER the assistant msg
                responded: set[str] = set()
                for m in result[idx + 1:]:
                    if m.get("role") == "tool":
                        responded.add(m.get("tool_call_id", ""))
                missing = pending_ids - responded
                if missing:
                    # Incomplete chain — truncate from the assistant message onward
                    result = result[:idx]
                    continue
            break  # Last message is a complete tool response
        break  # Last message is user or plain assistant — clean
    return result


# F7: cap for the Majlis fast-path. A greeting/farewell longer than this is
# assumed to carry a real request after the pleasantry and must reach the graph.
_MAJLIS_FAST_PATH_MAX_LEN = 60


async def _majlis_fast_path_reply(text: str, *, sender_id: str = "") -> str | None:
    """Return a canned cultural reply iff *text* is a SHORT pure greeting/farewell.

    Uses the live :class:`MajlisProtocol` orchestrator (per-sender phase
    machine). F7: only short-circuit when the message is ONLY a
    greeting/farewell. Fail-open: any error returns None.
    """
    if len((text or "").strip()) > _MAJLIS_FAST_PATH_MAX_LEN:
        return None
    try:
        from kazma_core.majlis_runtime import maybe_majlis_short_circuit

        return await maybe_majlis_short_circuit(text, sender_id=sender_id)
    except Exception:
        return None


def create_graph_handler(
    graph: Any = None,
    manager: Any = None,  # GatewayManager (avoid circular import)
    system_prompt: str = "",
    cost_breaker: Any = None,
    store: SessionStore | None = None,
    graph_getter: Callable[[], Any] | None = None,
) -> Callable[[IncomingMessage], Awaitable[None]]:
    """Create an async handler that processes messages through LangGraph.

    Args:
        graph:          Compiled LangGraph supervisor graph (snapshot; prefer
                        *graph_getter* so model switches rebind live).
        manager:        GatewayManager instance (for send() routing).
        system_prompt:  System prompt for the agent.
        cost_breaker:   Optional CostCircuitBreaker for budget control.
        store:          SessionStore for platform context persistence.
                        Falls back to in-memory store if not provided.
        graph_getter:   Optional callable returning the live compiled graph
                        (e.g. ``lambda: holder["graph"]``). When set, every
                        turn resolves the graph fresh so web UI model switches
                        apply to Telegram/Discord/Slack without re-registering
                        the handler.

    Returns:
        Async handler function compatible with manager.on_message().
    """
    # Use provided store or fall back to in-memory
    _store = store or _InMemoryStore()
    _fallback_graph = graph
    _graph_getter = graph_getter

    def _resolve_graph() -> Any:
        if _graph_getter is not None:
            try:
                g = _graph_getter()
                if g is not None:
                    return g
            except Exception as exc:
                logger.debug("[agent-handler] graph_getter failed: %s", exc)
        return _fallback_graph

    class _LiveGraph:
        """Proxy so nested helpers always hit the live holder graph."""

        def __getattr__(self, name: str) -> Any:
            g = _resolve_graph()
            if g is None:
                raise RuntimeError("Agent graph execution engine not initialized")
            return getattr(g, name)

        def __bool__(self) -> bool:
            return _resolve_graph() is not None

    # Shadow the snapshot with a live proxy — existing graph.ainvoke / aget_state
    # call sites keep working and follow model rebinds.
    graph = _LiveGraph()

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

        # Resolve thread_id using standardized resolver (synchronized).
        # ConfigStore / existing seasons win over the in-memory cache so a
        # /session take-over (or a restart) cannot mint a twin Telegram row.
        async with _sessions_lock:
            found = None
            try:
                from kazma_core.sessions.directory import find_mouth_thread

                found = find_mouth_thread(
                    sender,
                    platform=msg.platform,
                    username=str(
                        (msg.context_metadata or {}).get("username")
                        or (msg.context_metadata or {}).get("display_name")
                        or ""
                    ),
                )
            except Exception:
                logger.debug("[agent-handler] find_mouth_thread failed", exc_info=True)
            if found:
                _sessions[sender] = found
                _sessions.move_to_end(sender)
                thread_id = found
            elif sender in _sessions:
                _sessions.move_to_end(sender)
                thread_id = _sessions[sender]
            else:
                _sessions[sender] = _resolve_thread(msg)
                while len(_sessions) > _MAX_DICT_ENTRIES:
                    _sessions.popitem(last=False)
                thread_id = _sessions[sender]
            try:
                from kazma_core.sessions.directory import remember_sender_thread

                remember_sender_thread(sender, thread_id)
            except Exception:
                logger.debug("[agent-handler] remember_sender_thread failed", exc_info=True)

        # Inject the resolved thread_id into context_metadata
        # so _build_initial_state can pick it up
        msg.context_metadata["thread_id"] = thread_id

        # ── Typing keepalive (native "…typing" while the agent works) ──
        # Reactions (👀) are secondary; Telegram typing expires ~5s without refresh.
        typing_target = _build_target_id(msg.platform, msg.context_metadata)
        try:
            from kazma_gateway.typing_keepalive import get_typing_keepalive

            adapter = None
            for a in getattr(manager, "adapters", []) or []:
                if getattr(a, "name", None) == msg.platform:
                    adapter = a
                    break
            typing_fn = getattr(adapter, "_trigger_typing", None) if adapter else None
            if typing_fn is not None:
                await get_typing_keepalive().start(typing_target, typing_fn)
        except Exception:
            logger.debug("[agent-handler] typing keepalive start skipped", exc_info=True)

        # ── Bind delivery target for this turn ──────────────────────
        # Best-effort transport-layer bind so tools (schedule_task) can capture
        # the chat the reminder should return to. The authoritative bind lives
        # in the tool-worker node (graph_builder.py), which re-sets this from
        # the state's `_gateway` block — transport-layer ContextVars do not
        # reliably cross into LangGraph node execution (same caveat as the
        # thread_id bind above). Reuses the already-computed typing_target.
        _delivery_token = None
        try:
            from kazma_core.tools.send_message import set_current_delivery_target

            _delivery_token = set_current_delivery_target(typing_target)
        except Exception:
            logger.debug("[agent-handler] delivery-target bind skipped", exc_info=True)

        try:
            await _handler_body(msg, thread_id)
        finally:
            try:
                from kazma_gateway.typing_keepalive import get_typing_keepalive

                await get_typing_keepalive().stop(typing_target)
            except Exception:
                pass
            # Restore the delivery-target ContextVar (best-effort transport
            # bind; the authoritative bind lives in the tool-worker node).
            if _delivery_token is not None:
                try:
                    from kazma_core.tools.send_message import reset_current_delivery_target

                    reset_current_delivery_target(_delivery_token)
                except Exception:
                    pass

    async def _handler_body(msg: IncomingMessage, thread_id: str) -> None:
        """Inner handler body (typing keepalive wraps this)."""
        sender = msg.sender_id or "unknown"
        # Work slashes become graph turns (research / swarm dispatch / …).
        # Control slashes (help/list/status) stay on the intercepts below.
        try:
            from kazma_core.agent.slash_turns import rewrite_work_slash

            _rewritten = rewrite_work_slash(msg.text or "")
            if _rewritten:
                msg.text = _rewritten
        except Exception:
            logger.debug("[agent-handler] slash rewrite skipped", exc_info=True)
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

        # §17: pin working memory (attachments, constraints) so the intent
        # engine sees them at iteration 0. No platform IDs enter _wm.
        try:
            from kazma_core.agent.turn_input import build_turn_working_memory

            # Derive the turn text the same way as the history restore below:
            # last user message in state, falling back to the raw platform
            # text. (Deep-audit 2026-08-19: `user_text` was referenced here
            # before its first assignment, so the NameError was swallowed and
            # this pin silently never ran on the gateway path.)
            _wm_text = ""
            for _m in reversed(list(state.get("messages") or [])):
                if isinstance(_m, dict) and _m.get("role") == "user":
                    _wm_text = str(_m.get("content") or "")
                    break
            if not _wm_text:
                _wm_text = (msg.text or "").strip()
            _wm = build_turn_working_memory(
                _wm_text,
                messages=state.get("messages"),
                client_attachments=msg.attachments or [],
            )
            if _wm:
                state.update(_wm)
        except Exception as _wm_exc:
            logger.debug("[agent-handler] WM pin skipped: %s", _wm_exc)

        try:
            from kazma_core.agent.long_task import resolve_turn_budgets

            _budgets = resolve_turn_budgets(thread_id)
            _recursion_limit = int(_budgets["recursion_limit"])
        except Exception:
            _recursion_limit = 100
        config = {
            "configurable": {"thread_id": thread_id, "checkpoint_ns": ""},
            "recursion_limit": _recursion_limit,
        }

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
                    lock_getter=_get_thread_lock,
                )
                if hitl_handled:
                    return

        # ── Clear task grant on new user message ──────────────────
        # The user sent a real message (not an HITL approval callback,
        # which returned above). This is the natural task boundary —
        # the previous task is done, safety re-engages.
        try:
            from kazma_core.safety.task_grants import clear_task_grant

            if clear_task_grant(thread_id):
                logger.info("[agent-handler] Task grant cleared (new user message) thread=%s", thread_id)
        except Exception:
            pass

        # ── /sessions /session /switch /new — pick or mint a season ─
        # Same directory as the Web sidebar. Switching binds this mouth
        # to an existing thread_id and moves delivery here (take-over).
        async with _sessions_lock:
            session_handled = await try_session_command(
                msg,
                thread_id=thread_id,
                sender=sender,
                store=_store,
                manager=manager,
                sessions_map=_sessions,
                prepare_outbound=_prepare_tg_outbound,
            )
        if session_handled:
            return

        # ── Checkpoint-mutating commands serialize per thread ─────
        # /reset, /compact, /undo, /edit, /replay, /fork all write the
        # LangGraph checkpointer. The main turn below acquires the same
        # per-thread lock; without it, a command racing a running turn
        # (or another command) corrupts checkpoints/messages.
        cmd_lock = await _get_thread_lock(thread_id)

        # ── /reset: Clear conversation checkpoints and settings ───
        if msg.text and msg.text.strip().lower() == "/reset":
            async with cmd_lock:
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
            async with cmd_lock:
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

        # ── /yolo: Toggle session YOLO safety bypass (TTL + audit) ─
        if msg.text and msg.text.strip().lower() in (
            "/yolo", "/yolo on", "/yolo off", "/yolo status",
        ):
            from kazma_core.safety.yolo import disable_yolo, enable_yolo, yolo_status

            cmd = msg.text.strip().lower()
            actor = msg.sender_id or "gateway"
            if cmd == "/yolo status":
                st = yolo_status(thread_id)
                if st.get("active"):
                    rem = st.get("remaining_seconds")
                    ttl_note = f"Expires in ~{rem // 60}m." if rem is not None else "No auto-expiry."
                    reply_msg = f"🚀 YOLO is **ON**. {ttl_note}\nDisable: `/yolo off`"
                else:
                    reply_msg = "🛡️ YOLO is **OFF**. HITL is required for danger tools."
            elif cmd == "/yolo off":
                disable_yolo(thread_id, actor=actor)
                reply_msg = "🛡️ YOLO deactivated. Safety gates are active again."
            else:
                st = enable_yolo(thread_id, actor=actor)
                rem = st.get("remaining_seconds")
                ttl_note = (
                    f"Auto-expires in ~{rem // 60}m."
                    if rem is not None
                    else "No auto-expiry."
                )
                reply_msg = (
                    "🚀 **YOLO ON** for this chat only.\n"
                    "Danger tools run without approval until `/yolo off` or TTL ends.\n"
                    f"{ttl_note}\n"
                    "⚠️ Use only when you fully trust this session."
                )

            ctx = await _store.get(thread_id) or msg.context_metadata
            out_text, out_ctx = _prepare_tg_outbound(msg, reply_msg, ctx)
            await manager.send(OutboundMessage(
                target_id=_build_target_id(msg.platform, ctx),
                text=out_text,
                context_metadata=out_ctx,
            ))
            logger.info("[agent-handler] /yolo cmd=%s thread=%s", cmd, thread_id)
            return

        # ── /long + /mission + /unrestricted + /long yolo ───────────
        # Capacity (rounds) and YOLO (HITL) stay independent primitives;
        # apply_capacity_command is the one combiner (Web/SSE/gateway).
        if msg.text:
            from kazma_core.agent.capacity_commands import (
                apply_capacity_command,
                is_capacity_command,
            )

            if is_capacity_command(msg.text, require_slash=False):
                actor = msg.sender_id or "gateway"
                _cap = apply_capacity_command(
                    thread_id, msg.text, actor=actor, require_slash=False,
                )
                ctx = await _store.get(thread_id) or msg.context_metadata
                out_text, out_ctx = _prepare_tg_outbound(msg, _cap.reply, ctx)
                await manager.send(OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=out_text,
                    context_metadata=out_ctx,
                ))
                logger.info(
                    "[agent-handler] /long action=%s thread=%s yolo=%s",
                    _cap.action, thread_id, _cap.yolo_active,
                )
                return

        # ── /undo: Remove last assistant response ──────────────────
        if msg.text and msg.text.strip().lower() == "/undo":
            async with cmd_lock:
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
            async with cmd_lock:
                corrected_text, edit_result = await _handle_edit(thread_id, config, edit_match)
                ctx = await _store.get(thread_id) or msg.context_metadata
                edit_text, edit_ctx = _prepare_tg_outbound(msg, edit_result, ctx)
                await manager.send(OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=edit_text,
                    context_metadata=edit_ctx,
                ))
            return

        # ── /replay <n>: Restore from a snapshot (rewind in-place) ──
        replay_match = _extract_replay_command(msg.text)
        if replay_match is not None:
            async with cmd_lock:
                replay_result = await _handle_replay(thread_id, config, replay_match)
                ctx = await _store.get(thread_id) or msg.context_metadata
                replay_text, replay_ctx = _prepare_tg_outbound(msg, replay_result, ctx)
                await manager.send(OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=replay_text,
                    context_metadata=replay_ctx,
                ))
            return

        # ── /fork <n>: Branch from a snapshot into a new thread ────
        fork_match = _extract_fork_command(msg.text)
        if fork_match is not None:
            async with cmd_lock:
                fork_result = await _handle_fork(thread_id, config, fork_match, msg, _store, sender)
                ctx = await _store.get(thread_id) or msg.context_metadata
                fork_text, fork_ctx = _prepare_tg_outbound(msg, fork_result, ctx)
                await manager.send(OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=fork_text,
                    context_metadata=fork_ctx,
                ))
            return

        # ── /steer <text> (soft) / /steer! <text> (hard) / /abort ────
        # Out-of-band signals to a RUNNING turn. These intercept BEFORE the
        # real turn's thread_lock so they don't queue behind the in-flight
        # ainvoke. Soft push + abort cancel are lock-free; hard resume and
        # the abort marker take cmd_lock (free: the turn is paused/cancelled).
        _steer_kind: str | None = None
        _steer_text = ""
        if msg.text:
            _st = msg.text.strip()
            _low = _st.lower()
            if _low == "/steer" or _low.startswith("/steer "):
                _steer_kind = "soft"
                _steer_text = _st.split(maxsplit=1)[1].strip() if " " in _st else ""
            elif _low == "/steer!" or _low.startswith("/steer! "):
                _steer_kind = "hard"
                _steer_text = _st.split(maxsplit=1)[1].strip() if " " in _st else ""

        if _steer_kind is not None:
            from kazma_core.agent.steer import (
                clear_all_steers,
                is_hard_steer_interrupt,
                push_hard_steer,
                push_soft_steer,
            )

            ctx = await _store.get(thread_id) or msg.context_metadata

            if not _steer_text:
                _usage = (
                    "🧭 *Steer a running task*\n\n"
                    "• `/steer <extra context>` — add info; I fold it into the next step.\n"
                    "• `/steer! <requirement>` — pause the task, inject it, then resume.\n\n"
                    "Use either while a task is running."
                )
                _u_text, _u_ctx = _prepare_tg_outbound(msg, _usage, ctx)
                await manager.send(OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=_u_text, context_metadata=_u_ctx,
                ))
                return

            if _steer_kind == "soft":
                push_soft_steer(thread_id, _steer_text)
                logger.info("[agent-handler] /steer soft thread=%s", thread_id[:12])
                _ok, _ok_ctx = _prepare_tg_outbound(
                    msg, "🧭 Steer noted — I'll fold it into the next step.", ctx,
                )
                await manager.send(OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=_ok, context_metadata=_ok_ctx,
                ))
                return

            # ── hard steer: queue, wait for the interrupt, resume ──
            push_hard_steer(thread_id, _steer_text)
            _p_text, _p_ctx = _prepare_tg_outbound(
                msg, "⏸️ Pausing the task to apply your steer…", ctx,
            )
            await manager.send(OutboundMessage(
                target_id=_build_target_id(msg.platform, ctx),
                text=_p_text, context_metadata=_p_ctx,
            ))
            _paused = None
            _deadline = time.monotonic() + 12.0
            while time.monotonic() < _deadline:
                try:
                    _snap = await graph.aget_state(config)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[agent-handler] steer poll aget_state failed: %s", exc)
                    break
                _paused = is_hard_steer_interrupt(_snap)
                if _paused is not None or not getattr(_snap, "next", None):
                    break
                await asyncio.sleep(0.2)

            if _paused is None:
                # Turn never paused (finalizing) — demote to soft.
                clear_all_steers(thread_id)
                push_soft_steer(thread_id, _steer_text)
                logger.info("[agent-handler] /steer! demoted to soft thread=%s", thread_id[:12])
                _d_text, _d_ctx = _prepare_tg_outbound(
                    msg,
                    "⚠️ The task was finalizing, so I noted your steer for the "
                    "next step instead of pausing.",
                    ctx,
                )
                await manager.send(OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=_d_text, context_metadata=_d_ctx,
                ))
                return

            # Resume under cmd_lock (free: the paused turn released it).
            from kazma_core.safety.commitment.resume import build_resume_command

            try:
                async with cmd_lock:
                    _rs = await graph.ainvoke(
                        build_resume_command(action="apply"), config,
                    )
                _asst = ""
                for _m in reversed((_rs.get("messages") if isinstance(_rs, dict) else []) or []):
                    if isinstance(_m, dict) and _m.get("role") == "assistant" and _m.get("content"):
                        _asst = _m["content"]
                        break
                if _asst:
                    _a_text, _a_ctx = _prepare_tg_outbound(msg, _asst, ctx)
                    await manager.send(OutboundMessage(
                        target_id=_build_target_id(msg.platform, ctx),
                        text=_a_text, context_metadata=_a_ctx,
                    ))
                else:
                    _c_text, _c_ctx = _prepare_tg_outbound(
                        msg, "✅ Steer applied — continuing.", ctx,
                    )
                    await manager.send(OutboundMessage(
                        target_id=_build_target_id(msg.platform, ctx),
                        text=_c_text, context_metadata=_c_ctx,
                    ))
            except Exception:
                logger.exception("[agent-handler] /steer! resume failed thread=%s", thread_id[:12])
                _e_text, _e_ctx = _prepare_tg_outbound(
                    msg, "⚠️ Could not resume the task after steering.", ctx,
                )
                await manager.send(OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=_e_text, context_metadata=_e_ctx,
                ))
            return

        # ── /abort: cancel + abandon the running task ──────────────
        if msg.text and msg.text.strip().lower() == "/abort":
            from kazma_core.agent.steer import abort_marker, clear_all_steers

            ctx = await _store.get(thread_id) or msg.context_metadata
            clear_all_steers(thread_id)
            _ab_cancelled = False
            try:
                from kazma_ui.active_turns import cancel_turn as _gw_cancel

                _ab_cancelled = _gw_cancel(thread_id) is not None
            except Exception:
                pass
            try:
                async with cmd_lock:
                    _snap = await graph.aget_state(config)
                    _msgs = list(
                        (_snap.values if _snap and _snap.values else {}).get("messages") or []
                    )
                    _msgs.append({"role": "system", "content": abort_marker()})
                    await graph.aupdate_state(config, {
                        "messages": _msgs,
                        "task_status": "abandoned",
                        "auto_continue": False,
                    })
                logger.info(
                    "[agent-handler] /abort thread=%s cancelled=%s",
                    thread_id[:12], _ab_cancelled,
                )
                _ab_text, _ab_ctx = _prepare_tg_outbound(
                    msg,
                    "⛔ Task aborted — I won't continue it unless you ask me "
                    "to redo it.",
                    ctx,
                )
                await manager.send(OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=_ab_text, context_metadata=_ab_ctx,
                ))
            except Exception:
                logger.exception("[agent-handler] /abort marker failed thread=%s", thread_id[:12])
                _x_text, _x_ctx = _prepare_tg_outbound(
                    msg, "⚠️ Could not abort the task.", ctx,
                )
                await manager.send(OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=_x_text, context_metadata=_x_ctx,
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

        # ── Knowledge Library slash-command intercept ───────────────
        # /kb list|add|crawl|search|status|delete manages Knowledge
        # Libraries (ingested doc corpora used by the knowledge_search
        # tool). Skip the graph on any /kb command.
        kb_handled = await _try_kb_command(
            msg, _store, manager, thread_id,
        )
        if kb_handled:
            return  # /kb command handled, skip graph

        # ── Documents slash-command intercept ───────────────────────
        # /documents list|status|read|search|health surfaces the shared
        # DocumentIngestionService. Reads are opaque-ID based; no bytes are
        # parsed on the adapter and platform isolation is preserved.
        documents_handled = await _try_documents_command(
            msg, _store, manager, thread_id,
        )
        if documents_handled:
            return  # /documents command handled, skip graph

        research_handled = await _try_research_command(
            msg, _store, manager, thread_id,
        )
        if research_handled:
            return

        # ── Agent Skills slash-command intercept ──────────────────
        # /skill install|list|… installs SKILL.md skills without LLM thrash.
        skill_handled = await _try_skill_command(
            msg, _store, manager, thread_id,
        )
        if skill_handled:
            return

        # ── Majlis cultural fast-path ─────────────────────────────
        # Detect pure greetings/farewells before invoking the LLM.
        # Instant (< 50ms), zero token cost, culturally aware. F7: only
        # short pure greetings short-circuit (see _majlis_fast_path_reply).
        _majlis_reply = await _majlis_fast_path_reply(msg.text, sender_id=msg.sender_id)
        if _majlis_reply is not None:
            ctx = msg.context_metadata
            tg_text, tg_ctx = _prepare_tg_outbound(msg, _majlis_reply, ctx)
            await manager.send(
                OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=tg_text,
                    context_metadata=tg_ctx,
                )
            )
            logger.info("[agent-handler] Majlis fast-path (thread=%s)", thread_id)
            return  # skip LLM — instant cultural greeting/farewell

        # ── Serialize per thread_id ────────────────────────────────
        # Two concurrent messages for the same thread_id must NOT interleave
        # graph.ainvoke() calls, or LangGraph checkpoints and messages will
        # corrupt. Different thread_ids use different locks and stay parallel.
        thread_lock = await _get_thread_lock(thread_id)

        async with thread_lock:
            # ── Cancel stale HITL if user sent a new normal message ──
            # Without this, LangGraph discards the interrupt silently and
            # incomplete tool_calls get stripped — agent amnesia.
            if msg.text and not (msg.text or "").strip().lower().startswith(
                ("/hitl", "hitl ", "hitl\t")
            ):
                try:
                    from kazma_core.agent.hitl_supersede import cancel_pending_hitl

                    await cancel_pending_hitl(
                        graph,
                        config,
                        reason="superseded by new user message",
                    )
                except Exception:
                    logger.debug(
                        "[agent-handler] HITL supersede cancel skipped",
                        exc_info=True,
                    )

            # ── Restore conversation history ─────────────────────
            # SupervisorState has NO add_messages reducer — input replaces
            # checkpoint messages. Checkpointer is the sole agent transcript;
            # shared helper matches the Web SSE path.
            try:
                from kazma_core.agent.turn_input import build_turn_messages
                from kazma_core.agent.long_task import consume_long_task_turn

                # Consume long_task turn-budget at the start of each new message.
                consume_long_task_turn(thread_id)

                user_text = ""
                for m in reversed(list(state.get("messages") or [])):
                    if isinstance(m, dict) and m.get("role") == "user":
                        user_text = str(m.get("content") or "")
                        break
                if not user_text:
                    user_text = (msg.text or "").strip()
                rebuilt = await build_turn_messages(
                    graph,
                    config,
                    user_text=user_text,
                    system_messages=None,
                    fallback_history=None,
                )
                if rebuilt:
                    state = {**state, "messages": rebuilt}
            except Exception as _e:
                logger.debug("[agent-handler] history restore skipped: %s", _e)
                # Legacy fallback
                if graph is not None and getattr(graph, "checkpointer", None) is not None:
                    try:
                        snap = await graph.aget_state(config)
                        prior = list((snap.values or {}).get("messages") or []) if snap else []
                        if prior:
                            prior = _clean_prior_messages(prior)
                            state = {
                                **state,
                                "messages": prior + list(state.get("messages", [])),
                            }
                    except Exception:
                        pass

            # ── Active Agent Skill injection (/skill activate) ─────
            # If the user armed a skill via slash command, load its full
            # SKILL.md body into this turn so the agent follows it without
            # needing a separate tool call.
            try:
                sess = await _store.get(thread_id) or {}
                active_skill = sess.get("active_agent_skill")
                if active_skill:
                    from kazma_core.agent_skills.tools import activate_skill

                    skill_body = await activate_skill(name=str(active_skill))
                    if skill_body and not str(skill_body).startswith("Error:"):
                        msgs = list(state.get("messages") or [])
                        skill_sys = {
                            "role": "system",
                            "content": (
                                f"[ACTIVE AGENT SKILL: {active_skill}]\n"
                                f"{skill_body}\n"
                                f"[/ACTIVE AGENT SKILL]\n"
                                "Follow this skill's instructions for the "
                                "current user request. Prefer its workflow "
                                "over generic defaults."
                            ),
                        }
                        # Insert before the latest user message when possible
                        insert_at = len(msgs)
                        for i in range(len(msgs) - 1, -1, -1):
                            if isinstance(msgs[i], dict) and msgs[i].get("role") == "user":
                                insert_at = i
                                break
                        msgs.insert(insert_at, skill_sys)
                        state = {**state, "messages": msgs}
                        logger.info(
                            "[agent-handler] injected active skill=%s thread=%s",
                            active_skill,
                            thread_id,
                        )
            except Exception as _skill_exc:
                logger.debug(
                    "[agent-handler] active skill inject skipped: %s",
                    _skill_exc,
                )

            # ── Self-improvement Soul (Kazma-wide) ─────────────────
            # Deltas are wrapped in an untrusted data fence — treat as
            # observation context only, never instructions (prompt-injection
            # defense, audit C1).
            try:
                from kazma_core.safety.prompt_fence import format_untrusted_block
                from kazma_core.skills.self_improvement import get_agent_evolution_block

                evo = get_agent_evolution_block("supervisor")
                if evo:
                    msgs = list(state.get("messages") or [])
                    evo_sys = {
                        "role": "system",
                        "content": format_untrusted_block(evo, source="self_improvement"),
                    }
                    insert_at = 0
                    for i, m in enumerate(msgs):
                        if isinstance(m, dict) and m.get("role") == "user":
                            insert_at = i
                            break
                    msgs.insert(insert_at, evo_sys)
                    state = {**state, "messages": msgs}
            except Exception:
                logger.debug(
                    "[agent-handler] agent evolution inject skipped",
                    exc_info=True,
                )

            # ── Knowledge Library auto-inject (Phase 2) ────────────
            # For libraries with ``auto_inject=1``, fold the top-k chunks
            # relevant to this user message into the prompt — fenced as
            # untrusted data (doc content may be adversarial).  Kill switch
            # ``KAZMA_KB_AUTO_INJECT=0`` checked live inside the getter.
            try:
                from kazma_core.safety.prompt_fence import format_untrusted_block
                from kazma_core.stores.knowledge_index import (
                    get_knowledge_auto_inject_block,
                )

                kb_block = await get_knowledge_auto_inject_block(msg.text or "")
                if kb_block:
                    msgs = list(state.get("messages") or [])
                    kb_sys = {
                        "role": "system",
                        "content": format_untrusted_block(kb_block, source="knowledge"),
                    }
                    insert_at = 0
                    for i, m in enumerate(msgs):
                        if isinstance(m, dict) and m.get("role") == "user":
                            insert_at = i
                            break
                    msgs.insert(insert_at, kb_sys)
                    state = {**state, "messages": msgs}
            except Exception:
                logger.debug(
                    "[agent-handler] knowledge auto-inject skipped",
                    exc_info=True,
                )

            # Sync turn start to Web UI so the session appears immediately in sidebar
            _sync_platform_session_to_web(
                thread_id,
                msg.platform,
                msg.context_metadata,
                list(state.get("messages") or []),
            )

            # ── Invoke graph ───────────────────────────────────────
            # Long-task progress heartbeats: supervisor can send mid-turn
            # status via this sender (ContextVar).
            _lt_progress_token = None
            try:
                from kazma_core.agent.long_task import (
                    reset_progress_sender,
                    set_progress_sender,
                )

                async def _long_progress(text: str) -> None:
                    try:
                        pctx = await _store.get(thread_id) or msg.context_metadata
                        ptext, pctx2 = _prepare_tg_outbound(msg, text, pctx)
                        await manager.send(
                            OutboundMessage(
                                target_id=_build_target_id(msg.platform, pctx2),
                                text=ptext,
                                context_metadata=pctx2,
                            )
                        )
                    except Exception:
                        logger.debug(
                            "[agent-handler] long-task progress send failed",
                            exc_info=True,
                        )

                _lt_progress_token = set_progress_sender(_long_progress)
            except Exception:
                _lt_progress_token = None

            start = time.monotonic()
            # Register the running turn in the shared active_turns registry so
            # /abort can cancel it and is_turn_running() reflects gateway turns
            # (same registry the Web UI uses). Scoped to the ainvoke only.
            _gw_registered = False
            try:
                from kazma_ui.active_turns import register_turn as _gw_reg

                _gw_reg(thread_id, asyncio.current_task())
                _gw_registered = True
            except Exception:
                pass
            try:
                try:
                    from kazma_core.agent.turn import run_agent_turn

                    _turn = await run_agent_turn(
                        graph=graph,
                        thread_id=thread_id,
                        state=state,
                        config=config,
                    )
                    result_state = _turn.state or {}
                    if _turn.error and not _turn.interrupted and not result_state.get("messages"):
                        raise RuntimeError(_turn.error)
                finally:
                    if _gw_registered:
                        try:
                            from kazma_ui.active_turns import unregister_turn as _gw_unreg

                            _gw_unreg(thread_id)
                        except Exception:
                            pass
                duration_ms = (time.monotonic() - start) * 1000

                # ── HITL: detect interrupt() pause ──────────────────
                # When tool_worker_node calls interrupt() for a danger
                # tool, ainvoke returns a partial state and the graph is
                # paused at the checkpoint. Surface an approval prompt so
                # the user can resume via /hitl approve {thread_id}.
                hitl_payload = _turn.interrupt_payload if _turn.interrupted else None
                if hitl_payload is None:
                    hitl_payload = await _check_graph_interrupt(graph, config)
                if hitl_payload is not None:
                    ctx = await _store.get(thread_id)
                    if not ctx:
                        ctx = msg.context_metadata
                    prompt = _build_approval_prompt(
                        hitl_payload, thread_id, platform=msg.platform
                    )
                    # Interactive controls travel in context_metadata:
                    # Telegram → reply_markup, Discord → components, Slack → blocks.
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

                # Kazma-wide self-improvement (background)
                try:
                    from kazma_core.skills.self_improvement import (
                        schedule_chat_self_improvement,
                    )

                    empty = not (assistant_text or "").strip() or assistant_text.startswith(
                        "(No response"
                    )
                    schedule_chat_self_improvement(
                        user_message=(msg.text or "").strip() or "(gateway turn)",
                        success=not empty,
                        error="" if not empty else assistant_text[:400],
                        output_snippet=(assistant_text or "")[:600],
                    )
                except Exception:
                    logger.debug(
                        "[agent-handler] chat SI schedule skipped",
                        exc_info=True,
                    )

                _sync_platform_session_to_web(
                    thread_id,
                    msg.platform,
                    msg.context_metadata,
                    result_state.get("messages", []),
                )

                # ── Post-turn memory: fire AFTER graph is terminal ──
                # The memory consolidator (episode mirror, heuristic beliefs,
                # micro_consolidation enqueue) runs in a daemon thread. Moving
                # it here (from inside respond_node) prevents the CoT panel
                # from flickering back to "active" when the thread's SQLite
                # writes complete 5-10s after the graph signals "Done".
                _post_turn = result_state.get("_post_turn_memory")
                if _post_turn and isinstance(_post_turn, dict):
                    try:
                        from kazma_core.memory.consolidator import schedule_post_turn_memory

                        schedule_post_turn_memory(
                            result_state.get("messages", []),
                            session_id=_post_turn.get("session_id") or thread_id,
                            turn=_post_turn.get("turn", 0),
                            tenant_id=_post_turn.get("tenant_id", "default"),
                        )
                    except Exception:
                        logger.debug("[agent-handler] post-turn memory schedule failed", exc_info=True)

                # ── Restore platform IDs from SessionStore ─────────
                # The entry is intentionally NOT deleted here. It must persist
                # so crash-recovery routing can rehydrate the platform context
                # (chat_id, user_id) on the next inbound message. Stale
                # entries are evicted lazily by TTL below.
                ctx = dict(await _store.get(thread_id) or {})
                # Turn-scoped voice flag: only this inbound may request TTS.
                # Prefer live msg.metadata over a sticky store value.
                if msg.context_metadata.get("voice_transcribed"):
                    ctx["voice_transcribed"] = True
                else:
                    ctx.pop("voice_transcribed", None)

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

                # ── Re-sync with the FINAL assistant response ──────
                # The first sync (above, line 1136) captures the graph state
                # BEFORE tone adaptation. The assistant_text that was actually
                # sent to Telegram may differ (cultural tone wrapping). Append
                # it to the messages and re-sync so the Web UI sees the exact
                # same response the user received on Telegram.
                try:
                    _final_messages = list(result_state.get("messages", []))
                    # If the last message isn't the assistant_text we sent,
                    # append it so the Web UI shows the real response.
                    _last_is_assistant = (
                        _final_messages
                        and isinstance(_final_messages[-1], dict)
                        and _final_messages[-1].get("role") == "assistant"
                    )
                    if not _last_is_assistant or (
                        isinstance(_final_messages[-1], dict)
                        and assistant_text
                        and assistant_text not in str(_final_messages[-1].get("content", ""))
                    ):
                        _final_messages.append({
                            "role": "assistant",
                            "content": assistant_text,
                        })
                    _sync_platform_session_to_web(
                        thread_id,
                        msg.platform,
                        msg.context_metadata,
                        _final_messages,
                    )
                except Exception:
                    logger.debug("[agent-handler] post-send re-sync failed", exc_info=True)

            except Exception as inv_exc:
                logger.exception("[agent-handler] Graph invocation failed for %s", sender)
                # Use msg.context_metadata directly instead of re-accessing
                # the store (which may be the source of the original exception)
                ctx = msg.context_metadata
                err_msg = "⚠️ حدث خطأ أثناء معالجة رسالتك. (Processing error)"
                # LangGraph tool-loop cap — salvage partial work + guide recovery
                _exc_name = type(inv_exc).__name__
                _exc_s = str(inv_exc or "")
                if (
                    "GraphRecursionError" in _exc_name
                    or "Recursion limit" in _exc_s
                    or "recursion_limit" in _exc_s.lower()
                ):
                    partial = ""
                    try:
                        snap = await graph.aget_state(config)
                        vals = getattr(snap, "values", None) or {}
                        msgs = vals.get("messages") or []
                        for m in reversed(list(msgs)):
                            if not isinstance(m, dict):
                                continue
                            if m.get("role") not in ("assistant", "ai"):
                                continue
                            if m.get("tool_calls"):
                                continue
                            content = m.get("content") or ""
                            if isinstance(content, str) and content.strip():
                                partial = content.strip()
                                break
                        if not partial:
                            # Summarize last tool results for a usable footer
                            tool_bits: list[str] = []
                            for m in reversed(list(msgs)):
                                if not isinstance(m, dict) or m.get("role") != "tool":
                                    continue
                                tc = str(m.get("content") or "").strip()
                                if tc:
                                    tool_bits.append(tc[:400])
                                if len(tool_bits) >= 3:
                                    break
                            if tool_bits:
                                partial = (
                                    "(Partial tool findings before budget hit)\n\n"
                                    + "\n---\n".join(reversed(tool_bits))
                                )
                    except Exception:
                        logger.debug(
                            "[agent-handler] recursion salvage failed",
                            exc_info=True,
                        )
                    long_hint = ""
                    try:
                        from kazma_core.agent.long_task import (
                            is_long_task_active,
                            is_mission_mode,
                        )

                        if not is_long_task_active(thread_id):
                            long_hint = (
                                "\n\n💡 Tip: `/long mission` for a real long run "
                                "(~500 tool rounds hard wall), or `/long on` for a "
                                "soft Research budget — then continue with "
                                "*only remaining steps*."
                            )
                        elif not is_mission_mode(thread_id):
                            long_hint = (
                                "\n\n💡 Budget mode hit a soft wall. "
                                "Reply **Proceed**, or enable `/long mission` "
                                "for run-until-done (hard wall ~500 rounds)."
                            )
                    except Exception:
                        pass
                    if partial:
                        _was_long = False
                        try:
                            from kazma_core.agent.long_task import (
                                is_long_task_active,
                                pause_long_task,
                                record_budget_exhausted,
                                store_continue_context,
                            )

                            _was_long = is_long_task_active(thread_id)
                            store_continue_context(
                                thread_id, summary=partial, reason="recursion"
                            )
                            if _was_long:
                                # A Partial must NOT leave the long task
                                # silently active — subsequent user messages
                                # are fresh commands, not mission follow-ups
                                # (deep-audit 2026-08-19 Telegram desync).
                                pause_long_task(thread_id, reason="recursion")
                            record_budget_exhausted("recursion")
                        except Exception:
                            pass
                        err_msg = (
                            partial[:3500]
                            + "\n\n---\n"
                            "⚠️ Budget reached (graph recursion limit). "
                            "Above is salvaged progress — reply with *remaining steps only* "
                            "(or just **Proceed** — prior findings are remembered)."
                            + long_hint
                        )
                        if _was_long:
                            err_msg += (
                                "\n\n⏸️ Long task paused after this Partial — reply "
                                "**Proceed** to finish the remaining steps with the "
                                "salvaged context, or send a new task and it runs fresh."
                            )
                    else:
                        try:
                            from kazma_core.agent.long_task import record_budget_exhausted

                            record_budget_exhausted("recursion")
                        except Exception:
                            pass
                        err_msg = (
                            "⚠️ توقفت المهمة بعد حلقات أدوات كثيرة (حد التكرار).\n"
                            "⚠️ Turn stopped: tool loop hit the recursion limit "
                            "before finishing.\n"
                            "Reply with the *remaining* steps only so we continue "
                            "without re-doing graph cleanup."
                            + long_hint
                        )
                err_text, err_ctx = _prepare_tg_outbound(msg, err_msg, ctx)
                await manager.send(
                    OutboundMessage(
                        target_id=_build_target_id(msg.platform, ctx),
                        text=err_text,
                        context_metadata=err_ctx,
                    )
                )
            finally:
                if _lt_progress_token is not None:
                    try:
                        from kazma_core.agent.long_task import reset_progress_sender

                        reset_progress_sender(_lt_progress_token)
                    except Exception:
                        pass

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

    # ── /replay <n> + /fork <n>: Time-travel restore + branch ────────

    def _extract_replay_command(text: str | None) -> int | None:
        """Extract the iteration from ``/replay <n>``.

        Returns the int iteration, or None if the text isn't a numeric
        /replay command. Bare ``/replay`` (no arg) and ``/replay list``
        etc. return None (handled by the slash resolver).
        """
        if not text:
            return None
        parts = text.strip().split()
        if len(parts) != 2 or parts[0].lower() != "/replay":
            return None
        try:
            return int(parts[1])
        except (ValueError, TypeError):
            return None

    def _extract_fork_command(text: str | None) -> int | None:
        """Extract the iteration from ``/fork <n>``."""
        if not text:
            return None
        parts = text.strip().split()
        if parts[0].lower() != "/fork":
            return None
        if len(parts) < 2:
            return 0  # bare /fork → show usage
        try:
            return int(parts[1])
        except (ValueError, TypeError):
            return None

    async def _handle_replay(thread_id: str, config: dict[str, Any], iteration: int) -> str:
        """Restore a snapshot in-place: rewind the live thread to *iteration*."""
        try:
            from kazma_core.time_travel import create_recorder, ReplayEngine

            recorder = create_recorder()
            engine = ReplayEngine(recorder)
            state = engine.replay_from(thread_id, iteration)
            if state is None:
                return f"📭 No snapshot found for iteration `{iteration}`. Use `/replay list` to see available snapshots."

            # Write the snapshot state back to the live thread checkpoint.
            msg_count = len(state.get("messages", []))
            model = state.get("last_model", "unknown")
            await graph.aupdate_state(config, {"messages": state.get("messages", [])})
            logger.info(
                "[agent-handler] /replay restored thread=%s iter=%d msgs=%d",
                thread_id, iteration, msg_count,
            )
            return (
                f"🕰️ *Restored from iteration {iteration}.*\n\n"
                f"The conversation has been rewound to that point.\n"
                f"  Messages: {msg_count}\n"
                f"  Model: {model}\n\n"
                f"Your next message continues from here."
            )
        except Exception as exc:
            logger.warning("[agent-handler] /replay failed: %s", exc, exc_info=True)
            return f"⚠️ Could not replay iteration `{iteration}`: {exc}"

    async def _handle_fork(
        thread_id: str, config: dict[str, Any], iteration: int,
        msg: Any, _store: Any, sender: str,
    ) -> str:
        """Fork from a snapshot into a NEW thread (original stays intact)."""
        if iteration == 0:
            return "✏️ *Usage:* `/fork <iteration>` — branches from that snapshot into a new thread.\n\nUse `/replay list` to see available iterations."

        try:
            import uuid

            from kazma_core.time_travel import create_recorder, ReplayEngine

            recorder = create_recorder()
            engine = ReplayEngine(recorder)
            state = engine.replay_from(thread_id, iteration)
            if state is None:
                return f"📭 No snapshot found for iteration `{iteration}`. Use `/replay list` to see available snapshots."

            # Mint a new thread id (copy /new pattern).
            new_thread_id = f"gw-{msg.platform}-{sender.replace(':', '_')}-{uuid.uuid4().hex[:8]}"

            # Override thread identity in the state for the new branch.
            state["thread_id"] = new_thread_id
            gw = state.get("_gateway") or {}
            gw["thread_id"] = new_thread_id
            state["_gateway"] = gw

            # Seed the new thread with the snapshot state.
            new_config = {"configurable": {"thread_id": new_thread_id, "checkpoint_ns": ""}}
            await graph.aupdate_state(new_config, {"messages": state.get("messages", [])})
            logger.info("[agent-handler] /fork seeded new thread=%s from %s iter=%d", new_thread_id, thread_id, iteration)

            # Copy platform context so the fork can route replies. Override
            # the source thread_id (injected at handler entry) so consumers
            # reading ctx["thread_id"] on the fork's row get the fork id,
            # not the original thread (deep-audit 2026-08-19, finding #6).
            try:
                src_ctx = await _store.get(thread_id)
                if src_ctx:
                    fork_ctx = dict(src_ctx)
                    fork_ctx["thread_id"] = new_thread_id
                    await _store.put(new_thread_id, fork_ctx)
            except Exception:
                logger.debug("[agent-handler] /fork: could not copy session context", exc_info=True)

            # Create a Web UI session for the fork (visible in the sidebar).
            try:
                from kazma_ui.session_manager import get_session_manager, ChatSession

                web_store = get_session_manager()
                username = msg.context_metadata.get("username") or "fork"
                # Convert snapshot messages to plain dicts — the raw state can
                # contain LangGraph message objects the session store / sidebar
                # renderer don't understand.
                _fork_messages = _convert_messages_to_dicts(state.get("messages", []))
                web_session = ChatSession(
                    session_id=new_thread_id,
                    thread_id=new_thread_id,
                    title=f"Fork of {username} (iter {iteration})",
                    messages=_fork_messages,
                )
                web_store.put(web_session)
            except Exception:
                logger.debug("[agent-handler] /fork: could not create Web UI session", exc_info=True)

            msg_count = len(state.get("messages", []))
            return (
                f"🌿 *Forked from iteration {iteration} into a new thread.*\n\n"
                f"  New thread: `{new_thread_id}`\n"
                f"  Messages carried over: {msg_count}\n\n"
                f"The original thread is unchanged. The fork is available in the Web UI sidebar."
            )
        except Exception as exc:
            logger.warning("[agent-handler] /fork failed: %s", exc, exc_info=True)
            return f"⚠️ Could not fork from iteration `{iteration}`: {exc}"

    # ── Register telegram backend with core's send_message dispatcher ──
    try:
        from kazma_core.tools.send_message import register_message_backend

        async def _telegram_backend_handler(target_id: str, text: str, **kwargs: Any) -> str:
            # SessionStore is keyed by thread ids (gw-…), never by the
            # platform-prefixed target ids (telegram:…) this dispatcher
            # receives — the old unconditional _store.get(target_id) could
            # never hit. Only a genuine thread id (gw-…) resolves.
            ctx = None
            if str(target_id).startswith("gw-"):
                ctx = await _store.get(target_id)
            if not ctx:
                ctx = {"thread_id": target_id}
            # Always copy — HITL markup must not mutate a shared session ctx.
            out_ctx = dict(ctx)
            # target_id is prefixed "telegram:..." — convert markdown to HTML
            # so worker output renders instead of showing literal markers.
            if str(target_id).startswith("telegram:"):
                out_ctx["parse_mode"] = "HTML"
                out_text: str = md_to_tg_html(text)
            else:
                out_text = text
            out_ctx = apply_hitl_approval_markup(
                out_ctx,
                platform="telegram",
                hitl_approval=kwargs.get("hitl_approval")
                if isinstance(kwargs.get("hitl_approval"), dict)
                else None,
            )
            if kwargs.get("reply_markup"):
                out_ctx["reply_markup"] = kwargs["reply_markup"]
            # Build attachments from kwargs (file delivery via send_file_message).
            raw_attachments = kwargs.get("attachments")
            outbound_attachments: list[Attachment] = []
            if raw_attachments and isinstance(raw_attachments, list):
                for att in raw_attachments:
                    if isinstance(att, dict):
                        if att.get("data"):
                            outbound_attachments.append(Attachment(
                                kind=att.get("kind", "file"),
                                filename=att.get("filename", "file"),
                                mime=att.get("mime", "application/octet-stream"),
                                data=att["data"],
                            ))
                        elif att.get("path"):
                            from pathlib import Path
                            fpath = Path(att["path"]).expanduser().resolve()
                            if fpath.exists() and fpath.is_file():
                                outbound_attachments.append(Attachment(
                                    kind=att.get("kind", "document"),
                                    filename=fpath.name,
                                    mime=att.get("mime", "application/octet-stream"),
                                    data=fpath.read_bytes(),
                                ))

            # Auto-extract generated document file paths mentioned in response text
            import re
            from pathlib import Path
            file_matches = re.findall(r"(?:kazma-data/documents/|reports/|data/)[^\s\"'\(\)\[\]`]+\.(?:pdf|docx|html)", text)
            for file_path_str in file_matches:
                fpath = Path(file_path_str).expanduser().resolve()
                if fpath.exists() and fpath.is_file():
                    if not any(a.filename == fpath.name for a in outbound_attachments):
                        try:
                            outbound_attachments.append(Attachment(
                                kind="document",
                                filename=fpath.name,
                                mime="application/pdf" if fpath.suffix.lower() == ".pdf" else "application/octet-stream",
                                data=fpath.read_bytes(),
                            ))
                        except Exception as exc:
                            logger.warning("[telegram] auto-attach file failed: %s", exc)

            outbound = OutboundMessage(
                target_id=target_id, text=out_text, context_metadata=out_ctx,
                attachments=outbound_attachments,
            )
            await manager.send(outbound)
            return f"sent:{target_id}"

        register_message_backend("telegram", _telegram_backend_handler)
    except ImportError:
        logger.debug("[agent-handler] kazma_core not available — backend registration skipped")

    return handler
