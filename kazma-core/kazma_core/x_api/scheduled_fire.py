"""Deterministic fire loop for scheduled X posts.

Polls the :class:`~kazma_core.x_api.schedule.XScheduledStore` for due posts and
fires ``POST /2/tweets`` directly via :class:`XClient` — no LangGraph, no LLM.
The operator approved the exact draft at booking time (always-HITL), so this
loop simply executes that approval at the appointed moment.

Failure policy (the double-post guard):
  * HTTP 429 → the post was provably NOT created, so it is deferred by the
    ``Retry-After`` window (bounded by ``_MAX_ATTEMPTS``).
  * Any other error → the post is marked ``failed`` and the operator is
    notified. It is NEVER auto-retried, because a timeout / mid-stream drop
    leaves it unknown whether the post reached X (a retry could double-post).
    This mirrors ``XClient``'s "writes are not retried" contract.

Missed posts (server was down at fire time) are caught up once at boot — the
first poll fires anything already due. X cannot hold the schedule, so this is
the honest limitation: a post fires only if Kazma is up at (or restarted
after) its fire time.
"""

from __future__ import annotations

import asyncio
import logging

from kazma_core.x_api.client import XApiError, XClient
from kazma_core.x_api.config import get_x_config
from kazma_core.x_api.ledger import get_ledger
from kazma_core.x_api.schedule import (
    STATUS_PENDING,
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
    while True:
        try:
            if x_schedule_enabled():
                await _fire_due_posts()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("[x-schedule] poll error (loop continues)")
        await asyncio.sleep(poll_interval)


async def _fire_due_posts() -> None:
    store = get_x_scheduled_store()
    due = store.list_due()
    for post in due:
        try:
            await _fire_post(post)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("[x-schedule] unexpected error firing post %s", post.id)
            store.mark_failed(post.id, "internal error")


async def _fire_post(post: ScheduledXPost) -> None:
    store = get_x_scheduled_store()

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
    store = get_x_scheduled_store()

    cfg = get_x_config()
    if not cfg.can_post():
        store.mark_failed(
            post.id,
            "X connector disabled or unconfigured at fire time (KAZMA_X_POST / Settings → X).",
        )
        await _notify_failure(post, "X connector was disabled at fire time.")
        return

    # Re-check right before sending: if the operator cancelled this post in the
    # window between the poll and now, do NOT publish it.
    current = store.get(post.id)
    if current is None or current.status != STATUS_PENDING:
        return

    client = XClient(cfg.credentials)
    try:
        tweet = await client.create_tweet(post.text, reply_to_id=post.reply_to_id)
    except XApiError as exc:
        if exc.status == 429:
            attempts = store.bump_attempts(post.id)
            if attempts >= _MAX_ATTEMPTS:
                store.mark_failed(post.id, f"Rate-limited repeatedly ({attempts} attempts).")
                await _notify_failure(post, "X kept rate-limiting the scheduled post.")
            else:
                wait = _parse_retry_wait(exc)
                import time as _time

                store.defer(post.id, _time.time() + wait)
                logger.warning(
                    "[x-schedule] post %s rate-limited; deferred %.0fs (attempt %d/%d)",
                    post.id, wait, attempts, _MAX_ATTEMPTS,
                )
            return
        # Ambiguous / permanent failure — do NOT retry (double-post guard).
        store.mark_failed(post.id, str(exc))
        await _notify_failure(post, str(exc))
        return

    tweet_id = str(tweet.get("id") or "")
    store.mark_fired(post.id, tweet_id)
    try:
        get_ledger().record(tweet_id=tweet_id, text=post.text, handle=cfg.handle)
    except Exception:  # noqa: BLE001
        logger.warning("[x-schedule] ledger record failed for %s", tweet_id, exc_info=True)
    logger.info("[x-schedule] fired scheduled post %s -> tweet %s", post.id, tweet_id)
    await _notify_success(post, tweet_id)


def _parse_retry_wait(exc: XApiError) -> float:
    """Extract a Retry-After hint from the error message, else a default."""
    msg = str(exc)
    if "Retry-After" in msg:
        try:
            tail = msg.split("Retry-After", 1)[1]
            digits = "".join(ch for ch in tail.split(")")[0] if ch.isdigit())
            if digits:
                return max(1.0, float(digits))
        except Exception:  # noqa: BLE001
            pass
    return _DEFAULT_RATE_WAIT


async def _notify_success(post: ScheduledXPost, tweet_id: str) -> None:
    if not post.delivery_target or ":" not in post.delivery_target:
        return
    url = f"https://x.com/i/web/status/{tweet_id}" if tweet_id else ""
    text = f"✅ Scheduled post published on X.\n{post.text}\n{url}".strip()
    await _deliver(post.delivery_target, text)


async def _notify_failure(post: ScheduledXPost, reason: str) -> None:
    logger.warning("[x-schedule] post %s failed: %s", post.id, reason)
    if not post.delivery_target or ":" not in post.delivery_target:
        return
    text = (
        "⚠️ A scheduled X post FAILED and was NOT published.\n"
        f"Draft: {post.text[:200]}\nReason: {reason[:300]}\n"
        "You can re-book it from the Scheduled page or chat."
    )
    await _deliver(post.delivery_target, text)


async def _deliver(target: str, text: str) -> None:
    try:
        from kazma_core.tools.send_message import send_message

        platform = target.split(":", 1)[0]
        await send_message(target, text, backend=platform)
    except Exception:  # noqa: BLE001
        logger.critical("[x-schedule] could not deliver notification to %s", target, exc_info=True)
