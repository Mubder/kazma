"""TurnRunner + TurnCloser — one invoke, one session write.

Headless graph finishers (HITL watchdog auto-deny, approve resume, steer,
gateway HITL resume) used to call ``graph.ainvoke`` and never write the
SessionStore. The 2026-08-31 silent turn (checkpoint had the report,
chat still showed the HITL stub) is that class.

Production UI/gateway code must not call ``graph.ainvoke`` directly.
``invoke_turn`` is the invoke; ``close_turn`` always projects the
checkpoint into ``reply_sink`` afterwards (including when the client is
gone). Transports remain pipes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

__all__ = [
    "astream_events",
    "close_turn",
    "ensure_session_for_thread",
    "invoke_turn",
    "persist_reply",
    "resolve_session_id",
]

logger = logging.getLogger(__name__)


def _thread_id_of(config: dict[str, Any] | None, thread_id: str = "") -> str:
    if thread_id:
        return thread_id
    try:
        return str((config or {}).get("configurable", {}).get("thread_id") or "")
    except Exception:
        return ""


def resolve_session_id(thread_id: str, session_id: str = "") -> str:
    """Return *session_id* or the SessionStore row for *thread_id*."""
    if session_id:
        return session_id
    if not thread_id:
        return ""
    try:
        from kazma_ui.session_manager import get_session_manager

        sess = get_session_manager().get_by_thread_id(thread_id)
        return str(getattr(sess, "session_id", "") or "") if sess is not None else ""
    except Exception:
        logger.debug(
            "[turn_runtime] session lookup failed thread=%s",
            thread_id[:12],
            exc_info=True,
        )
        return ""


def ensure_session_for_thread(thread_id: str) -> str:
    """Mint or bind a web session for *thread_id*. Never raises.

    Used by HITL approve-resume when no sidebar row exists yet (Telegram
    pause, watchdog auto-deny, a thread the web UI has never opened).
    ``canonical_web_session`` prefers a named season; otherwise
    ``get_or_create`` mints one and we bind ``thread_id`` so later
    ``get_by_thread_id`` / ``persist_reply`` resolve.
    """
    if not thread_id:
        return ""
    try:
        from kazma_core.sessions.directory import canonical_web_session
        from kazma_ui.session_manager import get_session_manager

        store = get_session_manager()
        session = canonical_web_session(thread_id) or store.get_or_create(thread_id)
        if session is None:
            return ""
        if str(getattr(session, "thread_id", "") or "") != thread_id:
            session.thread_id = thread_id
            store.put(session)
        return str(getattr(session, "session_id", "") or "")
    except Exception:
        logger.debug(
            "[turn_runtime] session mint failed thread=%s",
            thread_id[:12],
            exc_info=True,
        )
        return ""


def _snapshot_paused(snap: Any) -> bool:
    try:
        if snap is None:
            return False
        for task in getattr(snap, "tasks", None) or []:
            if getattr(task, "interrupts", None):
                return True
        return bool(getattr(snap, "next", None))
    except Exception:
        return False


def persist_reply(
    session_id: str,
    reply_turn_id: str,
    content: str,
    *,
    interrupted: bool = False,
    pending: bool = False,
    open_turn: bool | None = None,
    thread_id: str = "",
    model: str = "",
    tokens: int | None = None,
    cost: float | None = None,
    activity: list[dict[str, Any]] | None = None,
    parts: list[dict[str, Any]] | None = None,
    streamed_text: str = "",
    allow_shrink: bool | None = None,
) -> bool:
    """Write this turn's reply through the sink. Never raises."""
    if not session_id or not reply_turn_id:
        return False
    if open_turn is None:
        open_turn = interrupted
    if allow_shrink is None:
        # Content-only writes must not clobber a longer hop. When *parts*
        # carries a text part, upsert derives content from that part.
        allow_shrink = False
    try:
        from kazma_ui.reply_sink import close_reply_turn, upsert_reply
        from kazma_ui.turn_document import activity_of, parts_from_stream

        if parts is None:
            parts = parts_from_stream(
                streamed=streamed_text,
                final=content,
                activity=activity,
            )
        derived_act = activity_of(parts) or activity

        ok = upsert_reply(
            session_id,
            reply_turn_id,
            content,
            open_turn=open_turn,
            pending=pending,
            activity=derived_act,
            parts=parts,
            model=model or None,
            tokens=tokens,
            cost=cost,
            allow_shrink=allow_shrink,
        )
        if not open_turn:
            if ok:
                close_reply_turn(thread_id)
            else:
                close_reply_turn(thread_id, session_id, reply_turn_id)
        logger.info(
            "[turn] Reply persisted thread=%s turn=%s chars=%d interrupted=%s",
            (thread_id or "")[:12],
            reply_turn_id[:12],
            len(str(content or "")),
            interrupted,
        )
        return ok
    except Exception:
        logger.warning(
            "[turn] Reply persist FAILED thread=%s turn=%s (the reply is still "
            "in the checkpoint; the session was NOT updated)",
            (thread_id or "")[:12],
            reply_turn_id[:12],
            exc_info=True,
        )
        try:
            from kazma_core.observability.ops_alerts import alert

            alert(
                "reply.persist_failed",
                "A reply was produced but NOT saved to the transcript.",
                f"session={str(session_id)[:12]} turn={str(reply_turn_id)[:12]} "
                f"chars={len(str(content or ''))}. The answer is still in the "
                f"checkpoint; a reload may show it missing.",
                severity="error",
            )
        except Exception:
            pass
        return False


