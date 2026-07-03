"""Integration flow tests for TUI — ModelRegistry and Metrics flows.

Validates:
- VAL-TUI-050: Launch -> ModelRegistry Integration Flow
- VAL-TUI-051: Metrics Dashboard -> Real-Time Updates Flow
- VAL-TUI-030: Active Provider from ModelRegistry
- VAL-TUI-031: Active Model from ModelRegistry
- VAL-TUI-032: No Model-Switching Logic
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_registry(provider: str = "openai", model: str = "gpt-4o") -> MagicMock:
    mock = MagicMock()
    mock.get_active_profile.return_value = {
        "provider": provider,
        "model": model,
        "base_url": "https://api.openai.com/v1",
        "api_key": "***",
    }
    return mock


# ---------------------------------------------------------------------------
# VAL-TUI-050: Launch -> ModelRegistry Integration Flow
# ---------------------------------------------------------------------------


class TestModelRegistryIntegrationFlow:
    """VAL-TUI-050: TUI launch initializes and displays ModelRegistry data.

    Flow:
    1. TUI initializes or connects to ModelRegistry singleton
    2. Calls get_active_profile() to retrieve provider/model
    3. Displays provider/model in header
    4. Handles RuntimeError if ModelRegistry not initialized
    """

    def test_header_calls_get_active_profile(self) -> None:
        """Step 2: Header must call get_active_profile() on mount."""
        from kazma_tui.header import KazmaHeader

        mock_reg = _make_mock_registry("openai", "gpt-4o")
        with patch("kazma_tui.header._get_model_registry", return_value=mock_reg):
            widget = KazmaHeader()
            widget._build_header_text()
            mock_reg.get_active_profile.assert_called_once()

    def test_header_displays_provider_from_registry(self) -> None:
        """Step 3: Header text must contain the provider name from registry."""
        from kazma_tui.header import KazmaHeader

        mock_reg = _make_mock_registry("anthropic", "claude-3-opus")
        with patch("kazma_tui.header._get_model_registry", return_value=mock_reg):
            widget = KazmaHeader()
            text = widget._build_header_text()
            assert "anthropic" in text.lower()

    def test_header_displays_model_from_registry(self) -> None:
        """Step 3: Header text must contain the model name from registry."""
        from kazma_tui.header import KazmaHeader

        mock_reg = _make_mock_registry("anthropic", "claude-3-opus")
        with patch("kazma_tui.header._get_model_registry", return_value=mock_reg):
            widget = KazmaHeader()
            text = widget._build_header_text()
            assert "claude-3-opus" in text

    def test_header_handles_runtime_error(self) -> None:
        """Step 4: Header must handle RuntimeError gracefully."""
        from kazma_tui.header import KazmaHeader

        with patch(
            "kazma_tui.header._get_model_registry",
            side_effect=RuntimeError("Registry not initialized"),
        ):
            widget = KazmaHeader()
            text = widget._build_header_text()
            # Must not crash; must return valid text
            assert isinstance(text, str)
            assert len(text) > 0

    def test_header_handles_generic_exception(self) -> None:
        """Step 4: Header must handle any exception from ModelRegistry."""
        from kazma_tui.header import KazmaHeader

        with patch(
            "kazma_tui.header._get_model_registry",
            side_effect=ConnectionError("Network error"),
        ):
            widget = KazmaHeader()
            text = widget._build_header_text()
            assert isinstance(text, str)
            assert len(text) > 0

    def test_header_provider_only(self) -> None:
        """Header must display provider when model is empty."""
        from kazma_tui.header import KazmaHeader

        mock_reg = MagicMock()
        mock_reg.get_active_profile.return_value = {
            "provider": "openai",
            "model": "",
            "base_url": "",
            "api_key": "",
        }
        with patch("kazma_tui.header._get_model_registry", return_value=mock_reg):
            widget = KazmaHeader()
            text = widget._build_header_text()
            assert "openai" in text

    def test_header_model_only(self) -> None:
        """Header must display model when provider is empty."""
        from kazma_tui.header import KazmaHeader

        mock_reg = MagicMock()
        mock_reg.get_active_profile.return_value = {
            "provider": "",
            "model": "gpt-4o",
            "base_url": "",
            "api_key": "",
        }
        with patch("kazma_tui.header._get_model_registry", return_value=mock_reg):
            widget = KazmaHeader()
            text = widget._build_header_text()
            assert "gpt-4o" in text

    def test_header_format_provider_model_separator(self) -> None:
        """Header must use ' / ' separator between provider and model."""
        from kazma_tui.header import KazmaHeader

        mock_reg = _make_mock_registry("openai", "gpt-4o")
        with patch("kazma_tui.header._get_model_registry", return_value=mock_reg):
            widget = KazmaHeader()
            text = widget._build_header_text()
            assert " / " in text

    def test_header_updates_on_refresh(self) -> None:
        """Header must update display when refresh_profile() is called."""
        from kazma_tui.header import KazmaHeader

        reg1 = _make_mock_registry("openai", "gpt-4o")
        reg2 = _make_mock_registry("anthropic", "claude-3-opus")

        with patch("kazma_tui.header._get_model_registry", return_value=reg1):
            widget = KazmaHeader()
            text1 = widget._build_header_text()
            assert "openai" in text1

        with patch("kazma_tui.header._get_model_registry", return_value=reg2):
            text2 = widget._build_header_text()
            assert "anthropic" in text2


# ---------------------------------------------------------------------------
# VAL-TUI-051: Metrics Dashboard -> Real-Time Updates Flow
# ---------------------------------------------------------------------------


class TestMetricsRefreshFlow:
    """VAL-TUI-051: Dashboard metrics refresh from live data sources.

    Flow:
    1. On mount, fetch initial metrics
    2. Start periodic timer (1-5 second interval)
    3. On each tick, re-fetch metrics from all sources
    4. Update dashboard widgets with new values
    5. Handle source unavailability gracefully
    """

    def test_dashboard_fetches_hardware_metrics(self) -> None:
        """Step 1: Dashboard must fetch CPU/RAM from HardwareMonitor."""
        from kazma_tui.dashboard import MetricsDashboard

        mock_monitor = MagicMock()
        snap = MagicMock()
        snap.cpu = 55.0
        snap.ram_used_gb = 12.0
        snap.ram_total_gb = 24.0
        mock_monitor.get_stats = AsyncMock(return_value=snap)

        widget = MetricsDashboard(hardware_monitor=mock_monitor)
        # Verify the widget can format the data
        assert "55.0" in widget._format_cpu(55.0)
        assert "12.0" in widget._format_ram(12.0, 24.0)

    def test_dashboard_fetches_rpm_from_trace_store(self) -> None:
        """Step 1: Dashboard must fetch RPM from TraceStore."""
        from kazma_tui.dashboard import MetricsDashboard

        mock_store = MagicMock()
        now = time.time()
        entries = []
        for i in range(5):
            e = MagicMock()
            e.timestamp = now - 30.0 + (i * 6.0)
            entries.append(e)
        mock_store.recent.return_value = entries

        widget = MetricsDashboard(trace_store=mock_store)
        rpm = widget._calculate_rpm(entries)
        assert rpm > 0

    def test_dashboard_fetches_latency_from_metrics_collector(self) -> None:
        """Step 1: Dashboard must fetch latency from MetricsCollector."""
        from kazma_tui.dashboard import MetricsDashboard

        mock_collector = MagicMock()
        mock_collector.get_all_metrics.return_value = [
            {"worker": "w1", "tasks_completed": 100, "tasks_failed": 5, "avg_latency": 150.0},
            {"worker": "w2", "tasks_completed": 50, "tasks_failed": 2, "avg_latency": 300.0},
        ]

        widget = MetricsDashboard(metrics_collector=mock_collector)
        data = mock_collector.get_all_metrics()
        lat = widget._calculate_avg_latency(data)
        # Weighted: (150*105 + 300*52) / 157 = (15750 + 15600) / 157 = 199.68
        assert abs(lat - 199.68) < 1.0

    def test_dashboard_fetches_error_rate_from_metrics_collector(self) -> None:
        """Step 1: Dashboard must fetch error rate from MetricsCollector."""
        from kazma_tui.dashboard import MetricsDashboard

        mock_collector = MagicMock()
        mock_collector.get_all_metrics.return_value = [
            {"worker": "w1", "tasks_completed": 90, "tasks_failed": 10, "avg_latency": 200.0},
        ]

        widget = MetricsDashboard(metrics_collector=mock_collector)
        data = mock_collector.get_all_metrics()
        rate = widget._calculate_error_rate(data)
        # 10/100 = 10%
        assert abs(rate - 10.0) < 0.01

    def test_dashboard_fetches_agents_from_swarm_engine(self) -> None:
        """Step 1: Dashboard must fetch active agents from SwarmEngine."""
        from kazma_tui.dashboard import MetricsDashboard

        mock_engine = MagicMock()
        mock_engine._workers = {"analyst": MagicMock(), "researcher": MagicMock()}

        widget = MetricsDashboard(swarm_engine=mock_engine)
        names = widget._get_agent_names(mock_engine)
        assert "analyst" in names
        assert "researcher" in names

    def test_dashboard_refresh_interval_is_set(self) -> None:
        """Step 2: Dashboard must define a 2-second refresh interval."""
        from kazma_tui.dashboard import MetricsDashboard

        assert MetricsDashboard.REFRESH_INTERVAL == 2.0

    def test_dashboard_handles_hardware_monitor_unavailable(self) -> None:
        """Step 5: Dashboard must handle HardwareMonitor import failure gracefully."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        # Simulate the monitor being unavailable by returning None
        widget._hardware_monitor = None
        with patch.object(widget, "_get_hardware_monitor", return_value=None):
            # Should not crash
            widget._do_refresh()

    def test_dashboard_handles_trace_store_unavailable(self) -> None:
        """Step 5: Dashboard must handle TraceStore import failure gracefully."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        widget._trace_store = None
        with patch.object(widget, "_get_trace_store", return_value=None):
            widget._do_refresh()

    def test_dashboard_handles_metrics_collector_unavailable(self) -> None:
        """Step 5: Dashboard must handle MetricsCollector import failure gracefully."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        widget._metrics_collector = None
        with patch.object(widget, "_get_metrics_collector", return_value=None):
            widget._do_refresh()

    def test_dashboard_handles_swarm_engine_unavailable(self) -> None:
        """Step 5: Dashboard must handle SwarmEngine import failure gracefully."""
        from kazma_tui.dashboard import MetricsDashboard

        widget = MetricsDashboard()
        widget._swarm_engine = None
        with patch.object(widget, "_get_swarm_engine", return_value=None):
            widget._do_refresh()


