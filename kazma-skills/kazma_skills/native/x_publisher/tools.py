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


#: The account's last counts and followers seen, for "since the last check".
#: Kazma's own state (``system.``): never in a settings backup.
_ACCOUNT_KEY = "system.x.account_counts"
_COUNTS = ("followers_count", "following_count", "tweet_count", "listed_count")
_KEEP_FOLLOWER_IDS = 1000


def _account_snapshot() -> dict:
    from kazma_core.config_store import get_config_store

    found = get_config_store().get(_ACCOUNT_KEY, None)
    return found if isinstance(found, dict) else {}


def _save_account_snapshot(snapshot: dict) -> None:
    from kazma_core.config_store import get_config_store

    get_config_store().set(_ACCOUNT_KEY, snapshot)


async def x_account(followers: bool = False) -> str:
    """The X account's own numbers, read through the official X API.

    Followers, following, posts and lists, and how each changed since the
    last time this was asked (one read from the X read budget). Use it for
    any question about the account's followers or following: x.com in a
    browser answers with a sign-in page, and X's terms forbid scraping it.

    Args:
        followers: Also list the newest followers (one more read), each
            marked new when it was not among the followers seen last time.
            X lists followers only on API access that includes follows
            lookup; without it the answer says so and keeps the counts.
    """
    import sqlite3

    from kazma_core.cron.scheduler import resolve_cron_zone
    from kazma_core.x_api.client import XApiError, XClient

    cfg = await asyncio.to_thread(get_x_config)
    if not cfg.credentials.complete():
        return _json({
            "ok": False,
            "error": "X is not configured: the operator saves the X keys in Settings → X (never in chat).",
        })
    client = XClient(cfg.credentials)
    try:
        account = await client.get_account()
    except XApiError as exc:
        return _json({"ok": False, "error": f"X did not return the account: {exc}"})
    counts = dict(account.get("public_metrics") or {})
    if not counts:
        return _json({"ok": False, "error": "X returned the account without its counts."})

    import time

    before, (zone, zone_name) = await asyncio.to_thread(
        lambda: (_account_snapshot(), resolve_cron_zone()))
    now = time.time()
    answer: dict = {
        "ok": True,
        "handle": account.get("username") or cfg.handle or "",
        "id": str(account.get("id") or ""),  # the user id x_api_read/x_api_act take as path id
        "name": account.get("name") or "",
        "followers": counts.get("followers_count"),
        "following": counts.get("following_count"),
        "posts": counts.get("tweet_count"),
        "listed": counts.get("listed_count"),
        "read_at": _when(now, zone),
        "time_zone": zone_name or "UTC",
    }
    if before.get("at"):
        answer["since"] = _when(before.get("at"), zone)
        answer["changes"] = {
            name.replace("_count", "").replace("tweet", "posts"): counts[name] - before[name]
            for name in _COUNTS
            if isinstance(counts.get(name), int) and isinstance(before.get(name), int)
        }
    else:
        answer["first_check"] = True
        answer["note"] = "No earlier reading to compare with; the next check will show the change."

    snapshot = {"at": now, **{n: counts[n] for n in _COUNTS if isinstance(counts.get(n), int)}}
    seen_before = [str(i) for i in (before.get("follower_ids") or []) if str(i).isdigit()]
    snapshot["follower_ids"] = seen_before
    if followers:
        try:
            people = await client.get_followers(str(account.get("id") or ""))
        except XApiError as exc:
            if exc.status in (401, 403):
                answer["followers_error"] = (
                    "X refused to list the followers (HTTP %s): this account's X API access does not "
                    "include follows lookup. The counts above are X's own." % exc.status)
            else:
                answer["followers_error"] = f"X did not list the followers: {exc}"
        else:
            known = set(seen_before)
            answer["newest_followers"] = [
                {
                    "handle": p.get("username"),
                    "name": p.get("name"),
                    "followers": (p.get("public_metrics") or {}).get("followers_count"),
                    "new": bool(known) and str(p.get("id")) not in known,
                }
                for p in people
            ]
            if not known:
                answer["followers_note"] = "First list read: none is marked new; the next list is compared with this one."
            ids = [str(p.get("id")) for p in people if str(p.get("id") or "").isdigit()]
            snapshot["follower_ids"] = (ids + [i for i in seen_before if i not in ids])[:_KEEP_FOLLOWER_IDS]
    try:
        await asyncio.to_thread(_save_account_snapshot, snapshot)
    except (RuntimeError, OSError, ValueError, sqlite3.Error):
        # The reading stands; only the next comparison is lost, and said.
        logger.warning("x_account: the reading could not be kept for the next comparison", exc_info=True)
        answer["note_saved"] = "This reading could not be kept, so the next check cannot compare with it."
    return _json(answer)


