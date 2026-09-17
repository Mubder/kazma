"""Poll X mentions and route summons into the auto-reply pipeline.

Modelled on :mod:`kazma_core.x_api.scheduled_fire`, with three differences
that come straight from what that loop taught us:

**It installs a tenant before touching config.** Provider and connector
credentials are tenant-scoped vault rows; a loop with no ContextVar resolves
none of them. Measured on the live install 2026-09-17: ``ConfigStore.get()``
returns 4/4 X credentials with ``tenant="default"`` installed and **0/4**
without. Cron and connector-health both shipped this bug before this loop
existed; it is not a theoretical hazard.

**Read quota is the budget, not rate limit.** The Free tier cannot call this
endpoint at all, and the paid tiers meter *tweets read* per month. Poll
interval is operator config with a 60s floor, and ``since_id`` means a quiet
account costs one near-empty response per poll rather than a re-read of the
window.

**A summon is not a mention.** Most mentions must be ignored. Four gates
before anything is drafted: the author is on the allowlist, the trigger
phrase is present (when configured), the mention actually replies to
something, and the parent is not the operator's own post. Everything that
survives goes to :func:`kazma_core.x_api.reply.handle_summon`, which claims
it idempotently before spending a model call.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "start_mentions_loop",
    "stop_mentions_loop",
    "get_mentions_task",
    "poll_once",
]

_loop_task: asyncio.Task | None = None
_DEFAULT_POLL = 600.0
#: Consecutive failures before the loop backs off hard. A 429 or a revoked
#: token should not mean one doomed request every ten minutes forever.
_MAX_CONSECUTIVE_ERRORS = 5
_BACKOFF_S = 3600.0


def get_mentions_task() -> asyncio.Task | None:
    return _loop_task


async def start_mentions_loop(poll_interval: float | None = None) -> None:
    """Start the poller (idempotent). Called once from the app lifespan."""
    global _loop_task
    if _loop_task is not None and not _loop_task.done():
        return
    from kazma_core.background import spawn_background

    _loop_task = spawn_background(_loop(poll_interval), name="x-mentions-poll")
    logger.info("[x-mentions] poller started")


async def stop_mentions_loop() -> None:
    global _loop_task
    if _loop_task is not None:
        _loop_task.cancel()
        try:
            await _loop_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _loop_task = None
        logger.info("[x-mentions] poller stopped")


async def _loop(poll_interval: float | None) -> None:
    errors = 0
    while True:
        wait = poll_interval or _DEFAULT_POLL
        try:
            from kazma_core.tenant_context import tenant_scope

            # Everything below reads tenant-scoped config/credentials.
            with tenant_scope("default"):
                from kazma_core.x_api.stance import get_reply_config

                cfg = get_reply_config()
                if poll_interval is None:
                    wait = float(cfg.poll_interval_s)
                if cfg.can_draft():
                    await poll_once(cfg=cfg)
            errors = 0
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            errors += 1
            logger.exception("[x-mentions] poll error (%d consecutive)", errors)
            if errors >= _MAX_CONSECUTIVE_ERRORS:
                logger.error(
                    "[x-mentions] %d consecutive failures — backing off %.0fs. "
                    "Check the X tier (Free cannot read mentions) and the tokens.",
                    errors, _BACKOFF_S,
                )
                wait = _BACKOFF_S
                errors = 0
        await asyncio.sleep(wait)


def _index_users(includes: dict[str, Any]) -> dict[str, dict[str, Any]]:
    users = includes.get("users")
    if not isinstance(users, list):
        return {}
    return {str(u.get("id")): u for u in users if isinstance(u, dict) and u.get("id")}


def _parent_id(tweet: dict[str, Any]) -> str:
    """The tweet this mention replies to, or "" if it is a standalone post."""
    refs = tweet.get("referenced_tweets")
    if not isinstance(refs, list):
        return ""
    for ref in refs:
        if isinstance(ref, dict) and ref.get("type") == "replied_to":
            return str(ref.get("id") or "")
    return ""


def _followers(user: dict[str, Any] | None) -> int | None:
    if not isinstance(user, dict):
        return None
    metrics = user.get("public_metrics")
    if isinstance(metrics, dict) and "followers_count" in metrics:
        try:
            return int(metrics["followers_count"])
        except (TypeError, ValueError):
            return None
    return None


async def poll_once(cfg: Any = None) -> list[dict[str, Any]]:
    """One poll cycle. Returns a summary row per mention considered.

    Separated from the loop so tests and the ``/x poll`` command can drive a
    single cycle without a running task.
    """
    from kazma_core.x_api.client import XApiError, XClient
    from kazma_core.x_api.config import get_x_config
    from kazma_core.x_api.reply import handle_summon
    from kazma_core.x_api.reply_store import get_reply_store
    from kazma_core.x_api.stance import get_reply_config

    cfg = cfg or get_reply_config()
    xcfg = get_x_config()
    if not xcfg.can_post():
        logger.debug("[x-mentions] X connector cannot post — not polling")
        return []

    client = XClient(xcfg.credentials)
    store = get_reply_store()

    try:
        me = await client.verify_credentials()
        uid = str(me.get("id") or "")
        my_handle = str(me.get("username") or "").lower()
    except XApiError as exc:
        logger.warning("[x-mentions] identity lookup failed: %s", exc)
        return []
    if not uid:
        return []

    since_id = await asyncio.to_thread(store.get_since_id)
    try:
        tweets, includes = await client.get_mentions(uid, since_id=since_id)
    except XApiError as exc:
        if exc.status in (401, 403):
            logger.error(
                "[x-mentions] HTTP %d reading mentions. The Free tier cannot "
                "read this endpoint — check the X API plan. Poller will keep "
                "retrying; disable it with connectors.x.reply.enabled=false.",
                exc.status,
            )
        raise

    if not tweets:
        return []

    users = _index_users(includes)
    results: list[dict[str, Any]] = []
    newest = since_id

    # Oldest first, so a partial failure leaves the cursor at the last
    # mention actually processed rather than skipping the rest of the batch.
    for tweet in sorted(tweets, key=lambda t: str(t.get("id") or "")):
        tid = str(tweet.get("id") or "")
        if not tid:
            continue
        newest = max(newest, tid, key=lambda s: (len(s), s))

        text = str(tweet.get("text") or "")
        author = users.get(str(tweet.get("author_id") or ""))
        summoner = str((author or {}).get("username") or "").lower()

        def _skip(reason: str) -> None:
            results.append({"mention": tid, "action": "skipped", "reason": reason})

        if summoner and summoner == my_handle:
            _skip("own tweet")
            continue
        if not cfg.is_summoner(summoner):
            _skip(f"@{summoner} not an allowlisted summoner")
            continue
        if cfg.trigger and cfg.trigger not in text.lower():
            _skip("trigger phrase absent")
            continue

        parent_id = _parent_id(tweet)
        if not parent_id:
            _skip("mention is not a reply — nothing to react to")
            continue
        if await asyncio.to_thread(store.seen, tid):
            _skip("already handled")
            continue

        # Fetch the post being replied to. One read per genuine summon,
        # which is why every cheap gate above runs first.
        try:
            parent, p_includes = await client.get_tweet(parent_id)
        except XApiError as exc:
            _skip(f"parent unreadable: {exc}")
            continue
        parent_text = str(parent.get("text") or "")
        p_users = _index_users(p_includes)
        p_author = p_users.get(str(parent.get("author_id") or ""))
        parent_handle = str((p_author or {}).get("username") or "").lower()

        if parent_handle and parent_handle == my_handle:
            _skip("parent is our own post")
            continue

        result = await handle_summon(
            summon_id=tid,
            parent_id=parent_id,
            parent_text=parent_text,
            parent_handle=parent_handle,
            summoner=summoner,
            target_followers=_followers(p_author),
            cfg=cfg,
            # The mention carries the emoji that dials the tone.
            summon_text=text,
        )
        row = result.to_dict()
        row["mention"] = tid
        results.append(row)

        if result.action == "awaiting_approval":
            _notify_draft(result, parent_handle=parent_handle, summoner=summoner)

    if newest and newest != since_id:
        await asyncio.to_thread(store.set_since_id, newest)
    return results


def _notify_draft(result: Any, *, parent_handle: str, summoner: str) -> None:
    """Push the held draft to the operator. Best-effort; never raises.

    ``draft`` mode is worthless if the draft sits in a database nobody looks
    at, so this goes out on the ops-alert bus (Telegram when configured).
    Approval itself happens through ``/x approve <id>``.
    """
    try:
        from kazma_core.observability.ops_alerts import alert

        alert(
            key=f"x_reply_draft:{result.parent_id}",
            title="X reply drafted — approval needed",
            detail=(
                f"subject: {result.subject_id}\n"
                f"to: @{parent_handle or '?'} (summoned by @{summoner or '?'})\n"
                f"post: https://x.com/i/web/status/{result.parent_id}\n\n"
                f"{result.draft}\n\n"
                f"Approve with:  /x approve {result.summon_id}"
            ),
            severity="info",
            cooldown_s=0,
        )
    except Exception:  # noqa: BLE001
        logger.debug("[x-mentions] draft notification failed", exc_info=True)
