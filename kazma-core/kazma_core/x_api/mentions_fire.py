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

**A summon is a mention that passed the cheap gates.** Allowlist (or
``anyone``), optional trigger phrase, not our own tweet. A standalone
``@handle 😂`` is a summon — we reply to that tweet. A reply under someone
else's post still reacts to the parent. Everything that survives goes to
:func:`kazma_core.x_api.reply.handle_summon`, which claims it idempotently
before spending a model call.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "start_mentions_loop",
    "stop_mentions_loop",
    "ensure_mentions_loop",
    "get_mentions_task",
    "poll_once",
]

_loop_task: asyncio.Task | None = None
#: (user_id, handle) for the connected account. It cannot change without
#: new credentials, but the poller called verify_credentials on EVERY
#: cycle -- a request per 10 minutes forever, and two identical rows in
#: the audit log for every poll, burying the posts.
_identity: tuple[str, str] | None = None
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
    global _loop_task, _identity
    _identity = None
    if _loop_task is not None:
        _loop_task.cancel()
        try:
            await _loop_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _loop_task = None
        logger.info("[x-mentions] poller stopped")


async def ensure_mentions_loop() -> bool:
    """Start or stop the poller to match live config. Returns whether it is running.

    Called from boot AND from Settings save, so turning auto-reply on in a
    running server no longer needs a restart. Config is re-read under
    tenant ``default`` because connector credentials are tenant-scoped.
    """
    from kazma_core.tenant_context import tenant_scope
    from kazma_core.x_api.stance import get_reply_config

    with tenant_scope("default"):
        cfg = get_reply_config()
    if cfg.can_draft():
        await start_mentions_loop()
        task = get_mentions_task()
        return task is not None and not task.done()
    await stop_mentions_loop()
    return False


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

    global _identity
    if _identity is None:
        try:
            me = await client.verify_credentials()
            _identity = (
                str(me.get("id") or ""),
                str(me.get("username") or "").lower(),
            )
        except XApiError as exc:
            logger.warning("[x-mentions] identity lookup failed: %s", exc)
            return []
    uid, my_handle = _identity
    if not uid:
        _identity = None
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
        # The commonest state, and it was invisible. "Polled, nothing new" and
        # "never polled" are different problems with the same silence.
        logger.info(
            "[x-mentions] polled — no new mentions since %s",
            since_id or "(start)",
        )
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

        async def _skip(reason: str, *, persist: bool = True) -> None:
            # Logged AND stored. A skip that only lived in the log made
            # "I mentioned it and nothing happened" unanswerable from
            # Conversations, which is the surface that exists to answer it.
            logger.info("[x-mentions] %s skipped: %s", tid, reason)
            results.append({"mention": tid, "action": "skipped", "reason": reason})
            if not persist:
                return
            if await asyncio.to_thread(store.seen, tid):
                return
            claimed = await asyncio.to_thread(
                lambda: store.claim(
                    summon_id=tid, parent_id="",
                    target_handle="", summoner=summoner,
                    parent_text="", summon_text=text,
                )
            )
            if claimed:
                await asyncio.to_thread(store.mark_skipped, tid, reason)

        if summoner and summoner == my_handle:
            await _skip("own tweet", persist=False)
            continue
        if not cfg.is_summoner(summoner):
            await _skip(f"@{summoner} not an allowlisted summoner")
            continue
        if cfg.trigger and cfg.trigger not in text.lower():
            await _skip("trigger phrase absent")
            continue

        parent_id = _parent_id(tweet)
        if await asyncio.to_thread(store.seen, tid):
            await _skip("already handled", persist=False)
            continue

        # A standalone `@KazmaAI 😂` is still a summon — reply TO that tweet,
        # reacting to its text. The old "must be a reply under someone else"
        # rule is what made mention-the-bot feel broken.
        parent_text = text
        parent_handle = summoner
        target_followers = None
        p_author: dict[str, Any] | None = author if isinstance(author, dict) else None

        if parent_id:
            try:
                parent, p_includes = await client.get_tweet(parent_id)
            except XApiError as exc:
                await _skip(f"parent unreadable: {exc}")
                continue
            parent_text = str(parent.get("text") or "") or text
            p_users = _index_users(p_includes)
            p_author = p_users.get(str(parent.get("author_id") or ""))
            parent_handle = str((p_author or {}).get("username") or summoner).lower()
            # Replying under our own post is a conversation with the bot,
            # not a loop: we never @-mention ourselves in drafts, and
            # summoner == me is already skipped above.
            target_followers = _followers(p_author)
        else:
            parent_id = tid

        result = await handle_summon(
            summon_id=tid,
            parent_id=parent_id,
            parent_text=parent_text,
            parent_handle=parent_handle,
            summoner=summoner,
            target_followers=target_followers,
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

    acted = [r for r in results if r.get("action") not in ("skipped", None)]
    logger.info(
        "[x-mentions] cycle done: %d mention(s), %d acted on, %d skipped, "
        "cursor now %s",
        len(tweets), len(acted), len(results) - len(acted), newest or since_id,
    )
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
