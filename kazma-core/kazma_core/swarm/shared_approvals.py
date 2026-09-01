"""Shared HITL approval wait state for multi-replica deployments.

In-process ``asyncio.Event`` maps break when the replica that posted the
approval card is not the replica that receives the button callback.
This module dual-writes pending approvals to ConfigStore (SQLite or
Postgres via ConfigStore backend) so any replica can:

* ``create_pending`` when posting an approval card
* ``resolve`` when a platform callback arrives
* ``wait`` by polling until resolved or timeout

Local Events are still used for low-latency same-process resolution.

H-12 (swarm bus only — this is NOT web ``claim_gate``):
Fan-out across Telegram/Discord/Slack used to settle on the first
*boolean*, so a Discord timeout mapped to False denied a later Telegram
Approve. Tri-state: ``True`` settles immediately; ``False`` is a reject
vote and only settles denied when votes >= ``expected_voters`` or the
overall deadline passes. A later True never loses to an earlier False.
Web ``claim_gate`` stays first-claim 200 / second 409.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

__all__ = [
    "create_pending",
    "get_result",
    "resolve",
    "wait_for_resolution",
]

logger = logging.getLogger(__name__)

_KEY_PREFIX = "swarm.approval."
_local_events: dict[str, asyncio.Event] = {}
_local_results: dict[str, bool] = {}
_lock = threading.RLock()


def _key(task_id: str) -> str:
    return f"{_KEY_PREFIX}{task_id}"


def _schedule_eviction(task_id: str, *, delay: float = 60.0) -> None:
    """Remove the in-process entries for *task_id* after *delay*.

    By then any waiter has woken (``wait_for_resolution``'s timeout is ≤60s)
    and read its result; the durable ConfigStore row remains the
    cross-replica source of truth. Bounds the module-level dicts, which
    previously grew for the whole process lifetime (audit finding).
    """
    def _evict() -> None:
        with _lock:
            _local_events.pop(task_id, None)
            _local_results.pop(task_id, None)
    try:
        loop = asyncio.get_running_loop()
        loop.call_later(delay, _evict)
    except RuntimeError:
        _evict()  # no running loop (sync context) — evict eagerly


def _load_payload(task_id: str) -> dict[str, Any]:
    try:
        from kazma_core.config_store import get_config_store

        raw = get_config_store().get(_key(task_id))
        if isinstance(raw, dict):
            return dict(raw)
    except Exception as exc:
        logger.debug("[shared_approvals] load durable failed: %s", exc)
    return {}


def _save_payload(task_id: str, payload: dict[str, Any]) -> None:
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().set(_key(task_id), payload, category="swarm")
    except Exception as exc:
        logger.debug("[shared_approvals] save durable failed: %s", exc)


def _is_resolved_true(payload: dict[str, Any]) -> bool:
    return payload.get("status") == "resolved" and bool(payload.get("result"))


def _is_resolved(payload: dict[str, Any]) -> bool:
    return payload.get("status") == "resolved" and payload.get("result") is not None


def _should_settle_reject(payload: dict[str, Any], reject_votes: int) -> bool:
    expected = max(1, int(payload.get("expected_voters") or 1))
    if reject_votes >= expected:
        return True
    deadline = payload.get("deadline_at")
    if deadline is not None:
        try:
            if time.time() >= float(deadline):
                return True
        except (TypeError, ValueError):
            pass
    return False


def _wake(task_id: str) -> None:
    ev = _local_events.get(task_id)
    if ev is not None:
        ev.set()


def create_pending(
    task_id: str,
    *,
    meta: dict[str, Any] | None = None,
    expected_voters: int = 1,
    deadline: float | None = None,
) -> None:
    """Register a pending approval (local event + durable row).

    Idempotent while pending: later calls raise ``expected_voters`` to the
    max seen (FanOut stamps N, then each adapter stamps 1) and keep
    reject votes. A settled True is never reset. This is the swarm-bus
    shared wait — not web ``claim_gate``.
    """
    if not task_id:
        return
    expected = max(1, int(expected_voters or 1))
    with _lock:
        if _local_results.get(task_id) is True:
            return
        if task_id not in _local_events:
            _local_events[task_id] = asyncio.Event()

        prev = _load_payload(task_id)
        if _is_resolved_true(prev):
            _local_results[task_id] = True
            _wake(task_id)
            return

        if prev.get("status") == "pending":
            expected = max(expected, int(prev.get("expected_voters") or 1))
            reject_votes = int(prev.get("reject_votes") or 0)
            created_at = prev.get("created_at") or time.time()
            prev_deadline = prev.get("deadline_at")
            if deadline is None:
                deadline = prev_deadline
            elif prev_deadline is not None:
                try:
                    deadline = min(float(deadline), float(prev_deadline))
                except (TypeError, ValueError):
                    deadline = prev_deadline
        else:
            # Fresh pending (or reuse after a settled False).
            reject_votes = 0
            created_at = time.time()
            _local_results.pop(task_id, None)
            ev = _local_events.get(task_id)
            if ev is not None:
                ev.clear()

        payload: dict[str, Any] = {
            "status": "pending",
            "created_at": created_at,
            "result": None,
            "expected_voters": expected,
            "reject_votes": reject_votes,
            **(prev if prev.get("status") == "pending" else {}),
            **(meta or {}),
        }
        payload["status"] = "pending"
        payload["result"] = None
        payload["expected_voters"] = expected
        payload["reject_votes"] = reject_votes
        payload["created_at"] = created_at
        if deadline is not None:
            payload["deadline_at"] = float(deadline)
        _save_payload(task_id, payload)


def resolve(task_id: str, approved: bool) -> None:
    """Resolve an approval from any replica/platform callback.

    True settles immediately and is sticky. False is a reject vote and
    only settles denied when every expected voter has rejected or the
    overall deadline has passed. Does not clobber an existing True.
    """
    if not task_id:
        return
    should_evict = False
    with _lock:
        if _local_results.get(task_id) is True:
            return
        prev = _load_payload(task_id)
        if _is_resolved_true(prev):
            _local_results[task_id] = True
            _wake(task_id)
            return
        if prev.get("status") == "resolved" and prev.get("result") is False:
            _local_results[task_id] = False
            _wake(task_id)
            return

        expected = max(1, int(prev.get("expected_voters") or 1))
        reject_votes = int(prev.get("reject_votes") or 0)
        payload = dict(prev) if prev else {}
        payload.setdefault("created_at", time.time())
        payload["expected_voters"] = expected

        if approved:
            payload.update(
                {
                    "status": "resolved",
                    "result": True,
                    "resolved_at": time.time(),
                    "reject_votes": reject_votes,
                }
            )
            _local_results[task_id] = True
            _save_payload(task_id, payload)
            _wake(task_id)
            should_evict = True
        else:
            reject_votes += 1
            payload["reject_votes"] = reject_votes
            if _should_settle_reject(payload, reject_votes):
                payload.update(
                    {
                        "status": "resolved",
                        "result": False,
                        "resolved_at": time.time(),
                    }
                )
                _local_results[task_id] = False
                _save_payload(task_id, payload)
                _wake(task_id)
                should_evict = True
            else:
                payload["status"] = "pending"
                payload["result"] = None
                _save_payload(task_id, payload)
                # Keep waiters parked — a later True can still win.
    if should_evict:
        _schedule_eviction(task_id)


def get_result(task_id: str) -> bool | None:
    """Return True/False if resolved, else None."""
    if task_id in _local_results:
        return _local_results[task_id]
    raw = _load_payload(task_id)
    if _is_resolved(raw):
        return bool(raw.get("result"))
    return None


async def wait_for_resolution(task_id: str, timeout: float = 60.0) -> bool:
    """Wait until resolved. Returns approved bool (False on timeout).

    A timeout is a reject *vote* (H-12). With ``expected_voters > 1`` it
    does not globally deny until every voter has rejected or the stamped
    deadline passes, so a later Approve on another platform can still win.
    """
    if not task_id:
        return False

    existing = get_result(task_id)
    if existing is not None:
        return existing

    create_pending(task_id)  # ensure local event exists; merge, do not reset
    ev = _local_events[task_id]
    deadline = time.monotonic() + max(0.5, float(timeout))
    poll = 0.15

    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            await asyncio.wait_for(ev.wait(), timeout=min(poll, remaining))
            result = get_result(task_id)
            if result is not None:
                return result
        except TimeoutError:
            pass

        result = get_result(task_id)
        if result is not None:
            return result

    logger.warning("[shared_approvals] timeout task_id=%s", task_id)
    if get_result(task_id) is None:
        resolve(task_id, False)
    settled = get_result(task_id)
    return bool(settled) if settled is not None else False
