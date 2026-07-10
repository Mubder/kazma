"""Metrics dashboard widget for the Kazma TUI.

Displays real-time system and application metrics:
- CPU percentage and RAM usage (from HardwareMonitor)
- Requests per minute (from TraceStore)
- Average latency (from MetricsCollector)
- Error rate percentage (from MetricsCollector)
- Active agents list (from SwarmEngine)
- VRAM usage (from HardwareMonitor)

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

from kazma_tui.widgets.sparkline import Sparkline

logger = logging.getLogger(__name__)

_NA = "N/A"


class MetricCard(Widget):
    """A single metric display card with label, value, and color status.

    Renders a metric with a label and color-coded value based on status.

    Args:
        label: Metric name (e.g. "VRAM", "Latency").
        value: Formatted value string.
        status: One of "normal", "warning", "critical".
        show_sparkline: True to show an inline sparkline.
        card_id: Optional widget ID.
    """

    DEFAULT_CSS = """
    MetricCard {
        height: auto;
        width: 1fr;
        padding: 1 2;
        background: $panel;
        margin: 0 1;
    }

    MetricCard > .card-label {
        color: $text-muted;
        text-style: bold;
    }

    MetricCard > Sparkline {
        margin-top: 1;
        color: $primary;
    }
    """

    def __init__(
        self,
        label: str = "",
        value: str = "",
        status: str = "normal",
        show_sparkline: bool = False,
        *,
        card_id: str | None = None,
    ) -> None:
        super().__init__(id=card_id)
        self._label = label
        self._value = value
        self._status = status
        self.show_sparkline = show_sparkline

    def compose(self) -> ComposeResult:
        """Compose the card with label, value, and optional sparkline."""
        yield Static(self._label, classes="card-label")
        yield Static(self._render_value())
        if self.show_sparkline:
            yield Sparkline(max_points=25, id="card-sparkline")

    def update_card(self, label: str, value: str, status: str = "normal", spark_val: float | None = None) -> None:
        """Update the card content and refresh the display.

        Args:
            label: New metric label.
            value: New formatted value.
            status: New status ("normal", "warning", "critical").
            spark_val: New numeric value for sparkline trend tracking.
        """
        self._label = label
        self._value = value
        self._status = status
        try:
            self.query_one(".card-label", Static).update(label)
            # The value is the second Static child
            value_widgets = self.query(Static)
            if len(value_widgets) >= 2:
                value_widgets[1].update(self._render_value())
            if self.show_sparkline and spark_val is not None:
                try:
                    sp = self.query_one("#card-sparkline", Sparkline)
                    sp.add_point(spark_val)
                except Exception:
                    pass
        except Exception:
            logger.debug("MetricCard widgets not yet mounted", exc_info=True)

    def _render_value(self) -> str:
        """Render value with kazma.ai color palette."""
        if self._status == "critical":
            return f"[bold $error]{self._value}[/bold $error]"
        if self._status == "warning":
            return f"[bold $secondary]{self._value}[/bold $secondary]"
        return f"[bold $primary]{self._value}[/bold $primary]"



class MetricsDashboard(Widget):
    """Real-time metrics dashboard widget.

    Displays 10 metrics in a 3-col grid with color-coded cards:
    Row 1: Throughput (RPM)  |  Latency (ms)    |  Health (CPU/Mem)
    Row 2: VRAM (GB)         |  Error Rate (%)   |  Active Agents
    Row 3: Uptime             |  Provider Health  |  Token Usage Today

    Refreshes every 2 seconds. Data sources are injectable for testing.
    """

    REFRESH_INTERVAL: float = 2.0

    DEFAULT_CSS = """
    MetricsDashboard {
        height: auto;
        padding: 1 2;
    }

    .metrics-grid {
        height: auto;
    }

    .metric-row {
        height: auto;
        layout: horizontal;
    }

    MetricCard {
        width: 1fr;
        height: auto;
        padding: 0 1;
    }

    MetricCard > .card-label {
        color: $text-muted;
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
        self._start_time: float = 0.0
        import time
        self._start_time = time.time()

    # ── Textual lifecycle ───────────────────────────────────────────

    def compose(self) -> ComposeResult:
        """Compose the dashboard as a 3x2 grid of MetricCard widgets."""
        from textual.containers import Horizontal, Vertical

        with Vertical(classes="metrics-grid"):
            with Horizontal(classes="metric-row"):
                yield MetricCard(
                    label="Throughput (RPM)",
                    value=self._format_rpm(None),
                    status="normal",
                    show_sparkline=True,
                    card_id="metric-rpm",
                )
                yield MetricCard(
                    label="Latency (ms)",
                    value=self._format_latency(None),
                    status="normal",
                    show_sparkline=True,
                    card_id="metric-latency",
                )
                yield MetricCard(
                    label="Health (CPU/Mem)",
                    value=self._format_health(None, None, None),
                    status="normal",
                    show_sparkline=True,
                    card_id="metric-health",
                )
            with Horizontal(classes="metric-row"):
                yield MetricCard(
                    label="VRAM (GB)",
                    value=self._format_vram(None, None),
                    status="normal",
                    show_sparkline=True,
                    card_id="metric-vram",
                )
                yield MetricCard(
                    label="Error Rate (%)",
                    value=self._format_error_rate(None),
                    status="normal",
                    show_sparkline=True,
                    card_id="metric-errors",
                )
                yield MetricCard(
                    label="Active Agents",
                    value=self._format_agents([]),
                    status="normal",
                    show_sparkline=False,
                    card_id="metric-agents",
                )
            with Horizontal(classes="metric-row"):
                yield MetricCard(
                    label="Uptime",
                    value=self._format_uptime(0),
                    status="normal",
                    show_sparkline=False,
                    card_id="metric-uptime",
                )
                yield MetricCard(
                    label="Provider Health",
                    value=self._format_provider_health(None),
                    status="normal",
                    show_sparkline=False,
                    card_id="metric-provider",
                )
                yield MetricCard(
                    label="Token Usage Today",
                    value=self._format_token_usage(None),
                    status="normal",
                    show_sparkline=False,
                    card_id="metric-tokens-today",
                )

    async def on_mount(self) -> None:
        """Run one refresh immediately, then schedule periodic refresh."""
        await self._do_refresh()
        self.set_interval(self.REFRESH_INTERVAL, self._do_refresh)

    def _refresh_now(self) -> None:
        """Synchronous helper kept for backward compatibility.

        Schedules an async refresh on the running loop.  The actual
        refresh logic lives in :meth:`_do_refresh` (async).
        """
        try:
            self.app.call_later(self._do_refresh)
        except Exception:
            logger.exception("Dashboard refresh failed")

    async def _do_refresh(self) -> None:
        """Fetch metrics from all sources and update MetricCard widgets.

        Runs inside Textual's event loop, so ``await`` is the natural
        way to pull the (async) HardwareMonitor snapshot.  All other
        sources are sync and consumed directly.
        """
        # ── CPU / RAM / VRAM (async only) ───────────────────────────
        cpu_value: float | None = None
        ram_used: float | None = None
        ram_total: float | None = None
        vram_used: float | None = None
        vram_total: float | None = None
        try:
            monitor = self._get_hardware_monitor()
        except Exception:
            logger.debug("HardwareMonitor init failed", exc_info=True)
            monitor = None
        if monitor is not None:
            try:
                snapshot = await monitor.get_stats()
                cpu_value = snapshot.cpu
                ram_used = snapshot.ram_used_gb
                ram_total = snapshot.ram_total_gb
                vram_used = snapshot.vram_used_gb
                vram_total = snapshot.vram_total_gb
            except Exception:
                logger.debug("HardwareMonitor unavailable", exc_info=True)

        # ── RPM (sync) ──────────────────────────────────────────────
        rpm_val: int | None = None
        try:
            store = self._get_trace_store()
        except Exception:
            logger.debug("TraceStore init failed", exc_info=True)
            store = None
        if store is not None:
            try:
                entries = store.recent(limit=200)
                rpm_val = self._calculate_rpm(entries)
            except Exception:
                logger.debug("TraceStore unavailable", exc_info=True)

        # ── Latency / Error Rate (sync) ─────────────────────────────
        latency_val: float | None = None
        error_val: float | None = None
        try:
            collector = self._get_metrics_collector()
        except Exception:
            logger.debug("MetricsCollector init failed", exc_info=True)
            collector = None
        if collector is not None:
            try:
                all_metrics = collector.get_all_metrics()
                latency_val = self._calculate_avg_latency(all_metrics)
                error_val = self._calculate_error_rate(all_metrics)
            except Exception:
                logger.debug("MetricsCollector unavailable", exc_info=True)

        # ── Active Agents ───────────────────────────────────────────
        agent_names: list[str] = []
        try:
            engine = self._get_swarm_engine()
        except Exception:
            logger.debug("SwarmEngine init failed", exc_info=True)
            engine = None
        if engine is not None:
            try:
                agent_names = self._get_agent_names(engine)
            except Exception:
                logger.debug("SwarmEngine unavailable", exc_info=True)

        # ── Update MetricCard widgets ───────────────────────────────
        try:
            rpm_card = self.query_one("#metric-rpm", MetricCard)
            rpm_card.update_card(
                label="Throughput (RPM)",
                value=self._format_rpm(rpm_val),
                status="normal",
                spark_val=float(rpm_val) if rpm_val is not None else 0.0,
            )

            latency_card = self.query_one("#metric-latency", MetricCard)
            latency_status = self._determine_latency_status(latency_val)
            latency_card.update_card(
                label="Latency (ms)",
                value=self._format_latency(latency_val),
                status=latency_status,
                spark_val=latency_val if latency_val is not None else 0.0,
            )

            health_card = self.query_one("#metric-health", MetricCard)
            health_card.update_card(
                label="Health (CPU/Mem)",
                value=self._format_health(cpu_value, ram_used, ram_total),
                status="normal",
                spark_val=cpu_value if cpu_value is not None else 0.0,
            )

            vram_card = self.query_one("#metric-vram", MetricCard)
            vram_status = self._determine_vram_status(vram_used, vram_total)
            vram_card.update_card(
                label="VRAM (GB)",
                value=self._format_vram(vram_used, vram_total),
                status=vram_status,
                spark_val=vram_used if vram_used is not None else 0.0,
            )

            error_card = self.query_one("#metric-errors", MetricCard)
            error_status = self._determine_error_status(error_val)
            error_card.update_card(
                label="Error Rate (%)",
                value=self._format_error_rate(error_val),
                status=error_status,
                spark_val=error_val if error_val is not None else 0.0,
            )

            agents_card = self.query_one("#metric-agents", MetricCard)
            agents_card.update_card(
                label="Active Agents",
                value=self._format_agents(agent_names),
                status="normal",
            )

            # ── Uptime ─────────────────────────────────────────────
            import time as _time
            uptime_card = self.query_one("#metric-uptime", MetricCard)
            uptime_card.update_card(
                label="Uptime",
                value=self._format_uptime(_time.time() - self._start_time),
                status="normal",
            )

            # ── Provider Health ────────────────────────────────────
            provider_status: str | None = None
            try:
                from kazma_core.model_registry import get_model_registry
                registry = get_model_registry()
                profile = registry.get_active_profile()
                provider_status = profile.get("provider", "?")
            except Exception:
                provider_status = None
            provider_card = self.query_one("#metric-provider", MetricCard)
            provider_card.update_card(
                label="Provider Health",
                value=self._format_provider_health(provider_status),
                status="normal" if provider_status else "warning",
            )

            # ── Token Usage Today ──────────────────────────────────
            tokens_today: int | None = None
            if store is not None:
                try:
                    stats = store.stats()
                    tokens_today = stats.get("total_tokens", 0)
                except Exception:
                    pass
            tokens_card = self.query_one("#metric-tokens-today", MetricCard)
            tokens_card.update_card(
                label="Token Usage Today",
                value=self._format_token_usage(tokens_today),
                status="normal",
            )
        except Exception:
            logger.debug("Dashboard MetricCard widgets not yet mounted", exc_info=True)

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

    def _format_health(
        self,
        cpu: float | None,
        ram_used: float | None,
        ram_total: float | None,
    ) -> str:
        """Format combined CPU/Memory health string.

        Returns e.g. ``"CPU: 45.2%  RAM: 16.4 / 32.0 GB"`` or ``"N/A"``
        when data is unavailable.
        """
        cpu_part = "N/A" if cpu is None else f"{cpu:.1f}%"
        if ram_used is None or ram_total is None:
            ram_part = "N/A"
        else:
            ram_part = f"{ram_used:.1f}/{ram_total:.1f} GB"
        return f"CPU: {cpu_part}  RAM: {ram_part}"

    def _format_vram(self, used: float | None, total: float | None) -> str:
        """Format VRAM as used/total GB. Returns 'N/A' when values are None."""
        if used is None or total is None:
            return _NA
        return f"{used:.1f} / {total:.1f} GB"

    def _format_rpm(self, value: int | None) -> str:
        """Format requests per minute. Returns 'N/A' when value is None."""
        if value is None:
            return _NA
        return str(value)

    def _format_latency(self, value: float | None) -> str:
        """Format average latency in ms. Returns 'N/A' when value is None."""
        if value is None:
            return _NA
        return f"{value:.1f}ms"

    def _format_error_rate(self, value: float | None) -> str:
        """Format error rate as percentage. Returns 'N/A' when value is None."""
        if value is None:
            return _NA
        return f"{value:.2f}%"

    def _format_agents(self, names: list[str]) -> str:
        """Format agent names as comma-separated list. Returns 'N/A' when empty."""
        if not names:
            return _NA
        return ", ".join(names)

    @staticmethod
    def _format_uptime(seconds: float) -> str:
        """Format uptime seconds as human-readable string."""
        if seconds <= 0:
            return "just started"
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        mins = int((seconds % 3600) // 60)
        if days > 0:
            return f"{days}d {hours}h {mins}m"
        if hours > 0:
            return f"{hours}h {mins}m"
        return f"{mins}m"

    @staticmethod
    def _format_provider_health(provider: str | None) -> str:
        """Format provider health status."""
        if not provider:
            return "No provider"
        return f"Connected: {provider}"

    @staticmethod
    def _format_token_usage(tokens: int | None) -> str:
        """Format token count with thousand separators."""
        if tokens is None or tokens == 0:
            return "0"
        return f"{tokens:,}"

    # ── Status determination helpers ────────────────────────────────

    @staticmethod
    def _determine_vram_status(
        used: float | None, total: float | None
    ) -> str:
        """Determine VRAM status based on usage percentage.

        Returns ``"critical"`` if usage > 90%, ``"warning"`` if > 70%,
        otherwise ``"normal"``.
        """
        if used is None or total is None or total <= 0:
            return "normal"
        pct = (used / total) * 100.0
        if pct > 90:
            return "critical"
        if pct > 70:
            return "warning"
        return "normal"

    @staticmethod
    def _determine_error_status(error_rate: float | None) -> str:
        """Determine error rate status.

        Returns ``"critical"`` if error_rate > 0, otherwise ``"normal"``.
        """
        if error_rate is not None and error_rate > 0:
            return "critical"
        return "normal"

    @staticmethod
    def _determine_latency_status(latency: float | None) -> str:
        """Determine latency status.

        Returns ``"warning"`` if latency > 200ms, otherwise ``"normal"``.
        """
        if latency is not None and latency > 200:
            return "warning"
        return "normal"

    # ── Calculation helpers (public for testing) ────────────────────

    def _calculate_rpm(self, entries: list[Any]) -> int:
        """Calculate requests per minute from recent trace entries.

        Counts entries within the last 60 seconds. Returns the actual
        count within the window without extrapolation to avoid
        over-inflating RPM for short observation periods.
        """
        if not entries:
            return 0
        now = time.time()
        window = 60.0
        recent = [e for e in entries if (now - e.timestamp) <= window]
        return len(recent)

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
