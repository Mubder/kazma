"""HITL approval-timeout watchdog.

The graph-level HITL gate (``tool_worker_node`` → ``interrupt()``) parks the
graph until someone resumes it via ``POST /api/approve/{thread_id}`` (or the
WS ``approve_tool`` message). Unlike the swarm bus gate
(``swarm/safety.py``, 60s), the graph gate previously had **no timeout** —
a missed/dismissed approval deadlocked the turn forever (audit §2.3).

This watchdog closes that gap: it periodically scans for threads paused on a
``hitl_approval`` interrupt, tracks when each was first seen, and — once
``safety.hitl.approval_timeout_seconds`` elapses — auto-resumes the graph
with ``{"approved": False, "reason": "approval timeout"}`` when
``safety.hitl.auto_deny_on_timeout`` is enabled (the default, fail-closed).
When auto-deny is disabled the watchdog only logs, preserving the manual
"wait forever" behaviour for operators who want it.

Design notes:
  * Age is tracked in-memory (first-seen monotonic). On process restart the
    timer restarts for still-pending approvals — a pending approval never
    survives *two* watchdog windows unnoticed, which is acceptable and keeps
    the implementation free of checkpoint-schema coupling.
  * Config is re-read live via ``get_hitl_config()`` so Settings changes take
    effect without a restart (mirrors get_proxy_provider).
  * The watchdog never raises; a scan failure is logged and retried next tick.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from kazma_core.shutdown import is_shutting_down

__all__ = ["register_chat_app_resumer", "start_hitl_timeout_watchdog", "stop_hitl_timeout_watchdog"]

logger = logging.getLogger(__name__)

_SCAN_INTERVAL_SECONDS = 15.0

_watchdog_task: asyncio.Task | None = None
# thread_id → monotonic timestamp when the pending approval was first seen
_first_seen: dict[str, float] = {}
# Strong refs so auto-deny graph tasks are not GC'd mid-resume.
_deny_tasks: set[asyncio.Task] = set()

# The chat apps' resume (kazma_gateway.agent_handler.hitl.make_timeout_resumer),
# registered by the gateway handler: platforms it serves, and the coroutine.
_chat_app_resumer: tuple[frozenset[str], Callable[..., Any]] | None = None


def register_chat_app_resumer(platforms: frozenset[str], resumer: Callable[..., Any]) -> None:
    """Let the gateway finish an expired approval of a chat app's thread.

    The watchdog's own resume drives the turn into the web journal, which
    only a browser reads: live 2026-10-18 a Telegram turn's next approval
    card and its answer never reached the chat after a timeout.
    """
    global _chat_app_resumer
    _chat_app_resumer = (frozenset(platforms), resumer)


async def _chat_app_route(graph: Any, config: dict[str, Any]) -> tuple[str, str] | None:
    """(platform, target id) when the paused turn belongs to a chat app.

    Read from the turn's own routing block in the checkpoint, never the
    session store (its rows expire in five minutes; the card outlives them).
    """
    if _chat_app_resumer is None:
        return None
    # The checkpoint was read a moment ago for the interrupt; a failure here
    # is the caller's (its handler logs it and resumes nothing).
    snapshot = await graph.aget_state(config)
    routing = (getattr(snapshot, "values", None) or {}).get("_gateway") or {}
    platform = str(routing.get("platform") or "").strip().lower()
    target = str(routing.get("delivery_target") or "").strip()
    if platform not in _chat_app_resumer[0]:
        return None
    if not target.startswith(f"{platform}:") or target.endswith(":unknown"):
        logger.warning(
            "[HITL-WD] %s thread has no delivery address; its timeout resumes in the web journal only",
            platform,
        )
        return None
    return platform, target


async def _auto_deny(graph: Any, thread_id: str, timeout_s: float) -> None:
    """Resume an expired interrupt with a deny decision."""

    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    # Phase 3: check if the pending interrupt is semantic (clarify/confirm) —
    # those need {tcid: "cancel"}, not {approved: false}.
    from kazma_core.safety.commitment.resume import build_resume_command, pending_interrupt

    _intr_payload, _intr_id = await pending_interrupt(graph, config)
    _resume_cmd = build_resume_command(
        _intr_payload, approved=False, scope="once",
        reason=(
            f"HITL approval timed out after {int(timeout_s)}s. The operator "
            "did not answer and is probably away from the device. "
            "This is NOT a refusal of this approach. "
            "Retrying the same action with different arguments, a different "
            "tool, or a different path only sends another approval request "
            "to someone who is not there. "
            "Stop now, do not attempt a workaround, and reply saying what "
            "you were about to do and what you need approved."
        ),
        extra={"timed_out": True} if not (_intr_payload and _intr_payload.get("kind") in ("semantic_clarify", "semantic_confirm")) else None,
        # The interrupt that expired, and no other (TT-02).
        interrupt_id=_intr_id,
    )
    if _resume_cmd is None:
        # Nothing pending (already resolved) — nothing to auto-deny.
        return
    logger.warning(
        "[HITL-WD] Approval for thread=%s expired after %.0fs — auto-denying",
        thread_id,
        timeout_s,
    )
    try:
        # Don't auto-deny if a manual resume (SSE/WS approve) is already in
        # flight for this thread — the watchdog's ainvoke(deny) would race
        # the just-clicked approve and could clobber it. The SSE/WS paths
        # register their resume via register_turn/is_turn_running (audit).
        from kazma_ui.hitl_status import is_resume_claimed

        if is_resume_claimed(thread_id):
            logger.info(
                "[HITL-WD] thread=%s has a turn in flight — skipping auto-deny "
                "(manual resume racing)", thread_id,
            )
            return
    except Exception:
        pass
    _iid = str((_intr_payload or {}).get("interrupt_id") or "")
    # What the timeout acts on is read BEFORE anything is recorded (Spaces
    # S2-05): whose card it is, from the live interrupt, and the chat app it
    # goes back to. A read that fails leaves the card pending and claimed by
    # nobody, and the next pass tries again; recorded first, a failure here
    # left the gate claimed and the turn stuck.
    recorded = False
    try:
        whose, departed = await _whose_and_departed(thread_id, _intr_id or _iid, _intr_payload)
        route = None if departed else await _chat_app_route(graph, config)
        if departed:
            recorded = True
            await _close_turn_of_departed(graph, config, thread_id, whose, _intr_payload, _intr_id or _iid)
            return
        from kazma_ui.active_turns import register_turn
        from kazma_ui.reply_sink import resolve_reply_turn
        from kazma_ui.sse_chat._streaming import (
            _drive_graph_to_journal,
            mark_thread_unpaused,
        )
        from kazma_ui.turn_runtime import ensure_session_for_thread, resolve_session_id

        # Chat-store reads and a write: off the loop (Postgres when the
        # session is not cached). resolve_session_id never raises.
        session_id = await asyncio.to_thread(resolve_session_id, thread_id)
        if not session_id:
            session_id = await asyncio.to_thread(ensure_session_for_thread, thread_id)
        turn_id = await asyncio.to_thread(resolve_reply_turn, thread_id, session_id)

        # The decision, written down everywhere it is read -- the registry
        # (the row stayed `pending` forever after an auto-deny until
        # 2026-09-01) AND the transcript part, which stayed `pending` until
        # 2026-09-26: the turn reloaded with a dead Approve row and its
        # answer missing (turn 9bdd89fd93cb).
        from kazma_core.safety.hitl_gates import TIMEOUT_ACTOR

        from kazma_ui.hitl_decision import record_gate_decision

        # The id the card is keyed by: the checkpoint payload has none, and a
        # timeout recorded without it became a second, stray row.
        recorded = True
        _iid = await record_gate_decision(
            thread_id,
            decision="timeout",
            actor=TIMEOUT_ACTOR,
            tool=str((_intr_payload or {}).get("tool") or ""),
            payload=dict(_intr_payload or {}),
            interrupt_id=_iid,
            session_id=session_id,
            turn_id=turn_id,
            require_durable=True,
        )

        try:
            from kazma_ui.delivery import get_turn_broker

            await get_turn_broker().emit(thread_id, {
                "type": "approval_timeout",
                "data": {
                    "thread_id": thread_id,
                    "interrupt_id": _iid,
                    "tool": str((_intr_payload or {}).get("tool") or ""),
                    "turn_id": turn_id,
                    "message": (
                        "Approval timed out after "
                        f"{int(timeout_s)}s — continuing without this tool."
                    ),
                },
            })
        except Exception:
            logger.debug("[HITL-WD] timeout frame skipped", exc_info=True)

        # A chat app's turn goes back to its chat (its answer and its next
        # card); any other is resumed into the journal -- not awaited on the
        # watchdog tick. invoke_turn still runs inside the streamer so
        # SessionStore gets the continuation (2026-08-31 silent turn
        # b1cb7994e22a). The route was read before the timeout was recorded.
        if route is not None and _chat_app_resumer is not None:
            task = asyncio.create_task(
                _chat_app_resumer[1](
                    thread_id=thread_id,
                    resume_cmd=_resume_cmd,
                    resume_config=config,
                    platform=route[0],
                    target_id=route[1],
                    tool=str((_intr_payload or {}).get("tool") or ""),
                    timeout_s=timeout_s,
                )
            )
        else:
            # The turn goes on as its asker (Spaces S2-05): the card's own,
            # read from the live interrupt before anything was recorded, not
            # the chat's opener whom a resume with nothing bound would run as.
            # Nothing is bound on an install that does not enforce Spaces.
            from kazma_ui.hitl_answerers import as_the_asker

            with as_the_asker(whose):
                task = asyncio.create_task(
                    _drive_graph_to_journal(
                        graph,
                        _resume_cmd,
                        config,
                        thread_id=thread_id,
                        session_id=session_id,
                        reply_turn_id=turn_id,
                    )
                )
        _deny_tasks.add(task)
        task.add_done_callback(_deny_tasks.discard)
        register_turn(thread_id, task)
        mark_thread_unpaused(thread_id)
    except Exception:
        if not recorded:
            # Nothing recorded yet: the card stays pending, claimed by
            # nobody, and the next pass tries again.
            logger.warning("[HITL-WD] thread=%s: the expired card could not be read; tried again next pass",
                           str(thread_id)[:12], exc_info=True)
        else:
            logger.exception("[HITL-WD] auto-deny resume failed for thread=%s", thread_id)


async def _whose_and_departed(thread_id: str, interrupt_id: str,
                              payload: dict[str, Any] | None) -> tuple[Any, bool]:
    """Whose the expired card is (``hitl_answerers.whose_card``, from the
    live interrupt's id and payload), and whether its asker has left the
    card's space since (``identity.asker_left``). ``(None, False)`` while the
    install does not enforce Spaces. Raises when either cannot be read."""
    from kazma_core.spaces.scope import spaces_enforced

    if not spaces_enforced():
        return None, False

    def _read() -> tuple[Any, bool]:
        from kazma_core.spaces.identity import asker_left
        from kazma_ui.hitl_answerers import whose_card

        whose = whose_card(thread_id, interrupt_id, dict(payload or {}))
        return whose, asker_left(whose.space_id, whose.asker)

    return await asyncio.to_thread(_read)


#: The line a turn ends with when the card it waited on belongs to someone
#: no longer in its space (Spaces S2-05): it is not resumed as anyone.
_DEPARTED_EN = ("⚠️ This turn was stopped: {name} is no longer a member of this space, "
                "so the turn they started does not go on.")
_DEPARTED_AR = "⚠️ توقف هذا الدور: {name} لم يعد عضواً في هذه المساحة، فلا يستمر الدور الذي بدأه."


def _departed_text(asker: str, values: dict[str, Any]) -> str:
    """The departed line in the language of the turn's question. Blocking
    (the identity store, for the person's name)."""
    from kazma_core.language_lock import in_their_language

    from kazma_core.spaces.identity import IdentityUnavailable, read_registry

    name = ""
    try:
        account = (read_registry().get("accounts") or {}).get(asker) or {}
        name = str(account.get("name") or "").strip()
    except IdentityUnavailable:
        # The line still says why, without the name.
        logger.debug("[HITL-WD] the departed person's name could not be read", exc_info=True)
    asked = ""
    for message in reversed(list(values.get("messages") or [])):
        if isinstance(message, dict) and message.get("role") == "user":
            asked = str(message.get("content") or "")
            break
    return in_their_language(asked,
                             _DEPARTED_EN.format(name=name or "the person who asked"),
                             _DEPARTED_AR.format(name=name or "صاحب الطلب"))


async def _close_turn_of_departed(graph: Any, config: dict[str, Any], thread_id: str, whose: Any,
                                  payload: dict[str, Any] | None, interrupt_id: str) -> None:
    """End a turn whose card expired after its asker left the card's space:
    the card is recorded as timed out and settled, the turn ends with a line
    saying why (``turn_budget.write_turn_end``), and nothing is resumed --
    not as the asker, who is gone, and not as anyone else. A step that fails
    raises to the watchdog's handler, which logs it; the next pass sees what
    is left. Kazma's own work for the card's space: it runs bound to that
    space and the account its own work acts for (the chat store is each
    space's; the person who left is not bound)."""
    from kazma_core.spaces.identity import acting_account
    from kazma_core.spaces.scope import bind_scope

    space = str(getattr(whose, "space_id", "") or "")
    with bind_scope(space, await asyncio.to_thread(acting_account, space)):
        await _close_the_turn(graph, config, thread_id, whose, payload, interrupt_id)


async def _close_the_turn(graph: Any, config: dict[str, Any], thread_id: str, whose: Any,
                          payload: dict[str, Any] | None, interrupt_id: str) -> None:
    """:func:`_close_turn_of_departed`'s steps, in the space's scope."""
    from kazma_core.agent.turn_budget import write_turn_end
    from kazma_core.safety.hitl_gates import TIMEOUT_ACTOR
    from kazma_ui.delivery import get_turn_broker
    from kazma_ui.hitl_decision import record_gate_decision
    from kazma_ui.hitl_gate_bridge import settle_thread_gates
    from kazma_ui.reply_sink import resolve_reply_turn
    from kazma_ui.sse_chat._streaming import mark_thread_unpaused
    from kazma_ui.turn_runtime import close_turn, ensure_session_for_thread, resolve_session_id

    session_id = await asyncio.to_thread(resolve_session_id, thread_id)
    if not session_id:
        session_id = await asyncio.to_thread(ensure_session_for_thread, thread_id)
    turn_id = await asyncio.to_thread(resolve_reply_turn, thread_id, session_id)
    snapshot = await graph.aget_state(config)
    values = dict(getattr(snapshot, "values", None) or {})
    text = await asyncio.to_thread(_departed_text, str(getattr(whose, "asker", "") or ""), values)
    await record_gate_decision(
        thread_id, decision="timeout", actor=TIMEOUT_ACTOR,
        tool=str((payload or {}).get("tool") or ""), payload=dict(payload or {}),
        interrupt_id=interrupt_id, session_id=session_id, turn_id=turn_id, require_durable=True,
    )
    await write_turn_end(graph, config, thread_id, text)
    await settle_thread_gates(thread_id, outcome="asker_left")
    mark_thread_unpaused(thread_id)
    await close_turn(graph, config, session_id=session_id, thread_id=thread_id, turn_id=turn_id)
    logger.info("[HITL-WD] thread=%s: the card's asker left its space; the turn was closed, not resumed",
                str(thread_id)[:12])
    # The page's frame last: the turn is closed whether or not it is heard.
    await get_turn_broker().emit(thread_id, {
        "type": "approval_timeout",
        "data": {
            "thread_id": thread_id,
            "interrupt_id": interrupt_id,
            "tool": str((payload or {}).get("tool") or ""),
            "turn_id": turn_id,
            "message": text,
        },
    })


async def _watchdog_loop(
    graph_getter: Callable[[], Any],
    checkpointer_getter: Callable[[], Any],
) -> None:
    from kazma_core.safety.hitl import get_hitl_config

    from kazma_ui.hitl_approval import _get_pending_approvals

    logger.info("[HITL-WD] Approval-timeout watchdog started")
    while not is_shutting_down():
        try:
            await asyncio.sleep(_SCAN_INTERVAL_SECONDS)
            if is_shutting_down():
                break
            from kazma_core.db import availability

            # An auto-deny resumes the turn from its checkpoint, which is the
            # database's: during an outage it only fails. A card nobody
            # answered is denied once the database answers again.
            if await availability.wait_while_away():
                break

            # Off the loop: a ConfigStore read (a Postgres round trip) plus a
            # YAML merge. This tick was the most frequent loop-stall site in
            # the live install's dumps (14 of 70, 2026-08-30 .. 09-23).
            cfg = await asyncio.to_thread(get_hitl_config)
            if not cfg.get("enabled"):
                continue

            # Tell processes with no bus that a human is reachable here.
            # `kazma mcp` runs as a child of an MCP client and cannot see the
            # in-process approval bus; it queues into the gate registry
            # instead, but only while this beat is fresh. Written from the
            # loop that reads pending gates on purpose -- it is evidence of
            # the behaviour being relied on, not a claim beside it.
            try:
                from kazma_core.safety.bus_bridge import record_watcher

                await asyncio.to_thread(
                    record_watcher, kind="ui", detail="hitl-timeout-watchdog"
                )  # a SQLite write to hitl_gates.db
            except Exception:
                logger.debug("[HITL-WD] watcher heartbeat skipped", exc_info=True)
            timeout_s = float(cfg.get("approval_timeout_seconds", 300) or 0)
            auto_deny = bool(cfg.get("auto_deny_on_timeout", True))
            if timeout_s <= 0:
                continue  # timeout disabled

            graph = graph_getter()
            checkpointer = checkpointer_getter()
            if graph is None or checkpointer is None:
                continue

            from kazma_ui.hitl_gate_bridge import pending_items_from_registry

            registry_items = await pending_items_from_registry()
            if registry_items is not None:
                pending = registry_items
            else:
                pending = await _get_pending_approvals(graph, checkpointer)
            now = time.monotonic()
            current_ids = {str(p.get("thread_id")) for p in pending if p.get("thread_id")}

            # Drop threads that resolved (approved/denied/expired) since last tick
            for tid in list(_first_seen):
                if tid not in current_ids:
                    _first_seen.pop(tid, None)

            for item in pending:
                tid = str(item.get("thread_id") or "")
                if not tid:
                    continue
                first = _first_seen.setdefault(tid, now)
                age = now - first
                if age < timeout_s:
                    continue
                # Expired
                _first_seen.pop(tid, None)
                if auto_deny:
                    # Registry = which is pending; checkpoint = how.
                    # A stale row must not resume a graph that is not paused.
                    from kazma_core.safety.commitment.resume import (
                        read_pending_interrupt,
                    )

                    graph_cfg = {
                        "configurable": {
                            "thread_id": tid,
                            "checkpoint_ns": "",
                        }
                    }
                    live = await read_pending_interrupt(graph, graph_cfg)
                    if live is None:
                        logger.info(
                            "[HITL-WD] skip auto-deny thread=%s — "
                            "no live checkpoint interrupt",
                            tid,
                        )
                        # Registry says pending but the checkpoint holds no
                        # live interrupt and no turn is running — a ghost row
                        # (crash between resume and settle, or a pre-dual-write
                        # auto-deny). Orphan-settle it here or the card sits
                        # in chat/dashboard forever and re-scans every window.
                        try:
                            from kazma_ui.active_turns import is_turn_running

                            if not is_turn_running(tid):
                                from kazma_core.metrics import (
                                    record_hitl_gate_reconciled,
                                )
                                from kazma_core.safety.hitl_gates import (
                                    live_gates_async,
                                    settle_gate_async,
                                )

                                for row in await live_gates_async(tid):
                                    if row.state == "pending":
                                        try:
                                            await settle_gate_async(
                                                row.gate_id, "orphaned"
                                            )
                                            record_hitl_gate_reconciled(
                                                "orphaned"
                                            )
                                            logger.info(
                                                "[HITL-WD] orphan-settled ghost "
                                                "gate=%s thread=%s",
                                                row.gate_id,
                                                tid,
                                            )
                                        except Exception:
                                            pass
                        except Exception:
                            logger.debug(
                                "[HITL-WD] orphan sweep skipped", exc_info=True
                            )
                        continue
                    await _auto_deny(graph, tid, timeout_s)
                else:
                    logger.warning(
                        "[HITL-WD] Approval for thread=%s is %.0fs past the %.0fs "
                        "timeout (auto_deny_on_timeout=false — waiting for a human)",
                        tid,
                        age,
                        timeout_s,
                    )
                    # Re-track so we log at most once per window
                    _first_seen[tid] = now
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[HITL-WD] scan failed — retrying next tick")


def start_hitl_timeout_watchdog(
    graph_getter: Callable[[], Any],
    checkpointer_getter: Callable[[], Any],
) -> asyncio.Task | None:
    """Start the approval-timeout watchdog (idempotent).

    Args:
        graph_getter: callable returning the live compiled graph (or None).
        checkpointer_getter: callable returning the live checkpointer (or None).

    Returns the created task, or the existing one if already running.
    """
    global _watchdog_task
    if _watchdog_task is not None and not _watchdog_task.done():
        return _watchdog_task
    _first_seen.clear()
    _watchdog_task = asyncio.create_task(
        _watchdog_loop(graph_getter, checkpointer_getter),
        name="hitl-timeout-watchdog",
    )
    return _watchdog_task


async def stop_hitl_timeout_watchdog() -> None:
    """Cancel the watchdog task (called during app shutdown)."""
    global _watchdog_task
    task = _watchdog_task
    _watchdog_task = None
    if task is not None and not task.done():
        from kazma_core.bounded_cancel import cancel_and_wait

        await cancel_and_wait(task, what="approval timeout watchdog")
    _first_seen.clear()
