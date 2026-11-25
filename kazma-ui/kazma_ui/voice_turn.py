"""Live-voice turn submission — the same pump /api/chat/stream uses.

The voice WebSocket is a MOUTH, not a second brain (Turn Delivery V2 §31).
An utterance is transcribed, submitted to the checkpointed supervisor graph
through ``_drive_graph_to_journal`` (the one pump), and only the terminal
user-facing reply is spoken. The journal + gate registry stay the sources of
truth: this module never persists replies itself, never closes turns, and
never mints gate rows — those live in the pump and its callers.

Session ownership (IDOR guard): the voice socket resolves the client's web
``session_id`` through the tenant-scoped SessionManager. A session that does
not belong to this principal is invisible to the store, so lookup failure IS
the rejection. Gateway threads (``gw-*``) are refused — the browser must not
drive a platform thread through ``/ws/voice``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

__all__ = [
    "VoiceTurnResult",
    "resolve_voice_session",
    "run_voice_user_turn",
    "watch_voice_resume",
]


@dataclass
class VoiceTurnResult:
    """Outcome of one voice-driven turn (parsed from the journal stream)."""

    #: Terminal user-facing reply (``done``/``turn_complete`` frame content).
    text: str = ""
    #: True when the turn paused on a HITL gate (resume via /api/approve).
    interrupted: bool = False
    #: Sanitized failure reason — never spoken as an answer.
    error: str = ""


def resolve_voice_session(session_id: str) -> tuple[Any, str] | None:
    """Resolve an OWNED web session for the live-voice socket.

    Returns ``(session, thread_id)`` or None when the id is unusable:
    empty, a ``gw-*`` gateway thread (platform threads are not drivable
    from the browser), or unknown to this principal's tenant-scoped store.
    Never accepts an arbitrary thread_id as a graph key.
    """
    sid = str(session_id or "").strip()
    if not sid or sid.startswith("gw-"):
        return None
    from kazma_ui.session_manager import get_session_manager

    store = get_session_manager()
    session = store.get(sid)  # tenant-scoped: a foreign session is None
    if session is None:
        return None
    # The rule /ws/chat and the chat controls follow
    # (thread_ownership.resolve_caller_thread): a thread another tenant's
    # session is also bound to is nobody's to drive.
    try:
        exclusive = not session.thread_id or store.thread_is_exclusive(session.thread_id)
    except Exception:
        logger.warning("[voice] thread ownership check failed -- refusing", exc_info=True)
        return None
    if not exclusive:
        logger.warning("[voice] refused a chat whose thread is not the caller's alone")
        return None
    if not session.thread_id:
        # The rule the chat's stream follows (spaces.threads.new_web_thread_id):
        # the session id in the owner's space, a fresh id in any other.
        from kazma_core.spaces.threads import new_web_thread_id

        session.thread_id = new_web_thread_id(sid)
        try:
            store.put(session)
        except Exception:
            logger.exception("[voice] failed to persist session thread binding")
    return session, session.thread_id


def _parse_sse_frame(frame: str) -> tuple[str, dict[str, Any]] | None:
    """Split an SSE frame into (event_type, data) or None."""
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


async def run_voice_user_turn(
    *,
    graph: Any,
    session: Any,
    thread_id: str,
    session_id: str,
    user_text: str,
    system_prompt: str = "",
    cost_breaker: Any = None,
    on_progress: Callable[..., Any] | None = None,
) -> VoiceTurnResult:
    """Submit one transcribed utterance to the SAME pump the SSE route uses.

    Mirrors ``POST /api/chat/stream``: supersede any in-flight turn, cancel a
    superseded HITL pause, persist the user message, drive the checkpointed
    graph through ``_drive_graph_to_journal``, and read the outcome from the
    journal attach stream. Terminal persistence stays in the pump — this
    coroutine is a journal subscriber, not a second closer.
    """
    from kazma_core.agent.long_task import resolve_turn_budgets
    from kazma_ui.active_turns import (
        cancel_turn,
        get_active_turn,
        reap_stale_turn,
        register_turn,
        unregister_turn,
    )
    from kazma_ui.delivery import get_turn_broker
    from kazma_ui.reply_sink import close_reply_turn, open_reply_turn
    from kazma_ui.sse_chat._streaming import (
        _drive_graph_to_journal,
        _sse_attach_stream,
    )
    from kazma_ui.session_manager import get_session_manager

    result = VoiceTurnResult()
    if graph is None:
        result.error = "Agent graph unavailable"
        return result

    # Cost breaker: record the interaction FIRST (un-halt), then gate —
    # same ordering as the SSE route.
    if cost_breaker is not None:
        try:
            cost_breaker.record_user_interaction()
            if cost_breaker.should_halt():
                result.error = "Session budget exceeded. Please restart."
                return result
        except Exception:
            logger.debug("[voice] cost breaker check skipped", exc_info=True)

    # A card waiting on another member (Spaces S2-05 review): the utterance
    # would drop it. Refused, nothing saved, as the typed message is.
    from kazma_ui.sse_chat import _card_waits_on_someone_else, _turn_control, _waiting_text

    waiting_on = await _card_waits_on_someone_else(thread_id, graph)
    if waiting_on is not None:
        result.error = _waiting_text(user_text, waiting_on)
        return result
    # Another member's turn running: the utterance would cancel it (the
    # supersede below). Refused, nothing saved, as the typed message is
    # (Spaces S2-05 second review); the same person's utterance supersedes.
    answering = await _turn_control(thread_id, graph, "message", user_text)
    if answering is not None:
        result.error = answering["message"]
        return result

    try:
        from kazma_core.sessions.directory import stamp_last_platform

        await asyncio.to_thread(stamp_last_platform, thread_id, "web")
    except Exception:
        pass

    # A new utterance supersedes; it does not wait. Cancel + await the old
    # pump so two graphs never interleave on the same checkpointer.
    detached = get_active_turn(thread_id)
    if detached is not None and not detached.done():
        stale = reap_stale_turn(thread_id)
        if stale is not None and stale is detached:
            logger.info("[voice] reaping stale detached turn thread=%s", thread_id[:12])
            from kazma_core.bounded_cancel import cancel_and_wait

            await cancel_and_wait(stale, what=f"stale voice turn of {thread_id[:12]}")
    if get_active_turn(thread_id) is not None and not get_active_turn(thread_id).done():
        logger.info("[voice] superseding in-flight turn thread=%s", thread_id[:12])
        old = cancel_turn(thread_id)
        if old is not None:
            from kazma_core.bounded_cancel import cancel_and_wait

            await cancel_and_wait(old, what=f"superseded voice turn of {thread_id[:12]}", timeout=15.0)

    graph_config = {
        "configurable": {"thread_id": thread_id, "checkpoint_ns": ""},
        "recursion_limit": 100,
    }
    try:
        graph_config["recursion_limit"] = int(
            (await asyncio.to_thread(resolve_turn_budgets, thread_id))["recursion_limit"]  # audit 2026-10-26 AUD-111
        )
    except Exception:
        pass

    # A new turn supersedes a pending HITL pause: deny it so the tool chain
    # closes cleanly instead of swallowing the prompt (same as SSE).
    try:
        from kazma_core.agent.hitl_supersede import cancel_pending_hitl

        _hitl_now = "idle"
        try:
            from kazma_ui.hitl_status import hitl_thread_status

            _hitl_now = await hitl_thread_status(thread_id, graph=graph)
        except Exception:
            _hitl_now = "idle"
        cancelled = await cancel_pending_hitl(
            graph,
            graph_config,
            reason="superseded by new voice utterance",
            auto_deny=_hitl_now != "pending",
        )
        if cancelled:
            logger.info("[voice] cancelled pending HITL before new turn thread=%s", thread_id[:16])
    except Exception:
        logger.debug("[voice] HITL supersede cancel skipped", exc_info=True)

    # Persist the user line immediately (journal/sidebar truth), under the
    # session's transaction: never a put of the copy this socket has held
    # since it opened. A chat that was deleted (or moved to another space)
    # is not brought back: refused, nothing run.
    ts = datetime.now(UTC).isoformat()
    from kazma_ui.reply_sink import authored
    from kazma_ui.sse_chat import chat_gone_text
    from kazma_ui.sse_chat._persistence import _mutate_session

    user_row = authored({"role": "user", "content": user_text, "ts": ts})
    try:
        stored = await asyncio.to_thread(
            _mutate_session, session_id, lambda sess: sess.messages.append(user_row), session)
        if stored is None:
            result.error = chat_gone_text()
            return result
        session = stored
    except Exception:
        logger.exception("[voice] failed to persist user message for session=%s", session_id)

    system_msgs = await _async_system_messages(user_text, system_prompt, thread_id)

    try:
        from kazma_core.agent.turn_input import build_turn_messages

        messages = await build_turn_messages(
            graph,
            graph_config,
            user_text=user_text,
            system_messages=system_msgs,
            fallback_history=session.messages[:-1],
        )
    except Exception:
        logger.exception("[voice] build_turn_messages failed — falling back to bare user line")
        messages = [{"role": "user", "content": user_text}]

    from kazma_core.agent.state import initial_supervisor_state
    from kazma_core.memory.config import resolve_tenant_id

    # The initial state and the tenant rule read settings (audit 2026-10-26 AUD-111).
    input_state = await asyncio.to_thread(
        lambda: initial_supervisor_state(
            thread_id=thread_id,
            tenant_id=resolve_tenant_id("web", "", session_id),
        )
    )
    input_state["messages"] = messages
    try:
        from kazma_core.tools.send_message import web_gateway_block

        input_state["_gateway"] = await asyncio.to_thread(web_gateway_block, thread_id)
    except Exception:
        logger.debug("[voice] web gateway stamp skipped", exc_info=True)
    try:
        from kazma_core.agent.turn_input import build_turn_working_memory

        input_state.update(
            build_turn_working_memory(user_text, messages=messages, client_attachments=[])
        )
    except Exception:
        logger.debug("[voice] working-memory pin skipped", exc_info=True)

    reply_turn = open_reply_turn(thread_id)

    # CQRS, same as SSE: the graph runs in a registered background task that
    # journals only; this coroutine is a journal subscriber. Cancelling the
    # voice utterance (barge-in) cancels only the subscription — the turn
    # survives and persists, and the next utterance supersedes it above.
    journal_head = get_turn_broker().head_seq(thread_id)
    from kazma_ui.thread_locks import thread_mutation

    # Started under the thread's lock, as a typed turn is: a move of the
    # chat to another space (or its delete) either finished before, and the
    # turn does not run, or sees it running.
    drive = None
    async with thread_mutation(thread_id):
        if not get_session_manager().chat_deleted(session_id, thread_id):
            drive = asyncio.create_task(
                _drive_graph_to_journal(
                    graph,
                    input_state,
                    graph_config,
                    thread_id=thread_id,
                    session_id=session_id,
                    reply_turn_id=reply_turn,
                ),
                name=f"voice-turn:{thread_id[:12]}",
            )
            register_turn(thread_id, drive)
    if drive is None:
        await asyncio.to_thread(close_reply_turn, thread_id, turn_id=reply_turn)  # no turn runs for it
        result.error = chat_gone_text()
        return result

    def _on_drive_done(t: asyncio.Task, tid: str = thread_id) -> None:
        unregister_turn(tid, t)

    drive.add_done_callback(_on_drive_done)

    try:
        # The utterance's own turn, from before it started: live.
        async for frame in _sse_attach_stream(
            thread_id, session_id, journal_head, replay_is_history=False,
        ):
            parsed = _parse_sse_frame(frame)
            if parsed is None:
                continue
            ev_type, data = parsed
            if ev_type in ("tool_call", "tool_result"):
                if on_progress is not None:
                    try:
                        res = on_progress(ev_type, data)
                        if inspect.isawaitable(res):
                            await res
                    except Exception:
                        logger.debug("[voice] progress callback failed", exc_info=True)
            elif ev_type == "approval_required":
                result.interrupted = True
                break
            elif ev_type in ("done", "turn_complete"):
                result.text = str(data.get("content") or "")
                result.interrupted = bool(data.get("interrupted"))
                break
            elif ev_type == "error":
                result.error = str(data.get("content") or "Turn failed")
                break
            elif ev_type == "status_update" and str(data.get("status") or "") == "resync":
                result.error = "Journal resync needed — reload the page."
                break
    finally:
        # Never cancel the drive here: the pump owns terminal persistence
        # and may legitimately outlive this subscriber (barge-in, socket drop).
        pass
    return result


async def _async_system_messages(
    user_text: str, system_prompt: str, thread_id: str
) -> list[dict[str, str]]:
    """Awaitable per-turn system block, mirroring the SSE route's assembly."""
    system_msgs: list[dict[str, str]] = []
    if system_prompt:
        system_msgs.append({"role": "system", "content": system_prompt})
    # The self-improvement lessons are the supervisor's, fenced, once per
    # turn (S1-10 review).
    # The Knowledge Library block is the supervisor's, once per turn
    # (Knowledge audit KB-S08).
    # IDE workspace awareness — fresh every turn (workspace switches apply
    # immediately; §10C).
    try:
        from kazma_core.ide.env_context import build_env_context

        env_block = await build_env_context()
        if env_block:
            system_msgs.append({"role": "system", "content": env_block})
    except Exception:
        logger.debug("[voice] env_context refresh skipped", exc_info=True)
    # Language lock.
    try:
        from kazma_core.language_lock import language_lock_message

        lock = language_lock_message(user_text)
        if lock:
            system_msgs.append({"role": "system", "content": lock})
    except Exception:
        logger.debug("[voice] language lock skipped", exc_info=True)
    # Long-task budget consumption notice.
    try:
        from kazma_core.agent.long_task import consume_long_task_turn

        notice = await asyncio.to_thread(consume_long_task_turn, thread_id)  # audit 2026-10-26 AUD-111
        if notice:
            system_msgs.append({
                "role": "system",
                "content": notice + " Begin your reply with this notice verbatim.",
            })
    except Exception:
        logger.debug("[voice] long-task notice skipped", exc_info=True)
    return system_msgs


