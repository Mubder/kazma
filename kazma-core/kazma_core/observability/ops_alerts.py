"""Operational alerts: tell the operator when something failed silently.

Phase 2 of the resilience plan. The audit's finding was not that Kazma
fails — it is that Kazma fails *quietly*. Reply-persist failures, MCP
servers dropping out, turns ending with no answer, checkpoint writes
failing: every one of these already logs at WARNING or ERROR and stops
there. The operator found out by scrolling a transcript.

The delivery path already existed and was unused. ``lifecycle_notifier``
proves it works: ``get_message_bus().adapter.send(...)`` reaches Telegram.
This module is the missing half — the part that decides *what is worth
waking someone for*, and makes sure the answer is never "sixty identical
messages".

Design constraints, each earned:

* **Deduplicated and counted.** MCP failed 60 times in eight days. Sixty
  Telegram messages is the same as zero, because the channel gets muted.
  The first occurrence sends immediately; repeats inside the cooldown are
  counted and folded into the next message.
* **Never raises, never blocks.** These calls sit in exception handlers on
  hot paths. An alert that breaks the thing it is reporting on is worse
  than silence.
* **Callable from sync code.** The persist failure this exists for happens
  in a synchronous ``except`` block, not an async one.
* **Separately switchable.** ``KAZMA_OPS_ALERTS=0`` turns it off without
  touching lifecycle notifications, so a noisy incident can be silenced
  without going blind to everything else.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "alert",
    "ops_alerts_enabled",
    "reset_alert_state",
    "alert_state",
    "bus_send_targets",
]

# How long the same key stays quiet after being reported. Long enough that a
# flapping dependency cannot spam, short enough that a NEW outage of the same
# kind is still timely.
DEFAULT_COOLDOWN_S = float(os.environ.get("KAZMA_OPS_ALERT_COOLDOWN_S", "900"))

# Body text cap: Telegram hard-limits at 4096, and a wall of stack trace is
# unreadable on a phone at 3am anyway.
MAX_DETAIL_CHARS = 600


@dataclass
class _KeyState:
    """Throttle bookkeeping for one alert key."""

    first_seen: float = 0.0
    last_seen: float = 0.0
    since_sent: int = 0          # occurrences suppressed since the last send
    last_sent: float = 0.0
    total: int = 0

    def snapshot(self) -> dict:
        return {
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "suppressed": self.since_sent,
            "last_sent": self.last_sent,
            "total": self.total,
        }


_state: dict[str, _KeyState] = {}
_lock = threading.RLock()

#: In-flight alert-delivery threads, so they can be awaited (drain_alerts)
#: rather than left to write into whatever the process tears down next.
_dispatch_threads: set[threading.Thread] = set()
_threads_lock = threading.RLock()


def ops_alerts_enabled() -> bool:
    """Separate switch from lifecycle notifications."""
    raw = os.environ.get("KAZMA_OPS_ALERTS", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def reset_alert_state() -> None:
    """Clear throttle state (tests, and a deliberate operator reset)."""
    with _lock:
        _state.clear()


def alert_state() -> dict[str, dict]:
    """Current throttle bookkeeping, for diagnostics and the daily digest."""
    with _lock:
        return {k: v.snapshot() for k, v in _state.items()}


def _should_send(key: str, cooldown_s: float) -> tuple[bool, int]:
    """Record an occurrence. Returns (send_now, suppressed_since_last_send)."""
    now = time.time()
    with _lock:
        rec = _state.get(key)
        if rec is None:
            rec = _KeyState(first_seen=now)
            _state[key] = rec
        rec.last_seen = now
        rec.total += 1

        if rec.last_sent == 0.0 or (now - rec.last_sent) >= cooldown_s:
            suppressed = rec.since_sent
            rec.since_sent = 0
            rec.last_sent = now
            return True, suppressed

        rec.since_sent += 1
        return False, rec.since_sent


def _format(severity: str, title: str, detail: str, suppressed: int,
            cooldown_s: float) -> str:
    from kazma_core.observability.alert_card import format_operator_card

    body = (detail or "")[:MAX_DETAIL_CHARS]
    if suppressed:
        mins = int(cooldown_s // 60) or 1
        note = (
            f"(+{suppressed} more in the last {mins} min — "
            "further repeats are being counted, not sent)"
        )
        body = f"{body}\n{note}" if body else note
    return format_operator_card("Ops", severity, title, body)


def bus_send_targets(adapter: Any, channels: list[str]) -> list[Any]:
    """Bus adapters that should receive a routed notification.

    Empty ``channels`` means every configured adapter (legacy fan-out).
    ``telegram-group`` is not a bus adapter and is ignored here — the
    caller sends that route via ``_telegram_direct(group_route=True)``.
    Selecting only ``telegram-group`` must not silently fan out to
    Discord/Slack (empty-after-strip is not "all").
    """
    from kazma_core.swarm.bus import FanOutBusAdapter, NullBusAdapter

    if adapter is None or isinstance(adapter, NullBusAdapter):
        return []
    if isinstance(adapter, FanOutBusAdapter):
        targets = list(adapter.adapters)
    else:
        targets = [adapter]
    selected = [str(c).strip().lower() for c in (channels or []) if str(c).strip()]
    if not selected:
        return targets
    channels = [c for c in selected if c != "telegram-group"]
    if not channels:
        return []
    return [
        a for a in targets
        if str(getattr(a, "name", "") or "").lower() in channels
    ]


def _ops_channels() -> list[str]:
    """User-selected delivery platforms for ops + lifecycle alerts (live-read).

    ``notifications.ops.channels`` = e.g. ``["telegram", "discord"]``.
    Empty/missing = ALL configured platforms (the previous fan-out
    behaviour). Never raises — routing config problems must not mute an
    alert (the mute-theorem in the module header).
    """
    try:
        from kazma_core.config_store import get_config_store

        raw = get_config_store().get("notifications.ops.channels", [])
        if isinstance(raw, str):
            raw = [p.strip().lower() for p in raw.split(",") if p.strip()]
        if isinstance(raw, (list, tuple)):
            return [str(p).strip().lower() for p in raw if str(p).strip()]
    except Exception:
        logger.debug("[ops_alerts] channels read failed — routing to all", exc_info=True)
    return []


def _telegram_direct(text: str, *, group_route: bool = False) -> bool:
    """Send straight to Telegram, bypassing the in-process bus.

    The bus adapter only exists in the process the gateway initialised. An
    alert raised from a worker, a CLI command, or a standalone script gets
    ``NullBusAdapter`` -- and the first version of this module simply
    RETURNED there, reporting success while delivering nothing. That is the
    same "looks fine, does nothing" failure this whole project is about,
    and it fooled its own author on 2026-08-28.

    Credentials come from the vault the same way the supervisor reads them,
    over stdlib urllib so this works with nothing else initialised.
    """
    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        token = str(cs.get("connectors.telegram.token", "") or "").strip()
        chat = (
            str(cs.get("guard.telegram.chat_id", "") or "").strip()
            or str(cs.get("connectors.telegram.swarm_chat_id", "") or "").strip()
            or str(cs.get("swarm.group_chat_id", "") or "").strip()
        )
        # 'telegram-group' route (2026-09-03): deliver to the configured
        # group via the dedicated swarm bot token when present (the group
        # route's own bot), else the main bot token.
        if group_route:
            ot = cs.get("swarm.output_target", None)
            if isinstance(ot, dict) and ot.get("enabled") and ot.get("chat_id"):
                chat = str(ot["chat_id"])
                grp_token = str(ot.get("bot_token", "") or "").strip()
                if grp_token:
                    token = grp_token
        if not (token and chat):
            logger.warning(
                "[ops_alerts] NOT DELIVERED: no bus adapter and no Telegram "
                "credentials (connectors.telegram.token / "
                "guard.telegram.chat_id). The alert exists only in this log."
            )
            return False

        import urllib.parse
        import urllib.request

        body = urllib.parse.urlencode({
            "chat_id": chat,
            "text": text[:4000],
            "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body, method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ops_alerts] Telegram delivery failed: %s", exc)
        return False


async def _deliver(text: str) -> bool:
    """Deliver an alert. Returns whether it actually went anywhere.

    Prefers the in-process bus (it reaches every configured platform, not
    just Telegram) and falls back to a direct send. Silence is never an
    acceptable outcome here: if neither works, that is logged at WARNING
    rather than swallowed.

    Routing (2026-09-03): ``notifications.ops.channels`` selects which
    platforms receive ops alerts (empty = all configured). With a FanOut
    bus only the selected adapters are sent through; the Telegram-direct
    fallback only fires when Telegram is among the selected (or routing
    is unset).
    """
    channels = _ops_channels()
    # The group route is Telegram-direct only (it is not a bus adapter):
    # always fire it alongside whatever the bus delivers.
    sent_group = False
    if "telegram-group" in channels:
        try:
            sent_group = await asyncio.get_running_loop().run_in_executor(
                None, lambda t=text: _telegram_direct(t, group_route=True)
            )
        except Exception:  # noqa: BLE001
            logger.warning("[ops_alerts] telegram-group delivery failed", exc_info=True)
    try:
        from kazma_core.swarm.bus import (
            BusMessage,
            NullBusAdapter,
            get_message_bus,
        )

        adapter = get_message_bus().adapter
        if not isinstance(adapter, NullBusAdapter):
            targets = bus_send_targets(adapter, channels)
            if channels and not targets:
                if sent_group:
                    return True  # group route already delivered
                logger.info(
                    "[ops_alerts] channels %s match no configured "
                    "adapter — alert not bus-delivered (operator "
                    "routing choice)", channels,
                )
                return True  # deliberately routed nowhere on the bus
            msg = BusMessage(
                worker_name="Kazma",
                worker_role="ops",
                content=text[:4000],
                level="warn",
            )
            await asyncio.wait_for(
                asyncio.gather(*(a.send(msg) for a in targets)),
                timeout=10.0,
            )
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ops_alerts] bus delivery failed, trying direct: %s", exc)

    # No platform bus in this process (worker/CLI/script) -- go direct.
    # Direct is Telegram-only: honor the routing choice.
    if channels and "telegram" not in channels:
        if sent_group:
            return True
        logger.info(
            "[ops_alerts] no bus adapter and Telegram not in channels %s "
            "— alert exists only in this log (operator routing choice)",
            channels,
        )
        return True
    return await asyncio.get_running_loop().run_in_executor(
        None, _telegram_direct, text
    )


def _has_any_sink() -> bool:
    """True if anything could actually receive an alert right now.

    Conservative on purpose: any doubt returns True, because failing to
    deliver an alert is worse than spawning a thread that discovers there was
    nowhere to send it. What this removes is the case where we KNOW nothing
    can receive -- no bus adapter and no Telegram credentials -- and would
    otherwise start a thread, spin up an event loop, attempt a network call,
    fail, and log a warning from inside that thread.

    That is not just waste. The thread is what segfaults CPython when it
    writes to a descriptor whose owner is tearing down (audit 2026-09-16);
    not starting it at all is the only way to close the window rather than
    narrow it.
    """
    try:
        from kazma_core.swarm.bus import NullBusAdapter, get_message_bus

        if not isinstance(get_message_bus().adapter, NullBusAdapter):
            return True
    except Exception:  # noqa: BLE001 — probe must never decide by raising
        return True

    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        token = str(cs.get("connectors.telegram.token", "") or "").strip()
        chat = (
            str(cs.get("guard.telegram.chat_id", "") or "").strip()
            or str(cs.get("connectors.telegram.swarm_chat_id", "") or "").strip()
            or str(cs.get("swarm.group_chat_id", "") or "").strip()
        )
        return bool(token and chat)
    except Exception:  # noqa: BLE001
        return True


def _dispatch(text: str) -> None:
    """Deliver without blocking or raising, from sync OR async callers."""
    if not _has_any_sink():
        # The WARNING in alert() is already the durable record; this only says
        # the interrupt had nowhere to go.
        logger.debug(
            "[ops_alerts] no bus adapter and no Telegram credentials — "
            "alert exists only in the log; not starting a delivery thread"
        )
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        # Already on an event loop: fire and forget. Never await here — the
        # caller is usually inside an exception handler on a hot path.
        task = loop.create_task(_deliver(text))

        def _report(t: asyncio.Task) -> None:
            exc = t.exception() if not t.cancelled() else None
            if exc is not None:
                logger.warning("[ops_alerts] delivery raised: %s", exc)
            elif t.done() and t.result() is False:
                logger.warning("[ops_alerts] alert was NOT delivered anywhere")

        task.add_done_callback(_report)
        return

    def _run() -> None:
        try:
            if not asyncio.run(_deliver(text)):
                logger.warning("[ops_alerts] alert was NOT delivered anywhere")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ops_alerts] delivery raised: %s", exc)
        finally:
            with _threads_lock:
                _dispatch_threads.discard(threading.current_thread())

    # Retained, not fire-and-forget (audit 2026-09-16). This used to be a
    # bare `threading.Thread(...).start()` with no handle kept anywhere: the
    # thread ran asyncio.run(_deliver(...)) -- real network I/O -- and logged
    # warnings from inside it, with nothing able to wait for it or stop it.
    #
    # That is the raw-thread twin of audit F-07 (fire-and-forget asyncio
    # tasks), and it segfaulted CPython: a daemon thread writing to stderr
    # while pytest tore down its fd capture in pytest_runtest_teardown.
    # Reproduced on Linux 2026-09-16 -- tests/test_reply_sink.py crashed with
    # SIGSEGV on the one test that reaches an alert, and passed 17/17 with
    # KAZMA_OPS_ALERTS=0. Anything that can write to a descriptor after its
    # owner has gone needs a handle; see drain_alerts().
    thread = threading.Thread(target=_run, name="ops-alert", daemon=True)
    with _threads_lock:
        _dispatch_threads.add(thread)
    thread.start()


def drain_alerts(timeout: float = 5.0) -> int:
    """Wait for in-flight alert deliveries. Returns how many were still running.

    Alerting is deliberately fire-and-forget on the hot path -- it is called
    from exception handlers guarding the very failures it reports -- but
    "nobody waits for it" must not mean "nobody CAN wait for it". Call this
    before tearing down anything the delivery thread might write to (a log
    handler, a captured stderr, the interpreter itself).
    """
    with _threads_lock:
        pending = [t for t in _dispatch_threads if t.is_alive()]
    for t in pending:
        t.join(timeout=timeout)
    still = sum(1 for t in pending if t.is_alive())
    if still:
        logger.debug("[ops_alerts] %d delivery thread(s) still running", still)
    return still


def alert(
    key: str,
    title: str,
    detail: str = "",
    *,
    severity: str = "warn",
    cooldown_s: float | None = None,
) -> bool:
    """Report an operational failure. Returns True if a message was sent.

    Args:
        key: Throttle identity. Occurrences sharing a key are collapsed, so
            it must describe the CONDITION ("mcp.server_down"), not the
            instance — otherwise every failure is unique and nothing is ever
            deduplicated.
        title: One line, readable on a phone.
        detail: Optional context. Truncated; never a raw stack trace.
        severity: info | warn | error | critical.
        cooldown_s: Override the quiet period for this key.

    Never raises. Ever. This is called from exception handlers guarding the
    very paths it reports on.
    """
    try:
        if not ops_alerts_enabled():
            return False
        cooldown = DEFAULT_COOLDOWN_S if cooldown_s is None else cooldown_s
        send, suppressed = _should_send(key, cooldown)

        # Always leave a log line, sent or not: the log is the record, the
        # alert is only the interrupt.
        logger.warning(
            "[ops_alert] %s | %s%s",
            key, title, "" if send else " (throttled)",
        )
        if not send:
            return False
        _dispatch(_format(severity, title, detail, suppressed, cooldown))
        return True
    except Exception as exc:  # noqa: BLE001 — alerting must never propagate
        logger.debug("[ops_alerts] alert() failed for %s: %s", key, exc)
        return False
