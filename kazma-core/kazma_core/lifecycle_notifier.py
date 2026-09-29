"""Server lifecycle status notifications.

One card per boot (2026-09-29, the owner's request). A reload used to send
three messages -- "shutting down gracefully", "starting up" and "restarted"
-- and the last one listed the adapters by name without saying whether any
of them had connected. A boot now sends one card, once the chat apps have
connected or failed to::

    🟢 [System] Kazma restarted
    Down for 34.8 s · build 3b7422d

    Adapters
    ✅ Telegram
    ✅ Discord
    ❌ Slack: Slack refused the app-level token (invalid_auth)

    Model: deepseek-flash

``starting`` and ``shutting_down`` still exist and are off by default: the
stop is recorded either way, and the next card says how long Kazma was down
-- or that the last run did not shut down cleanly. ``startup_failed`` stays
on.

Design — reuse the SwarmMessageBus, do NOT build a parallel path:
- The bus is wired during ``KazmaAppBuilder.build()`` (via
  ``bus.set_adapter(...)``) *before* the lifespan runs, so by the time
  ``_on_startup``'s first line executes the adapter is already in place.
- Delivery honors ``notifications.ops.channels`` (Settings → Delivery
  routing). Empty = every configured adapter; a selection filters the
  FanOut the same way ``ops_alerts`` does. ``telegram-group`` is not a
  bus adapter and uses the ops-alerts group route. Do NOT construct new
  adapters.
- ``NullBusAdapter`` (no platform configured, or under pytest) silently
  drops the message, so the feature self-disables cleanly.
- The bus adapters are standalone ``httpx`` clients, independent of
  ``gateway.start()`` / ``gateway.stop()``, so notifications work during
  early startup (before the inbound poller is up) and late shutdown (after
  ``gateway.stop()``, which tears down the inbound adapters, not the bus).

Config (live-re-read on every call, mirroring ``get_hitl_config`` /
``get_proxy_provider`` — Settings → Adapters & Routes → Server status
messages, or ``kazma.yaml``; takes effect at the next boot/shutdown):
- ``notifications.lifecycle.enabled``           (bool, default True)
- ``notifications.lifecycle.events``            (list, default
  ``[started, startup_failed]``; an empty list sends nothing)
- ``notifications.lifecycle.restart_window_seconds`` (int, default 60; 0
  turns off restart detection: every boot says "started", with no downtime)

Run markers (internal ConfigStore keys, plaintext), written whether or not
the event is announced:
- ``system.lifecycle.last_shutdown_epoch`` -- at the top of every graceful
  shutdown (``notify_lifecycle("shutting_down")``).
- ``system.lifecycle.last_boot_epoch`` -- at the top of every boot
  (``notify_lifecycle("starting")`` → ``_record_boot``), after reading both:
  a shutdown stamp at or after the previous boot's means the previous run
  stopped cleanly; one before it means it did not (a crash, a forced stop,
  a power cut).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "CONNECT_WAIT_S",
    "EVENTS_KEY",
    "EVENT_NAMES",
    "PreviousRun",
    "announce_started",
    "get_lifecycle_config",
    "notify_lifecycle",
    "parse_lifecycle_events",
]

#: Every event an operator can switch on or off, in the order Settings
#: lists them. ``restarted`` is not one: ``started`` says it by itself.
EVENT_NAMES: tuple[str, ...] = ("started", "startup_failed", "starting", "shutting_down")
#: The setting that lists the events switched on.
EVENTS_KEY = "notifications.lifecycle.events"

# Card head per event. ``started`` / ``restarted`` are composed by
# ``_compose_start_card``, which picks the severity from what it reports.
_EVENTS: dict[str, dict[str, str]] = {
    "starting": {"level": "info", "label": "Kazma is starting"},
    "started": {"level": "success", "label": "Kazma started"},
    "restarted": {"level": "success", "label": "Kazma restarted"},
    "shutting_down": {"level": "warn", "label": "Kazma is shutting down"},
    "startup_failed": {"level": "error", "label": "Kazma failed to start"},
}

# Events a caller may pass in.
_CALLER_EVENTS = frozenset(EVENT_NAMES)

# Bounded send so a slow/unreachable platform API can never hang boot or
# shutdown. The bus adapter's own httpx timeout is 15s; we cap well below
# uvicorn's graceful-shutdown window (15s per the Dockerfile).
_SEND_TIMEOUT_SECONDS = 5.0

#: How long the start card waits for the chat apps to connect. Live they
#: connect ~4 s after the gateway starts (2026-09-29); one that has not
#: after this is reported as not connected.
CONNECT_WAIT_S = 45.0
_POLL_S = 0.5

# Internal ConfigStore keys: the last graceful shutdown and the last boot.
_LAST_SHUTDOWN_KEY = "system.lifecycle.last_shutdown_epoch"
_LAST_BOOT_KEY = "system.lifecycle.last_boot_epoch"

_DEFAULT_EVENTS = ["started", "startup_failed"]
_DEFAULT_RESTART_WINDOW = 60

#: A reason longer than this is cut on the card (the log has it whole).
_WHY_MAX = 200


@dataclass(frozen=True)
class PreviousRun:
    """How the run before this one ended, as the run markers recorded it."""

    #: When it began its graceful shutdown (epoch), when it did.
    stopped_at: float | None
    #: True: it shut down cleanly. False: it did not (a crash, a forced
    #: stop, a power cut). None: unknown (no run recorded before this one).
    clean: bool | None


#: This process's answer, set once by ``_record_boot`` (it stamps the boot,
#: so a second reading would see this very boot as the last one).
_boot: PreviousRun | None = None


def parse_lifecycle_events(raw: Any) -> tuple[list[str], list[str]]:
    """``(known events in EVENT_NAMES order, unknown names)`` in *raw*: a
    list or a comma-separated string. Anything else is one unknown name."""
    if raw is None:
        return [], []
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        parts = [str(p).strip() for p in raw if str(p).strip()]
    else:
        return [], [repr(raw)]
    known = [e for e in EVENT_NAMES if e in parts]
    unknown = [p for p in parts if p not in _CALLER_EVENTS]
    return known, unknown


def get_lifecycle_config() -> dict[str, Any]:
    """Re-read lifecycle-notification settings LIVE from the ConfigStore.

    Mirrors ``get_hitl_config`` / ``get_proxy_provider``: imports
    ``get_config_store`` locally inside a try, reads the flat dotted keys,
    and falls back to safe defaults on any error. Never raises. Blocking
    (a settings read): async callers run it with ``asyncio.to_thread``.

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

        raw_events = cs.get(EVENTS_KEY)
        if raw_events is not None:
            known, unknown = parse_lifecycle_events(raw_events)
            if unknown:
                logger.warning(
                    "[LifecycleNotifier] notifications.lifecycle.events names no such "
                    "event(s): %s (known: %s)", ", ".join(unknown), ", ".join(EVENT_NAMES),
                )
            # An empty list is the operator turning every message off;
            # a list of nothing but unknown names is a typo: the defaults.
            events = known if (known or not unknown) else list(_DEFAULT_EVENTS)

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
        logger.warning(
            "[LifecycleNotifier] config read failed, using defaults: %s", exc
        )

    return {
        "enabled": enabled,
        "events": events,
        "restart_window_seconds": max(0, restart_window),
    }


