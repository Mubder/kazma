"""The decision of one swarm-bus approval request, shared by every platform.

``SafetyMiddleware.check`` -- the owner of a request -- opens a row for it
under a freshly minted id (:func:`open_request`), the platform adapters post
their cards and wait on that row (:func:`_wait_outcome`), a platform callback
decides it (:func:`resolve`), and the owner reads the outcome and closes the
row (:func:`close`). The row lives in the settings store, so a callback
arriving at another replica decides it too; local events wake same-process
waiters at once.

Swarm audit 2026-10-13, Stage 1 (each with its regression in
``tests/test_swarm_bus_approvals.py``):

* **SW-S02** -- the id used to come from the tool's own arguments
  (``arguments.get("task_id")``), a settled "yes" was never reset or deleted,
  and ``resolve`` of an id nobody posted created a resolved row: one click
  approved every later request carrying that id, another tool's included,
  after a restart too. Now an id is opened once (a second open fails), bound
  to the tool and an arguments hash, decided once by compare-and-set,
  deleted when its owner has read it, and ``resolve`` changes only a pending
  row it finds.
* **SW-S04** -- each adapter had its own timer, a timeout left the card live,
  and a timeout counted as a "reject". Now the row's deadline is the one
  deadline: the first waiter past it settles ``timeout``, a cancelled waiter
  settles ``cancelled``, and both are outcomes of their own, never a deny.
* **SW-S05** -- with several platforms, the card where a human pressed Reject
  said REJECTED while another platform's Approve ran the tool. Every
  platform's card now waits on this one row, so each shows the one final
  outcome; an explicit human Deny is final (owner decision D7), like an
  Approve.
* **SW-S12** -- the durable reads and writes run off the event loop.

``hitl_gates.db`` keeps the record of the decision and who made it: the
owner claims and settles the gate row from this outcome. This row is only
the wait state, and is gone once read.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import threading
import time
from typing import Any

__all__ = [
    "OUTCOMES",
    "args_hash",
    "await_card",
    "close",
    "decide_from_button",
    "decide_from_web",
    "describe",
    "get",
    "open_request",
    "outcome_text",
    "purge_stale",
    "resolve",
    "resolve_from_callback",
    "settle",
]

logger = logging.getLogger(__name__)

_KEY_PREFIX = "swarm.approval."
#: Every final state of a request. ``pending`` is the only other one.
OUTCOMES = ("approved", "denied", "timeout", "cancelled")
#: The outcomes no human chose: the deadline passed, or the request ended.
_ENDINGS = tuple(o for o in OUTCOMES if o not in ("approved", "denied"))
#: How often a waiter re-reads the durable row (another replica's callback);
#: a callback in this process wakes it at once.
_POLL_S = 1.0

#: Same-process waiters: one event per request, on the loop that waits.
_waiters: dict[str, tuple[asyncio.Event, asyncio.AbstractEventLoop]] = {}
_lock = threading.RLock()


def _key(approval_id: str) -> str:
    return f"{_KEY_PREFIX}{approval_id}"


def args_hash(tool: str, args: str | None) -> str:
    """What a decision is bound to besides its id: the tool and its arguments."""
    return hashlib.sha256(f"{tool}\0{args or ''}".encode()).hexdigest()[:32]


def _store():
    from kazma_core.config_store import get_config_store

    return get_config_store()


def get(approval_id: str) -> dict[str, Any] | None:
    """The request's row, or None (never opened, or closed)."""
    if not approval_id:
        return None
    raw = _store().get(_key(approval_id))
    return dict(raw) if isinstance(raw, dict) else None


#: A gate record's terminal state -> the request's outcome (the decision
#: column holds approve/deny, or the ending ``settle_gate`` was given).
_GATE_OUTCOMES = {"approve": "approved", "deny": "denied", "cancelled": "cancelled"}


