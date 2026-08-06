"""Server lifecycle status notifications.

Pushes a status update to every configured platform (Telegram/Discord/Slack)
when the Kazma server starts, restarts, shuts down, or fails to boot — so an
operator can tell from chat when something went wrong (hung boot, a crash
that emits no shutdown message, a bad bot token, etc.).

Design — reuse the SwarmMessageBus, do NOT build a parallel path:
- The bus is wired during ``KazmaAppBuilder.build()`` (via
  ``bus.set_adapter(...)``) *before* the lifespan runs, so by the time
  ``_on_startup``'s first line executes the adapter is already in place.
- ``FanOutBusAdapter`` fans a single ``adapter.send(BusMessage)`` out to
  every configured platform concurrently; each ``*BusAdapter`` holds its own
  destination ``chat_id`` / ``channel_id`` (from the existing
  ``connectors.<platform>.swarm_chat_id`` keys). No new recipient config.
- ``NullBusAdapter`` (no platform configured, or under pytest) silently
  drops the message, so the feature self-disables cleanly.
- The bus adapters are standalone ``httpx`` clients, independent of
  ``gateway.start()`` / ``gateway.stop()``, so notifications work during
  early startup (before the inbound poller is up) and late shutdown (after
  ``gateway.stop()``, which tears down the inbound adapters, not the bus).

Config (live-re-read on every call, mirroring ``get_hitl_config`` /
``get_proxy_provider`` — toggling via the Settings API takes effect on the
next boot/shutdown without a restart):
- ``notifications.lifecycle.enabled``           (bool, default True)
- ``notifications.lifecycle.events``            (list, default all four)
- ``notifications.lifecycle.restart_window_seconds`` (int, default 60; 0
  disables restart detection)

Restart detection:
- On ``shutting_down`` we stamp ``system.lifecycle.last_shutdown_epoch``
  (internal ConfigStore key, plaintext). On ``started``, if that epoch is
  within ``restart_window_seconds`` we report "🔄 Restarted" instead of
  "🟢 Started". A hard crash leaves no marker, so the next boot shows a
  plain "Started" — letting an operator distinguish restart from
  crash-recovery. ``restart_window_seconds: 0`` turns detection off.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["notify_lifecycle", "get_lifecycle_config"]

# Valid lifecycle events. ``restarted`` is *emitted* (never passed in by a
# caller) — ``notify_lifecycle("started")`` upgrades itself to ``restarted``
# when a recent graceful-shutdown marker is present.
_EVENTS: dict[str, dict[str, str]] = {
    "starting": {"icon": "🔵", "level": "info", "label": "Kazma server starting up", "level_label": "Info"},
    "started": {"icon": "🟢", "level": "success", "label": "Kazma server started", "level_label": "Success"},
    "restarted": {"icon": "🔄", "level": "info", "label": "Kazma server restarted", "level_label": "Info"},
    "shutting_down": {"icon": "🟡", "level": "warn", "label": "Kazma server shutting down gracefully", "level_label": "Warning"},
    "startup_failed": {"icon": "🔴", "level": "error", "label": "Kazma startup FAILED", "level_label": "Error"},
}

# Events a caller may pass in. ``restarted`` is synthesized internally.
_CALLER_EVENTS = {"starting", "started", "shutting_down", "startup_failed"}

# Bounded send so a slow/unreachable platform API can never hang boot or
# shutdown. The bus adapter's own httpx timeout is 15s; we cap well below
# uvicorn's graceful-shutdown window (15s per the Dockerfile).
_SEND_TIMEOUT_SECONDS = 5.0

# Internal ConfigStore key recording the epoch of the last graceful shutdown.
_LAST_SHUTDOWN_KEY = "system.lifecycle.last_shutdown_epoch"

_DEFAULT_EVENTS = ["starting", "started", "shutting_down", "startup_failed"]
_DEFAULT_RESTART_WINDOW = 60


def get_lifecycle_config() -> dict[str, Any]:
    """Re-read lifecycle-notification settings LIVE from the ConfigStore.

    Mirrors ``get_hitl_config`` / ``get_proxy_provider``: imports
    ``get_config_store`` locally inside a try, reads the flat dotted keys,
    and falls back to safe defaults on any error. Never raises.

    Returns a dict with keys ``enabled`` (bool), ``events`` (list[str]),
    ``restart_window_seconds`` (int).
    """
    enabled = True
    events: list[str] = list(_DEFAULT_EVENTS)
    restart_window = _DEFAULT_RESTART_WINDOW
    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()

        raw_enabled = cs.get("notifications.lifecycle.enabled")
        if raw_enabled is not None:
            enabled = bool(raw_enabled)

        raw_events = cs.get("notifications.lifecycle.events")
        if raw_events is not None:
            if isinstance(raw_events, str):
                # Tolerate a comma/whitespace-separated string.
                parts = [p.strip() for p in raw_events.split(",") if p.strip()]
            else:
                parts = list(raw_events)
            # Keep only valid event names; preserve caller order.
            cleaned = [p for p in parts if p in _CALLER_EVENTS]
            if cleaned:
                events = cleaned

        raw_window = cs.get("notifications.lifecycle.restart_window_seconds")
        if raw_window is not None:
            try:
                restart_window = int(raw_window)
            except (TypeError, ValueError):
                logger.debug(
                    "[LifecycleNotifier] ignoring non-int restart_window_seconds=%r",
                    raw_window,
                )
    except Exception as exc:  # noqa: BLE001 — config must never break boot
        logger.debug("[LifecycleNotifier] config read failed, using defaults: %s", exc)

    return {
        "enabled": enabled,
        "events": events,
        "restart_window_seconds": max(0, restart_window),
    }


def _stamp_shutdown_epoch() -> None:
    """Record the current epoch as the last graceful-shutdown time."""
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().set(_LAST_SHUTDOWN_KEY, time.time(), category="internal")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[LifecycleNotifier] failed to stamp shutdown epoch: %s", exc)


def _consume_recent_shutdown(window_seconds: int) -> float | None:
    """Return seconds-since-last-graceful-shutdown if within ``window``, else None.

    Returns None when there is no marker, the window is disabled (0), the
    stored value is unparseable, or the marker is stale. Does NOT clear the
    marker — a stale value is harmless and gets overwritten on next shutdown.
    """
    if window_seconds <= 0:
        return None
    try:
        from kazma_core.config_store import get_config_store

        raw = get_config_store().get(_LAST_SHUTDOWN_KEY)
        if raw is None:
            return None
        epoch = float(raw)
    except Exception:  # noqa: BLE001
        return None
    delta = time.time() - epoch
    if 0 <= delta <= window_seconds:
        return delta
    return None


async def notify_lifecycle(event: str, detail: str = "") -> None:
    """Push a lifecycle status update to every configured platform.

    Args:
        event: One of ``starting``, ``started``, ``shutting_down``,
            ``startup_failed``. ``started`` auto-upgrades to ``restarted``
            when a recent graceful-shutdown marker exists.
        detail: Optional extra body text (adapter list, model name, the
            boot error). Appended on its own line(s).

    Never raises — a notification failure is logged at debug level and
    swallowed so it can never break boot or shutdown. No-ops silently when
    the feature is disabled, the event isn't in the allow-list, or no
    platform bus adapter is configured (NullBusAdapter / pytest).
    """
    if event not in _CALLER_EVENTS:
        logger.debug("[LifecycleNotifier] unknown event %r — ignoring", event)
        return

    cfg = get_lifecycle_config()
    if not cfg["enabled"] or event not in cfg["events"]:
        return

    # Restart detection: stamp on shutdown, upgrade started→restarted.
    if event == "shutting_down":
        _stamp_shutdown_epoch()
    elif event == "started":
        down_for = _consume_recent_shutdown(cfg["restart_window_seconds"])
        if down_for is not None:
            event = "restarted"
            if detail:
                detail = f"was down ~{down_for:.1f}s\n{detail}"
            else:
                detail = f"was down ~{down_for:.1f}s"

    spec = _EVENTS[event]
    # Elegant format with bold key words:
    # Line 1: 🔵 Kazma — "Info"      (icon + name + level label in quotes)
    # Line 2: Kazma server starting up   (the label)
    # Line 3: Role: "System"             (role in quotes)
    text = f"{spec['icon']} Kazma — \"{spec['level_label']}\"\n{spec['label']}"
    if detail:
        text += f"\n{detail}"
    text += f"\nRole: \"System\""

    try:
        from kazma_core.swarm.bus import BusMessage, NullBusAdapter, get_message_bus

        bus = get_message_bus()
        adapter = bus.adapter
        if isinstance(adapter, NullBusAdapter):
            # No platform configured (or pytest) — nothing to send to.
            return
        await asyncio.wait_for(
            adapter.send(
                BusMessage(
                    worker_name="Kazma",
                    worker_role="system",
                    content=text[:4000],
                    level=spec["level"],
                )
            ),
            timeout=_SEND_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — never break boot/shutdown
        logger.debug("[LifecycleNotifier] send failed for %s: %s", event, exc)