async def close_turn(
    graph: Any = None,
    config: dict[str, Any] | None = None,
    *,
    session_id: str = "",
    thread_id: str = "",
    turn_id: str = "",
    streamed_text: str = "",
    activity: list[dict[str, Any]] | None = None,
    interrupted: bool | None = None,
) -> bool:
    """Project checkpoint + streamed text into SessionStore. Never raises.

    Called after every graph settlement (complete, HITL pause, auto-deny
    resume, disconnect). Same ``turn_id`` converges on one row.
    """
    try:
        thread_id = _thread_id_of(config, thread_id)
        session_id = resolve_session_id(thread_id, session_id)
        snap = None
        asst = ""
        if graph is not None and config is not None:
            try:
                snap = await graph.aget_state(config)
                if snap and getattr(snap, "values", None):
                    from kazma_ui.sse_chat._helpers import _last_assistant_text

                    asst = _last_assistant_text(snap.values.get("messages") or [])
            except Exception:
                logger.debug("[turn] close_turn aget_state failed", exc_info=True)
        paused = _snapshot_paused(snap)
        running = False
        claimed = False
        if thread_id:
            try:
                from kazma_ui.active_turns import is_turn_running

                running = is_turn_running(thread_id)
            except Exception:
                running = False
            try:
                from kazma_ui.hitl_status import is_resume_claimed

                claimed = is_resume_claimed(thread_id)
            except Exception:
                claimed = running
        leftover_claimed = False
        new_gate = False
        registry_answered = False
        # Gate registry (P6): decision truth for open/closed.
        # Pending row + paused ⇒ stay OPEN (silence rule).
        # Paused + no covering row ⇒ backfill from snapshot and stay OPEN
        # (unregistered pending gate — never "no question").
        # Pending row + not paused + not running ⇒ orphan-settle in seconds.
        if thread_id:
            try:
                from kazma_ui.hitl_gate_bridge import (
                    ensure_paused_gate,
                    registry_on,
                )

                if registry_on():
                    import asyncio as _aio

                    from kazma_core.safety.hitl_gates import (
                        live_gates,
                        settle_gate,
                    )

                    _rows = await _aio.to_thread(live_gates, thread_id)
                    _pending = [g for g in _rows if g.state == "pending"]
                    if _pending and paused:
                        new_gate = True
                    elif paused:
                        if await ensure_paused_gate(
                            thread_id,
                            snap,
                            session_id=session_id or "",
                            turn_id=turn_id or "",
                        ):
                            new_gate = True
                    elif _pending and not paused and not running:
                        from kazma_core.metrics import (
                            record_hitl_gate_reconciled,
                        )

                        for g in _pending:
                            try:
                                await _aio.to_thread(
                                    settle_gate, g.gate_id, "orphaned"
                                )
                                record_hitl_gate_reconciled("orphaned")
                            except Exception:
                                pass
                    registry_answered = True
            except Exception:
                logger.debug("[turn] gate registry check skipped", exc_info=True)
        if paused and thread_id and not new_gate and not registry_answered:
            # Thin execution fallback (kill-switch / registry outage): ask
            # the ONE status helper instead of re-deriving. A live
            # unanswered interrupt — including a NEW second gate raised
            # after Approve — classifies "pending" and must keep the turn
            # open, or the write→delete fake wrap-up returns in degraded
            # mode (2026-09-01 incident class).
            try:
                from kazma_ui.hitl_status import hitl_thread_status

                if await hitl_thread_status(thread_id, snapshot=snap) == "pending":
                    new_gate = True
            except Exception:
                logger.debug("[turn] thin fallback status skipped", exc_info=True)
        if paused and thread_id and not new_gate:
            leftover_claimed = bool(claimed or running)
        # A leftover checkpoint interrupt after Approve must not re-open
        # the row as pending. A live resume (running/claimed) is the same —
        # unless the pause is a NEW gate (see above), which always wins.
        stale_pause = (
            paused and not new_gate and (running or claimed or leftover_claimed)
        )
        if interrupted is None:
            interrupted = bool(paused) and not stale_pause
        else:
            interrupted = bool(interrupted) or (bool(paused) and not stale_pause)

        from kazma_ui.reply_sink import resolve_reply_text, resolve_reply_turn

        if not turn_id:
            turn_id = resolve_reply_turn(thread_id, session_id)
        # An interrupted/cancelled turn's checkpoint often still holds the
        # PREVIOUS assistant message. Streamed narration is this turn's row.
        if interrupted and str(streamed_text or "").strip():
            text = str(streamed_text).strip()
        else:
            text = resolve_reply_text(asst, streamed_text)
        if not text and not interrupted:
            text = (
                "⚠️ Your previous turn finished without producing a reply "
                "(the model may have failed silently). Please try again."
            )
        if not session_id:
            logger.warning(
                "[turn] close_turn has no session for thread=%s — checkpoint "
                "holds the reply; the chat transcript was NOT updated",
                (thread_id or "")[:12],
            )
            return False
        from kazma_ui.turn_document import parts_from_stream

        # The visible row is always ``text``. Distinct streamed notes that
        # lost the resolve (a short final hop) become ``reasoning``.
        parts = parts_from_stream(
            streamed=streamed_text,
            final=text,
            activity=activity,
        )
        return persist_reply(
            session_id,
            turn_id,
            text,
            interrupted=bool(interrupted),
            thread_id=thread_id,
            activity=activity,
            parts=parts,
            streamed_text=streamed_text,
        )
    except Exception:
        logger.warning(
            "[turn] close_turn FAILED thread=%s session=%s",
            (thread_id or "")[:12],
            (session_id or "")[:12],
            exc_info=True,
        )
        return False


