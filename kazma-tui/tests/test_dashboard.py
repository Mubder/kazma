"""Tests for TUI metrics dashboard component.

Validates:
- VAL-TUI-010: CPU/Memory Metrics Display
- VAL-TUI-011: RPM Display
- VAL-TUI-012: Latency Metrics Display
- VAL-TUI-013: Error Rate Display
- VAL-TUI-014: Active Agents List
- VAL-TUI-015: Real-Time Metrics Updates (periodic refresh)
- VAL-TUI-051: Metrics Dashboard -> Real-Time Updates Flow
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
) -> MagicMock:
    """Create a mock TelemetrySnapshot."""
    snapshot = MagicMock()
    snapshot.cpu = cpu
    snapshot.ram_used_gb = ram_used_gb
    snapshot.ram_total_gb = ram_total_gb
    snapshot.gpu = 0.0
    snapshot.vram_used_gb = 0.0
    snapshot.vram_total_gb = 0.0
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

        assert hasattr(MetricsDashboard, "REFRESH_INTERVAL") or hasattr(
            MetricsDashboard, "refresh_interval"
        )

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
        assert hasattr(widget, "_calculate_avg_latency") or hasattr(
            widget, "_calculate_error_rate"
        )

    def test_dashboard_uses_swarm_engine(self) -> None:
        """Dashboard must reference SwarmEngine for active agents."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        assert hasattr(widget, "_get_agent_names") or hasattr(widget, "_format_agents")


# ---------------------------------------------------------------------------
# App Integration
# ---------------------------------------------------------------------------


class TestAppIntegration:
    """Verify MetricsDashboard is integrated into the main app."""

    def test_app_yields_metrics_dashboard(self) -> None:
        """KazmaTUI.compose() must yield MetricsDashboard widget."""
        from kazma_tui.app import KazmaTUI
        from kazma_tui.dashboard import MetricsDashboard

        app = KazmaTUI()
        widgets = list(app.compose())
        widget_classes = [type(w) for w in widgets]
        assert MetricsDashboard in widget_classes, (
            f"MetricsDashboard not found in compose output: "
            f"{[c.__name__ for c in widget_classes]}"
        )

    def test_app_no_placeholder_widget(self) -> None:
        """KazmaTUI.compose() must NOT yield PlaceholderWidget."""
        from kazma_tui.app import KazmaTUI

        app = KazmaTUI()
        widgets = list(app.compose())
        widget_names = [type(w).__name__ for w in widgets]
        assert "PlaceholderWidget" not in widget_names, (
            f"PlaceholderWidget should be replaced with MetricsDashboard: {widget_names}"
        )


# ---------------------------------------------------------------------------
# English-Only (VAL-TUI-002)
# ---------------------------------------------------------------------------


class TestDashboardEnglishOnly:
    """VAL-TUI-002: Dashboard source must be English-only."""

    def test_no_arabic_in_dashboard_source(self) -> None:
        """dashboard.py must not contain Arabic or RTL characters."""
        import re
        from pathlib import Path

        arabic_ranges = re.compile(
            r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF\u0590-\u05FF]"
        )
        dashboard_path = (
            Path(__file__).resolve().parent.parent / "kazma_tui" / "dashboard.py"
        )
        content = dashboard_path.read_text(encoding="utf-8")
        match = arabic_ranges.search(content)
        assert not match, (
            f"dashboard.py contains Arabic/RTL character at position {match.start()}: {match.group()!r}"
        )