async def watch_voice_resume(
    *,
    thread_id: str,
    session_id: str,
    speak: Callable[[str], Awaitable[None]],
) -> None:
    """Wait out a HITL pause and speak the resumed turn's final reply.

    Attaches to the paused thread's journal; the approve request (existing
    ``POST /api/approve/{thread_id}``) drives the continuation through the
    same pump. Runs as a background task and is cancelled by the voice
    socket on close / interrupt / next utterance — a superseding turn must
    not be spoken twice.
    """
    from kazma_ui.delivery import get_turn_broker
    from kazma_ui.sse_chat._streaming import _sse_attach_stream

    try:
        head = 0
        try:
            head = int(get_turn_broker().resume(thread_id, 0)[2] or 0)
        except Exception:
            head = 0
        # Attached at the head: nothing to replay; what follows is live.
        async for frame in _sse_attach_stream(
            thread_id, session_id, head, replay_is_history=True,
        ):
            parsed = _parse_sse_frame(frame)
            if parsed is None:
                continue
            ev_type, data = parsed
            if ev_type == "approval_required":
                continue  # chained gate — keep waiting for the real terminal
            if ev_type in ("done", "turn_complete"):
                text = str(data.get("content") or "")
                if text.strip() and not text.lstrip().startswith("⚠️"):
                    await speak(text)
                return
            if ev_type == "error":
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("[voice] resume watcher ended", exc_info=True)
