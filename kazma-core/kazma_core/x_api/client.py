"""Official X API v2 client (OAuth 1.0a user context).

Only ``POST /2/tweets``, ``DELETE /2/tweets/:id``, and ``GET /2/users/me``.
No scrape, no like/follow/DM, no Bearer posting, no Playwright.
Writes are **not** retried (a retry after a dropped 201 would double-post).
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import math
import time
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from kazma_core.x_api import config as _config
from kazma_core.x_api.audit import log_x_event
from kazma_core.x_api.config import XCredentials
from kazma_core.x_api.oauth1 import oauth1_authorization_header, sign_request

logger = logging.getLogger(__name__)

__all__ = ["XApiError", "XClient", "user_agent"]

API_HOST = "https://api.x.com"

#: Cap what we *read* from X before parse/audit.
#: Writes (POST /2/tweets) return a tiny tweet object — 8 KB is plenty and
#: keeps a runaway HTML dump out of x_audit.db.
#: Reads (mentions timeline + expansions) are not tiny. 8 KB truncated the
#: JSON mid-object, the parser treated a 200 as "non-JSON success body",
#: and the poller backed off an hour. 512 KB covers a full mentions page.
_MAX_WRITE_RESPONSE_BYTES = 8192
_MAX_READ_RESPONSE_BYTES = 512 * 1024
_MAX_RESPONSE_BYTES = _MAX_WRITE_RESPONSE_BYTES  # back-compat alias


def _bounded_response(resp: httpx.Response, limit: int = _MAX_RESPONSE_BYTES) -> tuple[Any, str, bool]:
    """Return ``(parsed_json_or_none, text, truncated)`` from a capped read."""
    raw = bytes(getattr(resp, "content", b"") or b"")
    truncated = len(raw) > limit
    chunk = raw[:limit]
    encoding = getattr(resp, "encoding", None) or "utf-8"
    try:
        text = chunk.decode(encoding, errors="replace")
    except (LookupError, UnicodeError):
        text = chunk.decode("utf-8", errors="replace")
    payload: Any = None
    if text:
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
    return payload, text, truncated


def _default_audit_action(method: str, path: str) -> str:
    """Human-readable action label derived from the endpoint."""
    if method == "POST" and path.startswith("/2/tweets"):
        return "post"
    if method == "DELETE" and path.startswith("/2/tweets/"):
        return "delete"
    if "/users/me" in path:
        return "verify_credentials"
    return f"{method.lower()} {path}"


@functools.lru_cache(maxsize=1)
def user_agent() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        ver = version("kazma")
    except PackageNotFoundError:
        ver = "0.10.0"
    return f"Kazma/{ver} (self-hosted; official X API v2)"


async def _audit(**fields: Any) -> None:
    """``log_x_event`` appends to x_audit.db (SQLite): keep it off the loop."""
    from kazma_core.x_api.operation_context import current_operation_id

    fields["operation_id"] = current_operation_id()
    await asyncio.to_thread(log_x_event, **fields)


def _rate_limit_hints(headers: Any) -> tuple[float | None, float | None]:
    """Return Retry-After seconds and the reset epoch as distinct values."""
    def number(value: Any) -> float | None:
        try:
            parsed = float(value)
            return parsed if math.isfinite(parsed) and parsed >= 0 else None
        except (TypeError, ValueError):
            return None

    raw = headers.get("retry-after")
    seconds = number(raw)
    if seconds is None and raw:
        try:
            date = parsedate_to_datetime(raw)
            seconds = max(0.0, date.timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            pass
    return seconds, number(headers.get("x-rate-limit-reset"))


class XApiError(Exception):
    def __init__(
        self, message: str, *, status: int = 0, transient: bool = False,
        retry_after_seconds: float | None = None, rate_limit_reset: float | None = None,
        outcome: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.transient = transient
        self.retry_after_seconds = retry_after_seconds
        self.rate_limit_reset = rate_limit_reset
        self.outcome = outcome or ("rejected" if 400 <= status < 500 else "unknown")


class XClient:
    def __init__(self, credentials: XCredentials | None = None) -> None:
        self._creds = credentials or _config.get_x_config().credentials

    def _headers(self, method: str, url: str) -> dict[str, str]:
        c = self._creds
        oauth = sign_request(
            method=method,
            url=url,
            consumer_key=c.api_key,
            consumer_secret=c.api_key_secret,
            token=c.access_token,
            token_secret=c.access_token_secret,
        )
        return {
            "Authorization": oauth1_authorization_header(oauth),
            "User-Agent": user_agent(),
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float = 20.0,
        audit_action: str = "",
        audit_tweet_id: str | None = None,
    ) -> dict[str, Any]:
        from kazma_core.x_api.shadow import shadow_transport_blocked

        if shadow_transport_blocked():
            raise XApiError("X network requests are blocked in isolated shadow evaluation.", outcome="not_sent")
        # Audit hook (operator decision 2026-08-27): EVERY X API call —
        # request payload, full response/error body, HTTP status, duration,
        # local date/time — is appended to kazma-data/x_audit.db. Best-effort
        # inside log_x_event; never blocks or breaks the call itself.
        action = audit_action or _default_audit_action(method, path)
        started = time.monotonic()
        if method.upper() == "GET":
            from kazma_core.x_api.ai_budget import reserve_read

            try:
                await asyncio.to_thread(reserve_read)
            except Exception as exc:
                await _audit(action=action, method=method, endpoint=path, status="budget_hold",
                             response_body={"error": "read_budget_unavailable"}, duration_ms=0)
                raise XApiError("X read budget is exhausted or unavailable. No request was sent; review X usage settings.",
                                outcome="not_sent") from exc
        url = f"{API_HOST}{path}"
        # user_agent() reads installed-package metadata on its first call
        # (then it is cached): stall-20260923-041456 caught that on the loop
        # for 45.6s. Warm it in a worker; later calls are a cache hit.
        await asyncio.to_thread(user_agent)
        headers = self._headers(method, url)
        if json_body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        try:
            # One process-wide SSL context, built off the loop: constructing
            # a client builds a fresh one otherwise, and that CA load is
            # where two loop-stall dumps caught the mentions poller.
            from kazma_core.llm_provider import _shared_ssl_context

            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=False, verify=await _shared_ssl_context()
            ) as client:
                resp = await client.request(method, url, headers=headers, json=json_body)
        except httpx.TimeoutException as exc:
            await _audit(
                action=action, method=method, endpoint=path, status="network_error",
                request_body=json_body, response_body={"error": "timeout"},
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            unsent = isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout))
            raise XApiError(
                "X API timed out. Did not retry (avoids double-post).",
                transient=True, outcome="not_sent" if unsent else "unknown",
            ) from exc
        except httpx.HTTPError as exc:
            await _audit(
                action=action, method=method, endpoint=path, status="network_error",
                request_body=json_body,
                response_body={"error": type(exc).__name__},
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            raise XApiError(
                f"X API network error: {type(exc).__name__}. Did not retry.",
                transient=True, outcome="not_sent" if isinstance(exc, httpx.ConnectError) else "unknown",
            ) from exc

        duration_ms = int((time.monotonic() - started) * 1000)

        limit = (
            _MAX_WRITE_RESPONSE_BYTES
            if method.upper() in ("POST", "PUT", "PATCH", "DELETE")
            else _MAX_READ_RESPONSE_BYTES
        )
        parsed, body_text, truncated = _bounded_response(resp, limit=limit)

        if resp.status_code in (200, 201):
            if not isinstance(parsed, dict):
                await _audit(
                    action=action, method=method, endpoint=path, status="error",
                    http_status=resp.status_code, request_body=json_body,
                    response_body={
                        "error": "non-JSON success body",
                        "raw": body_text[:2000],
                        "truncated": truncated,
                    },
                    duration_ms=duration_ms,
                )
                raise XApiError("X API returned a non-JSON success body.")
            # `data` is a dict for writes (POST /2/tweets) and a LIST for every
            # read (mentions timeline, /2/tweets?ids=, any timeline). Assuming
            # dict raised AttributeError *after* a successful 200, inside the
            # audit line — the call had already worked (2026-09-17 tier probe).
            data = parsed.get("data") or {}
            single_id = data.get("id") if isinstance(data, dict) else None
            await _audit(
                action=action, method=method, endpoint=path, status="success",
                http_status=resp.status_code,
                tweet_id=str(single_id or "") or audit_tweet_id,
                request_body=json_body, response_body=parsed,
                duration_ms=duration_ms,
            )
            return parsed

        detail = ""
        if isinstance(parsed, dict):
            err = parsed.get("detail") or parsed.get("title") or parsed.get("errors")
            detail = str(err)[:400] if err else body_text[:400]
        else:
            detail = body_text[:400]

        await _audit(
            action=action, method=method, endpoint=path, status="error",
            http_status=resp.status_code, tweet_id=audit_tweet_id,
            request_body=json_body,
            response_body={
                "detail": detail,
                "body": body_text[:2000],
                "truncated": truncated,
            },
            duration_ms=duration_ms,
        )

        if resp.status_code == 429:
            retry_after, reset = _rate_limit_hints(resp.headers)
            raise XApiError(
                "X rate limit (HTTP 429). Wait before retrying"
                + (f" (Retry-After {retry_after:g}s)" if retry_after is not None else "")
                + ". Kazma did not auto-retry.",
                status=429,
                transient=True,
                retry_after_seconds=retry_after,
                rate_limit_reset=reset,
            )
        if resp.status_code in (401, 403):
            low = detail.lower()
            if "deleted" in low or "not visible" in low:
                raise XApiError(
                    "That tweet is gone — deleted or not visible to this account. "
                    "Write a new mention; Refresh on Conversations pulls it from X. "
                    f"{detail}",
                    status=resp.status_code,
                )
            if "mentioned" in low or "are the author" in low:
                raise XApiError(
                    "X will only let this account reply to a post that mentions "
                    "it or that it wrote. Replying to the original post (not the "
                    f"mention) hits that wall. {detail}",
                    status=resp.status_code,
                )
            raise XApiError(
                f"X auth/permission error HTTP {resp.status_code}. "
                "Confirm the app is Read + Write and the four OAuth 1.0a user tokens "
                f"(not the Bearer token) are in Settings → X. {detail}",
                status=resp.status_code,
            )
        raise XApiError(
            f"X API HTTP {resp.status_code}: {detail or 'no body'}",
            status=resp.status_code,
        )

    async def verify_credentials(self) -> dict[str, Any]:
        data = await self._request(
            "GET",
            "/2/users/me?user.fields=username,name,id",
            audit_action="verify_credentials",
        )
        return data.get("data") or data

    async def create_tweet(self, text: str, *, reply_to_id: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {"text": text}
        if reply_to_id:
            body["reply"] = {"in_reply_to_tweet_id": str(reply_to_id).strip()}
        data = await self._request(
            "POST", "/2/tweets", json_body=body,
            audit_action="reply" if reply_to_id else "post",
        )
        tweet = data.get("data") or data
        ident = str(tweet.get("id") or "") if isinstance(tweet, dict) else ""
        if not ident.isascii() or not ident.isdigit():
            raise XApiError("X accepted the request but did not return a valid tweet ID. Outcome unknown; do not resend.", outcome="unknown")
        return tweet

    async def get_mentions(
        self,
        user_id: str,
        *,
        since_id: str = "",
        start_time: str = "",
        max_results: int = 25,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """``GET /2/users/:id/mentions`` — tweets mentioning this account.

        Returns ``(tweets, includes)``. ``includes["users"]`` carries the
        authors, because a mention is useless without knowing who sent it
        (the summoner allowlist is the first gate on every reply).

        **Not available on the Free tier** — it 403s there. The account this
        was built against probed HTTP 200 on 2026-09-17, i.e. Basic or above.
        Read quota is the scarce resource: poll interval is operator config
        (``connectors.x.reply.poll_interval_s``), not a constant here.
        """
        params = [
            f"max_results={max(5, min(100, int(max_results)))}",
            "tweet.fields=author_id,conversation_id,referenced_tweets,created_at,text,attachments,note_tweet",
            "expansions=author_id,referenced_tweets.id,referenced_tweets.id.author_id",
            "user.fields=username,public_metrics",
        ]
        sid = str(since_id or "").strip()
        if sid:
            params.append(f"since_id={sid}")
        # RFC 3339, second granularity, inclusive. The poller's cursor: a
        # time cannot be deleted, a since_id tweet can (mentions_fire).
        start = str(start_time or "").strip()
        if start:
            from urllib.parse import quote

            params.append(f"start_time={quote(start, safe='')}")
        path = f"/2/users/{str(user_id).strip()}/mentions?" + "&".join(params)
        data = await self._read_context(path, action="read_mentions")
        tweets = data.get("data")
        includes = data.get("includes")
        return (
            [self._canonical_post(t) for t in tweets] if isinstance(tweets, list) else [],
            self._canonical_includes(includes),
        )

    async def get_tweet(self, tweet_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """``GET /2/tweets/:id`` — one tweet plus its author. Basic tier and up.

        Used to fetch the *parent* of a summon: the post being replied to,
        whose text the drafter reacts to and whose author's follower count
        feeds the small-account floor.
        """
        params = (
            "tweet.fields=author_id,conversation_id,created_at,text,attachments,note_tweet,"
            "referenced_tweets"
            "&expansions=author_id,referenced_tweets.id,"
            "referenced_tweets.id.author_id"
            "&user.fields=username,public_metrics"
        )
        data = await self._read_context(f"/2/tweets/{str(tweet_id).strip()}?{params}",
                                        action="read_tweet", tweet_id=str(tweet_id).strip())
        tweet = data.get("data")
        includes = data.get("includes")
        return (
            self._canonical_post(tweet),
            self._canonical_includes(includes),
        )

    async def _read_context(self, path: str, *, action: str, tweet_id: str = "") -> dict[str, Any]:
        """Bounded read-only negotiation for legacy/current long-form field names."""
        try:
            return await self._request("GET", path, audit_action=action, audit_tweet_id=tweet_id)
        except XApiError as exc:
            if exc.status != 400 or "field" not in str(exc).lower():
                raise
        try:
            return await self._request("GET", path.replace("note_tweet", "note_post"),
                                       audit_action=action, audit_tweet_id=tweet_id)
        except XApiError as exc:
            if exc.status != 400 or "field" not in str(exc).lower():
                raise
        data = await self._request("GET", path.replace(",note_tweet", ""), audit_action=action, audit_tweet_id=tweet_id)
        posts = data.get("data")
        for post in posts if isinstance(posts, list) else [posts]:
            if isinstance(post, dict):
                post["_kazma_context_incomplete"] = True
        includes = data.get("includes") or {}
        for post in includes.get("tweets", includes.get("posts", [])):
            if isinstance(post, dict):
                post["_kazma_context_incomplete"] = True
        return data

    @staticmethod
    def _canonical_post(post: Any) -> dict[str, Any]:
        if not isinstance(post, dict):
            return {}
        post = dict(post)
        if "referenced_posts" in post and "referenced_tweets" not in post:
            post["referenced_tweets"] = post["referenced_posts"]
        if "note_post" in post and "note_tweet" not in post:
            post["note_tweet"] = post["note_post"]
        return post

    @classmethod
    def _canonical_includes(cls, includes: Any) -> dict[str, Any]:
        if not isinstance(includes, dict):
            return {}
        result = dict(includes)
        posts = includes.get("tweets", includes.get("posts"))
        if isinstance(posts, list):
            result["tweets"] = [cls._canonical_post(post) for post in posts]
        return result

    async def delete_tweet(self, tweet_id: str) -> dict[str, Any]:
        tid = str(tweet_id).strip()
        data = await self._request(
            "DELETE", f"/2/tweets/{tid}",
            audit_action="delete", audit_tweet_id=tid,
        )
        result = data.get("data") or data
        if not isinstance(result, dict) or result.get("deleted") is not True:
            raise XApiError("X did not confirm deletion. Outcome unknown; verify on X.", outcome="unknown")
        return result