# ── run markers ──────────────────────────────────────────────────────────


def _as_epoch(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.debug("[LifecycleNotifier] malformed run marker %r", raw)
        return None


def _stamp(key: str) -> None:
    """Record now under *key*. Blocking; never raises."""
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().set(key, time.time(), category="internal")
    except Exception as exc:  # noqa: BLE001 — a marker must never break boot or shutdown
        logger.warning("[LifecycleNotifier] could not record %s: %s", key, exc)


def _read_previous_run() -> PreviousRun:
    """How the last run ended, from the markers (nothing is written).
    Blocking; never raises -- unreadable markers are "unknown"."""
    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        last_boot = _as_epoch(cs.get(_LAST_BOOT_KEY))
        last_shutdown = _as_epoch(cs.get(_LAST_SHUTDOWN_KEY))
    except Exception as exc:  # noqa: BLE001 — a card with less in it beats no card
        logger.warning("[LifecycleNotifier] could not read the run markers: %s", exc)
        return PreviousRun(stopped_at=None, clean=None)
    if last_shutdown is not None and (last_boot is None or last_shutdown >= last_boot):
        return PreviousRun(stopped_at=last_shutdown, clean=True)
    if last_boot is not None:
        return PreviousRun(stopped_at=None, clean=False)
    return PreviousRun(stopped_at=None, clean=None)


def _record_boot() -> PreviousRun:
    """Read how the last run ended, then stamp this boot -- once per
    process (a second call returns the first answer). Blocking."""
    global _boot
    if _boot is None:
        _boot = _read_previous_run()
        _stamp(_LAST_BOOT_KEY)
    return _boot


# ── the start card ───────────────────────────────────────────────────────


def _duration(seconds: float) -> str:
    """34.8 s · 5 min 12 s · 2 h 5 min · 3 d 4 h."""
    if seconds < 60:
        return f"{seconds:.1f} s"
    whole = int(seconds)
    if whole < 3600:
        m, s = divmod(whole, 60)
        return f"{m} min {s} s" if s else f"{m} min"
    if whole < 86400:
        h, m = divmod(whole // 60, 60)
        return f"{h} h {m} min" if m else f"{h} h"
    d, h = divmod(whole // 3600, 24)
    return f"{d} d {h} h" if h else f"{d} d"


def _short(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= _WHY_MAX else text[: _WHY_MAX - 1] + "…"


def _connection_line(conn: Mapping[str, Any], waited_s: float) -> tuple[bool, str]:
    """(connected?, the card line) for one adapter's report."""
    name = str(conn.get("name") or "Adapter")
    state = str(conn.get("state") or "")
    why = _short(str(conn.get("detail") or ""))
    if state in ("connected", "running"):
        return True, f"✅ {name}"
    if state == "connecting":
        line = f"❌ {name}: no connection after {int(round(waited_s))} s"
        return False, f"{line} ({why})" if why else line
    return False, f"❌ {name}: {why or 'not connected'}"


def _compose_start_card(
    previous: PreviousRun,
    *,
    now: float,
    restart_window_s: int,
    connections: Sequence[Mapping[str, Any]] | None,
    waited_s: float = 0.0,
    model: str = "",
    build: str = "",
    detail: str = "",
) -> tuple[str, str]:
    """``(severity, card text)`` for a finished boot. Pure.

    Green when every chat app connected and the last run stopped cleanly;
    yellow when one did not connect or the last run did not shut down
    cleanly. ``connections`` is None when the caller has no adapters to
    report (the bare ``notify_lifecycle("started", detail)`` form).
    """
    from kazma_core.observability.alert_card import format_operator_card

    detect = restart_window_s > 0
    down_for = None
    if detect and previous.clean and previous.stopped_at is not None:
        down_for = max(0.0, now - previous.stopped_at)
    restarted = down_for is not None and down_for <= restart_window_s
    title = _EVENTS["restarted" if restarted else "started"]["label"]
    worry = detect and previous.clean is False

    lines: list[str] = []
    head = ""
    if down_for is not None:
        head = f"Down for {_duration(down_for)}"
    elif worry:
        lines.append("The last run did not shut down cleanly (a crash, a forced stop or a power cut).")
    if build:
        head = f"{head} · build {build}" if head else f"Build {build}"
    if head:
        lines.append(head)

    if connections is not None:
        lines.append("")
        if connections:
            lines.append("Adapters")
            for conn in connections:
                ok, line = _connection_line(conn, waited_s)
                worry = worry or not ok
                lines.append(line)
        else:
            lines.append("Adapters: none set up")
    if detail.strip():
        lines.append(detail.strip())
    if model:
        if connections is not None:
            lines.append("")
        lines.append(f"Model: {model}")

    level = "warn" if worry else "success"
    return level, format_operator_card("System", level, title, "\n".join(lines))


async def _settled(
    connections: Callable[[], Sequence[Mapping[str, Any]]], wait_s: float
) -> list[dict[str, Any]]:
    """The adapters' report once none is still connecting, the wait is
    over, or Kazma began shutting down -- whichever comes first."""
    from kazma_core.shutdown import is_shutting_down

    deadline = time.monotonic() + max(0.0, wait_s)
    while True:
        report = [dict(c) for c in connections()]
        if not any(c.get("state") == "connecting" for c in report):
            return report
        if time.monotonic() >= deadline or is_shutting_down():
            return report
        await asyncio.sleep(_POLL_S)


async def announce_started(
    connections: Callable[[], Sequence[Mapping[str, Any]]] | None = None,
    *,
    model: str = "",
    build: str = "",
    detail: str = "",
    wait_s: float = CONNECT_WAIT_S,
) -> bool:
    """Send the start card: how long Kazma was down, each chat app's
    connection, the build and the model. True when a route took it.

    ``connections`` returns one ``{"name", "state", "detail"}`` per adapter
    (``GatewayManager.connection_report``); the card waits up to *wait_s*
    for every "connecting" one to connect or fail. The app runs this in the
    background: boot never waits for it. Not sent when ``started`` is off,
    or when Kazma begins shutting down during the wait.
    """
    from kazma_core.shutdown import is_shutting_down

    previous = _boot if _boot is not None else await asyncio.to_thread(_read_previous_run)
    cfg = await asyncio.to_thread(get_lifecycle_config)
    if not cfg["enabled"] or "started" not in cfg["events"]:
        logger.info("[LifecycleNotifier] Start card off (notifications.lifecycle)")
        return False
    began = time.monotonic()
    report = await _settled(connections, wait_s) if connections is not None else None
    if is_shutting_down():
        logger.info("[LifecycleNotifier] Start card not sent: Kazma began shutting down")
        return False
    level, text = _compose_start_card(
        previous,
        now=time.time(),
        restart_window_s=cfg["restart_window_seconds"],
        connections=report,
        waited_s=time.monotonic() - began,
        model=model,
        build=build,
        detail=detail,
    )
    delivered = await _deliver(text, level)
    down = [c["name"] for c in (report or []) if c.get("state") not in ("connected", "running")]
    logger.info(
        "[LifecycleNotifier] Start card (%s) sent to %s; adapters: %d, not connected: %s",
        text.split("\n", 1)[0], ", ".join(delivered) or "no route",
        len(report or []), ", ".join(down) or "none",
    )
    return bool(delivered)


# ── delivery ─────────────────────────────────────────────────────────────


async def _deliver(text: str, level: str) -> list[str]:
    """Send *text* to the alert routes (``notifications.ops.channels``);
    the names of the routes that took it. Never raises."""
    try:
        from kazma_core.observability import ops_alerts
        from kazma_core.swarm.bus import BusMessage, get_message_bus

        channels = await asyncio.to_thread(ops_alerts._ops_channels)
        targets = ops_alerts.bus_send_targets(get_message_bus().adapter, channels)
        msg = BusMessage(
            worker_name="Kazma",
            worker_role="system",
            content=text[:4000],
            level=level,
        )
        names = [str(getattr(a, "name", "") or type(a).__name__) for a in targets]
        sends: list[Any] = [a.send(msg) for a in targets]
        if "telegram-group" in channels:
            names.append("telegram-group")
            sends.append(asyncio.to_thread(ops_alerts._telegram_direct, text, group_route=True))
        if not sends:
            return []
        results = await asyncio.wait_for(
            asyncio.gather(*sends, return_exceptions=True),
            timeout=_SEND_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — never break boot/shutdown
        logger.warning("[LifecycleNotifier] could not send %r: %s", text.split("\n", 1)[0], exc)
        return []
    delivered = []
    for name, result in zip(names, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("[LifecycleNotifier] %s did not take the message: %s", name, result)
        elif result is False:
            logger.warning("[LifecycleNotifier] %s did not take the message", name)
        else:
            delivered.append(name)
    return delivered


async def notify_lifecycle(event: str, detail: str = "") -> None:
    """Record a lifecycle event, and announce it when it is switched on.

    Args:
        event: ``starting`` (records the boot), ``shutting_down`` (records
            the stop), ``startup_failed``, or ``started`` -- the start card
            without the adapters' report (the app sends the full card with
            ``announce_started``).
        detail: Optional extra body text (the boot error). Appended on its
            own line(s).

    Never raises — a notification failure is logged and swallowed so it can
    never break boot or shutdown. Sends nothing when the feature is
    disabled, the event isn't switched on, or no platform bus adapter is
    configured (NullBusAdapter / pytest). Routing is
    ``notifications.ops.channels`` — the same checkboxes as ops alerts.
    """
    if event not in _CALLER_EVENTS:
        logger.debug("[LifecycleNotifier] unknown event %r — ignoring", event)
        return
    if event == "started":
        await announce_started(detail=detail)
        return

    # The run's bookends are recorded whether or not they are announced:
    # the start card reads them.
    if event == "starting":
        await asyncio.to_thread(_record_boot)
    elif event == "shutting_down":
        await asyncio.to_thread(_stamp, _LAST_SHUTDOWN_KEY)

    cfg = await asyncio.to_thread(get_lifecycle_config)
    if not cfg["enabled"] or event not in cfg["events"]:
        return

    from kazma_core.observability.alert_card import format_operator_card

    spec = _EVENTS[event]
    text = format_operator_card("System", spec["level"], spec["label"], detail)
    await _deliver(text, spec["level"])