def describe(approval_id: str) -> dict[str, Any] | None:
    """What the Web dashboard shows of one request (Swarm audit SW-S03).

    The whole call comes from the gate record (``hitl_gates``, kept after the
    request ends); the state from this row while it is open, else from that
    record. None when neither knows the id -- or when the id names another
    mechanism's gate: a chat approval is not this page's to show. Blocking:
    a settings read and a SQLite read.
    """
    from kazma_core.swarm.approval_card import arguments_digest

    if not approval_id:
        return None
    row = get(approval_id)
    if row is not None and row.get("status") == "closed":
        row = None
    gate = None
    try:
        from kazma_core.safety.hitl_gates import gate_for, gate_registry_enabled

        if gate_registry_enabled():
            gate = gate_for(approval_id)
    except (sqlite3.Error, OSError):  # the open row still answers
        logger.warning("[shared_approvals] could not read the gate record of %s", approval_id, exc_info=True)
    if gate is not None and gate.mechanism != "swarm_bus":
        return None
    if row is None and gate is None:
        return None
    if row is not None:
        status = str(row.get("status") or "pending")
    elif gate.state == "timeout":
        status = "timeout"
    elif gate.state == "settled":
        status = _GATE_OUTCOMES.get(gate.decision, "ended")
    else:  # the request ended without its outcome on record (a crash)
        status = "ended"
    args = gate.args_json if gate is not None else None
    return {
        "id": approval_id,
        "tool": str((row or {}).get("tool") or (gate.tool if gate else "")),
        "status": status,
        "arguments": args,
        "arguments_digest": arguments_digest(args) if args is not None else "",
        "opened_at": float((row or {}).get("opened_at") or (gate.created_at if gate else 0) or 0),
        "deadline_at": float((row or {}).get("deadline_at") or 0) or None,
        "decided_by": str((row or {}).get("actor") or (gate.actor if gate else "") or ""),
        "decided_on": str((row or {}).get("platform") or ""),
        "decided_at": (row or {}).get("decided_at") or (gate.settled_at if gate else None),
    }


def _transition(approval_id: str, mutate) -> tuple[dict[str, Any] | None, bool]:
    """Compare-and-set on the row: *mutate* returns the new row, or None to
    leave it. Returns (row as it is now, whether this call changed it). A
    row that is not there is left alone: an id nobody opened, or a closed
    one, is never decided."""
    if get(approval_id) is None:
        return None, False
    changed = [False]

    def _apply(prev: Any) -> Any:
        if not isinstance(prev, dict):
            # Closed between the read above and this write: the store writes
            # what this returns, so leave a dated tombstone purge_stale takes.
            return {"status": "closed", "opened_at": 0}
        new = mutate(dict(prev))
        if new is None:
            return prev
        changed[0] = True
        return new

    from kazma_core.config_store import ConfigStore

    store = _store()
    with _lock:
        if isinstance(store, ConfigStore) or hasattr(store, "atomic_update"):
            row = store.atomic_update(_key(approval_id), _apply, category="swarm")
        else:  # a store without CAS (a test double): one process only
            row = _apply(store.get(_key(approval_id)))
            if changed[0]:
                store.set(_key(approval_id), row, category="swarm")
    return (dict(row) if isinstance(row, dict) else None), changed[0]


def open_request(
    approval_id: str,
    *,
    tool: str,
    args: str | None,
    deadline: float,
    platforms: int = 1,
) -> dict[str, Any]:
    """Open the row for a new request. An id is opened once: a row already
    there (a reused id) raises, so no request ever inherits a decision."""
    if not approval_id:
        raise ValueError("an approval request needs an id")
    row = {
        "status": "pending",
        "tool": tool,
        "args_hash": args_hash(tool, args),
        "opened_at": time.time(),
        "deadline_at": float(deadline),
        "platforms": max(1, int(platforms or 1)),
    }
    created = [False]

    def _create(prev: Any) -> Any:
        if prev is not None:
            return prev
        created[0] = True
        return row

    store = _store()
    with _lock:
        if hasattr(store, "atomic_update"):
            store.atomic_update(_key(approval_id), _create, category="swarm")
        elif store.get(_key(approval_id)) is None:
            created[0] = True
            store.set(_key(approval_id), row, category="swarm")
        if not created[0]:
            raise ValueError(f"approval id {approval_id!r} is already in use")
    return row