async def invoke_turn(
    graph: Any,
    input_state: Any,
    config: dict[str, Any],
    *,
    session_id: str = "",
    thread_id: str = "",
    turn_id: str = "",
    streamed_text: str = "",
    activity: list[dict[str, Any]] | None = None,
    persist: bool = True,
    register: bool = True,
) -> Any:
    """``graph.ainvoke`` plus mandatory closer. The only UI/gateway invoke.

    ``persist=False`` is for ``/compact``, which rewrites history itself
    rather than producing a chat reply.
    """
    thread_id = _thread_id_of(config, thread_id)
    session_id = resolve_session_id(thread_id, session_id)
    if not turn_id and persist:
        try:
            from kazma_ui.reply_sink import resolve_reply_turn

            turn_id = resolve_reply_turn(thread_id, session_id)
        except Exception:
            turn_id = ""

    task = None
    if register and thread_id:
        try:
            from kazma_ui.active_turns import register_turn

            task = asyncio.current_task()
            register_turn(thread_id, task)
        except Exception:
            task = None

    try:
        return await graph.ainvoke(input_state, config)
    finally:
        try:
            if persist:
                await close_turn(
                    graph,
                    config,
                    session_id=session_id,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    streamed_text=streamed_text,
                    activity=activity,
                )
        except Exception:
            logger.warning(
                "[turn] invoke_turn closer failed thread=%s",
                (thread_id or "")[:12],
                exc_info=True,
            )
        if register and thread_id:
            try:
                from kazma_ui.active_turns import unregister_turn

                unregister_turn(thread_id, task)
            except Exception:
                pass


async def astream_events(
    graph: Any,
    input_state: Any,
    config: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Pass-through so live SSE still streams; invoke stays in this module.

    The live pump already persists via ``close_turn`` (detached callback).
    This wrapper exists so ``graph.astream_events`` is not a second
    untracked entry in UI code.
    """
    kwargs.setdefault("version", "v2")
    async for ev in graph.astream_events(input_state, config=config, **kwargs):
        yield ev