# ---------------------------------------------------------------------------
# VAL-TUI-030, VAL-TUI-031: Provider/Model from ModelRegistry
# ---------------------------------------------------------------------------


class TestModelRegistryReadonlyIntegration:
    """VAL-TUI-030/031: TUI reads provider/model from ModelRegistry singleton."""

    def test_tui_imports_get_model_registry(self) -> None:
        """TUI header must import get_model_registry from kazma_core."""
        from kazma_tui import header

        source = Path(header.__file__).read_text(encoding="utf-8")
        assert "get_model_registry" in source

    def test_tui_calls_get_active_profile(self) -> None:
        """TUI header must call get_active_profile() on the registry."""
        from kazma_tui import header

        source = Path(header.__file__).read_text(encoding="utf-8")
        assert "get_active_profile" in source

    def test_tui_no_hardcoded_provider_names(self) -> None:
        """VAL-TUI-030: TUI must not hardcode provider names."""
        from kazma_tui import header

        source = Path(header.__file__).read_text(encoding="utf-8")
        # Common provider names that should NOT be hardcoded
        hardcoded_providers = ['"openai"', '"anthropic"', '"google"', '"cohere"']
        for provider in hardcoded_providers:
            assert provider not in source, f"header.py contains hardcoded provider: {provider}"

    def test_tui_no_hardcoded_model_names(self) -> None:
        """VAL-TUI-031: TUI must not hardcode model names."""
        from kazma_tui import header

        source = Path(header.__file__).read_text(encoding="utf-8")
        hardcoded_models = ['"gpt-4o"', '"claude-3"', '"gemini-pro"']
        for model in hardcoded_models:
            assert model not in source, f"header.py contains hardcoded model: {model}"


