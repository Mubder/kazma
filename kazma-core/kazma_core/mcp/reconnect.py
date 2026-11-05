"""Bring MCP servers back after they drop, instead of losing them for good.

Phase 3 of the resilience plan. The audit paired every fault against its
recovery and found three with none at all; this was the clearest of them:
**60 connection failures in eight days, zero reconnects.** A server that
failed at boot stayed dead until somebody restarted Kazma, and the agent
carried on planning around tools that were no longer there.

``connect_from_config`` is already idempotent -- it skips healthy servers
and reconnects broken ones -- so the missing piece was never the connecting.
It was that nothing ever tried again.

Design notes
------------
* **Per-server backoff.** One flapping server must not set the retry pace
  for the others.
* **Hopeless errors back off to the cap immediately.** ``test-mcp`` on the
  reference host runs ``echo hello``; ``echo`` is a shell builtin, so it can
  never execute. Retrying that every 30 seconds forever is how a recovery
  mechanism becomes a new source of noise. It still retries at the cap,
  because the fix is a config edit and the operator should not have to
  restart to pick it up.
* **Recovery is worth saying out loud.** Phase 2 tells the operator when
  tools vanish; the counterpart is telling them when tools come back,
  otherwise the only news is bad news and the channel gets muted.
* **Never raises.** The loop is a background task next to the agent; an
  exception here must not take anything else down.
* **A "still down" page is owed its "back" note across restarts.** The page
  is marked in :mod:`kazma_core.observability.outage_pages`; a new process
  reads the marks at its first sweep, so a server that comes back after
  Kazma restarted is still announced (the acquisition monitor lost its note
  that way, live 2026-11-03 and 11-04).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["MCPReconnector", "classify_failure", "start_mcp_reconnector"]

# Seconds between sweeps. The loop is cheap: it only acts on servers whose
# own backoff has expired.
SWEEP_INTERVAL_S = float(30)

# Per-server backoff ladder, capped. Deliberately long at the end: an MCP
# server that has been down for half an hour is a config or environment
# problem, and hammering it helps nobody.
BACKOFF_LADDER_S: tuple[float, ...] = (30, 60, 120, 300, 900, 1800)

# Consecutive failed reconnects before the operator is told. The first
# failure is already reported by the connect path; this is for "it is still
# down and retrying is not helping".
ALERT_AFTER_ATTEMPTS = 3

#: Outage marks of servers paged "has not come back" (one per server).
_PAGE_PREFIX = "mcp.reconnect_failing:"

# Substrings that mean "retrying at speed cannot possibly help": the server
# is misconfigured rather than temporarily unreachable.
_HOPELESS_MARKERS = (
    "command not found",
    "no such file",
    "cannot find the file",
    "is not recognized",
    "unsupported transport",
    "must be an object",
    "permission denied",
)


def classify_failure(message: str) -> str:
    """``"hopeless"`` if only a config change can fix it, else ``"transient"``.

    The distinction is not about giving up -- both are retried -- but about
    the pace. A hopeless error goes straight to the slowest interval so a
    permanently broken entry cannot generate a retry storm.
    """
    low = str(message or "").lower()
    return "hopeless" if any(m in low for m in _HOPELESS_MARKERS) else "transient"


@dataclass
class _ServerState:
    attempts: int = 0
    next_attempt_at: float = 0.0
    last_error: str = ""
    kind: str = "transient"
    alerted: bool = False
    recovered_count: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "kind": self.kind,
            "last_error": self.last_error[:120],
            "retry_in_s": max(0, int(self.next_attempt_at - time.monotonic())),
            "alerted": self.alerted,
            "recovered": self.recovered_count,
        }


class MCPReconnector:
    """Retries failed MCP servers with per-server backoff."""

    def __init__(
        self,
        manager: Any,
        config_provider: Callable[[], list[dict[str, Any]]],
        *,
        sweep_interval_s: float = SWEEP_INTERVAL_S,
    ) -> None:
        self._manager = manager
        self._config = config_provider
        self._sweep = sweep_interval_s
        self._state: dict[str, _ServerState] = {}
        self._stop = False
        # Servers paged "has not come back" whose "back" note is still owed,
        # read from the outage marks at the first sweep (None until then).
        self._owed: set[str] | None = None

    # -- introspection -------------------------------------------------

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Per-server retry state, for health checks and the daily digest."""
        return {name: st.snapshot() for name, st in self._state.items()}

    def stop(self) -> None:
        self._stop = True

    # -- internals -----------------------------------------------------

    def _backoff_for(self, st: _ServerState) -> float:
        if st.kind == "hopeless":
            # Straight to the cap: only a config edit will fix it, and the
            # sweep will still pick that up without a restart.
            return BACKOFF_LADDER_S[-1]
        idx = min(max(st.attempts - 1, 0), len(BACKOFF_LADDER_S) - 1)
        return BACKOFF_LADDER_S[idx]

    def _due(self, name: str) -> bool:
        st = self._state.get(name)
        return st is None or time.monotonic() >= st.next_attempt_at

    def _alert(self, key: str, title: str, detail: str, severity: str) -> None:
        try:
            from kazma_core.observability.ops_alerts import alert

            alert(key, title, detail, severity=severity)
        except Exception:  # noqa: BLE001 — alerting must never break recovery
            logger.debug("[MCP-reconnect] alert failed", exc_info=True)

    async def _load_owed(self) -> None:
        """The servers an earlier process paged and never said were back."""
        from kazma_core.observability.outage_pages import owed_outage_pages

        # The marks never raise: an unreadable file is logged there and read
        # as no marks, so the sweep runs either way.
        marks = await asyncio.to_thread(owed_outage_pages, _PAGE_PREFIX)
        self._owed = {key[len(_PAGE_PREFIX):] for key in marks}

    async def _mark(self, name: str, *, owed: bool, cause: str = "") -> None:
        """Record (or settle) the "back" note owed for *name*."""
        from kazma_core.observability.outage_pages import note_outage_page, settle_outage_page

        if self._owed is None:
            self._owed = set()
        # A mark that cannot be written is logged by outage_pages and never
        # raised: it costs only the note after a restart.
        if owed:
            await asyncio.to_thread(note_outage_page, _PAGE_PREFIX + name,
                                    since=time.time(), cause=cause)
            self._owed.add(name)
        else:
            await asyncio.to_thread(settle_outage_page, _PAGE_PREFIX + name)
            self._owed.discard(name)

    def _connected_names(self) -> set[str]:
        """Names the manager currently considers connected.

        Uses ``list_servers()`` rather than ``is_server_connected``: for
        stdio transports it verifies the child process is still alive, which
        the manager's own comment calls avoiding "the reconnect lie where
        status says connected but the server is dead". A reconnect
        supervisor that trusts a lie is worse than none.

        ``is_server_connected`` lives on UnifiedToolExecutor and NOT on the
        manager, so it is only a fallback for objects that expose it.
        """
        try:
            rows = self._manager.list_servers() or []
            return {
                str(r.get("name")) for r in rows
                if isinstance(r, dict) and r.get("connected")
            }
        except Exception:
            pass
        names: set[str] = set()
        probe = getattr(self._manager, "is_server_connected", None)
        if callable(probe):
            try:
                for r in (self._config() or []):
                    n = str(r.get("name") or "")
                    if n and probe(n):
                        names.add(n)
            except Exception:
                return set()
        return names

    async def sweep_once(self) -> int:
        """One pass. Returns how many servers came back. Never raises."""
        recovered = 0
        try:
            # A settings read plus a YAML file: stall-20260920-221210 (49.7s)
            # and stall-20260923-171300 (28.5s) caught it here on the loop.
            configured = list(await asyncio.to_thread(self._config) or [])
        except Exception as exc:  # noqa: BLE001
            logger.debug("[MCP-reconnect] config unavailable: %s", exc)
            return 0

        from kazma_core.mcp_servers_store import server_enabled

        by_name = {
            str(c.get("name") or "unnamed"): c
            for c in configured
            if isinstance(c, dict) and server_enabled(c)
        }
        errors = dict(getattr(self._manager, "connection_errors", {}) or {})
        connected_names = self._connected_names()

        if self._owed is None:
            await self._load_owed()
        owed = self._owed if self._owed is not None else set()

        # Forget state for servers that are no longer configured or have been
        # disabled -- otherwise a removed server retries forever. A removed
        # server is owed no "back" note.
        for gone in [n for n in self._state if n not in by_name]:
            self._state.pop(gone, None)
        for gone in [n for n in owed if n not in by_name]:
            await self._mark(gone, owed=False)

        for name, cfg in by_name.items():
            if name in connected_names:
                st = self._state.pop(name, None)
                if st is not None and st.attempts:
                    st.recovered_count += 1
                    logger.info(
                        "[MCP-reconnect] '%s' recovered after %d attempt(s)",
                        name, st.attempts,
                    )
                    self._alert(
                        "mcp.server_recovered",
                        f"MCP server '{name}' is back — its tools are available again.",
                        f"Recovered after {st.attempts} retry attempt(s).",
                        "info",
                    )
                    recovered += 1
                elif name in owed:
                    logger.info("[MCP-reconnect] '%s' is back (paged before a restart)", name)
                    self._alert(
                        "mcp.server_recovered",
                        f"MCP server '{name}' is back — its tools are available again.",
                        "It was still down when Kazma restarted.",
                        "info",
                    )
                    recovered += 1
                if name in owed:
                    await self._mark(name, owed=False)
                continue

            if name in owed and name not in self._state:
                # Paged by an earlier process: keep retrying, never page twice.
                self._state[name] = _ServerState(alerted=True)

            if name not in errors and name not in self._state:
                # Not connected and not previously an error: it may simply
                # not have been started yet. Let the boot path own that.
                continue

            if not self._due(name):
                continue

            st = self._state.setdefault(name, _ServerState())
            st.attempts += 1
            st.last_error = str(errors.get(name, "") or st.last_error)
            st.kind = classify_failure(st.last_error)

            try:
                await self._manager.connect_from_config([cfg])
                ok = name in self._connected_names()
                if not ok:
                    st.last_error = str(
                        dict(getattr(self._manager, "connection_errors", {}) or {})
                        .get(name, st.last_error)
                    )
                    st.kind = classify_failure(st.last_error)
            except Exception as exc:  # noqa: BLE001
                st.last_error = str(exc)
                st.kind = classify_failure(st.last_error)
                ok = False

            if ok:
                logger.info("[MCP-reconnect] '%s' reconnected", name)
                self._alert(
                    "mcp.server_recovered",
                    f"MCP server '{name}' is back — its tools are available again.",
                    f"Recovered after {st.attempts} retry attempt(s).",
                    "info",
                )
                self._state.pop(name, None)
                if name in owed:
                    await self._mark(name, owed=False)
                recovered += 1
                continue

            delay = self._backoff_for(st)
            st.next_attempt_at = time.monotonic() + delay
            logger.info(
                "[MCP-reconnect] '%s' still down (%s, attempt %d) — retrying in %ds",
                name, st.kind, st.attempts, int(delay),
            )
            if st.attempts >= ALERT_AFTER_ATTEMPTS and not st.alerted:
                st.alerted = True
                self._alert(
                    "mcp.reconnect_failing",
                    f"MCP server '{name}' has not come back after "
                    f"{st.attempts} retries.",
                    f"{st.kind}: {st.last_error[:120]}. Still retrying every "
                    f"{int(delay)}s.",
                    "warn",
                )
                await self._mark(name, owed=True, cause=st.last_error[:120])
        return recovered

    async def run(self) -> None:
        """Sweep forever. Cancellation-safe; individual failures are absorbed."""
        logger.info("[MCP-reconnect] supervisor started (every %ds)", int(self._sweep))
        while not self._stop:
            try:
                await asyncio.sleep(self._sweep)
                if self._stop:
                    break
                from kazma_core.db import availability

                # The stored configs are the database's, and a database
                # outage is usually Docker's: every container-run server is
                # down with it. Retrying then only pages "not coming back".
                if await availability.wait_while_away():
                    break
                await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a bad sweep must not end the loop
                logger.warning("[MCP-reconnect] sweep failed", exc_info=True)


_task_refs: set[asyncio.Task] = set()


def start_mcp_reconnector(
    manager: Any,
    config_provider: Callable[[], list[dict[str, Any]]],
) -> MCPReconnector | None:
    """Start the reconnect supervisor as a background task.

    The task is held in a module-level set: an unreferenced asyncio task can
    be garbage-collected mid-loop, which is the "scheduler existed but never
    ran" failure this codebase has already hit.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("[MCP-reconnect] no running loop — not started")
        return None
    rec = MCPReconnector(manager, config_provider)
    task = loop.create_task(rec.run())
    _task_refs.add(task)
    task.add_done_callback(_task_refs.discard)
    return rec
