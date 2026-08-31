"""Shared booking + publish logic for X posts.

``book_x_post`` is used by BOTH the chat tool (``x_schedule_post``) and the
Web Scheduled page / X Studio so the ToU policy is applied identically no
matter where a post is booked. HITL approval happens on the chat/agent path;
the human authoring a draft in the Web UI is the approval for that path.

``publish_x_post`` is the single immediate-post choke (chat ``x_post`` and
X Studio Post now). Policy first, then one ``POST /2/tweets``, then ledger.

Policy applied at booking:
  * connector enabled + configured (``can_post``) and scheduling not killed
  * ``evaluate_post`` fail-safes (length, mentions, cashtags, hashtags, dedupe,
    caps on already-fired posts)
  * future-only fire time
  * quota reservation — pending scheduled posts count toward the daily/monthly
    caps so the schedule cannot be used to exceed them
  * dedupe against pending drafts
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["book_x_post", "delete_x_post", "publish_x_post"]


def _public_x_error(exc: BaseException) -> str:
    """Chat/UI-safe failure text. Full exception stays in the log."""
    from kazma_core.x_api.client import XApiError

    if isinstance(exc, XApiError):
        return str(exc)
    return "X request failed. Details are in the server log."


# The shared timing parser accepts one RECURRING form ("daily at 9am") and
# collapses it to the next single occurrence. That is correct for cron,
# which reschedules itself after each run, and wrong for scheduled X posts,
# where the fire loop is deliberately one-shot.
_RECURRING_TIMING = re.compile(r"^\s*(daily|hourly|weekly|every)\b", re.IGNORECASE)


def _parse_when(when: str) -> float:
    """Parse a timing expression into a future epoch. Raises ValueError.

    Recurring expressions are refused rather than silently degraded. The
    parser would happily turn "daily at 9am" into tomorrow at 09:00, the
    post would fire once, and the operator would believe they had scheduled
    a daily tweet -- a silent single-shot dressed as a recurrence.

    Refusing is also the right answer on the merits, not just a limitation
    of the fire loop. The post ledger enforces a duplicate window
    (``connectors.x.duplicate_window_days``, 30 by default) and X itself
    rejects identical tweets, so an honestly-implemented daily repeat of the
    same text would start failing on its second run. Recurring identical
    posts are also the pattern automation rules flag.

    Recurring work that genuinely needs to repeat belongs on the cron path,
    which reschedules itself correctly after every fire.
    """
    from kazma_core.cron.scheduler import parse_timing

    raw = (when or "").strip()
    if _RECURRING_TIMING.match(raw):
        raise ValueError(
            f"'{raw}' is a recurring time, and scheduled X posts fire once. "
            "Give a one-off time instead ('2h', '2026-09-01T09:00', or an ISO "
            "timestamp). For something that must repeat, schedule a recurring "
            "task rather than a post -- X rejects identical tweets, so the "
            "same text cannot be republished on a schedule anyway."
        )

    dt = parse_timing(raw)
    epoch = dt.timestamp()
    if epoch <= time.time():
        raise ValueError("The requested time is in the past. Pick a future time.")
    return epoch


def book_x_post(
    *,
    text: str,
    when: str,
    reply_to_id: str = "",
    tenant_id: str = "default",
    thread_id: str = "",
    delivery_target: str = "",
) -> tuple[bool, dict[str, Any]]:
    """Validate + store a scheduled X post. Returns ``(ok, payload)``.

    ``payload`` is a JSON-ready dict: on success it carries ``scheduled``,
    ``id``, ``text``, ``fire_at``, ``tz``; on failure a single ``error``.
    """
    from kazma_core.x_api.config import get_x_config
    from kazma_core.x_api.ledger import get_ledger, text_hash
    from kazma_core.x_api.policy import evaluate_post
    from kazma_core.x_api.schedule import get_x_scheduled_store, x_schedule_enabled

    if not x_schedule_enabled():
        return False, {
            "error": "X post scheduling is disabled (KAZMA_X_SCHEDULE=0 or KAZMA_X_POST=0)."
        }

    cfg = get_x_config()
    if not cfg.can_post():
        return False, {
            "error": "X connector is not configured. Save the four OAuth 1.0a keys in Settings → X first."
        }

    body = (text or "").strip()
    decision = evaluate_post(body, cfg=cfg, reply_to_id=reply_to_id or "")
    if not decision.allow:
        return False, {"error": decision.reason}

    try:
        fire_at = _parse_when(when)
    except ValueError as exc:
        return False, {"error": str(exc)}

    store = get_x_scheduled_store()
    ledger = get_ledger()

    # Reserve quota: pending scheduled posts count toward the caps too.
    pending = store.count_pending(tenant_id=tenant_id)
    now = time.time()
    if ledger.count_since(now - 86400) + pending >= cfg.max_posts_per_day:
        return False, {
            "error": (
                f"Daily cap reached ({cfg.max_posts_per_day}/day) counting both "
                "posted and already-scheduled tweets."
            )
        }
    if ledger.count_since(now - 30 * 86400) + pending >= cfg.max_posts_per_month:
        return False, {
            "error": (
                f"Monthly cap reached ({cfg.max_posts_per_month}/30d) counting both "
                "posted and already-scheduled tweets."
            )
        }

    # Dedupe against pending drafts (the ledger only knows fired posts).
    draft_hash = text_hash(body)
    for p in store.list_all(tenant_id=tenant_id, limit=500):
        if p.status == "pending" and text_hash(p.text) == draft_hash:
            return False, {"error": f"An identical tweet is already scheduled (id {p.id})."}

    tz_name = ""
    try:
        from kazma_core.cron.scheduler import get_cron_timezone

        tz_name = str(get_cron_timezone())
    except Exception:  # noqa: BLE001
        tz_name = ""

    post_id = store.add(
        text=body, fire_at=fire_at, tz=tz_name,
        reply_to_id=(reply_to_id or "").strip(),
        thread_id=thread_id, delivery_target=delivery_target,
        tenant_id=tenant_id,
    )

    from datetime import datetime

    fire_iso = datetime.fromtimestamp(fire_at).astimezone().isoformat(timespec="seconds")
    return True, {
        "scheduled": True,
        "id": post_id,
        "text": body,
        "fire_at": fire_iso,
        "tz": tz_name,
        "reply_to_id": reply_to_id or "",
    }


async def publish_x_post(
    *,
    text: str,
    reply_to_id: str = "",
) -> tuple[bool, dict[str, Any]]:
    """Validate + POST /2/tweets once. Returns ``(ok, payload)``.

    Writes are never retried (a dropped 201 plus a retry would double-post).
    The caller is responsible for HITL on the agent path; the Web studio
    treats the operator click as the approval, matching ``book_x_post``.
    """
    from kazma_core.x_api.client import XApiError, XClient
    from kazma_core.x_api.config import get_x_config
    from kazma_core.x_api.ledger import get_ledger
    from kazma_core.x_api.policy import evaluate_post

    cfg = get_x_config()
    body = (text or "").strip()
    decision = evaluate_post(body, cfg=cfg, reply_to_id=reply_to_id or "")
    if not decision.allow:
        return False, {"posted": False, "error": decision.reason}
    try:
        tweet = await XClient(cfg.credentials).create_tweet(
            body, reply_to_id=reply_to_id or ""
        )
    except XApiError as exc:
        logger.warning("publish_x_post API error: %s", exc)
        return False, {"posted": False, "error": _public_x_error(exc)}
    except Exception as exc:
        logger.exception("publish_x_post failed")
        return False, {"posted": False, "error": _public_x_error(exc)}
    tweet_id = str(tweet.get("id") or "")
    if tweet_id:
        get_ledger().record(tweet_id=tweet_id, text=body, handle=cfg.handle)
    url = f"https://x.com/i/web/status/{tweet_id}" if tweet_id else ""
    return True, {
        "posted": True,
        "tweet_id": tweet_id,
        "url": url,
        "text": body,
        "policy": decision.reason,
    }


async def delete_x_post(*, tweet_id: str) -> tuple[bool, dict[str, Any]]:
    """DELETE /2/tweets/:id once. Returns ``(ok, payload)``.

    Same no-retry contract as publish. Web Studio treats the operator click
    as approval; the chat tool stays always-HITL outside this function.
    """
    from kazma_core.x_api.client import XApiError, XClient
    from kazma_core.x_api.config import get_x_config
    from kazma_core.x_api.ledger import get_ledger
    from kazma_core.x_api.policy import evaluate_delete

    tid = (tweet_id or "").strip()
    if not tid:
        return False, {"deleted": False, "error": "tweet_id is required."}
    cfg = get_x_config()
    decision = evaluate_delete(cfg=cfg)
    if not decision.allow:
        return False, {"deleted": False, "error": decision.reason}
    try:
        data = await XClient(cfg.credentials).delete_tweet(tid)
    except XApiError as exc:
        logger.warning("delete_x_post API error: %s", exc)
        return False, {"deleted": False, "error": _public_x_error(exc), "tweet_id": tid}
    except Exception as exc:
        logger.exception("delete_x_post failed")
        return False, {"deleted": False, "error": _public_x_error(exc), "tweet_id": tid}
    get_ledger().mark_deleted(tid)
    return True, {"deleted": True, "tweet_id": tid, "api": data}