def _checked_media_ids(media_ids: object) -> list[str]:
    """At most four media ids from x_upload_media, or a refusal."""
    if not media_ids:
        return []
    ids = media_ids if isinstance(media_ids, (list, tuple)) else str(media_ids).split(",")
    clean = [str(i).strip() for i in ids if str(i).strip()]
    if len(clean) > 4 or any(not (i.isascii() and i.isdigit()) for i in clean):
        raise RefusedBeforeEffect(_json({
            "ok": False, "error": "media_ids takes up to four media ids from x_upload_media.",
        }))
    return clean


async def x_post(text: str, reply_to_id: str = "", proposal_id: str = "",
                 media_ids: list[str] | None = None) -> str:
    """Post one tweet via official X API v2. HITL is mandatory outside this function.

    ``proposal_id`` is REQUIRED by the commitment gate (S1-3): the text
    posted is rewritten from the stored proposal item, so approval resolves
    a durable id — not the model's memory. Workflow: (1) persist drafts with
    ``save_proposal(kind, items)``, (2) call this ONCE PER ITEM with that
    item's proposal_id. One call must not fan out to multiple drafts.

    Args:
        text: The post's text (rewritten from the saved draft).
        reply_to_id: A post id to reply to, for a thread.
        proposal_id: The saved draft's item id (from save_proposal).
        media_ids: Up to four media ids from x_upload_media: the photos,
            a GIF or a video the post carries.
    """
    media = _checked_media_ids(media_ids)
    ok, payload = await publish_x_post(text=text, reply_to_id=reply_to_id or "",
                                     idempotency_key="proposal:" + proposal_id if proposal_id else "",
                                     metadata={"proposal_id": proposal_id, **({"media_ids": media} if media else {})})
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
    media_ids: list[str] | None = None,
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
        media_ids: Up to four media ids from x_upload_media, posted with it.

    Returns:
        JSON with the scheduled post id, text and the STORED fire time and
        state. ``ok`` is false unless the booking is scheduled; rebooking a
        draft already booked at another time is a conflict.
    """
    media = _checked_media_ids(media_ids)
    try:
        from kazma_core.x_api.booking import book_x_post

        # The tenant rule can read settings: off the loop (SCH-S11).
        tenant_id, thread_id, delivery_target = await asyncio.to_thread(_booking_identity)
        ok, payload = await asyncio.to_thread(book_x_post, text=text, when=when,
            reply_to_id=reply_to_id or "", tenant_id=tenant_id, thread_id=thread_id,
            delivery_target=delivery_target, idempotency_key="proposal:" + proposal_id if proposal_id else "",
            proposal_id=proposal_id, media_ids=media)
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


async def x_draft_posts(brief: str, count: int = 1) -> str:
    """Draft X posts as X Studio's Generate does, and save them for review.

    The same pipeline: the X model, the operator's reference documents
    (Settings -> X), the four content checks. A post the chat model writes
    itself and saves with save_proposal has none of them, so a post about the
    product said whatever the model remembered. The drafts are saved under
    this turn: they are never published in it.
    """
    from kazma_core.agent.artifacts import get_artifact_store
    from kazma_core.agent.tool_refusal import KnownEffectError
    from kazma_core.safety.commitment.proposals import current_turn
    from kazma_core.safety.hitl import get_current_thread_id
    from kazma_core.x_api.post_drafting import draft_posts
    from kazma_core.x_api.reply import DraftFailed
    from kazma_core.x_api.text_length import validate_text

    try:
        count = int(count)
    except (TypeError, ValueError):
        raise RefusedBeforeEffect("count must be 1, 2 or 3.") from None
    try:
        result = await draft_posts(str(brief or ""), count=count)
    except DraftFailed as exc:
        # The model calls ran and nothing was saved; another call is safe.
        raise KnownEffectError(f"{exc} Nothing was saved; adjust the brief and call again.") from exc
    except ValueError as exc:
        # A bad brief, the X settings, or a reference document that cannot be
        # read: refused before any model call.
        raise RefusedBeforeEffect(str(exc)) from exc
    from kazma_core.x_api.ownership import x_tenant_id

    # The X account's space (S1-17 part b): never the owner's for work bound
    # to no space. A settings read when no tenant is bound: off the loop.
    tenant = await asyncio.to_thread(x_tenant_id)
    thread, turn = get_current_thread_id() or "", current_turn()
    reviews = [{**review, "models": list(result.models)} for review in result.reviews]
    store = await asyncio.to_thread(get_artifact_store)
    payload = await asyncio.to_thread(store.save_proposal, tenant, thread, "x_posts", list(result.drafts),
                                      reviews=reviews, saved_turn=turn)
    limit = int((await asyncio.to_thread(get_x_config)).max_chars or 280)
    sources = ", ".join(ref["name"] for ref in result.references)
    lines = [f"Saved to X Studio's drafts as {payload['proposal_id']}: {len(payload['items'])} written by the X "
             f"model from the brief{' and ' + sources if sources else ' alone (no reference documents are set)'}."]
    for item, review in zip(payload["items"], result.reviews):
        counted = validate_text(str(item["text"]), maximum=limit)
        verdicts = ", ".join(f"{check['check']} {check['verdict']}" for check in review["checks"])
        lines.append(f"- {item['id']} [X length {counted.weighted}/{limit}"
                     f"{'' if counted.valid else ', TOO LONG'}; checks: {verdicts}]")
        lines.append(f"  {item['text']}")
        lines.extend(f"  {check['check']}: {check['reason']}" for check in review["checks"]
                     if check["verdict"] != "pass" and check["check"] != "context")
    lines.append("The user has not seen these drafts yet: show them the full text and the checks, and ask "
                 "before posting. A draft is never published in the turn that wrote it; post one with "
                 "x_post(proposal_id=<item id>) after the user answers.")
    return "\n".join(lines)


#: What X takes, by file extension: the MIME type and the post category.
_MEDIA_TYPES = {
    ".jpg": ("image/jpeg", "tweet_image"), ".jpeg": ("image/jpeg", "tweet_image"),
    ".png": ("image/png", "tweet_image"), ".webp": ("image/webp", "tweet_image"),
    ".bmp": ("image/bmp", "tweet_image"), ".tif": ("image/tiff", "tweet_image"),
    ".tiff": ("image/tiff", "tweet_image"), ".gif": ("image/gif", "tweet_gif"),
    ".mp4": ("video/mp4", "tweet_video"), ".mov": ("video/quicktime", "tweet_video"),
    ".webm": ("video/webm", "tweet_video"), ".srt": ("text/srt", "subtitles"),
    ".vtt": ("text/vtt", "subtitles"),
}
_MAX_UPLOAD_BYTES = 512 * 1024 * 1024


async def x_upload_media(file_path: str, alt_text: str = "", for_dm: bool = False) -> str:
    """Upload a photo, GIF or video from the workspace to X, through the
    official X API, and answer its media id: pass it in media_ids to x_post
    or x_schedule_post (or a direct message's attachments). Nothing is
    posted: an uploaded file no post uses expires on X.

    Args:
        file_path: The file in the workspace (jpg, png, webp, gif, mp4, mov,
            webm; srt or vtt subtitles).
        alt_text: The description people with screen readers hear (up to
            1,000 characters); set it for every photo.
        for_dm: Upload it for a direct message instead of a post.
    """
    from pathlib import Path as _Path

    from kazma_core.agent.tool_refusal import KnownEffectError
    from kazma_core.agent.tool_scope import resolve_in_scope
    from kazma_core.x_api.client import XApiError, XClient

    path, refusal = await resolve_in_scope(file_path, "reads")
    if refusal:
        raise RefusedBeforeEffect(refusal)
    kind = _MEDIA_TYPES.get(_Path(path).suffix.lower())
    if kind is None:
        raise RefusedBeforeEffect(_json({"ok": False, "error": "X takes jpg, png, webp, gif, mp4, mov or webm here (srt or vtt subtitles)."}))
    media_type, category = kind
    if for_dm and category.startswith("tweet_"):
        category = category.replace("tweet_", "dm_")
    cfg = await asyncio.to_thread(get_x_config)
    if not cfg.credentials.complete():
        raise RefusedBeforeEffect(_json({"ok": False, "error": "X is not configured: the operator saves the X keys in Settings → X."}))

    def _read() -> bytes:
        size = _Path(path).stat().st_size
        if size > _MAX_UPLOAD_BYTES:
            raise ValueError("The file is larger than X takes (512 MB).")
        return _Path(path).read_bytes()

    try:
        data = await asyncio.to_thread(_read)
    except (OSError, ValueError) as exc:
        raise RefusedBeforeEffect(_json({"ok": False, "error": f"The file could not be read: {exc}"})) from exc
    try:
        uploaded = await XClient(cfg.credentials).upload_media(
            data, media_type=media_type, category=category, alt_text=alt_text)
    except XApiError as exc:
        # An upload that failed posts nothing; another try uploads afresh.
        raise KnownEffectError(_json({"ok": False, "error": f"X did not take the upload: {exc}"})) from exc
    return _json({"ok": True, **uploaded,
                  "next": "Pass media_id in media_ids to x_post or x_schedule_post."})


# ── The whole X API (2026-11-17) ───────────────────────────────────────────
# x_api_operations finds an operation in X's own spec (x_api/operations.py),
# x_api_read calls any read, x_api_act any write -- the write always behind a
# one-time approval card that names the operation. Posting and deleting a
# post stay x_post / x_delete_post.

_MAX_READ_CHARS = 24_000


def _needs(op, have: dict) -> str:
    """Why *op* cannot be called with the authentications this install has."""
    if op.refused:
        return op.refused
    wanted = [a for a in op.auth]
    if not wanted:
        return "X lists no authentication for it."
    if any(have.get(a) for a in wanted):
        return ""
    words = {
        "user": "the X keys in Settings → X",
        "oauth2": "Sign in with X (OAuth 2.0) in Settings → X",
        "app": "the app's Bearer token in Settings → X",
    }
    return "Needs " + " or ".join(words[a] for a in wanted if a in words) + "."


def _auth_for(op, have: dict) -> str:
    for a in op.auth:
        if have.get(a):
            if a == "oauth2" and getattr(op, "scopes", ()):
                from kazma_core.x_api.auth_tokens import oauth2_account

                granted = set(oauth2_account().get("scopes") or ())
                if granted and not set(op.scopes) <= granted:
                    continue
            return a
    return ""


async def x_api_operations(search: str = "", area: str = "", kind: str = "") -> str:
    """Find an X API operation: every operation of X's own API spec, with
    what it takes and whether Kazma can call it now.

    Args:
        search: Words the operation's name, path or summary holds
            ("followers", "bookmarks", "dm events", "list members").
        area: An X API group: Posts, Users, Lists, Direct Messages, Spaces,
            Media, Trends, Usage, Communities, Community Notes, News,
            Articles, Chat, Broadcasts, Calls, Bots, Stream, Webhooks,
            Activity, Account Activity, Compliance, Connections, Account.
        kind: read, write, stream or refused.
    """
    from kazma_core.x_api import operations as ops
    from kazma_core.x_api.auth_tokens import available_auth

    have = await asyncio.to_thread(available_auth)
    if not (search or area or kind):
        return _json({
            "spec_version": ops.spec_version(),
            "areas": ops.areas(),
            "available_auth": have,
            "next": "Call again with search, area or kind to list the operations.",
        })
    found = ops.find(search, area=area, kind=kind, limit=40)
    rows = []
    for op in found:
        row = op.describe()
        why = _needs(op, have)
        row["available"] = not why
        if why:
            row["why_not"] = why
        rows.append(row)
    return _json({
        "spec_version": ops.spec_version(),
        "available_auth": have,
        "operations": rows,
        "how": "Read with x_api_read(operation, path, query); write with x_api_act(operation, path, query, body).",
    })


def _fenced(payload: dict) -> str:
    from kazma_core.safety.prompt_fence import fence_untrusted

    text = json.dumps(payload, ensure_ascii=False, indent=1)
    cut = len(text) > _MAX_READ_CHARS
    if cut:
        text = text[:_MAX_READ_CHARS]
    note = "\n(cut: the answer was longer; ask for fewer fields or a smaller max_results)" if cut else ""
    return fence_untrusted(text, source="x_api") + note


async def _prepared(operation: str, path, query, body, *, kinds: tuple[str, ...]):
    from kazma_core.x_api import operations as ops
    from kazma_core.x_api.api_call import CallRefused, prepare
    from kazma_core.x_api.auth_tokens import available_auth

    op = ops.operation(operation)
    if op is None:
        raise RefusedBeforeEffect(_json({
            "ok": False,
            "error": f"No X API operation {operation!r}. Find it with x_api_operations(search=...).",
        }))
    if op.kind not in kinds:
        hint = {
            "read": "It is a read: call x_api_read.",
            "stream": "It is a stream: call x_api_read (it reads for a bounded time).",
            "write": "It changes something: call x_api_act.",
            "refused": op.refused,
        }[op.kind]
        raise RefusedBeforeEffect(_json({"ok": False, "operation": op.id, "error": hint}))
    have = await asyncio.to_thread(available_auth)
    auth = await asyncio.to_thread(_auth_for, op, have)
    if not auth:
        raise RefusedBeforeEffect(_json({"ok": False, "operation": op.id, "error": _needs(op, have)
                                         or "The X sign-in lacks a permission this operation needs; sign in with X again."}))
    try:
        call = prepare(op, path, query, body)
    except CallRefused as exc:
        raise RefusedBeforeEffect(_json({"ok": False, "operation": op.id, "error": str(exc),
                                         "takes": op.describe()})) from None
    return op, call, auth


async def x_api_read(operation: str, path: dict | None = None, query: dict | None = None,
                     max_events: int = 10, seconds: int = 20) -> str:
    """Call any read of the X API (one read from the X read budget); the
    answer is X's own JSON, fenced as outside text.

    Args:
        operation: The operation's id from x_api_operations (getUsersFollowers,
            searchPostsRecent, getDmEvents...) or "GET /2/...".
        path: The path's values by name, for each {name} in its path
            (id: a user or post id; username: a handle without @).
        query: Query values by name, as x_api_operations lists them
            (max_results: a number; user.fields: a list or "a,b"; query: the search).
        max_events: For a stream: stop after this many events (1-100).
        seconds: For a stream: stop after this many seconds (1-60).
    """
    from kazma_core.x_api.client import XApiError, XClient

    op, call, auth = await _prepared(operation, path, query, None, kinds=("read", "stream"))
    cfg = await asyncio.to_thread(get_x_config)
    client = XClient(cfg.credentials)
    try:
        if op.kind == "stream":
            answer = await client.stream_operation(
                call, auth=auth, max_events=max(1, min(int(max_events or 10), 100)),
                seconds=max(1, min(int(seconds or 20), 60)))
        else:
            answer = await client.request_operation(call, auth=auth)
    except XApiError as exc:
        return _json({"ok": False, "operation": op.id, "status": exc.status, "error": str(exc)})
    return _fenced({"ok": True, "operation": op.id, "auth": auth, "answer": answer})


async def x_api_act(operation: str, path: dict | None = None, query: dict | None = None,
                    body: dict | None = None) -> str:
    """Do anything the X API can do with the account -- like, repost,
    follow, block, mute, lists, bookmarks, direct messages, X Chat, notes,
    spaces, broadcasts -- after the operator approves this one call on a
    card that names it. Posting and deleting a post are x_post and
    x_delete_post.

    Args:
        operation: The operation's id from x_api_operations (likePost,
            followUser, createDirectMessagesByParticipantId...) or "POST /2/...".
        path: The path's values by name, for each {name} in its path
            (id: the account's own user id for most user actions).
        query: Query values by name, when the operation takes any.
        body: The JSON body's fields by name, as x_api_operations lists
            them (tweet_id: the post to like; text: a message's text).
    """
    from kazma_core.agent.tool_refusal import KnownEffectError
    from kazma_core.x_api.client import XApiError, XClient

    op, call, auth = await _prepared(operation, path, query, body, kinds=("write",))
    cfg = await asyncio.to_thread(get_x_config)
    try:
        answer = await XClient(cfg.credentials).request_operation(call, auth=auth)
    except XApiError as exc:
        if exc.outcome in ("rejected", "not_sent"):
            # X refused it, or it never left: nothing changed on X.
            raise KnownEffectError(_json({"ok": False, "operation": op.id, "status": exc.status,
                                          "error": str(exc)})) from exc
        return _json({"ok": False, "operation": op.id, "outcome": "unknown", "error": str(exc),
                      "note": "It may have taken effect on X: check before doing it again."})
    return _fenced({"ok": True, "operation": op.id, "answer": answer})
