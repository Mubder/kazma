"""A turn that is open with nobody working on it is an invariant violation.

Every failure in this area has reached the operator as a symptom rather than an
alert. A turn reopened by a straggling write sat as "Action required" until
somebody noticed; a backup dropped two thirds of its data behind a sentence
that read as minor. In both cases the system knew — it logged a line — and
nobody was told.

`reply_sink` now makes the corrupting write impossible (the lifecycle is a
join-semilattice; see `tests/test_turn_state_confluence.py`). This module is
the other half, and the more durable one: it does not care *why* a turn is
stuck. Whatever future bug hangs a turn — a crashed pump, a dropped socket, an
exception between the last flush and the close — the invariant is the same and
it fires.

**The invariant.** A reply turn may be open only while something is working on
it, or while it waits for a human. Open with neither is impossible.

So a turn is reported only when all three hold:

* it has been open, unchanged, for longer than the grace period;
* no task is registered for its thread (``active_turns.is_turn_running``);
* it is not parked on a HITL approval, which is a legitimate open turn with
  its own timeout watchdog.

Healing is deliberately separate from reporting and off by default. Closing an
abandoned turn unsticks the page, but a close is what the bug did wrong, and a
watchdog that guesses can do the same damage the join was added to prevent.
Turn it on with ``KAZMA_TURN_LIVENESS_HEAL=1`` once the alerts have shown what
actually accumulates here.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

__all__ = [
    "check_once",
    "start_turn_liveness_watchdog",
    "stop_turn_liveness_watchdog",
]

logger = logging.getLogger(__name__)

_SCAN_INTERVAL_SECONDS = 30.0

#: How long a turn may sit open with nothing working on it before it is
#: reported. Generous on purpose: a slow tool, a long model call and a
#: reconnecting socket are all normal, and a watchdog that cries during
#: ordinary work teaches operators to ignore it.
_DEFAULT_GRACE_SECONDS = 600.0

_task: asyncio.Task[None] | None = None

#: thread_id -> when this watchdog FIRST saw the turn idle. Owned here, not in
#: reply_sink: the sink stores no timestamps and adding one to its hot path to
#: serve a background sweep would be the wrong trade.
_idle_since: dict[str, float] = {}

#: Threads already reported, so a stuck turn pages once rather than every scan.
#: `ops_alerts` throttles too; this also stops the log filling up.
_reported: set[str] = set()


def _grace_seconds() -> float:
    raw = (os.environ.get("KAZMA_TURN_LIVENESS_GRACE_S") or "").strip()
    try:
        return max(60.0, float(raw)) if raw else _DEFAULT_GRACE_SECONDS
    except ValueError:
        return _DEFAULT_GRACE_SECONDS


def _healing_enabled() -> bool:
    return (os.environ.get("KAZMA_TURN_LIVENESS_HEAL") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _is_running(thread_id: str) -> bool:
    """Is a live task registered for this thread?"""
    try:
        from kazma_ui.active_turns import is_turn_running

        return bool(is_turn_running(thread_id))
    except Exception:
        # Unknown means "assume busy". A watchdog that reports because it
        # could not tell is worse than one that stays quiet.
        logger.debug("[turn-liveness] liveness check failed", exc_info=True)
        return True


def _is_awaiting_human(thread_id: str) -> bool:
    """Is the turn parked on an approval?

    That is a legitimately open turn — the HITL timeout watchdog owns it, and
    reporting it here would page the operator for a card they are looking at.
    """
    try:
        from kazma_ui.hitl_status import persisted_hitl_for_thread

        part = persisted_hitl_for_thread(thread_id) or {}
        state = str(part.get("state") or "").strip().lower()
        if not part:
            return False
        # Anything already settled ("approved"/"denied") does not hold a turn
        # open; only an unresolved card does.
        return state in ("", "pending", "awaiting", "awaiting_approval")
    except Exception:
        logger.debug("[turn-liveness] HITL check failed", exc_info=True)
        return True  # assume parked rather than page wrongly


def check_once(*, now: float | None = None) -> list[dict[str, Any]]:
    """One sweep. Returns the turns reported this pass (usually empty).

    Pure enough to call from a test: it reads a snapshot, consults two
    predicates, and alerts. Never raises — a watchdog that dies takes the
    warning with it.
    """
    stamp = time.time() if now is None else now
    grace = _grace_seconds()
    reported: list[dict[str, Any]] = []

    try:
        from kazma_ui.reply_sink import close_reply_turn, open_turns_snapshot

        snapshot = open_turns_snapshot()
    except Exception:
        logger.debug("[turn-liveness] snapshot failed", exc_info=True)
        return reported

    # Forget threads that are no longer open at all.
    for gone in set(_idle_since) - set(snapshot):
        _idle_since.pop(gone, None)
        _reported.discard(gone)

    for thread_id, turn_id in snapshot.items():
        if _is_running(thread_id) or _is_awaiting_human(thread_id):
            # Working or waiting on a human: both legitimate. Reset the clock
            # so a turn that resumes does not inherit an old idle stamp.
            _idle_since.pop(thread_id, None)
            _reported.discard(thread_id)
            continue

        first_seen = _idle_since.setdefault(thread_id, stamp)
        idle_for = stamp - first_seen
        if idle_for < grace or thread_id in _reported:
            continue

        _reported.add(thread_id)
        entry = {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "idle_seconds": round(idle_for, 1),
            "healed": False,
        }
        logger.error(
            "[turn-liveness] turn open with nothing running it: thread=%s "
            "turn=%s idle=%.0fs",
            thread_id[:12], str(turn_id)[:12], idle_for,
        )
        try:
            from kazma_core.observability.ops_alerts import alert

            alert(
                "turn.stuck_open",
                "A chat turn is stuck open with nothing working on it.",
                (
                    f"thread={thread_id[:12]} turn={str(turn_id)[:12]} "
                    f"idle={idle_for:.0f}s. The page will show this turn as "
                    "still running and never present a reply. No task is "
                    "registered for the thread and it is not waiting on an "
                    "approval."
                ),
                severity="error",
            )
        except Exception:
            logger.debug("[turn-liveness] alert failed", exc_info=True)

        if _healing_enabled():
            try:
                close_reply_turn(thread_id)
                entry["healed"] = True
                logger.warning(
                    "[turn-liveness] closed abandoned turn thread=%s", thread_id[:12]
                )
            except Exception:
                logger.debug("[turn-liveness] heal failed", exc_info=True)

        reported.append(entry)

    return reported


async def _loop() -> None:
    logger.info(
        "[turn-liveness] watchdog started (grace=%.0fs, heal=%s)",
        _grace_seconds(), _healing_enabled(),
    )
    while True:
        try:
            await asyncio.sleep(_SCAN_INTERVAL_SECONDS)
            check_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[turn-liveness] sweep failed", exc_info=True)


def start_turn_liveness_watchdog() -> None:
    """Start the sweep. Idempotent."""
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop())


async def stop_turn_liveness_watchdog() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except (asyncio.CancelledError, Exception):
        pass
    _task = None
    _idle_since.clear()
    _reported.clear()
