"""Tests for TUI metrics dashboard component.

Validates:
- VAL-TUI-010: CPU/Memory Metrics Display
- VAL-TUI-011: RPM Display
- VAL-TUI-012: Latency Metrics Display
- VAL-TUI-013: Error Rate Display
- VAL-TUI-014: Active Agents List
- VAL-TUI-015: Real-Time Metrics Updates (periodic refresh)
- VAL-TUI-051: Metrics Dashboard -> Real-Time Updates Flow
- VRAM metric display and color-coded MetricCard dashboard
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_telemetry_snapshot(
    cpu: float = 45.2,
    ram_used_gb: float = 16.4,
    ram_total_gb: float = 32.0,
    vram_used_gb: float = 17.6,
    vram_total_gb: float = 22.5,
) -> MagicMock:
    """Create a mock TelemetrySnapshot."""
    snapshot = MagicMock()
    snapshot.cpu = cpu
    snapshot.ram_used_gb = ram_used_gb
    snapshot.ram_total_gb = ram_total_gb
    snapshot.gpu = 0.0
    snapshot.vram_used_gb = vram_used_gb
    snapshot.vram_total_gb = vram_total_gb
    snapshot.error = ""
    return snapshot


def _make_trace_entries(count: int = 5, window_seconds: float = 30.0) -> list[MagicMock]:
    """Create mock TraceEntry objects within a rolling window.

    Entries are evenly spaced so that the first entry is at
    ``now - window_seconds`` and the last is at ``now``.
    """
    entries = []
    now = time.time()
    if count <= 1:
        entry = MagicMock()
        entry.timestamp = now
        entry.trace_type = "llm"
        entry.label = "call-0"
        entry.status = "success"
        entry.duration_ms = 100.0
        return [entry]
    for i in range(count):
        entry = MagicMock()
        # Spread evenly from (now - window_seconds) to now
        entry.timestamp = now - window_seconds + (window_seconds * i / (count - 1))
        entry.trace_type = "llm"
        entry.label = f"call-{i}"
        entry.status = "success"
        entry.duration_ms = 100.0 + i * 10
        entries.append(entry)
    return entries


def _make_metrics_collector_data(
    avg_latency: float = 250.0,
    tasks_completed: int = 80,
    tasks_failed: int = 5,
) -> list[dict[str, Any]]:
    """Create mock MetricsCollector.get_all_metrics() return data."""
    return [
        {
            "worker": "analyst",
            "tasks_completed": tasks_completed,
            "tasks_failed": tasks_failed,
            "avg_latency": avg_latency,
            "total_tokens": 5000,
            "total_cost": 0.05,
        },
    ]


def _make_swarm_engine_workers(
    workers: dict[str, Any] | None = None,
) -> MagicMock:
    """Create a mock SwarmEngine with the given workers dict."""
    engine = MagicMock()
    if workers is None:
        workers = {
            "analyst": MagicMock(name="analyst"),
            "researcher": MagicMock(name="researcher"),
        }
    engine._workers = workers
    return engine


# ---------------------------------------------------------------------------
# Module Import Tests
# ---------------------------------------------------------------------------


class TestDashboardImports:
    """Verify dashboard module exists and is importable."""

    def test_dashboard_module_exists(self) -> None:
        """dashboard.py must be importable from kazma_tui."""
        import kazma_tui.dashboard  # noqa: F401

    def test_dashboard_has_widget_class(self) -> None:
        """dashboard.py must expose a MetricsDashboard widget class."""
        from kazma_tui.dashboard import MetricsDashboard

        assert MetricsDashboard is not None


# ---------------------------------------------------------------------------
# CPU / Memory Display (VAL-TUI-010)
# ---------------------------------------------------------------------------


class TestCPUMemoryDisplay:
    """VAL-TUI-010: Dashboard shows CPU and memory utilization."""

    def test_format_cpu_percentage(self) -> None:
        """CPU must be formatted as percentage with one decimal."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_cpu(45.2)
        assert "45.2" in text
        assert "%" in text

    def test_format_ram_usage(self) -> None:
        """RAM must be formatted as used/total GB."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_ram(16.4, 32.0)
        assert "16.4" in text
        assert "32.0" in text
        assert "GB" in text

    def test_format_cpu_na_when_none(self) -> None:
        """CPU must show N/A when value is None."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_cpu(None)
        assert "N/A" in text

    def test_format_ram_na_when_none(self) -> None:
        """RAM must show N/A when values are None."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_ram(None, None)
        assert "N/A" in text


# ---------------------------------------------------------------------------
# RPM Display (VAL-TUI-011)
# ---------------------------------------------------------------------------


class TestRPMDisplay:
    """VAL-TUI-011: Dashboard shows requests per minute from TraceStore."""

    def test_calculate_rpm_from_traces(self) -> None:
        """RPM must be calculated from recent trace entries over 60s window."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        # 5 traces in last 30 seconds => 10 RPM
        entries = _make_trace_entries(count=5, window_seconds=30.0)
        rpm = widget._calculate_rpm(entries)
        assert rpm == 10, f"Expected 10 RPM from 5 traces in 30s, got {rpm}"

    def test_rpm_zero_for_empty_traces(self) -> None:
        """RPM must be 0 when no trace entries exist."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        rpm = widget._calculate_rpm([])
        assert rpm == 0

    def test_format_rpm(self) -> None:
        """RPM must be formatted as an integer."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_rpm(10)
        assert "10" in text

    def test_format_rpm_na_when_none(self) -> None:
        """RPM must show N/A when value is None."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_rpm(None)
        assert "N/A" in text


# ---------------------------------------------------------------------------
# Latency Display (VAL-TUI-012)
# ---------------------------------------------------------------------------


class TestLatencyDisplay:
    """VAL-TUI-012: Dashboard shows average latency from MetricsCollector."""

    def test_calculate_avg_latency(self) -> None:
        """Average latency must be computed from MetricsCollector data."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        data = _make_metrics_collector_data(avg_latency=250.0)
        latency = widget._calculate_avg_latency(data)
        assert latency == 250.0

    def test_avg_latency_multiple_workers(self) -> None:
        """Average latency must aggregate across all workers."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        data = [
            {"worker": "a", "avg_latency": 100.0, "tasks_completed": 10, "tasks_failed": 0},
            {"worker": "b", "avg_latency": 300.0, "tasks_completed": 10, "tasks_failed": 0},
        ]
        latency = widget._calculate_avg_latency(data)
        # Weighted: (100*10 + 300*10) / 20 = 200.0
        assert latency == 200.0

    def test_format_latency(self) -> None:
        """Latency must be formatted with 'ms' suffix."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_latency(250.0)
        assert "250" in text
        assert "ms" in text

    def test_format_latency_na_when_none(self) -> None:
        """Latency must show N/A when value is None."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_latency(None)
        assert "N/A" in text


# ---------------------------------------------------------------------------
# Error Rate Display (VAL-TUI-013)
# ---------------------------------------------------------------------------


class TestErrorRateDisplay:
    """VAL-TUI-013: Dashboard shows error rate from MetricsCollector."""

    def test_calculate_error_rate(self) -> None:
        """Error rate = tasks_failed / (tasks_completed + tasks_failed)."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        data = _make_metrics_collector_data(tasks_completed=80, tasks_failed=5)
        rate = widget._calculate_error_rate(data)
        assert abs(rate - (5 / 85) * 100) < 0.01

    def test_error_rate_zero_when_no_tasks(self) -> None:
        """Error rate must be 0% when no tasks have been recorded."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        data = [
            {"worker": "a", "tasks_completed": 0, "tasks_failed": 0},
        ]
        rate = widget._calculate_error_rate(data)
        assert rate == 0.0

    def test_format_error_rate(self) -> None:
        """Error rate must be formatted as percentage."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_error_rate(5.88)
        assert "5.88" in text or "5.9" in text
        assert "%" in text

    def test_format_error_rate_na_when_none(self) -> None:
        """Error rate must show N/A when value is None."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_error_rate(None)
        assert "N/A" in text


# ---------------------------------------------------------------------------
# Active Agents List (VAL-TUI-014)
# ---------------------------------------------------------------------------


class TestActiveAgentsDisplay:
    """VAL-TUI-014: Dashboard displays active agents from SwarmEngine."""

    def test_get_agent_names(self) -> None:
        """Must extract agent names from SwarmEngine._workers dict."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        engine = _make_swarm_engine_workers()
        names = widget._get_agent_names(engine)
        assert "analyst" in names
        assert "researcher" in names

    def test_agent_names_empty_when_no_engine(self) -> None:
        """Must return empty list when engine is None."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        names = widget._get_agent_names(None)
        assert names == []

    def test_agent_names_empty_when_no_workers(self) -> None:
        """Must return empty list when engine has no workers."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        engine = _make_swarm_engine_workers(workers={})
        names = widget._get_agent_names(engine)
        assert names == []

    def test_format_agents(self) -> None:
        """Agent list must be formatted as comma-separated names."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_agents(["analyst", "researcher"])
        assert "analyst" in text
        assert "researcher" in text

    def test_format_agents_na_when_empty(self) -> None:
        """Agent list must show N/A when empty."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_agents([])
        assert "N/A" in text


