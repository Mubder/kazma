"""Write + read bridge: web HITL sites ↔ the gate registry.

The registry (`kazma_core.safety.hitl_gates`) is the DECISION-TRUTH store
(P6). Web readers (`hitl_thread_status`, pending-approvals, ``close_turn``,
chat via ``gates`` + ``gates_authoritative``) treat a registry row as the
answer. Kill-switch off or a registry outage degrades to a **thin
execution fallback**: a live checkpoint interrupt is pending (live card),
never an inferred Approved stamp. ``created_missing`` / ``orphaned``
counters remain the residual drift signal.

Every write here is **best-effort and exception-proof**: a registry
failure logs + increments the mismatch metric and NEVER blocks the
user-facing action. All entry points are async (`asyncio.to_thread` under
the hood — §23, the server loop must not block) and no-op instantly when
the ``KAZMA_GATE_REGISTRY`` kill-switch is off.

Alias convergence (two-id rule): both this bridge and any pre-pause
registration compute the SAME deterministic alias via
:func:`hitl_gates.make_gate_id`, so a graph-side provisional row and the
post-stream row with the real LangGraph id merge into one row — one pause
can never mint two cards.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "gate_pending_from_payload",
    "gate_claimed",
    "gate_claimed_for_thread",
    "gate_resuming",
    "settle_thread_gates",
    "registry_on",
    "gate_row_to_pending_item",
    "pending_items_from_registry",
    "ensure_paused_gate",
]


def registry_on() -> bool:
    try:
        from kazma_core.safety.hitl_gates import gate_registry_enabled

        return gate_registry_enabled()
    except Exception:
        return False


def _alias_for(thread_id: str, tool: str, args: Any) -> str:
    from kazma_core.safety.hitl_gates import make_gate_id

    return make_gate_id(thread_id, tool, args)


async def gate_pending_from_payload(
    payload: dict[str, Any],
    *,
    session_id: str = "",
    turn_id: str = "",
    tenant_id: str = "",
    mechanism: str = "graph",
) -> None:
    """Register a pending gate row from a saved HITL payload.

    Called from the post-stream interrupt scan — the primary register site
    (the pause is observed and ``interrupt_id`` is the real id when LangGraph
    provided one). Idempotent on both ids; safe to call on every scan.
    """
    if not registry_on():
        return
    try:
        from kazma_core.metrics import record_hitl_gate
        from kazma_core.safety.hitl_gates import GateRow, register_gate_async

        thread_id = str(payload.get("thread_id") or "")
        tool = str(payload.get("tool") or "")
        gate_id = str(payload.get("interrupt_id") or "").strip()
        args = payload.get("args") or {}
        alias = _alias_for(thread_id, tool, args)
        if not gate_id:
            gate_id = alias
        row = GateRow(
            gate_id=gate_id,
            alias_id=alias if alias != gate_id else "",
            thread_id=thread_id,
            tenant_id=tenant_id,
            session_id=session_id,
            turn_id=turn_id,
            mechanism=mechanism,
            kind=str(payload.get("kind") or "security"),
            tool=tool,
            args_json=json.dumps(args, default=str)[:20000],
            message=str(payload.get("message") or "")[:2000],
            payload_json=json.dumps(payload, default=str)[:40000],
        )
        created = await register_gate_async(row)
        record_hitl_gate(created.state, mechanism)
    except Exception:
        logger.warning("[GateBridge] register failed (user action unaffected)", exc_info=True)
        _mismatch("register")


async def gate_claimed(
    thread_id: str,
    interrupt_id: str,
    decision: str,
    actor: str,
    *,
    tool: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    """Record the human decision. Missing row ⇒ create-then-claim (the
    crash-between-interrupt-and-write case — checkpoint said it existed,
    the approve endpoint verified it, so the registry must reflect it)."""
    if not registry_on():
        return
    try:
        from kazma_core.metrics import (
            record_hitl_gate,
            record_hitl_gate_reconciled,
        )
        from kazma_core.safety.hitl_gates import (
            GateRow,
            TransitionConflict,
            claim_gate_async,
            gate_for_async,
            register_gate_async,
        )

        gid = str(interrupt_id or "").strip()
        row = await gate_for_async(gid) if gid else None
        if row is None:
            # Reconcile-on-approve: legacy verified a real pending interrupt.
            src = dict(payload or {})
            create = GateRow(
                gate_id=gid or _alias_for(thread_id, tool, src.get("args") or {}),
                thread_id=thread_id,
                tool=tool or str(src.get("tool") or ""),
                kind=str(src.get("kind") or "security"),
                message=str(src.get("message") or "")[:2000],
                payload_json=json.dumps(src, default=str)[:40000],
            )
            row = await register_gate_async(create)
            record_hitl_gate_reconciled("created_missing")
        try:
            claimed = await claim_gate_async(row.gate_id, decision, actor)
            record_hitl_gate(claimed.state, claimed.mechanism)
        except TransitionConflict as tc:
            # The endpoint verified a real pending interrupt but the
            # registry disagrees — exactly the drift the parity counter
            # exists for. The claim proceeds (the checkpoint interrupt is
            # execution truth); the mismatch is recorded, not hidden.
            logger.warning(
                "[GateBridge] claim conflict gate=%s expected=pending actual=%s "
                "(parity mismatch recorded)", row.gate_id, tc.actual,
            )
            _mismatch("claim")
    except Exception:
        logger.warning("[GateBridge] claim failed (user action unaffected)", exc_info=True)
        _mismatch("claim")


async def gate_resuming(interrupt_id: str) -> None:
    """CAS claimed→resuming when the resume drive is spawned."""
    if not registry_on() or not interrupt_id:
        return
    try:
        from kazma_core.metrics import record_hitl_gate
        from kazma_core.safety.hitl_gates import (
            TransitionConflict,
            gate_for_async,
            mark_resuming_async,
        )

        row = await gate_for_async(str(interrupt_id))
        if row is None:
            _mismatch("resuming")
            return
        try:
            r = await mark_resuming_async(row.gate_id)
            record_hitl_gate(r.state, r.mechanism)
        except TransitionConflict:
            _mismatch("resuming")
    except Exception:
        logger.warning("[GateBridge] mark_resuming failed", exc_info=True)


async def settle_thread_gates(thread_id: str, *, outcome: str = "") -> None:
    """Settle every claimed/resuming gate on a thread (turn reached terminal).

    Deliberately leaves ``pending`` rows alone — a second gate that paused
    the drive again is a live question, not a leftover (the silence rule).
    """
    if not registry_on() or not thread_id:
        return
    try:
        from kazma_core.metrics import record_hitl_gate
        from kazma_core.safety.hitl_gates import (
            TransitionConflict,
            live_gates_async,
            settle_gate_async,
        )

        for row in await live_gates_async(thread_id):
            if row.state in ("claimed", "resuming"):
                try:
                    s = await settle_gate_async(row.gate_id, outcome)
                    record_hitl_gate(s.state, s.mechanism)
                except TransitionConflict:
                    _mismatch("settle")
    except Exception:
        logger.warning("[GateBridge] settle failed", exc_info=True)


async def gate_claimed_for_thread(
    thread_id: str,
    decision: str,
    actor: str,
    *,
    tool: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    """Claim the OLDEST pending gate on a thread (gateway/platform path —
    platform cards are per-thread, they carry no interrupt id)."""
    if not registry_on() or not thread_id:
        return
    try:
        from kazma_core.safety.hitl_gates import live_gates_async

        target = ""
        for row in await live_gates_async(thread_id):
            if row.state == "pending":
                target = row.gate_id
                break
        if target:
            await gate_claimed(
                thread_id, target, decision, actor, tool=tool, payload=payload
            )
        else:
            # Legacy verified a real pending interrupt the registry missed.
            await gate_claimed(
                thread_id, "", decision, actor, tool=tool, payload=payload
            )
    except Exception:
        logger.warning("[GateBridge] thread claim failed", exc_info=True)


def gate_row_to_pending_item(row: Any) -> dict[str, Any]:
    """Dashboard / timeout card shape from a pending registry row."""
    payload = row.payload() if hasattr(row, "payload") else {}
    if not isinstance(payload, dict):
        payload = {}
    args = row.args() if hasattr(row, "args") else {}
    if not isinstance(args, dict):
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        if not args and isinstance(payload.get("arguments"), dict):
            args = payload["arguments"]
    return {
        "thread_id": str(getattr(row, "thread_id", "") or ""),
        "tool_name": str(getattr(row, "tool", "") or payload.get("tool") or ""),
        "arguments": args,
        "message": str(getattr(row, "message", "") or payload.get("message") or ""),
        "yolo_allowed": payload.get("yolo_allowed", True),
        "interrupt_id": str(getattr(row, "gate_id", "") or ""),
        "kind": str(getattr(row, "kind", "") or "security"),
        "items": payload.get("items"),
    }


async def pending_items_from_registry() -> list[dict[str, Any]] | None:
    """Pending dashboard items from the registry.

    Returns ``None`` when the registry is off or unreadable so the caller
    can degrade to a checkpoint scan. An empty list means "nothing pending"
    and is authoritative.
    """
    if not registry_on():
        return None
    try:
        from kazma_core.safety.hitl_gates import pending_gates_async

        rows = await pending_gates_async()
        return [gate_row_to_pending_item(r) for r in rows]
    except Exception:
        logger.warning("[GateBridge] pending_gates read failed", exc_info=True)
        _mismatch("pending_read")
        return None


def _live_row_covers_snapshot(rows: list[Any], snapshot: Any) -> bool:
    """True when a claimed/resuming row is the leftover of this snapshot pause."""
    try:
        from kazma_ui.hitl_status import snapshot_interrupt_id, snapshot_interrupt_payload
    except Exception:
        return False
    iid = snapshot_interrupt_id(snapshot)
    payload = snapshot_interrupt_payload(snapshot) or {}
    tool = str(payload.get("tool") or payload.get("tool_name") or "").strip().lower()
    for row in rows:
        st = str(getattr(row, "state", "") or "")
        if st not in ("claimed", "resuming"):
            continue
        gid = str(getattr(row, "gate_id", "") or "")
        alias = str(getattr(row, "alias_id", "") or "")
        if iid and iid in (gid, alias):
            return True
        rtool = str(getattr(row, "tool", "") or "").strip().lower()
        if tool and rtool and tool == rtool and not iid:
            return True
    return False


async def ensure_paused_gate(
    thread_id: str,
    snapshot: Any,
    *,
    session_id: str = "",
    turn_id: str = "",
) -> bool:
    """Paused + no covering pending row ⇒ register from the snapshot.

    Absence of a row for a paused thread is an unregistered pending gate,
    never "no question" (P6 amendment). Returns True when a pending
    question exists after this call (existing or just backfilled).

    A claimed/resuming row that matches this snapshot is a leftover of
    Approve — do not mint a ghost pending card for it.
    """
    if not thread_id or not registry_on():
        return False
    try:
        from kazma_core.metrics import record_hitl_gate_reconciled
        from kazma_core.safety.hitl_gates import live_gates_async
        from kazma_ui.hitl_status import snapshot_interrupt_payload

        rows = await live_gates_async(thread_id)
        if any(r.state == "pending" for r in rows):
            return True
        if _live_row_covers_snapshot(rows, snapshot):
            return False
        payload = snapshot_interrupt_payload(snapshot)
        if not payload:
            return False
        # Leftover checkpoint interrupt of a gate already persisted as
        # approved is not a new question — do not mint a ghost pending row.
        try:
            from kazma_ui.hitl_status import (
                is_new_gate,
                persisted_hitl_for_thread,
            )

            part = persisted_hitl_for_thread(thread_id)
            st = str((part or {}).get("state") or "").lower()
            if st in ("approved", "denied", "inflight", "settled") and not is_new_gate(
                part, snapshot
            ):
                return False
        except Exception:
            pass
        if not payload.get("thread_id"):
            payload = dict(payload)
            payload["thread_id"] = thread_id
        await gate_pending_from_payload(
            payload, session_id=session_id, turn_id=turn_id
        )
        record_hitl_gate_reconciled("created_missing")
        return True
    except Exception:
        logger.debug("[GateBridge] ensure_paused_gate skipped", exc_info=True)
        return False


def _mismatch(site: str) -> None:
    try:
        from kazma_core.metrics import record_hitl_gate_parity_mismatch

        record_hitl_gate_parity_mismatch(site)
    except Exception:
        pass
