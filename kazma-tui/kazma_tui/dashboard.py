"""Metrics dashboard widget for the Kazma TUI.

Displays real-time system and application metrics:
- CPU percentage and RAM usage (from HardwareMonitor)
- Requests per minute (from TraceStore)
- Average latency (from MetricsCollector)
- Error rate percentage (from MetricsCollector)
- Active agents list (from SwarmEngine)

Refreshes every 2 seconds using Textual's ``set_interval``.
Missing or unavailable metrics are shown as "N/A".
"""

from __future__ import annotations

import logging
import time
from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

logger = logging.getLogger(__name__)

_NA = "N/A"


class MetricsDashboard(Widget):
    """Real-time metrics dashboard widget.

    Displays CPU, RAM, RPM, latency, error rate, and active agents.
    Refreshes every 2 seconds. Data sources are injectable for testing.

    Layout::

        CPU: 45.2%  |  RAM: 16.4 / 32.0 GB
        RPM: 120    |  Latency: 250.0ms
        Errors: 5.88%  |  Agents: analyst, researcher
    """

    REFRESH_INTERVAL: float = 2.0

    DEFAULT_CSS = """
    MetricsDashboard {
        height: 1fr;
        border: solid $primary;
        padding: 1 2;
    }

    MetricsDashboard > .metric-row {
        height: auto;
        margin: 0 0 1 0;
    }

    MetricsDashboard > .metric-row > Static {
        width: 1fr;
    }
    """

    def __init__(
        self,
        *,
        hardware_monitor: Any = None,
        trace_store: Any = None,
        metrics_collector: Any = None,
        swarm_engine: Any = None,
    ) -> None:
        """Initialise the dashboard.

        All data source parameters are optional. When ``None``, the
        widget lazily imports the global singletons on first refresh.
        """
        super().__init__()
        self._hardware_monitor = hardware_monitor
        self._trace_store = trace_store
        self._metrics_collector = metrics_collector
        self._swarm_engine = swarm_engine

    # ── Textual lifecycle ───────────────────────────────────────────

    def compose(self) -> ComposeResult:
        """Compose the dashboard layout with metric rows."""
        with Static(classes="metric-row"):
            yield Static(self._format_cpu(None), id="metric-cpu")
            yield Static(self._format_ram(None, None), id="metric-ram")
        with Static(classes="metric-row"):
            yield Static(self._format_rpm(None), id="metric-rpm")
            yield Static(self._format_latency(None), id="metric-latency")
        with Static(classes="metric-row"):
            yield Static(self._format_error_rate(None), id="metric-errors")
            yield Static(self._format_agents([]), id="metric-agents")

    def on_mount(self) -> None:
        """Start periodic refresh on mount."""
        self._refresh_now()
        self.set_interval(self.REFRESH_INTERVAL, self._refresh_now)

    def _refresh_now(self) -> None:
        """Fetch all metrics and update widgets synchronously."""
        try:
            self._do_refresh()
        except Exception:
            logger.exception("Dashboard refresh failed")

    def _do_refresh(self) -> None:
        """Fetch metrics from all sources and update display widgets."""
        # ── CPU / RAM ───────────────────────────────────────────────
        cpu_text = self._format_cpu(None)
        ram_text = self._format_ram(None, None)
        monitor = self._get_hardware_monitor()
        if monitor is not None:
            try:
                import asyncio

                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Schedule async fetch; update on next tick
                    import asyncio as _aio

                    _aio.ensure_future(self._update_hardware(monitor))
                else:
                    snapshot = loop.run_until_complete(monitor.get_stats())
                    cpu_text = self._format_cpu(snapshot.cpu)
                    ram_text = self._format_ram(
                        snapshot.ram_used_gb, snapshot.ram_total_gb
                    )
            except Exception:
                logger.debug("HardwareMonitor unavailable", exc_info=True)

        # ── RPM ─────────────────────────────────────────────────────
        rpm_text = self._format_rpm(None)
        store = self._get_trace_store()
        if store is not None:
            try:
                entries = store.recent(limit=200)
                rpm = self._calculate_rpm(entries)
                rpm_text = self._format_rpm(rpm)
            except Exception:
                logger.debug("TraceStore unavailable", exc_info=True)

        # ── Latency / Error Rate ────────────────────────────────────
        latency_text = self._format_latency(None)
        error_text = self._format_error_rate(None)
        collector = self._get_metrics_collector()
        if collector is not None:
            try:
                all_metrics = collector.get_all_metrics()
                avg_lat = self._calculate_avg_latency(all_metrics)
                err_rate = self._calculate_error_rate(all_metrics)
                latency_text = self._format_latency(avg_lat)
                error_text = self._format_error_rate(err_rate)
            except Exception:
                logger.debug("MetricsCollector unavailable", exc_info=True)

        # ── Active Agents ───────────────────────────────────────────
        agents_text = self._format_agents([])
        engine = self._get_swarm_engine()
        if engine is not None:
            try:
                names = self._get_agent_names(engine)
                agents_text = self._format_agents(names)
            except Exception:
                logger.debug("SwarmEngine unavailable", exc_info=True)

        # ── Update widgets ──────────────────────────────────────────
        try:
            self.query_one("#metric-cpu", Static).update(cpu_text)
            self.query_one("#metric-ram", Static).update(ram_text)
            self.query_one("#metric-rpm", Static).update(rpm_text)
            self.query_one("#metric-latency", Static).update(latency_text)
            self.query_one("#metric-errors", Static).update(error_text)
            self.query_one("#metric-agents", Static).update(agents_text)
        except Exception:
            logger.debug("Dashboard widgets not yet mounted", exc_info=True)

    async def _update_hardware(self, monitor: Any) -> None:
        """Async helper to fetch hardware metrics and update widgets."""
        try:
            snapshot = await monitor.get_stats()
            cpu_text = self._format_cpu(snapshot.cpu)
            ram_text = self._format_ram(
                snapshot.ram_used_gb, snapshot.ram_total_gb
            )
            self.query_one("#metric-cpu", Static).update(cpu_text)
            self.query_one("#metric-ram", Static).update(ram_text)
        except Exception:
            logger.debug("Async hardware update failed", exc_info=True)

    # ── Data source resolution ──────────────────────────────────────

    def _get_hardware_monitor(self) -> Any:
        """Return the HardwareMonitor instance (lazy-init from kazma-core)."""
        if self._hardware_monitor is not None:
            return self._hardware_monitor
        try:
            from kazma_core.telemetry import HardwareMonitor

            self._hardware_monitor = HardwareMonitor()
        except ImportError:
            logger.debug("HardwareMonitor not available")
        return self._hardware_monitor

    def _get_trace_store(self) -> Any:
        """Return the TraceStore singleton."""
        if self._trace_store is not None:
            return self._trace_store
        try:
            from kazma_core.tracing import get_trace_store

            self._trace_store = get_trace_store()
        except ImportError:
            logger.debug("TraceStore not available")
        return self._trace_store

    def _get_metrics_collector(self) -> Any:
        """Return the MetricsCollector instance."""
        if self._metrics_collector is not None:
            return self._metrics_collector
        try:
            from kazma_core.swarm.metrics import MetricsCollector

            # MetricsCollector is typically held by SwarmEngine.
            # Try to get it from the swarm engine singleton first.
            engine = self._get_swarm_engine()
            if engine is not None and hasattr(engine, "_metrics_collector"):
                self._metrics_collector = engine._metrics_collector
            else:
                self._metrics_collector = MetricsCollector()
        except ImportError:
            logger.debug("MetricsCollector not available")
        return self._metrics_collector

    def _get_swarm_engine(self) -> Any:
        """Return the SwarmEngine singleton."""
        if self._swarm_engine is not None:
            return self._swarm_engine
        try:
            from kazma_core.swarm.engine import get_swarm_engine

            self._swarm_engine = get_swarm_engine()
        except ImportError:
            logger.debug("SwarmEngine not available")
        return self._swarm_engine

    # ── Formatting helpers (public for testing) ─────────────────────

    def _format_cpu(self, value: float | None) -> str:
        """Format CPU percentage. Returns 'N/A' when value is None."""
        if value is None:
            return f"CPU: {_NA}"
        return f"CPU: {value:.1f}%"

    def _format_ram(self, used: float | None, total: float | None) -> str:
        """Format RAM as used/total GB. Returns 'N/A' when values are None."""
        if used is None or total is None:
            return f"RAM: {_NA}"
        return f"RAM: {used:.1f} / {total:.1f} GB"

    def _format_rpm(self, value: int | None) -> str:
        """Format requests per minute. Returns 'N/A' when value is None."""
        if value is None:
            return f"RPM: {_NA}"
        return f"RPM: {value}"

    def _format_latency(self, value: float | None) -> str:
        """Format average latency in ms. Returns 'N/A' when value is None."""
        if value is None:
            return f"Latency: {_NA}"
        return f"Latency: {value:.1f}ms"

    def _format_error_rate(self, value: float | None) -> str:
        """Format error rate as percentage. Returns 'N/A' when value is None."""
        if value is None:
            return f"Errors: {_NA}"
        return f"Errors: {value:.2f}%"

    def _format_agents(self, names: list[str]) -> str:
        """Format agent names as comma-separated list. Returns 'N/A' when empty."""
        if not names:
            return f"Agents: {_NA}"
        return f"Agents: {', '.join(names)}"

    # ── Calculation helpers (public for testing) ────────────────────

    def _calculate_rpm(self, entries: list[Any]) -> int:
        """Calculate requests per minute from recent trace entries.

        Counts entries within the last 60 seconds. If the actual time
        span of those entries is shorter than 60 seconds, the count is
        extrapolated to a full minute for a more accurate rate.
        """
        if not entries:
            return 0
        now = time.time()
        window = 60.0
        recent = [e for e in entries if (now - e.timestamp) <= window]
        if not recent:
            return 0
        count = len(recent)
        # Determine actual time span of the recent entries
        timestamps = [e.timestamp for e in recent]
        span = max(timestamps) - min(timestamps)
        if span <= 0:
            # All entries at the same instant; treat as a burst
            return count
        if span < window:
            # Extrapolate to a full minute
            return int(round(count * (window / span)))
        return count

    def _calculate_avg_latency(self, data: list[dict[str, Any]]) -> float:
        """Calculate weighted average latency from MetricsCollector data.

        Weights by total tasks (completed + failed) per worker.
        """
        if not data:
            return 0.0
        total_weighted = 0.0
        total_tasks = 0
        for row in data:
            tasks = row.get("tasks_completed", 0) + row.get("tasks_failed", 0)
            latency = row.get("avg_latency", 0.0)
            total_weighted += latency * tasks
            total_tasks += tasks
        if total_tasks == 0:
            return 0.0
        return total_weighted / total_tasks

    def _calculate_error_rate(self, data: list[dict[str, Any]]) -> float:
        """Calculate error rate percentage from MetricsCollector data.

        Returns 0.0 when no tasks have been recorded (avoids division by zero).
        """
        if not data:
            return 0.0
        total_completed = 0
        total_failed = 0
        for row in data:
            total_completed += row.get("tasks_completed", 0)
            total_failed += row.get("tasks_failed", 0)
        total = total_completed + total_failed
        if total == 0:
            return 0.0
        return (total_failed / total) * 100.0

    def _get_agent_names(self, engine: Any) -> list[str]:
        """Extract active agent names from SwarmEngine._workers dict."""
        if engine is None:
            return []
        workers = getattr(engine, "_workers", {})
        return sorted(workers.keys())