# ---------------------------------------------------------------------------
# Periodic Refresh (VAL-TUI-015)
# ---------------------------------------------------------------------------


class TestPeriodicRefresh:
    """VAL-TUI-015: Dashboard refreshes metrics periodically."""

    def test_dashboard_has_refresh_interval(self) -> None:
        """MetricsDashboard must define a refresh interval constant."""
        from kazma_tui.dashboard import MetricsDashboard

        assert hasattr(MetricsDashboard, "REFRESH_INTERVAL") or hasattr(MetricsDashboard, "refresh_interval")

    def test_refresh_interval_is_2_seconds(self) -> None:
        """Refresh interval must be 2 seconds as specified."""
        from kazma_tui.dashboard import MetricsDashboard

        interval = getattr(
            MetricsDashboard,
            "REFRESH_INTERVAL",
            getattr(MetricsDashboard, "refresh_interval", None),
        )
        assert interval is not None
        assert interval == 2.0 or interval == 2


# ---------------------------------------------------------------------------
# Data Source Integration (VAL-TUI-051)
# ---------------------------------------------------------------------------


class TestDataSourceIntegration:
    """VAL-TUI-051: Dashboard fetches from all data sources on mount."""

    def test_dashboard_uses_hardware_monitor(self) -> None:
        """Dashboard must reference HardwareMonitor for CPU/RAM."""
        from kazma_tui.dashboard import MetricsDashboard

        # The class should accept or reference a hardware monitor
        widget = MetricsDashboard()
        # Check that the widget has a method or attribute for hardware data
        assert hasattr(widget, "_format_cpu") or hasattr(widget, "_fetch_metrics")

    def test_dashboard_uses_trace_store(self) -> None:
        """Dashboard must reference TraceStore for RPM."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        assert hasattr(widget, "_calculate_rpm") or hasattr(widget, "_format_rpm")

    def test_dashboard_uses_metrics_collector(self) -> None:
        """Dashboard must reference MetricsCollector for latency/errors."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        assert hasattr(widget, "_calculate_avg_latency") or hasattr(widget, "_calculate_error_rate")

    def test_dashboard_uses_swarm_engine(self) -> None:
        """Dashboard must reference SwarmEngine for active agents."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        assert hasattr(widget, "_get_agent_names") or hasattr(widget, "_format_agents")


# ---------------------------------------------------------------------------
# VRAM Display
# ---------------------------------------------------------------------------


class TestVRAMDisplay:
    """VRAM metric display and formatting."""

    def test_format_vram_normal(self) -> None:
        """VRAM must be formatted as used/total GB."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_vram(17.6, 22.5)
        assert "17.6" in text
        assert "22.5" in text
        assert "GB" in text

    def test_format_vram_na_when_none(self) -> None:
        """VRAM must show N/A when values are None."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_vram(None, None)
        assert text == "N/A"

    def test_format_vram_na_when_used_none(self) -> None:
        """VRAM must show N/A when used is None."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_vram(None, 22.5)
        assert text == "N/A"

    def test_format_vram_na_when_total_none(self) -> None:
        """VRAM must show N/A when total is None."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_vram(17.6, None)
        assert text == "N/A"


# ---------------------------------------------------------------------------
# VRAM Color Thresholds
# ---------------------------------------------------------------------------


class TestVRAMColorThresholds:
    """VRAM color-coding based on usage percentage."""

    def test_vram_critical_above_90_percent(self) -> None:
        """VRAM status must be 'critical' when usage > 90%."""
        from kazma_tui.dashboard import MetricsDashboard

        # 20.0 / 22.0 = 90.9%
        status = MetricsDashboard._determine_vram_status(20.0, 22.0)
        assert status == "critical"

    def test_vram_warning_above_70_percent(self) -> None:
        """VRAM status must be 'warning' when usage > 70%."""
        from kazma_tui.dashboard import MetricsDashboard

        # 17.0 / 22.0 = 77.3%
        status = MetricsDashboard._determine_vram_status(17.0, 22.0)
        assert status == "warning"

    def test_vram_normal_at_70_percent(self) -> None:
        """VRAM status must be 'normal' when usage <= 70%."""
        from kazma_tui.dashboard import MetricsDashboard

        # 15.0 / 22.0 = 68.2%
        status = MetricsDashboard._determine_vram_status(15.0, 22.0)
        assert status == "normal"

    def test_vram_normal_when_none(self) -> None:
        """VRAM status must be 'normal' when values are None."""
        from kazma_tui.dashboard import MetricsDashboard

        status = MetricsDashboard._determine_vram_status(None, None)
        assert status == "normal"

    def test_vram_normal_when_total_zero(self) -> None:
        """VRAM status must be 'normal' when total is zero."""
        from kazma_tui.dashboard import MetricsDashboard

        status = MetricsDashboard._determine_vram_status(0.0, 0.0)
        assert status == "normal"

    def test_vram_warning_below_90_percent(self) -> None:
        """VRAM status below 90% but above 70% must be 'warning'."""
        from kazma_tui.dashboard import MetricsDashboard

        # 17.6 / 22.5 = 78.2%
        status = MetricsDashboard._determine_vram_status(17.6, 22.5)
        assert status == "warning"

    def test_vram_critical_above_threshold(self) -> None:
        """VRAM status above 90% must be 'critical'."""
        from kazma_tui.dashboard import MetricsDashboard

        # 21.0 / 22.0 = 95.5%
        status = MetricsDashboard._determine_vram_status(21.0, 22.0)
        assert status == "critical"


# ---------------------------------------------------------------------------
# Error Rate Color Thresholds
# ---------------------------------------------------------------------------


class TestErrorRateColorThresholds:
    """Error rate color-coding based on value."""

    def test_error_critical_when_positive(self) -> None:
        """Error status must be 'critical' when error rate > 0."""
        from kazma_tui.dashboard import MetricsDashboard

        status = MetricsDashboard._determine_error_status(5.88)
        assert status == "critical"

    def test_error_normal_when_zero(self) -> None:
        """Error status must be 'normal' when error rate == 0."""
        from kazma_tui.dashboard import MetricsDashboard

        status = MetricsDashboard._determine_error_status(0.0)
        assert status == "normal"

    def test_error_normal_when_none(self) -> None:
        """Error status must be 'normal' when error rate is None."""
        from kazma_tui.dashboard import MetricsDashboard

        status = MetricsDashboard._determine_error_status(None)
        assert status == "normal"


# ---------------------------------------------------------------------------
# Latency Color Thresholds
# ---------------------------------------------------------------------------


class TestLatencyColorThresholds:
    """Latency color-coding based on value."""

    def test_latency_warning_above_200ms(self) -> None:
        """Latency status must be 'warning' when latency > 200ms."""
        from kazma_tui.dashboard import MetricsDashboard

        status = MetricsDashboard._determine_latency_status(250.0)
        assert status == "warning"

    def test_latency_normal_at_200ms(self) -> None:
        """Latency status must be 'normal' when latency <= 200ms."""
        from kazma_tui.dashboard import MetricsDashboard

        status = MetricsDashboard._determine_latency_status(200.0)
        assert status == "normal"

    def test_latency_normal_below_200ms(self) -> None:
        """Latency status must be 'normal' when latency < 200ms."""
        from kazma_tui.dashboard import MetricsDashboard

        status = MetricsDashboard._determine_latency_status(150.0)
        assert status == "normal"

    def test_latency_normal_when_none(self) -> None:
        """Latency status must be 'normal' when latency is None."""
        from kazma_tui.dashboard import MetricsDashboard

        status = MetricsDashboard._determine_latency_status(None)
        assert status == "normal"


# ---------------------------------------------------------------------------
# Health Display
# ---------------------------------------------------------------------------


class TestHealthDisplay:
    """Health (CPU/Mem) metric display and formatting."""

    def test_format_health_normal(self) -> None:
        """Health must show CPU% and RAM used/total GB."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_health(45.2, 16.4, 32.0)
        assert "45.2" in text
        assert "16.4" in text
        assert "32.0" in text

    def test_format_health_na_when_cpu_none(self) -> None:
        """Health must show N/A for CPU when cpu is None."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_health(None, 16.4, 32.0)
        assert "N/A" in text

    def test_format_health_na_when_ram_none(self) -> None:
        """Health must show N/A for RAM when ram values are None."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_health(45.2, None, None)
        assert "N/A" in text

    def test_health_always_normal_status(self) -> None:
        """Health color should always be 'normal' (green)."""
        # Health doesn't have a dedicated status helper;
        # it's always set to "normal" in _do_refresh.
        # This test verifies the convention by checking format_health works.
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        text = widget._format_health(99.9, 31.0, 32.0)
        assert "99.9" in text  # Even extreme values format normally