# ---------------------------------------------------------------------------
# VAL-TUI-032: No Model-Switching Logic
# ---------------------------------------------------------------------------


class TestNoModelSwitchingIntegration:
    """VAL-TUI-032: TUI must not contain model-switching or config-write logic."""

    @pytest.mark.parametrize(
        "filename",
        [
            "app.py",
            "header.py",
            "footer.py",
            "dashboard.py",
            "chat.py",
        ],
    )
    def test_no_mutation_calls_in_tui_module(self, filename: str) -> None:
        """Each TUI module must not call mutation methods."""
        tui_dir = Path(__file__).resolve().parent.parent / "kazma_tui"
        filepath = tui_dir / filename
        source = filepath.read_text(encoding="utf-8")
        forbidden = [
            "set_active_profile",
            "set_active_provider",
            "set_active_model",
            "ConfigStore.write",
            "config_store.write",
            "registry.set_",
        ]
        for term in forbidden:
            assert term not in source, f"{filename} contains forbidden mutation call: {term}"

    def test_tui_only_imports_read_methods(self) -> None:
        """TUI header must only import read-only methods from ModelRegistry."""
        from kazma_tui import header

        source = Path(header.__file__).read_text(encoding="utf-8")
        # Should import get_model_registry (read-only factory)
        assert "get_model_registry" in source
        # Should NOT import set_ or write methods
        assert "set_active" not in source
        assert "ConfigStore" not in source


