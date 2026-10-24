"""Official X publisher tools — thin wrappers over kazma_core.x_api."""

from __future__ import annotations

import asyncio
import json
import logging
import re

from kazma_core.x_api.booking import delete_x_post, publish_x_post
from kazma_core.x_api.config import get_x_config
from kazma_core.x_api.ledger import get_ledger
from kazma_core.x_api.schedule import get_x_scheduled_store
from kazma_core.agent.tool_refusal import RefusedBeforeEffect

logger = logging.getLogger(__name__)


def _json(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _when(epoch: object, zone: object) -> str | None:
    """An epoch as "YYYY-MM-DD HH:MM" in *zone*, or None."""
    from datetime import datetime, timezone

    try:
        moment = datetime.fromtimestamp(float(epoch), tz=timezone.utc)  # type: ignore[arg-type]
        return moment.astimezone(zone).strftime("%Y-%m-%d %H:%M")  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError, OSError):
        return None


async def _attach_metrics(posts: list[dict], cfg: object) -> dict:
    """Each listed post's public counts from X, in ONE budgeted read; what
    could not be read is said, never shown as zero."""
    from kazma_core.x_api.client import XApiError, XClient

    ids = [p["tweet_id"] for p in posts if p.get("tweet_id") and not p.get("deleted")]
    if not ids:
        return {}
    if not cfg.credentials.complete():  # type: ignore[attr-defined]
        return {"metrics_error": "X is not configured (Settings → X)."}
    try:
        found = await XClient(cfg.credentials).get_public_metrics(ids)  # type: ignore[attr-defined]
    except XApiError as exc:
        return {"metrics_error": f"X did not return the counts: {exc}"}
    for post in posts:
        counts = found.get(str(post.get("tweet_id")))
        if counts is not None:
            post["metrics"] = counts
    missing = [i for i in ids if i not in found]
    return {"metrics_missing": missing} if missing else {}