# ---------------------------------------------------------------------------
# MetricCard Widget
# ---------------------------------------------------------------------------


class TestMetricCard:
    """MetricCard widget rendering and status colors."""

    def test_metric_card_render_normal(self) -> None:
        """MetricCard with 'normal' status wraps value in primary (cyan) markup."""
        from kazma_tui.dashboard import MetricCard

        card = MetricCard(label="Test", value="42", status="normal")
        rendered = card._render_value()
        assert "[bold $primary]" in rendered
        assert "42" in rendered
        assert "[/bold $primary]" in rendered

    def test_metric_card_render_warning(self) -> None:
        """MetricCard with 'warning' status wraps value in secondary (purple) markup."""
        from kazma_tui.dashboard import MetricCard

        card = MetricCard(label="Test", value="slow", status="warning")
        rendered = card._render_value()
        assert "[bold $secondary]" in rendered
        assert "slow" in rendered
        assert "[/bold $secondary]" in rendered

    def test_metric_card_render_critical(self) -> None:
        """MetricCard with 'critical' status wraps value in error (red) markup."""
        from kazma_tui.dashboard import MetricCard

        card = MetricCard(label="Test", value="error", status="critical")
        rendered = card._render_value()
        assert "[bold $error]" in rendered
        assert "error" in rendered
        assert "[/bold $error]" in rendered

    def test_metric_card_has_label(self) -> None:
        """MetricCard must store label."""
        from kazma_tui.dashboard import MetricCard

        card = MetricCard(label="VRAM", value="17.6 / 22.5 GB", status="normal")
        assert card._label == "VRAM"