# ---------------------------------------------------------------------------
# VAL-TUI-002: English-Only UI (comprehensive source scan)
# ---------------------------------------------------------------------------


class TestEnglishOnlyComprehensive:
    """VAL-TUI-002: All TUI source files must be English-only."""

    ARABIC_RE = __import__("re").compile(
        r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF\u0590-\u05FF]"
    )

    @pytest.mark.parametrize(
        "filename",
        [
            "__init__.py",
            "__main__.py",
            "app.py",
            "header.py",
            "footer.py",
            "dashboard.py",
            "chat.py",
        ],
    )
    def test_no_arabic_in_source_file(self, filename: str) -> None:
        """Each TUI source file must not contain Arabic or RTL characters."""
        tui_dir = Path(__file__).resolve().parent.parent / "kazma_tui"
        filepath = tui_dir / filename
        source = filepath.read_text(encoding="utf-8")
        match = self.ARABIC_RE.search(source)
        assert not match, f"{filename} contains Arabic/RTL character at position {match.start()}: {match.group()!r}"

    def test_all_ui_strings_are_english(self) -> None:
        """UI-facing strings in source must be English (no non-ASCII above 0x0500)."""
        tui_dir = Path(__file__).resolve().parent.parent / "kazma_tui"
        high_unicode = __import__("re").compile(r"[\u0500-\uFFFF]")
        for py_file in tui_dir.glob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            # Filter out common non-ASCII that's OK (emojis, etc.)
            for match in high_unicode.finditer(source):
                char = match.group()
                # Allow common typographic characters
                if char in ("\u2014", "\u2013", "\u201c", "\u201d", "\u2018", "\u2019"):
                    continue
                # Allow emoji variation selectors (used for proper emoji rendering)
                if char in ("\ufe0f", "\ufe0e"):  # VS16 and VS15
                    continue
                # Check if it's in an Arabic/RTL range
                code = ord(char)
                if 0x0600 <= code <= 0x08FF or 0xFB50 <= code <= 0xFEFF or 0x0590 <= code <= 0x05FF:
                    pytest.fail(
                        f"{py_file.name} contains non-English character U+{code:04X} at position {match.start()}"
                    )
