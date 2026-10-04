"""Deterministic fire loop for scheduled X posts.

Polls the :class:`~kazma_core.x_api.schedule.XScheduledStore` for due posts and
fires through the durable publication service — no LangGraph, no LLM.
The operator approved the exact draft at booking time (always-HITL), so this
loop simply executes that approval at the appointed moment.

Failure policy (the double-post guard):
  * HTTP 429 → the post was provably NOT created, so it is deferred by the
    ``Retry-After`` window (bounded by ``_MAX_ATTEMPTS``).
  * Other failures retain typed rejected/unsent or unknown state and the
    operator is notified. It is NEVER auto-retried, because a timeout / mid-stream drop
    leaves it unknown whether the post reached X (a retry could double-post).
    This mirrors ``XClient``'s "writes are not retried" contract.

Missed publications are held after a five-minute grace period. The operator
must review and reschedule them; restart never silently catches up a campaign.
"""

from __future__ import annotations

import asyncio
import logging

from kazma_core.x_api import config as _config
from kazma_core.x_api.client import XApiError
from kazma_core.x_api.schedule import (
    STATUS_PENDING,
    STATUS_SENDING,
    ScheduledXPost,
    get_x_scheduled_store,
    x_schedule_enabled,
)

logger = logging.getLogger(__name__)

__all__ = [
    "start_scheduled_x_loop",
    "stop_scheduled_x_loop",
    "get_scheduled_x_task",
]

_POLL_INTERVAL = 30.0
_MAX_ATTEMPTS = 8  # bound 429 deferrals so a stuck post eventually fails
_DEFAULT_RATE_WAIT = 60.0

_loop_task: asyncio.Task | None = None


def get_scheduled_x_task() -> asyncio.Task | None:
    return _loop_task


async def start_scheduled_x_loop(poll_interval: float = _POLL_INTERVAL) -> None:
    """Start the fire loop (idempotent). Called once from ``app.py`` startup."""
    global _loop_task
    if _loop_task is not None and not _loop_task.done():
        return
    _loop_task = asyncio.create_task(_loop(poll_interval), name="x-scheduled-fire")
    logger.info("[x-schedule] fire loop started (poll_interval=%.0fs)", poll_interval)


async def stop_scheduled_x_loop() -> None:
    global _loop_task
    if _loop_task is not None:
        _loop_task.cancel()
        try:
            await _loop_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _loop_task = None
        logger.info("[x-schedule] fire loop stopped")