def resolve(approval_id: str, approved: bool, *, actor: str = "", platform: str = "") -> bool:
    """A human's decision from a platform callback. Decides a PENDING row
    only, once: an id nobody opened, a closed one or a decided one is left
    as it is (False). True when this call decided it."""
    if not approval_id:
        return False

    def _decide(row: dict[str, Any]) -> dict[str, Any] | None:
        if row.get("status") != "pending":
            return None
        row.update(status="approved" if approved else "denied", actor=str(actor or ""),
                   platform=str(platform or ""), decided_at=time.time())
        return row

    _row, changed = _transition(approval_id, _decide)
    if changed:
        _wake(approval_id)
    return changed


def settle(approval_id: str, outcome: str) -> bool:
    """End a pending request without a decision: ``timeout`` or ``cancelled``."""
    if outcome not in _ENDINGS:
        raise ValueError(f"not an ending: {outcome!r}")

    def _end(row: dict[str, Any]) -> dict[str, Any] | None:
        if row.get("status") != "pending":
            return None
        row.update(status=outcome, decided_at=time.time())
        return row

    _row, changed = _transition(approval_id, _end)
    if changed:
        _wake(approval_id)
    return changed


def _wake(approval_id: str) -> None:
    """Wake this process's waiters, from any thread."""
    with _lock:
        waiter = _waiters.get(approval_id)
    if waiter is None:
        return
    ev, loop = waiter
    if loop.is_closed():
        return
    try:
        if asyncio.get_running_loop() is loop:
            ev.set()
            return
    except RuntimeError:
        pass
    loop.call_soon_threadsafe(ev.set)


async def _wait_outcome(approval_id: str) -> dict[str, Any]:
    """Wait until the request leaves ``pending``; returns its row.

    The deadline is the row's own (the owner set it at open): the first
    waiter past it settles ``timeout``. Cancelled while waiting, the request
    is settled ``cancelled`` before the cancellation goes on. Every durable
    read and write runs off the event loop."""
    loop = asyncio.get_running_loop()
    with _lock:
        waiter = _waiters.get(approval_id)
        if waiter is None or waiter[1] is not loop:
            waiter = (asyncio.Event(), loop)
            _waiters[approval_id] = waiter
    ev = waiter[0]
    try:
        while True:
            row = await asyncio.to_thread(get, approval_id)
            if row is None or row.get("status") == "closed":  # closed by its owner
                return {"status": "cancelled"}
            if row.get("status") != "pending":
                return row
            remaining = float(row.get("deadline_at") or 0) - time.time()
            if remaining <= 0:
                await asyncio.to_thread(settle, approval_id, "timeout")
                continue
            try:
                await asyncio.wait_for(ev.wait(), timeout=min(_POLL_S, remaining))
            except TimeoutError:
                pass
    except asyncio.CancelledError:
        await asyncio.shield(asyncio.to_thread(settle, approval_id, "cancelled"))
        raise


def close(approval_id: str) -> None:
    """Delete the row: its owner has read the outcome. A later callback for
    this id finds nothing and changes nothing."""
    with _lock:
        _waiters.pop(approval_id, None)
    try:
        _store().delete(_key(approval_id))
    except Exception:  # a store without delete (a test double), or a failure
        logger.warning("[shared_approvals] could not delete approval %s", approval_id, exc_info=True)


def purge_stale(older_than_s: float = 86400.0, *, now: float | None = None) -> int:
    """Delete the rows whose owner never closed them (a crash mid-request).
    The maintenance cadence runs it; a row that old has no waiter."""
    store = _store()
    cutoff = (now if now is not None else time.time()) - older_than_s
    try:
        rows = store.get(_KEY_PREFIX.rstrip(".")) or {}
    except Exception:
        logger.warning("[shared_approvals] could not list approval rows", exc_info=True)
        return 0
    removed = 0
    if not isinstance(rows, dict):
        return 0
    for approval_id, row in rows.items():
        if isinstance(row, dict) and float(row.get("opened_at") or 0) < cutoff:
            close(str(approval_id))
            removed += 1
    return removed


# ── For the platform adapters: one card, one outcome ───────────────────────

_CARD_EDIT_TIMEOUT_S = 10.0


