"""Official X publisher tools — thin wrappers over kazma_core.x_api."""

from __future__ import annotations

import json
import logging

from kazma_core.x_api.client import XApiError, XClient
from kazma_core.x_api.config import get_x_config
from kazma_core.x_api.ledger import get_ledger
from kazma_core.x_api.policy import evaluate_delete, evaluate_post

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


async def x_post(text: str, reply_to_id: str = "") -> str:
    """Post one tweet via official X API v2. HITL is mandatory outside this function."""
    try:
        cfg = get_x_config()
        decision = evaluate_post(text, cfg=cfg, reply_to_id=reply_to_id or "")
        if not decision.allow:
            return _json({"ok": False, "posted": False, "error": decision.reason})
        client = XClient(cfg.credentials)
        tweet = await client.create_tweet(text.strip(), reply_to_id=reply_to_id or "")
        tweet_id = str(tweet.get("id") or "")
        if tweet_id:
            get_ledger().record(tweet_id=tweet_id, text=text.strip(), handle=cfg.handle)
        url = f"https://x.com/i/web/status/{tweet_id}" if tweet_id else ""
        return _json(
            {
                "ok": True,
                "posted": True,
                "tweet_id": tweet_id,
                "url": url,
                "text": text.strip(),
                "policy": decision.reason,
            }
        )
    except XApiError as exc:
        logger.warning("x_post API error: %s", exc)
        return _json({"ok": False, "posted": False, "error": str(exc)})
    except Exception as exc:
        logger.exception("x_post failed")
        return _json({"ok": False, "posted": False, "error": str(exc)})


async def x_delete_post(tweet_id: str) -> str:
    """Delete a tweet by id via official X API v2."""
    tid = (tweet_id or "").strip()
    if not tid:
        return _json({"ok": False, "error": "tweet_id is required."})
    try:
        cfg = get_x_config()
        decision = evaluate_delete(cfg=cfg)
        if not decision.allow:
            return _json({"ok": False, "deleted": False, "error": decision.reason})
        client = XClient(cfg.credentials)
        data = await client.delete_tweet(tid)
        get_ledger().mark_deleted(tid)
        return _json({"ok": True, "deleted": True, "tweet_id": tid, "api": data})
    except XApiError as exc:
        logger.warning("x_delete_post API error: %s", exc)
        return _json({"ok": False, "deleted": False, "error": str(exc)})
    except Exception as exc:
        logger.exception("x_delete_post failed")
        return _json({"ok": False, "deleted": False, "error": str(exc)})