async def x_status(recent: int = 5, offset: int = 0, metrics: bool = False) -> str:
    """Read-only connector status. Never returns secret values.

    Args:
        recent: How many posts to list (1-50, default 5). This is the
            model's read path for the post ledger (``x_posts.db``); the
            database itself is refused to the SQL, file and exec tools.
        offset: How many of the newest posts to skip. ``posts_total`` says
            how many the ledger holds and ``next_offset`` where the next
            page starts (null on the last): live 2026-10-18 an archive of
            "every post" read the newest 50 at most and stopped, 17 short.
        metrics: Also read each listed post's public counts from X (likes,
            reposts, replies, quotes, bookmarks, impressions): one read
            from Kazma's X read budget for the whole page.
    """
    try:
        cfg = await asyncio.to_thread(get_x_config)
        ledger = get_ledger()
        import time

        try:
            n_recent = max(1, min(int(recent or 5), 50))
        except (TypeError, ValueError):
            n_recent = 5
        try:
            n_offset = max(0, int(offset or 0))
        except (TypeError, ValueError):
            n_offset = 0
        from kazma_core.cron.scheduler import resolve_cron_zone

        day, month, total, recent_rows, (zone, zone_name) = await asyncio.to_thread(
            lambda: (
                ledger.count_since(time.time() - 86400),
                ledger.count_since(time.time() - 30 * 86400),
                ledger.count_all(),
                ledger.recent(n_recent, n_offset),
                resolve_cron_zone(),
            )
        )
        shown_to = n_offset + len(recent_rows)
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
                    # Readable, in the operator's zone: the epoch alone sent
                    # the model to python_exec (an approval card) to convert
                    # it (live 2026-10-18, twice in one archive).
                    "created": _when(r.get("created_at"), zone),
                    "deleted": bool(r.get("deleted_at")),
                }
                for r in recent_rows
            ],
            "posts_total": total,
            "time_zone": zone_name or "UTC",
            "recent_offset": n_offset,
            "next_offset": shown_to if shown_to < total else None,
            "setup": (
                "Open Settings → X. Create a Project + App at developer.x.com, "
                "set User authentication to Read and write, generate the four "
                "OAuth 1.0a keys, and save them there (never in chat). "
                "Label the X account as Automated. Bearer tokens cannot post."
            ),
        }
        if metrics:
            payload.update(await _attach_metrics(payload["recent_posts"], cfg))
        from kazma_core.x_api.publication_service import operation_inventory

        payload.update(await asyncio.to_thread(operation_inventory, limit=n_recent))
        from kazma_core.x_api.ai_budget import budget_status

        payload["ai_budget"] = await asyncio.to_thread(budget_status)
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
    ok, payload = await publish_x_post(text=text, reply_to_id=reply_to_id or "",
                                     idempotency_key="proposal:" + proposal_id if proposal_id else "",
                                     metadata={"proposal_id": proposal_id})
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
    from kazma_core.x_api.ownership import x_tenant_id

    tenant_id = x_tenant_id()
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
        when: When to post, once: '30m', '2h', or a date and time
            ('2026-12-01T09:00' is read in the operator's time zone; an offset
            or 'Z' is honored). An X post does not repeat: a recurring time is
            refused (book a recurring task instead).
        reply_to_id: Optional tweet id to reply to (thread).
        proposal_id: The saved proposal item id (from save_proposal).

    Returns:
        JSON with the scheduled post id, text and the STORED fire time and
        state. ``ok`` is false unless the booking is scheduled; rebooking a
        draft already booked at another time is a conflict.
    """
    try:
        from kazma_core.x_api.booking import book_x_post

        # The tenant rule can read settings: off the loop (SCH-S11).
        tenant_id, thread_id, delivery_target = await asyncio.to_thread(_booking_identity)
        ok, payload = await asyncio.to_thread(book_x_post, text=text, when=when,
            reply_to_id=reply_to_id or "", tenant_id=tenant_id, thread_id=thread_id,
            delivery_target=delivery_target, idempotency_key="proposal:" + proposal_id if proposal_id else "",
            proposal_id=proposal_id)
        if ok:
            payload = dict(payload)
            payload["ok"] = True
            payload["note"] = (
                "Approved at booking. Kazma publishes it at the scheduled time "
                "while the server runs."
            )
        else:
            # What the booking is now (its stored time and state), never the
            # requested time over a cancelled row (SCH-E08).
            payload = {"ok": False, "scheduled": False, **{k: payload[k] for k in (
                "error", "code", "operation_id", "state", "fire_at", "tz") if k in payload}}
        return _json(payload)
    except Exception as exc:
        logger.exception("x_schedule_post failed")
        return _json({"ok": False, "scheduled": False, "error": str(exc)})


async def x_list_scheduled() -> str:
    """List scheduled X posts (pending first, then recent fired/cancelled/failed).

    Returns:
        JSON with the scheduled posts and their status.
    """
    try:
        tenant_id, _, _ = await asyncio.to_thread(_booking_identity)
        store = await asyncio.to_thread(get_x_scheduled_store)
        posts = await asyncio.to_thread(store.list_all, tenant_id=tenant_id, limit=100)
        from datetime import datetime

        from kazma_core.cron.scheduler import resolve_cron_zone

        # Times in the operator's zone, named -- the server's own local zone
        # was used here while ``tz`` named the cron zone (SCH-E13).
        zone, zone_name = await asyncio.to_thread(resolve_cron_zone)
        out = []
        for p in posts:
            out.append({
                "id": p.id,
                "text": p.text,
                "status": p.status,
                "fire_at": datetime.fromtimestamp(p.fire_at, tz=zone).isoformat(timespec="seconds"),
                "tz": zone_name,
                "reply_to_id": p.reply_to_id,
                "tweet_id": p.tweet_id,
                "error": p.error,
            })
        from kazma_core.x_api.publication_service import operation_inventory

        inventory = await asyncio.to_thread(operation_inventory, limit=100)
        inventory["operations"] = [r for r in inventory["operations"] if r["origin"] == "schedule"]
        return _json({"ok": True, "count": len(out), "posts": out, **inventory})
    except Exception as exc:
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
        # The stores are SQLite: off the loop, the error path too (SCH-S11).
        store = await asyncio.to_thread(get_x_scheduled_store)
        tenant_id, _, _ = await asyncio.to_thread(_booking_identity)
        from kazma_core.x_api.publication_service import cancel_schedule

        cancelled = await asyncio.to_thread(cancel_schedule, pid)
        if cancelled:
            return _json({"ok": True, "cancelled": True, "id": pid})
        existing = await asyncio.to_thread(store.get, pid, tenant_id=tenant_id)
        if existing is None:
            raise RefusedBeforeEffect(_json({"ok": False, "cancelled": False, "error": f"No scheduled post with id {pid}."}))
        raise RefusedBeforeEffect(_json({
            "ok": False, "cancelled": False,
            "error": f"Post {pid} is already '{existing.status}' and cannot be cancelled.",
        }))
    except RefusedBeforeEffect:
        raise
    except Exception as exc:
        logger.exception("x_cancel_scheduled_post failed")
        return _json({"ok": False, "cancelled": False, "error": str(exc)})


# ── media download ────────────────────────────────────────────────────────

#: The only hosts X serves post media from; a variant naming any other host
#: is not fetched.
_MEDIA_HOSTS = frozenset({"video.twimg.com", "pbs.twimg.com"})
#: What send_file sends (a chat app's upload limit), so a download it could
#: not send is never kept.
_MAX_MEDIA_BYTES = 50 * 1024 * 1024
_POST_URL = re.compile(
    r"(?:x|twitter)\.com/(?:i/web/|i/status/|[A-Za-z0-9_]{1,15}/)status(?:es)?/(\d{1,25})",
    re.IGNORECASE,
)


def _post_id(post: str) -> str:
    """The post id in an x.com / twitter.com link, or a bare id; else ''."""
    text = str(post or "").strip()
    if text.isdigit() and len(text) <= 25:
        return text
    match = _POST_URL.search(text)
    return match.group(1) if match else ""


def _media_files(media: dict) -> list[tuple[str, str]]:
    """``(url, extension)`` candidates for one media item, best first.

    A video or GIF: its MP4 variants by bit rate, highest first (the caller
    takes the first that fits). A photo: its full-size file."""
    from urllib.parse import urlsplit

    kind = str(media.get("type") or "")
    found: list[tuple[int, str, str]] = []
    if kind == "photo":
        url = str(media.get("url") or "")
        ext = urlsplit(url).path.rsplit(".", 1)[-1].lower() or "jpg"
        found.append((0, f"{url}?name=orig" if url else "", ext))
    else:
        for variant in media.get("variants") or []:
            if not isinstance(variant, dict) or variant.get("content_type") != "video/mp4":
                continue
            found.append((int(variant.get("bit_rate") or 0), str(variant.get("url") or ""), "mp4"))
        found.sort(key=lambda f: f[0], reverse=True)
    return [
        (url, ext) for _, url, ext in found
        if url.startswith("https://") and (urlsplit(url).hostname or "").lower() in _MEDIA_HOSTS
    ]


async def _download_one(candidates: list[tuple[str, str]]) -> tuple[bytes, str] | str:
    """The first candidate that downloads whole under the cap, or why none did."""
    import httpx

    from kazma_core.web_acquire.http import fetch_public
    from kazma_core.web_acquire.result import AcquisitionError

    def _on_x(url: str) -> bool:
        from urllib.parse import urlsplit

        return (urlsplit(url).hostname or "").lower() in _MEDIA_HOSTS

    reason = "X listed no file for it"
    for url, ext in candidates:
        try:
            got = await fetch_public(url, max_bytes=_MAX_MEDIA_BYTES, timeout=120.0, scope=_on_x)
        except (httpx.HTTPError, AcquisitionError, TimeoutError, OSError, ValueError) as exc:
            # SSRFError is a ValueError. The next, smaller file may still come.
            reason = f"the download failed ({type(exc).__name__})"
            continue
        if got.response.status_code != 200:
            reason = f"X's media server answered HTTP {got.response.status_code}"
            continue
        if got.truncated:
            reason = "every version of it is larger than 50 MB, the most a chat can be sent"
            continue
        return got.response.content, ext
    return reason


def _save_media(tweet_id: str, files: list[tuple[bytes, str]]) -> list[dict]:
    """Write each file to ``<workspace>/downloads/x`` (blocking: a thread)."""
    from kazma_core.workspace.binding import resolve_active_root

    folder = resolve_active_root() / "downloads" / "x"
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[dict] = []
    for n, (data, ext) in enumerate(files, start=1):
        suffix = f"-{n}" if len(files) > 1 else ""
        path = folder / f"{tweet_id}{suffix}.{ext}"
        partial = path.with_name(path.name + ".partial")
        partial.write_bytes(data)
        partial.replace(path)
        saved.append({"path": str(path), "size_kb": max(1, len(data) // 1024)})
    return saved


async def x_download_media(post: str) -> str:
    """Download the video, GIF or photos attached to a public X post.

    Reads the post's media through the official X API (one read from the X
    read budget) and saves each file in the workspace's ``downloads/x``
    folder; send it to the user with ``send_file(file_path=<path>)``. A video
    is saved at the highest quality under 50 MB, the most a chat can be sent.

    Args:
        post: The post's link (x.com or twitter.com) or its id.
    """
    from kazma_core.agent.tool_refusal import KnownEffectError
    from kazma_core.x_api.client import XApiError, XClient

    tweet_id = _post_id(post)
    if not tweet_id:
        raise RefusedBeforeEffect(_json({
            "ok": False, "error": "Give the post's x.com link or its id.",
        }))
    cfg = await asyncio.to_thread(get_x_config)
    if not cfg.credentials.complete():
        raise RefusedBeforeEffect(_json({
            "ok": False,
            "error": "X is not configured: the operator saves the X keys in Settings → X.",
        }))
    try:
        media = await XClient(cfg.credentials).get_media(tweet_id)
    except XApiError as exc:
        # A read: nothing was saved, and asking again repeats nothing anyone sees.
        raise KnownEffectError(_json({"ok": False, "saved": [], "error": f"X did not return the post: {exc}"})) from exc
    if not media:
        raise KnownEffectError(_json({
            "ok": False, "saved": [],
            "error": "X returned no photo, video or GIF for this post (none attached, or not visible to Kazma's X app).",
        }))
    files: list[tuple[bytes, str]] = []
    problems: list[str] = []
    for item in media:
        got = await _download_one(_media_files(item))
        if isinstance(got, str):
            problems.append(f"{item.get('type') or 'media'}: {got}")
        else:
            files.append(got)
    saved = await asyncio.to_thread(_save_media, tweet_id, files) if files else []
    if not saved:
        raise KnownEffectError(_json({"ok": False, "saved": [], "error": "; ".join(problems)}))
    answer: dict = {
        "ok": True,
        "post_id": tweet_id,
        "saved": saved,
        "next": "Send each file to the user with send_file(file_path=<path>).",
    }
    if problems:
        answer["not_saved"] = problems
    return _json(answer)