async def _loop(poll_interval: float) -> None:
    from kazma_core.x_api.health import record_cycle

    while True:
        try:
            await _fire_due_posts()
            await asyncio.to_thread(record_cycle, "scheduler", success=True, interval=poll_interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[x-schedule] poll error (loop continues)")
            await asyncio.to_thread(record_cycle, "scheduler", success=False, interval=poll_interval)
        await asyncio.sleep(poll_interval)


async def _fire_due_posts() -> None:
    from kazma_core.x_api.publication_service import fire_due_operations

    await fire_due_operations()
    if not x_schedule_enabled():
        return
    # Every store call is a SQLite round trip: off the loop. list_due was
    # caught holding the loop in six loop-stall dumps (2026-09-20..23).
    store = await asyncio.to_thread(get_x_scheduled_store)
    due = await asyncio.to_thread(store.list_due)
    for post in due:
        try:
            await _fire_post(post)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[x-schedule] unexpected error firing post %s", post.id)
            current = await asyncio.to_thread(store.get, post.id, tenant_id=post.tenant_id)
            if current and current.status == STATUS_SENDING:
                await asyncio.to_thread(store.mark_unknown, post.id, "internal error after send claim; verify on X")
            elif current and current.status == STATUS_PENDING:
                await asyncio.to_thread(store.mark_failed, post.id, "internal error before send")


async def _fire_post(post: ScheduledXPost) -> None:
    # The post carries the tenant it was booked under. A background loop
    # has NO request context, so tenant-scoped vault entries (X OAuth keys
    # saved via Settings live under tenant 'default') were invisible here —
    # every scheduled fire failed with "connector disabled at fire time"
    # while the same credentials worked from chat (2026-09-03). Same
    # pattern as the cron delivery_target fix (§16): bind context at
    # schedule time, restore it at fire time.
    _tenant_token = None
    if post.tenant_id:
        from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id

        _tenant_token = set_current_tenant_id(post.tenant_id)
    try:
        await _fire_post_inner(post)
    finally:
        if _tenant_token is not None:
            from kazma_core.tenant_context import reset_current_tenant_id

            reset_current_tenant_id(_tenant_token)


async def _fire_post_inner(post: ScheduledXPost) -> None:
    store = await asyncio.to_thread(get_x_scheduled_store)

    cfg = await asyncio.to_thread(_config.get_x_config)
    if not cfg.can_post():
        await asyncio.to_thread(
            store.mark_failed,
            post.id,
            "X connector disabled or unconfigured at fire time (KAZMA_X_POST / Settings → X).",
        )
        await _notify_failure(post, "X connector was disabled at fire time.")
        return

    # Re-check right before sending: if the operator cancelled this post in the
    # window between the poll and now, do NOT publish it.
    current = await asyncio.to_thread(store.claim_send, post.id, tenant_id=post.tenant_id)
    if current is None:
        return
    post = current
    if not x_schedule_enabled():
        await asyncio.to_thread(store.mark_failed, post.id, "scheduling disabled before send")
        return

    from kazma_core.x_api.publication_service import fire_legacy_schedule

    try:
        ok, result = await fire_legacy_schedule(post, cfg)
    except asyncio.CancelledError:
        await asyncio.shield(asyncio.to_thread(store.mark_unknown, post.id, "send interrupted; verify on X before any resend"))
        raise
    if result.get("replayed"):
        return
    if ok:
        await _notify_success(post, result["tweet_id"])
    elif result.get("state") == "deferred":
        return
    else:
        # Managed projections belong to the durable operation. A failed adoption
        # has no operation and is provably unsent; retain that distinction.
        if not result.get("operation_id"):
            await asyncio.to_thread(store.mark_failed, post.id, result.get("error", "Legacy publication could not be adopted."))
        await _notify_failure(post, result.get("error", "Publication needs review."), unknown=result.get("outcome") == "unknown")


def _parse_retry_wait(exc: XApiError) -> float:
    """Use typed hints: a reset epoch is not a duration. Bound each wait."""
    import math
    import time

    waits = []
    for value, absolute in (
        (exc.retry_after_seconds, False), (exc.rate_limit_reset, True),
    ):
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value >= 0:
            waits.append(value - time.time() if absolute else value)
    return max(1.0, min(86400.0, max(waits))) if waits else _DEFAULT_RATE_WAIT


async def _notify_success(post: ScheduledXPost, tweet_id: str) -> None:
    url = f"https://x.com/i/web/status/{tweet_id}" if tweet_id else ""
    text = f"✅ Scheduled post published on X.\n{post.text}\n{url}".strip()
    await _deliver(post, text)


async def _notify_failure(post: ScheduledXPost, reason: str, *, unknown: bool = False) -> None:
    logger.warning("[x-schedule] post %s failed: %s", post.id, reason)
    text = (
        ("⚠️ A scheduled X post has an UNKNOWN publication outcome.\n" if unknown else
         "⚠️ A scheduled X post was not published.\n")
        +
        f"Draft: {post.text[:200]}\nReason: {reason[:300]}\n"
        + ("Verify on X before any new send. Do not re-book this draft." if unknown else
           "Review the failure before booking a new post.")
    )
    await _deliver(post, text)


async def _deliver(post: ScheduledXPost, text: str) -> None:
    """Every fire outcome is announced. Explicit delivery target first
    (captured at booking); otherwise fan out through the SwarmMessageBus
    to every configured platform — the same route the lifecycle notifier
    uses (§17A). A post booked from a context with no target used to fire
    completely silently, success AND failure (2026-09-03)."""
    target = post.delivery_target or ""
    if target and ":" in target:
        try:
            from kazma_core.tools.send_message import send_failed, send_message

            platform = target.split(":", 1)[0]
            result = await send_message(target, text, backend=platform)
            if not send_failed(result):
                return
            # Refused: fall through to every connected chat app (it returned
            # here as if delivered until 2026-10-02).
            logger.critical(
                "[x-schedule] could not deliver notification to %s: %s", target, result,
            )
        except Exception:
            logger.critical(
                "[x-schedule] could not deliver notification to %s",
                target, exc_info=True,
            )
    try:
        import asyncio as _aio

        from kazma_core.swarm.bus import BusMessage, NullBusAdapter, get_message_bus

        bus = get_message_bus()
        adapter = bus.adapter
        if isinstance(adapter, NullBusAdapter):
            # No platform configured — nothing to announce to. Not an
            # error: single-operator headless installs still fire posts.
            return
        took = await _aio.wait_for(
            adapter.send(
                BusMessage(
                    worker_name="Kazma",
                    worker_role="system",
                    content=text[:4000],
                    level="info",
                )
            ),
            timeout=15.0,
        )
        if not took:
            # The senders report a refused send (2026-10-02); it used to pass
            # as delivered.
            logger.critical(
                "[x-schedule] no chat app took the notification for post %s", post.id,
            )
    except Exception:  # a notification must never fail the fire
        logger.critical(
            "[x-schedule] notification fan-out failed for post %s",
            post.id, exc_info=True,
        )
