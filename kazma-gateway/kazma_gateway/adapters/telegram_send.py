"""Telegram send helpers — chat_id resolve, chunking, send-with-retry (S5)."""

from __future__ import annotations

import asyncio
import html as html_lib
import logging
import random
import re
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_LEN = 4096
SEND_MAX_RETRIES = 3
SEND_BASE_DELAY = 1.0

# Track blockquote open/close when splitting HTML across messages
_BLOCKQUOTE_TAG_RE = re.compile(
    r"</?blockquote(?:\s[^>]*)?>",
    re.IGNORECASE,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def resolve_chat_id(
    context_metadata: dict[str, Any],
    target_id: str,
) -> int | None:
    """Resolve a numeric Telegram chat_id from metadata or target_id."""
    chat_id = context_metadata.get("chat_id")
    if chat_id:
        try:
            return int(chat_id)
        except (TypeError, ValueError):
            logger.error("[telegram] Invalid chat_id in metadata: %s", chat_id)
            return None
    if ":" in target_id:
        try:
            return int(target_id.split(":", 1)[1])
        except (ValueError, IndexError):
            logger.error(
                "[telegram] Cannot parse chat_id from target_id: %s",
                target_id,
            )
            return None
    logger.error("[telegram] No chat_id available for send()")
    return None


def strip_telegram_html(text: str) -> str:
    """Remove HTML tags and unescape entities for plain-text fallback."""
    if not text:
        return ""
    return html_lib.unescape(_HTML_TAG_RE.sub("", text))


def _find_html_split(window: str, max_len: int) -> int:
    """Pick a split index inside *window* that prefers tag/paragraph boundaries."""
    min_keep = max(64, max_len // 5)
    for marker in ("</blockquote>\n", "</blockquote>", "\n\n", "\n", " "):
        idx = window.rfind(marker)
        if idx >= min_keep:
            return idx + len(marker)
    return max_len


def chunk_html_message(text: str, max_len: int = TELEGRAM_MAX_MESSAGE_LEN) -> list[str]:
    """Split HTML text without breaking tags; re-wrap open blockquotes across chunks.

    Naive ``text[i:i+4096]`` splits mid-tag (e.g. ``<co`` / ``de>``), which makes
    Telegram reject parse_mode=HTML and fall back to raw tags. This keeps each
    chunk valid HTML for the tags we emit (b/code/blockquote).
    """
    if not text:
        return [""]
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text
    # Leave room to append closing </blockquote> tags without exceeding max_len
    close_reserve = len("</blockquote>") * 2  # we only nest one level in practice
    body_limit = max(256, max_len - close_reserve)

    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        window = remaining[:body_limit]
        split_at = _find_html_split(window, body_limit)

        # Never cut inside an HTML tag
        last_lt = window[:split_at].rfind("<")
        last_gt = window[:split_at].rfind(">")
        if last_lt > last_gt:
            split_at = last_lt if last_lt > 0 else body_limit

        if split_at <= 0:
            split_at = body_limit

        part = remaining[:split_at]
        rest = remaining[split_at:]

        # Close any unclosed <blockquote> in this chunk, reopen on the next
        depth = 0
        for m in _BLOCKQUOTE_TAG_RE.finditer(part):
            tag = m.group(0).lower()
            if tag.startswith("</"):
                depth = max(0, depth - 1)
            else:
                depth += 1

        if depth > 0:
            part = part + ("</blockquote>" * depth)
            rest = ("<blockquote>" * depth) + rest

        # Hard safety: never emit over Telegram's limit
        if len(part) > max_len:
            part = part[:max_len]

        chunks.append(part)
        remaining = rest

    return chunks


def chunk_message(
    text: str,
    max_len: int = TELEGRAM_MAX_MESSAGE_LEN,
    *,
    parse_mode: str | None = None,
) -> list[str]:
    """Split *text* into Telegram-safe message chunks.

    When *parse_mode* is HTML, use tag-aware splitting so markup stays valid.
    """
    if not text:
        return [""]
    if parse_mode and str(parse_mode).upper() == "HTML":
        return chunk_html_message(text, max_len=max_len)
    return [text[i : i + max_len] for i in range(0, len(text), max_len)]


async def send_chunks_with_retry(
    *,
    http: httpx.AsyncClient,
    chat_id: int,
    chunks: list[str],
    parse_mode: str | None,
    reply_markup: Any | None,
    rate_acquire: Callable[[], Awaitable[None]],
    max_retries: int = SEND_MAX_RETRIES,
    base_delay: float = SEND_BASE_DELAY,
) -> bool:
    """POST sendMessage for each chunk with 429 backoff and Markdown fallback.

    Returns True if every chunk was accepted by Telegram.
    """
    all_sent = True
    for chunk_idx, chunk in enumerate(chunks):
        is_last = chunk_idx == len(chunks) - 1
        payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if is_last and reply_markup:
            payload["reply_markup"] = reply_markup

        parse_mode_fallback_done = False
        chunk_sent = False

        for attempt in range(max_retries):
            try:
                await rate_acquire()
                resp = await http.post("/sendMessage", json=payload)

                if resp.status_code == 429:
                    try:
                        body = resp.json()
                        retry_after = body.get("parameters", {}).get(
                            "retry_after",
                            base_delay * (2**attempt),
                        )
                    except Exception:
                        retry_after = base_delay * (2**attempt)
                    wait = float(retry_after) + random.uniform(0.5, 1.5)
                    logger.warning(
                        "[telegram] Rate-limited (429) on send to %d — "
                        "retrying in %.1fs (attempt %d/%d)",
                        chat_id,
                        wait,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(wait)
                    continue

                resp.raise_for_status()
                result = resp.json()
                if result.get("ok"):
                    chunk_sent = True
                    break
                logger.error("[telegram] sendMessage not ok: %s", result)
                chunk_sent = False
                break

            except httpx.HTTPStatusError as exc:
                if (
                    exc.response.status_code == 400
                    and "parse_mode" in payload
                    and not parse_mode_fallback_done
                ):
                    logger.debug(
                        "[telegram] sendMessage 400 — retrying without parse_mode"
                    )
                    payload.pop("parse_mode", None)
                    # Strip markup so users don't see raw <b>/<blockquote> tags
                    if parse_mode and str(parse_mode).upper() == "HTML":
                        payload["text"] = strip_telegram_html(chunk)
                    parse_mode_fallback_done = True
                    continue
                try:
                    err_body = exc.response.text[:300]
                except Exception:
                    err_body = "<unreadable>"
                logger.error(
                    "[telegram] HTTP %d on send to %d: %s",
                    exc.response.status_code,
                    chat_id,
                    err_body,
                )
                chunk_sent = False
                break
            except Exception:
                logger.exception("[telegram] Failed to send to %d", chat_id)
                chunk_sent = False
                break

        if not chunk_sent:
            all_sent = False
            break

    return all_sent
