"""Shared HITL approval wait state for multi-replica deployments.

In-process ``asyncio.Event`` maps break when the replica that posted the
approval card is not the replica that receives the button callback.
This module dual-writes pending approvals to ConfigStore (SQLite or
Postgres via ConfigStore backend) so any replica can:

* ``create_pending`` when posting an approval card
* ``resolve`` when a platform callback arrives
* ``wait`` by polling until resolved or timeout

Local Events are still used for low-latency same-process resolution.
"""

from __future__ import annotations

import asyncio
import logging
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
        _local_events.pop(task_id, None)
        _local_results.pop(task_id, None)
    try:
        loop = asyncio.get_running_loop()
        loop.call_later(delay, _evict)
    except RuntimeError:
        _evict()  # no running loop (sync context) — evict eagerly


def create_pending(task_id: str, *, meta: dict[str, Any] | None = None) -> None:
    """Register a pending approval (local event + durable row)."""
    if not task_id:
        return
    _local_results.pop(task_id, None)
    ev = _local_events.get(task_id)
    if ev is None:
        ev = asyncio.Event()
        _local_events[task_id] = ev
    else:
        ev.clear()

    payload = {
        "status": "pending",
        "created_at": time.time(),
        "result": None,
        **(meta or {}),
    }
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().set(_key(task_id), payload, category="swarm")
    except Exception as exc:
        logger.debug("[shared_approvals] create durable failed: %s", exc)


def resolve(task_id: str, approved: bool) -> None:
    """Resolve an approval from any replica/platform callback."""
    if not task_id:
        return
    _local_results[task_id] = bool(approved)
    ev = _local_events.get(task_id)
    if ev is not None:
        ev.set()

    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        prev = cs.get(_key(task_id)) or {}
        if not isinstance(prev, dict):
            prev = {}
        prev.update(
            {
                "status": "resolved",
                "result": bool(approved),
                "resolved_at": time.time(),
            }
        )
        cs.set(_key(task_id), prev, category="swarm")
    except Exception as exc:
        logger.debug("[shared_approvals] resolve durable failed: %s", exc)

    # Evict the local event/result after waiters have drained.
    _schedule_eviction(task_id)


def get_result(task_id: str) -> bool | None:
    """Return True/False if resolved, else None."""
    if task_id in _local_results:
        return _local_results[task_id]
    try:
        from kazma_core.config_store import get_config_store

        raw = get_config_store().get(_key(task_id))
        if isinstance(raw, dict) and raw.get("status") == "resolved":
            return bool(raw.get("result"))
    except Exception:
        pass
    return None


async def wait_for_resolution(task_id: str, timeout: float = 60.0) -> bool:
    """Wait until resolved. Returns approved bool (False on timeout)."""
    if not task_id:
        return False

    # Fast path: already resolved
    existing = get_result(task_id)
    if existing is not None:
        return existing

    create_pending(task_id)  # ensure local event exists
    ev = _local_events[task_id]
    deadline = time.monotonic() + max(0.5, float(timeout))
    poll = 0.15

    while time.monotonic() < deadline:
        # Local event (same process callback)
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

        # Cross-replica: poll ConfigStore
        result = get_result(task_id)
        if result is not None:
            return result

    logger.warning("[shared_approvals] timeout task_id=%s", task_id)
    # Mark timed-out so other replicas stop waiting
    if get_result(task_id) is None:
        resolve(task_id, False)
    return False