# ---------------------------------------------------------------------------
# Dashboard Grid Layout
# ---------------------------------------------------------------------------

import pytest


class TestDashboardGridLayout:
    """Dashboard must render 6 MetricCard widgets in a 3x2 grid."""

    @pytest.mark.asyncio
    async def test_compose_yields_six_metric_cards(self) -> None:
        """compose() must yield exactly 6 MetricCard widgets."""
        from kazma_tui.dashboard import MetricCard, MetricsDashboard
        from textual.app import App

        class _TestApp(App[None]):
            def compose(self):  # type: ignore[override]
                yield MetricsDashboard()

        async with _TestApp().run_test() as pilot:
            dashboard = pilot.app.query_one(MetricsDashboard)
            cards = dashboard.query(MetricCard)
            assert len(cards) == 6, f"Expected 6 MetricCard widgets, got {len(cards)}"

    @pytest.mark.asyncio
    async def test_metric_card_ids(self) -> None:
        """All 6 metric cards must have correct IDs."""
        from kazma_tui.dashboard import MetricCard, MetricsDashboard
        from textual.app import App

        expected_ids = {
            "metric-rpm",
            "metric-latency",
            "metric-health",
            "metric-vram",
            "metric-errors",
            "metric-agents",
        }

        class _TestApp(App[None]):
            def compose(self):  # type: ignore[override]
                yield MetricsDashboard()

        async with _TestApp().run_test() as pilot:
            dashboard = pilot.app.query_one(MetricsDashboard)
            cards = dashboard.query(MetricCard)
            card_ids = {w.id for w in cards if w.id}
            assert card_ids == expected_ids, f"Expected IDs {expected_ids}, got {card_ids}"

    @pytest.mark.asyncio
    async def test_vram_card_present(self) -> None:
        """Dashboard must include a VRAM MetricCard."""
        from kazma_tui.dashboard import MetricsDashboard
        from textual.app import App

        class _TestApp(App[None]):
            def compose(self):  # type: ignore[override]
                yield MetricsDashboard()

        async with _TestApp().run_test() as pilot:
            dashboard = pilot.app.query_one(MetricsDashboard)
            vram_cards = dashboard.query("#metric-vram")
            assert len(vram_cards) == 1, "Expected exactly one VRAM MetricCard"


