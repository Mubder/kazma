"""Who changed memory, and how the change answered (Memory audit M35).

Every request that changes memory (``POST``/``PUT``/``PATCH``/``DELETE`` under
``/api/memory``) passes :class:`MemoryActionRecorder`, an ASGI middleware:

* **a trail** -- one INFO line (``[memory] operator action=...``) and one
  ``memory_audit_log`` row naming the route, the actor (from the request's
  session, never the body), the tenant, the ids in the path and the status:
  never a value. No memory mutation left a trail but undo, and a forget
  logged nothing on success. The daily digest counts the forgets from the line;
* **real status codes** -- a handler answering ``{"ok": false, ...}`` with 200
  is answered 404 (not found), 409 (refused: protected, a no-op, not active),
  422 (the request is wrong) or 500 (``safe_error``'s "code (ref: ...)").
  Most routes answered 200 for every failure, ``not_found`` included.

One recorder rather than a call in each of about thirty handlers: a new route
under ``/api/memory`` is recorded the day it is added.
``tests/test_memory_actions.py`` holds every mutating memory route to it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["MemoryActionRecorder", "action_counts"]

_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_PREFIX = "/api/memory"
#: Operator actions recorded in this process, by route (``kazma_memory_operator_actions``).
_COUNTS: Counter = Counter()

_NOT_FOUND = re.compile(r"not[ _]found|expired")
_REFUSED = re.compile(r"protected|no-op|returned nothing|not active|cannot ")
_INVALID = re.compile(r"required|invalid json|too long|too_long|must |provide |endpoint-only|body must|"
                      r"unknown |not allowed")
_SERVER = re.compile(r"\(ref: [0-9a-f]+\)|hygiene_partial|failed")

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
ASGIApp = Callable[[Scope, Callable[[], Awaitable[Message]], Callable[[Message], Awaitable[None]]], Awaitable[None]]


def _status_for(status: int, body: bytes) -> int:
    """The status a memory mutation's answer should carry: *status* unless
    the handler said ``ok: false`` with 200."""
    if status != 200 or not body:
        return status
    try:
        data = json.loads(body)
    except ValueError:
        return status
    if not isinstance(data, dict) or data.get("ok") is not False:
        return status
    error = str(data.get("code") or data.get("error") or "").lower()
    if _NOT_FOUND.search(error):
        return 404
    if _REFUSED.search(error):
        return 409
    if _INVALID.search(error):
        return 422
    if _SERVER.search(error):
        return 500
    return 409  # refused for a reason of its own: the request did not happen


def action_counts() -> dict[str, int]:
    """Operator memory actions recorded by this process, by route."""
    return dict(_COUNTS)


def _actor_and_tenant(headers: list[tuple[bytes, bytes]]) -> tuple[str, str]:
    """``web:<role>:<session8>`` and the session's tenant, from the session
    cookie (the approve route's rule: never a client-supplied label)."""
    from http.cookies import SimpleCookie

    from kazma_core.security.web_sessions import SESSION_COOKIE, get_session_payload

    raw = b"; ".join(v for k, v in headers if k.lower() == b"cookie").decode("latin-1")
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:  # noqa: BLE001 - a malformed cookie header names no session
        jar = SimpleCookie()
    sid = jar[SESSION_COOKIE].value if SESSION_COOKIE in jar else ""
    if not sid:
        auth = any(k.lower() == b"authorization" for k, _v in headers)
        return ("web:token" if auth else "web:anon"), "default"
    payload = get_session_payload(sid) or {}
    role = str(payload.get("role") or "viewer")
    tenant = str(payload.get("tenant_id") or "default")
    return f"web:{role}:{str(payload.get('session_id') or sid)[:8]}", tenant


def _record(action: str, ids: list[str], status: int, headers: list[tuple[bytes, bytes]]) -> None:
    """The trail: one INFO line and one audit row (ids, never values)."""
    try:
        actor, tenant = _actor_and_tenant(headers)
    except (sqlite3.Error, OSError, ValueError, KeyError):
        logger.warning("[memory] the actor of %s could not be read", action, exc_info=True)
        actor, tenant = "web:unknown", "default"
    ids = [str(i)[:120] for i in ids][:20]
    _COUNTS[action] += 1
    logger.info("[memory] operator action=%s actor=%s tenant=%s ids=%s status=%d",
                action.replace(" ", "_"), actor, tenant, ",".join(ids) or "-", status)
    from kazma_core.memory.schema_v2 import ensure_ops_schema
    from kazma_core.paths import memory_ops_db

    try:
        conn = sqlite3.connect(memory_ops_db(), timeout=5)
        try:
            ensure_ops_schema(conn)
            conn.execute(
                "INSERT INTO memory_audit_log (id, tenant_id, timestamp, event_type, target_table, target_id, "
                "actor, reason) VALUES (?, ?, ?, 'operator_action', 'memory', ?, ?, ?)",
                ("op_" + uuid.uuid4().hex[:20], tenant, time.time(), ",".join(ids)[:500] or "-", actor,
                 f"{action} -> {status}"),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        logger.warning("[memory] the audit row of %s was not written", action, exc_info=True)


class MemoryActionRecorder:
    """ASGI middleware: records every memory mutation and gives its failure
    answer a real status (see the module docstring)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Callable[[], Awaitable[Message]],
                       send: Callable[[Message], Awaitable[None]]) -> None:
        if (scope.get("type") != "http" or scope.get("method") not in _MUTATING
                or not str(scope.get("path", "")).startswith(_PREFIX)):
            await self.app(scope, receive, send)
            return
        held: dict[str, Any] = {}
        chunks: list[bytes] = []

        async def capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                held["start"] = message
                return
            if message["type"] == "http.response.body" and "start" in held and not held.get("sent"):
                chunks.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                body = b"".join(chunks)
                start = held["start"]
                status = _status_for(int(start["status"]), body)
                held["status"] = status
                headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
                headers.append((b"content-length", str(len(body)).encode()))
                held["sent"] = True
                await send({**start, "status": status, "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return
            await send(message)

        await self.app(scope, receive, capture)
        route = scope.get("route")
        template = getattr(route, "path", None) or str(scope.get("path", ""))
        ids = [str(v) for v in (scope.get("path_params") or {}).values()]
        status = int(held.get("status") or (held.get("start") or {}).get("status") or 500)
        await asyncio.to_thread(_record, f"{scope['method']} {template}", ids, status,
                                list(scope.get("headers") or []))