def outcome_text(row: dict[str, Any]) -> str:
    """The final line a card shows, the same on every platform (SW-S05)."""
    status = row.get("status")
    by = row.get("actor") or ""
    on = row.get("platform") or ""
    who = (f" by {by}" if by else "") + (f" on {on}" if on else "")
    if status == "approved":
        return f"✅ APPROVED{who}"
    if status == "denied":
        return f"❌ DENIED{who}: the tool did not run"
    if status == "timeout":
        return "⏱️ TIMED OUT: nobody answered, the tool did not run"
    return "⛔ CANCELLED: the request ended before an answer, the tool did not run"


async def await_card(approval_id: str, edit) -> bool:
    """Wait for the request's outcome and show it on this platform's card.

    *edit* is ``async (row) -> None`` and replaces the card's buttons with
    :func:`outcome_text`. Cancelled while waiting, the card is edited to
    "cancelled" (bounded, shielded) before the cancellation goes on: a card
    left with live buttons invited a click that could decide nothing.
    Returns True only for ``approved``."""
    try:
        row = await _wait_outcome(approval_id)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(asyncio.wait_for(edit({"status": "cancelled"}), _CARD_EDIT_TIMEOUT_S))
        except (asyncio.CancelledError, Exception):  # the card stays as it is; the row is settled
            logger.warning("[shared_approvals] could not mark the card of %s cancelled", approval_id)
        raise
    try:
        await asyncio.wait_for(edit(row), _CARD_EDIT_TIMEOUT_S)
    except Exception:
        logger.warning("[shared_approvals] could not show the outcome on the card of %s", approval_id,
                       exc_info=True)
    return row.get("status") == "approved"


def resolve_from_callback(approval_id: str, approved: bool, *, actor: str = "", platform: str = "") -> None:
    """A platform button: decide the request, off the event loop when one
    runs here (the decision is a settings-store write, SW-S12)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _resolve_reported(approval_id, approved, actor=actor, platform=platform)
        return
    from kazma_core.background import spawn_background

    spawn_background(
        asyncio.to_thread(_resolve_reported, approval_id, approved, actor=actor, platform=platform),
        name=f"swarm-approval:{approval_id}",
    )


def decide_from_web(approval_id: str, approved: bool, *, actor: str) -> bool | None:
    """The Web dashboard's Approve or Deny (SW-S03): the same decision as a
    button, recorded as ``web``. True decided, False not pending (answered,
    ended or unknown), None not recorded (said, as for a button)."""
    return _resolve_reported(approval_id, approved, actor=actor, platform="web")


def _resolve_reported(approval_id: str, approved: bool, *, actor: str, platform: str) -> bool | None:
    """:func:`resolve`, saying when the decision could not be recorded (Swarm
    audit SW-S15: the adapters swallowed it -- the human pressed a button, the
    request stayed pending and timed out, and nothing said why). None when it
    could not be recorded."""
    try:
        return resolve(approval_id, approved, actor=actor, platform=platform)
    except Exception:
        logger.warning(
            "[SwarmApproval] %s's %s on request %s (%s) could not be recorded; the "
            "request stays pending until it times out",
            actor or "someone", "Approve" if approved else "Deny", approval_id,
            platform or "?", exc_info=True,
        )
        from kazma_core.metrics import record_swarm_failure

        record_swarm_failure("approval_resolve")
        return None


#: A swarm card's button data: the decision and the request's id.
_BUTTON_PREFIXES = (("swarm_approve_", True), ("swarm_reject_", False))


def decide_from_button(data: str, *, actor: str = "", platform: str = "") -> str | None:
    """A swarm card's button press on any platform: decide the request its
    data names (``swarm_approve_<id>`` / ``swarm_reject_<id>``) and return
    the id, or None when *data* is not a swarm button. The row is shared, so
    no adapter instance is needed: a sender rebuilt since the card was posted
    (a saved setting, AGENTS §48) does not lose the click. An id nobody is
    waiting on changes nothing (:func:`resolve`)."""
    for prefix, approved in _BUTTON_PREFIXES:
        if data.startswith(prefix):
            approval_id = data[len(prefix):]
            if approval_id:
                resolve_from_callback(approval_id, approved, actor=actor, platform=platform)
                return approval_id
    return None
