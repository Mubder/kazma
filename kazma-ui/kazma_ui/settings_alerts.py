"""Settings -> Alerts: one place for every sender's alerts (the unified alerts
plan, Stage 3, ``docs/plans/UNIFIED_ALERTS_PLAN.md``).

The tab replaces the alert parts of Adapters & Routes and System's read-only
outside-watch card. Every route is under ``/api/settings`` (admin only,
``platform_rbac``); every write goes through ``settings_validation`` and is
read by the sender it configures:

* ``notifications.ops.channels`` -- where Kazma's alerts go (``ops_alerts``);
  ``POST /api/settings/notifications/test-alert`` sends one along them.
* ``notifications.lifecycle.events`` -- the server status messages
  (``lifecycle_notifier``).
* ``notifications.ops.notices_to_chats`` -- whether info and success cards go
  to the chat apps, or only to the bell and the digest (``ops_alerts``).
* ``guard.telegram.chat_id`` -- the chat the guard pages (the guard's settings
  lookup, and ``ops_alerts``' direct fallback); the guard's test page is
  ``kazma_core.observability.guard_pager``.
* Kazma Watch -- pairing, unpairing, its test page and what it is told
  (``notifications.watch.email_to`` and ``cron.timezone``):
  ``kazma_core.observability.outside_watch``.

The pairing token the owner pastes is passed to the observer once and never
stored, logged or answered back. Blocking work runs in FastAPI's threadpool
(plain ``def`` routes) or ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from kazma_core.settings_validation import SettingRejected, validate_setting
from kazma_ui.json_body import InvalidJsonBody, invalid_json_response, read_json
from kazma_ui.same_origin import verify_same_origin

logger = logging.getLogger(__name__)

__all__ = ["register_alert_routes"]

#: The routes Kazma's alerts can take, in the order the page lists them.
_ROUTES = ("telegram", "telegram-group", "discord", "slack")


def _refused(code: str, status: int = 400, **extra: Any) -> JSONResponse:
    return JSONResponse({"ok": False, "code": code, **extra}, status_code=status)


def _routes_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [p.strip().lower() for p in raw.split(",") if p.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(p).strip().lower() for p in raw if str(p).strip()]
    return []


def _route_states(config_store: Any) -> dict[str, bool]:
    """Whether each route has what it needs to send (never a secret's value)."""
    from kazma_core.db.pg_helpers import store_errors

    def has(key: str) -> bool:
        try:
            return bool(str(config_store.get(key) or "").strip())
        except store_errors():  # unknown reads as not set up
            logger.debug("[alerts] %s not read", key, exc_info=True)
            return False

    group = config_store.get("swarm.output_target") or {}
    return {
        "telegram": has("connectors.telegram.token"),
        "telegram-group": isinstance(group, dict) and bool(group.get("enabled") and group.get("chat_id")),
        "discord": has("connectors.discord.token"),
        "slack": has("connectors.slack.token"),
    }


def _alerts_state(config_store: Any) -> dict[str, Any]:
    """What the tab shows (blocking: settings reads, a file, a signed request)."""
    from kazma_core.lifecycle_notifier import EVENT_NAMES, EVENTS_KEY, parse_lifecycle_events
    from kazma_core.observability.guard_pager import pager_status
    from kazma_core.observability.ops_alerts import notices_to_chats
    from kazma_core.observability.outside_watch import (
        EMAIL_TO_KEY,
        outside_status,
        watch_settings_wanted,
    )

    ready = _route_states(config_store)
    fallback = str(config_store.get("swarm.group_chat_id") or "").strip()
    raw_events = config_store.get(EVENTS_KEY)
    events = (list(parse_lifecycle_events(raw_events)[0]) if raw_events is not None
              else ["started", "startup_failed"])
    watch = outside_status()
    wanted = watch_settings_wanted(config_store)
    return {
        "routes": {
            "chosen": [r for r in _routes_list(config_store.get("notifications.ops.channels"))
                       if r in _ROUTES],
            "names": list(_ROUTES),
            "ready": ready,
        },
        "lifecycle": {"events": events, "names": list(EVENT_NAMES)},
        "notices_to_chats": notices_to_chats(config_store),
        "guard": {
            "chat": str(config_store.get("guard.telegram.chat_id") or ""),
            # kazma.yaml ships 0 for "no group chat".
            "fallback_chat": "" if fallback in ("", "0") else fallback,
            "bot": ready["telegram"],
            **pager_status(),
        },
        "watch": {
            **watch,
            "email_to": str(config_store.get(EMAIL_TO_KEY) or ""),
            "timezone": wanted["timezone"],
        },
    }


def _save_alerts(config_store: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Validate every field the body names, then write them in one batch.
    Raises :class:`SettingRejected` naming the field."""
    from kazma_core.lifecycle_notifier import EVENTS_KEY

    items: list[tuple[str, Any, str]] = []
    fields = {
        "routes": "notifications.ops.channels",
        "lifecycle_events": EVENTS_KEY,
        "notices_to_chats": "notifications.ops.notices_to_chats",
        "guard_chat": "guard.telegram.chat_id",
    }
    saved: list[str] = []
    for field, key in fields.items():
        if field not in body:
            continue
        value = body[field]
        if field == "routes" and isinstance(value, list):
            value = ",".join(str(v) for v in value)  # stored as the page always stored it
        try:
            value, category = validate_setting(key, value)
        except SettingRejected as exc:
            raise SettingRejected(f"{field}: {exc}") from None
        saved.append(field)
        if field == "routes":
            items.append(("notifications.ops.channels", value, category or "notifications"))
        elif field == "lifecycle_events":
            items.append(("notifications.lifecycle.events", value, category or "notifications"))
        elif field == "notices_to_chats":
            items.append(("notifications.ops.notices_to_chats", value, category or "notifications"))
        else:
            items.append(("guard.telegram.chat_id", value, category or "guard"))
    if items:
        config_store.batch_set(items)
    return {"ok": True, "saved": saved}


def _save_watch_settings(config_store: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Keep the email recipient, then tell Kazma Watch it and the time zone."""
    from kazma_core.observability.outside_watch import push_watch_settings

    items: list[tuple[str, Any, str]] = []
    if "email_to" in body:
        value, category = validate_setting("notifications.watch.email_to", body["email_to"])
        items.append(("notifications.watch.email_to", value, category or "notifications"))
    if items:
        config_store.batch_set(items)
    told = push_watch_settings(config_store)
    return {"ok": bool(told.get("ok")), "saved": bool(items), "told": told}


def register_alert_routes(router: APIRouter, config_store: Any) -> None:
    """Mount Settings -> Alerts' routes on the settings *router*."""

    @router.get("/api/settings/alerts")
    def api_get_alerts() -> dict[str, Any]:
        """Everything the tab shows: routes, status messages, notices, the
        guard's pager, Kazma Watch. Never a token or a key."""
        return _alerts_state(config_store)

    @router.put("/api/settings/alerts", dependencies=[Depends(verify_same_origin)])
    async def api_put_alerts(request: Request) -> Any:
        """Save the fields the body names (``routes``, ``lifecycle_events``,
        ``notices_to_chats``, ``guard_chat``); a value a field cannot hold is
        refused with what it may hold, and nothing is written."""
        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        if not isinstance(body, dict):
            return _refused("invalid_body")
        try:
            return await asyncio.to_thread(_save_alerts, config_store, body)
        except SettingRejected as exc:
            return _refused("rejected", error=str(exc))

    @router.post("/api/settings/alerts/guard-test", dependencies=[Depends(verify_same_origin)])
    def api_guard_test() -> Any:
        """Ask the running guard for a test page; the page polls the answer."""
        from kazma_core.observability.guard_pager import request_test_page

        result = request_test_page()
        if not result.get("ok"):
            return _refused(str(result.get("code") or "failed"), 409)
        return result

    @router.get("/api/settings/alerts/guard-test")
    def api_guard_test_result(id: str = "", at: float | None = None) -> dict[str, Any]:
        """The guard's answer to the test page *id* (asked at *at*)."""
        from kazma_core.observability.guard_pager import pager_test_result

        return pager_test_result(id[:40], at)

    @router.post("/api/settings/alerts/watch/pair", dependencies=[Depends(verify_same_origin)])
    async def api_watch_pair(request: Request) -> Any:
        """Pair with Kazma Watch: the observer's pairing token (used once,
        never kept) and its address. Then tell it the time zone."""
        from kazma_core.observability.outside_watch import (
            DEFAULT_OBSERVER_URL,
            PairingError,
            pair_with_observer,
            push_watch_settings,
        )

        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        if not isinstance(body, dict):
            return _refused("invalid_body")
        token = str(body.get("token") or "")
        url = str(body.get("url") or "").strip() or DEFAULT_OBSERVER_URL
        try:
            done = await asyncio.to_thread(pair_with_observer, url, token)
        except PairingError as exc:
            logger.warning("[alerts] pairing with %s did not happen (%s)", url, exc.code)
            return _refused(exc.code, 502 if exc.code in ("unreachable", "refused") else 400)
        told = await asyncio.to_thread(push_watch_settings, config_store)
        return {"ok": True, "install": done["install"], "url": done["url"], "told": told}

    @router.post("/api/settings/alerts/watch/unpair", dependencies=[Depends(verify_same_origin)])
    async def api_watch_unpair(request: Request) -> Any:
        """Stop Kazma Watch watching this install (``forget``: and delete
        what it kept), with a request signed by the install's key."""
        from kazma_core.observability.outside_watch import leave_observer

        try:
            body = await read_json(request, allow_empty=True)
        except InvalidJsonBody:
            return invalid_json_response()
        forget = bool(isinstance(body, dict) and body.get("forget") is True)
        result = await asyncio.to_thread(leave_observer, forget=forget)
        if not result.get("ok"):
            return _refused(str(result.get("error") or "failed"), 502)
        return result

    @router.post("/api/settings/alerts/watch/test", dependencies=[Depends(verify_same_origin)])
    def api_watch_test() -> Any:
        """Kazma Watch sends one test card; the answer names the channels
        that took it."""
        from kazma_core.observability.outside_watch import watch_test_page

        result = watch_test_page()
        if "channels" not in result:
            return _refused(str(result.get("error") or "failed"), 502)
        return result

    @router.put("/api/settings/alerts/watch/settings", dependencies=[Depends(verify_same_origin)])
    async def api_watch_settings(request: Request) -> Any:
        """Keep the email recipient and tell Kazma Watch it and the time zone."""
        try:
            body = await read_json(request, allow_empty=True)
        except InvalidJsonBody:
            return invalid_json_response()
        if not isinstance(body, dict):
            return _refused("invalid_body")
        try:
            return await asyncio.to_thread(_save_watch_settings, config_store, body)
        except SettingRejected as exc:
            return _refused("rejected", error=str(exc))

    @router.get("/api/settings/alerts/recent")
    def api_recent_alerts() -> dict[str, Any]:
        """The last 7 days of alerts from Kazma, its guard and Kazma Watch."""
        from kazma_core.observability.recent_alerts import recent_alerts

        return recent_alerts()