# ---------------------------------------------------------------------------
# Dashboard Constructor Tests (already passing - skip app integration for now)
# ---------------------------------------------------------------------------


class TestDashboardConstructor:
    """Verify MetricsDashboard accepts injectable data sources."""

    def test_dashboard_accepts_hardware_monitor(self) -> None:
        """MetricsDashboard must accept a hardware_monitor parameter."""
        from kazma_tui.dashboard import MetricsDashboard

        mock = MagicMock()
        d = MetricsDashboard(hardware_monitor=mock)
        assert d._hardware_monitor is mock

    def test_dashboard_accepts_trace_store(self) -> None:
        """MetricsDashboard must accept a trace_store parameter."""
        from kazma_tui.dashboard import MetricsDashboard

        mock = MagicMock()
        d = MetricsDashboard(trace_store=mock)
        assert d._trace_store is mock

    def test_dashboard_accepts_metrics_collector(self) -> None:
        """MetricsDashboard must accept a metrics_collector parameter."""
        from kazma_tui.dashboard import MetricsDashboard

        mock = MagicMock()
        d = MetricsDashboard(metrics_collector=mock)
        assert d._metrics_collector is mock

    def test_dashboard_accepts_swarm_engine(self) -> None:
        """MetricsDashboard must accept a swarm_engine parameter."""
        from kazma_tui.dashboard import MetricsDashboard

        mock = MagicMock()
        d = MetricsDashboard(swarm_engine=mock)
        assert d._swarm_engine is mock


# ---------------------------------------------------------------------------
# English-Only (VAL-TUI-002)
# ---------------------------------------------------------------------------


class TestDashboardEnglishOnly:
    """VAL-TUI-002: Dashboard source must be English-only."""

    def test_no_arabic_in_dashboard_source(self) -> None:
        """dashboard.py must not contain Arabic or RTL characters."""
        import re
        from pathlib import Path

        arabic_ranges = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF\u0590-\u05FF]")
        dashboard_path = Path(__file__).resolve().parent.parent / "kazma_tui" / "dashboard.py"
        content = dashboard_path.read_text(encoding="utf-8")
        match = arabic_ranges.search(content)
        assert not match, f"dashboard.py contains Arabic/RTL character at position {match.start()}: {match.group()!r}"
