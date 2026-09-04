"""Load a season transcript for the TUI without blocking on a hung HTTP hop."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

__all__ = [
    "coerce_visible_messages",
    "fetch_season_messages_http",
    "fetch_season_messages_http_async",
    "load_season_messages",
    "load_season_messages_async",
    "local_season_messages",
    "message_text",
    "session_messages_url",
]


def message_text(msg: dict[str, Any]) -> str:
    """Flatten string or OpenAI-style content parts into plain text."""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "\n".join(p for p in parts if p)
    if content is None:
        return str(msg.get("text") or "")
    return str(content)


def coerce_visible_messages(raw: Any) -> list[dict[str, Any]]:
    """Keep user/assistant rows with readable text."""
    if isinstance(raw, dict):
        raw = raw.get("messages") or raw.get("items") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for msg in raw:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").lower()
        if role not in ("user", "assistant"):
            continue
        text = message_text(msg)
        if not str(text).strip():
            continue
        row = {"role": role, "content": text}
        ts = msg.get("ts") or msg.get("timestamp")
        if ts:
            row["ts"] = ts
        out.append(row)
    return out


def session_messages_url(base: str, session_id: str) -> str:
    sid = quote(str(session_id), safe="")
    return f"{base.rstrip('/')}/api/chat/sessions/{sid}/messages"


def local_season_messages(session_id: str, thread_id: str = "") -> list[dict[str, Any]]:
    """Read the process-local SessionManager (same DB the TUI list uses)."""
    try:
        from kazma_ui.session_manager import get_session_manager

        sm = get_session_manager()
        if session_id.startswith("gw-") or (thread_id and thread_id.startswith("gw-")):
            try:
                sm._refresh_from_db(session_id)
                if thread_id and thread_id != session_id:
                    sm._refresh_from_db(thread_id)
            except Exception:
                logger.debug("TUI season DB refresh failed", exc_info=True)
        sess = sm.get(session_id)
        if sess is None and thread_id:
            sess = sm.get(thread_id) or sm.get_by_thread_id(thread_id)
        if sess is None:
            return []
        return coerce_visible_messages(sess.messages or [])
    except Exception:
        logger.debug("TUI local season history failed", exc_info=True)
        return []


def fetch_season_messages_http(
    session_id: str,
    *,
    timeout: float = 4.0,
    connect: float = 1.0,
) -> list[dict[str, Any]]:
    """GET /api/chat/sessions/{id}/messages from the live server."""
    import httpx

    from kazma_core.runtime.local_api import auth_headers, candidate_api_bases

    headers = dict(auth_headers())
    last_status = 0
    try:
        timeout_cfg = httpx.Timeout(timeout, connect=connect)
    except Exception:
        timeout_cfg = timeout
    for base in candidate_api_bases():
        url = session_messages_url(base, session_id)
        try:
            resp = httpx.get(url, headers=headers, timeout=timeout_cfg)
        except Exception:
            continue
        last_status = resp.status_code
        if resp.status_code >= 400:
            continue
        try:
            data = resp.json()
        except Exception:
            continue
        rows = coerce_visible_messages(data)
        if rows:
            return rows
    if last_status in (401, 403):
        logger.info(
            "TUI season HTTP %s for %s — set KAZMA_SECRET to the server secret",
            last_status,
            session_id[-8:] if session_id else "?",
        )
    return []


def load_season_messages(session_id: str, thread_id: str = "") -> list[dict[str, Any]]:
    """Local store first (instant), then HTTP if that row is empty."""
    rows = local_season_messages(session_id, thread_id)
    if rows:
        return rows
    rows = fetch_season_messages_http(session_id)
    if rows:
        return rows
    if thread_id and thread_id != session_id:
        return fetch_season_messages_http(thread_id)
    return []


async def fetch_season_messages_http_async(
    session_id: str,
    *,
    timeout: float = 4.0,
    connect: float = 1.0,
) -> list[dict[str, Any]]:
    """GET /api/chat/sessions/{id}/messages from the live server asynchronously."""
    import httpx

    from kazma_core.runtime.local_api import auth_headers, candidate_api_bases

    headers = dict(auth_headers())
    last_status = 0
    try:
        timeout_cfg = httpx.Timeout(timeout, connect=connect)
    except Exception:
        timeout_cfg = timeout
    for base in candidate_api_bases():
        url = session_messages_url(base, session_id)
        try:
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                resp = await client.get(url, headers=headers)
        except Exception:
            continue
        last_status = resp.status_code
        if resp.status_code >= 400:
            continue
        try:
            data = resp.json()
        except Exception:
            continue
        rows = coerce_visible_messages(data)
        if rows:
            return rows
    if last_status in (401, 403):
        logger.info(
            "TUI season HTTP %s for %s — set KAZMA_SECRET to the server secret",
            last_status,
            session_id[-8:] if session_id else "?",
        )
    return []


async def load_season_messages_async(session_id: str, thread_id: str = "") -> list[dict[str, Any]]:
    """Local store first (instant), then async HTTP if that row is empty."""
    import asyncio

    rows = await asyncio.to_thread(local_season_messages, session_id, thread_id)
    if rows:
        return rows
    rows = await fetch_season_messages_http_async(session_id)
    if rows:
        return rows
    if thread_id and thread_id != session_id:
        return await fetch_season_messages_http_async(thread_id)
    return []
