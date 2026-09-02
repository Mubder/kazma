"""Official X publisher tools — thin wrappers over kazma_core.x_api."""

from __future__ import annotations

import json
import logging

from kazma_core.x_api.booking import delete_x_post, publish_x_post
from kazma_core.x_api.config import get_x_config
from kazma_core.x_api.ledger import get_ledger
from kazma_core.x_api.schedule import get_x_scheduled_store

logger = logging.getLogger(__name__)


def _json(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


async def x_status() -> str:
    """Read-only connector status. Never returns secret values."""
    try:
        cfg = get_x_config()
        ledger = get_ledger()
        import time

        day = ledger.count_since(time.time() - 86400)
        month = ledger.count_since(time.time() - 30 * 86400)
        recent = ledger.recent(5)
        payload = {
            "configured": cfg.credentials.complete(),
            "enabled": cfg.enabled,
            "kill_switch": cfg.kill_switch,
            "handle": cfg.handle or "",
            "can_post": cfg.can_post(),
            "caps": {
                "max_chars": cfg.max_chars,
                "max_posts_per_day": cfg.max_posts_per_day,
                "posts_today": day,
                "max_posts_per_month": cfg.max_posts_per_month,
                "posts_30d": month,
                "max_mentions": cfg.max_mentions,
                "max_cashtags": cfg.max_cashtags,
                "max_hashtags": cfg.max_hashtags,
                "duplicate_window_days": cfg.duplicate_window_days,
            },
            "always_hitl": True,
            "official_api_only": True,
            "recent_posts": [
                {
                    "tweet_id": r.get("tweet_id"),
                    "preview": r.get("text_preview"),
                    "created_at": r.get("created_at"),
                    "deleted": bool(r.get("deleted_at")),
                }
                for r in recent
            ],
            "setup": (
                "Open Settings → X. Create a Project + App at developer.x.com, "
                "set User authentication to Read and write, generate the four "
                "OAuth 1.0a keys, and save them there (never in chat). "
                "Label the X account as Automated. Bearer tokens cannot post."
            ),
        }
        return _json(payload)
    except Exception as exc:
        logger.exception("x_status failed")
        return f"Error reading X status: {exc}"


async def x_post(text: str, reply_to_id: str = "", proposal_id: str = "") -> str:
    """Post one tweet via official X API v2. HITL is mandatory outside this function.

    ``proposal_id`` is REQUIRED by the commitment gate (S1-3): the text
    posted is rewritten from the stored proposal item, so approval resolves
    a durable id — not the model's memory. Workflow: (1) persist drafts with
    ``save_proposal(kind, items)``, (2) call this ONCE PER ITEM with that
    item's proposal_id. One call must not fan out to multiple drafts.
    """
    ok, payload = await publish_x_post(text=text, reply_to_id=reply_to_id or "")
    payload["ok"] = ok
    if proposal_id:
        payload["proposal_id"] = proposal_id
    return _json(payload)


async def x_delete_post(tweet_id: str) -> str:
    """Delete a tweet by id via official X API v2."""
    ok, payload = await delete_x_post(tweet_id=tweet_id)
    payload["ok"] = ok
    return _json(payload)


# ── Scheduled posts ───────────────────────────────────────────────────
# X has no native scheduled-post API, so Kazma stores the draft and fires
# POST /2/tweets at the appointed time (see kazma_core/x_api/schedule.py).
# HITL approval happens once, at booking. Quota is reserved at booking so
# the schedule cannot be used to exceed the daily/monthly caps.


def _booking_identity() -> tuple[str, str, str]:
    """(tenant_id, thread_id, delivery_target) captured at booking time."""
    tenant_id = "default"
    try:
        from kazma_core.tenant_isolation import require_tenant_id

        tenant_id = require_tenant_id() or "default"
    except Exception:  # noqa: BLE001
        pass
    thread_id = ""
    try:
        from kazma_core.safety.hitl import get_current_thread_id

        thread_id = get_current_thread_id() or ""
    except Exception:  # noqa: BLE001
        pass
    delivery_target = ""
    try:
        from kazma_core.tools.send_message import get_current_delivery_target

        delivery_target = get_current_delivery_target() or ""
    except Exception:  # noqa: BLE001
        pass
    return tenant_id, thread_id, delivery_target


async def x_schedule_post(
    text: str,
    when: str,
    reply_to_id: str = "",
    proposal_id: str = "",
) -> str:
    """Schedule a tweet to be posted automatically at a future time.

    HITL approval is required at booking. Kazma stores the draft and publishes
    it at the appointed time (X has no native post scheduling). The post only
    fires while the Kazma server is running.

    ``proposal_id`` is REQUIRED by the commitment gate: the scheduled text is
    rewritten from the stored proposal item. Persist drafts with
    ``save_proposal(kind, items)`` first, then book ONE call per item.

    Args:
        text: The exact tweet text to publish.
        when: When to post: '5m', '1h', 'daily at 9am', or an ISO timestamp.
        reply_to_id: Optional tweet id to reply to (thread).
        proposal_id: The saved proposal item id (from save_proposal).

    Returns:
        JSON with the scheduled post id, text and fire time.
    """
    try:
        from kazma_core.x_api.booking import book_x_post

        tenant_id, thread_id, delivery_target = _booking_identity()
        ok, payload = book_x_post(
            text=text,
            when=when,
            reply_to_id=reply_to_id or "",
            tenant_id=tenant_id,
            thread_id=thread_id,
            delivery_target=delivery_target,
        )
        if ok:
            payload = dict(payload)
            payload["ok"] = True
            payload["note"] = (
                "Approved at booking. Kazma publishes it at the scheduled time "
                "while the server runs."
            )
        else:
            payload = {"ok": False, "scheduled": False, "error": payload.get("error", "")}
        return _json(payload)
    except Exception as exc:  # noqa: BLE001
        logger.exception("x_schedule_post failed")
        return _json({"ok": False, "scheduled": False, "error": str(exc)})


async def x_list_scheduled() -> str:
    """List scheduled X posts (pending first, then recent fired/cancelled/failed).

    Returns:
        JSON with the scheduled posts and their status.
    """
    try:
        tenant_id, _, _ = _booking_identity()
        store = get_x_scheduled_store()
        posts = store.list_all(tenant_id=tenant_id, limit=100)
        from datetime import datetime

        out = []
        for p in posts:
            out.append({
                "id": p.id,
                "text": p.text,
                "status": p.status,
                "fire_at": datetime.fromtimestamp(p.fire_at).astimezone().isoformat(timespec="seconds"),
                "tz": p.tz,
                "reply_to_id": p.reply_to_id,
                "tweet_id": p.tweet_id,
                "error": p.error,
            })
        return _json({"ok": True, "count": len(out), "posts": out})
    except Exception as exc:  # noqa: BLE001
        logger.exception("x_list_scheduled failed")
        return _json({"ok": False, "error": str(exc)})


async def x_cancel_scheduled_post(post_id: int) -> str:
    """Cancel a scheduled X post before it fires (releases its reserved quota).

    Args:
        post_id: The id of the scheduled post to cancel.

    Returns:
        JSON indicating whether the post was cancelled.
    """
    try:
        pid = int(post_id)
    except (TypeError, ValueError):
        return _json({"ok": False, "cancelled": False, "error": "post_id must be a number."})
    try:
        store = get_x_scheduled_store()
        cancelled = store.cancel(pid)
        if cancelled:
            return _json({"ok": True, "cancelled": True, "id": pid})
        existing = store.get(pid)
        if existing is None:
            return _json({"ok": False, "cancelled": False, "error": f"No scheduled post with id {pid}."})
        return _json({
            "ok": False, "cancelled": False,
            "error": f"Post {pid} is already '{existing.status}' and cannot be cancelled.",
        })
    except Exception as exc:  # noqa: BLE001
        logger.exception("x_cancel_scheduled_post failed")
        return _json({"ok": False, "cancelled": False, "error": str(exc)})
