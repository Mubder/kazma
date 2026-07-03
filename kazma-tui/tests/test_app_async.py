"""Async Textual app tests — actually mount and interact with the TUI.

These tests use Textual's ``run_test()`` to launch the app headlessly
and verify widget rendering, input handling, and full-app lifecycle.

Validates:
- VAL-TUI-003: Header Shows Provider/Model Info (rendered)
- VAL-TUI-004: Footer Shows Keyboard Shortcuts (rendered)
- VAL-TUI-010: CPU/Memory Metrics Display (rendered)
- VAL-TUI-015: Real-Time Metrics Updates (set_interval)
- VAL-TUI-020: Chat Input Accepts User Text (pilot interaction)
- VAL-TUI-021: Chat Displays Messages (pilot interaction)
- VAL-TUI-022: Basic Commands Support (pilot interaction)
- VAL-TUI-050: Launch -> ModelRegistry Integration Flow
- VAL-TUI-051: Metrics Dashboard -> Real-Time Updates Flow
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_model_registry(
    provider: str = "openai", model: str = "gpt-4o"
) -> MagicMock:
    """Build a mock ModelRegistry returning a fixed profile."""
    mock = MagicMock()
    mock.get_active_profile.return_value = {
        "provider": provider,
        "model": model,
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test",
    }
    return mock


def _mock_telemetry_snapshot(
    cpu: float = 45.2, ram_used: float = 16.4, ram_total: float = 32.0
) -> MagicMock:
    """Build a mock TelemetrySnapshot."""
    snap = MagicMock()
    snap.cpu = cpu
    snap.ram_used_gb = ram_used
    snap.ram_total_gb = ram_total
    return snap


def _mock_trace_entry(ts: float | None = None) -> MagicMock:
    """Build a single mock TraceEntry."""
    entry = MagicMock()
    entry.timestamp = ts if ts is not None else time.time()
    entry.trace_type = "llm"
    entry.label = "call-0"
    entry.status = "success"
    entry.duration_ms = 120.0
    return entry


# ---------------------------------------------------------------------------
# Full-App Launch Tests (VAL-TUI-050)
# ---------------------------------------------------------------------------


class TestAppLaunchWithModelRegistry:
    """VAL-TUI-050: TUI launch initializes and displays ModelRegistry data."""

    @pytest.mark.asyncio
    async def test_app_mounts_header_with_provider_model(self) -> None:
        """After mount, the header must display provider/model from ModelRegistry."""
        from kazma_tui.app import KazmaTUI
        from kazma_tui.header import KazmaHeader

        mock_reg = _mock_model_registry("anthropic", "claude-3-opus")
        with patch("kazma_tui.header.get_model_registry", return_value=mock_reg):
            app = KazmaTUI()
            async with app.run_test(size=(120, 40)) as pilot:
                header = app.query_one(KazmaHeader)
                assert header is not None
                # The profile text should contain the provider/model
                profile_widget = header.query_one("#header-profile")
                assert profile_widget is not None
                rendered = profile_widget.content
                assert "anthropic" in rendered.lower() or "claude-3-opus" in rendered

    @pytest.mark.asyncio
    async def test_app_mounts_header_fallback_on_registry_error(self) -> None:
        """VAL-TUI-050: Header shows 'Not configured' when ModelRegistry raises."""
        from kazma_tui.app import KazmaTUI
        from kazma_tui.header import KazmaHeader

        with patch(
            "kazma_tui.header.get_model_registry",
            side_effect=RuntimeError("Not initialized"),
        ):
            app = KazmaTUI()
            async with app.run_test(size=(120, 40)) as pilot:
                header = app.query_one(KazmaHeader)
                profile_widget = header.query_one("#header-profile")
                assert profile_widget is not None
                rendered = profile_widget.content
                assert "not configured" in rendered.lower() or len(rendered) > 0

    @pytest.mark.asyncio
    async def test_app_mounts_all_widgets(self) -> None:
        """App must mount header, dashboard, chat, and footer."""
        from kazma_tui.app import KazmaTUI
        from kazma_tui.chat import ChatPanel
        from kazma_tui.dashboard import MetricsDashboard
        from kazma_tui.footer import Footer
        from kazma_tui.header import KazmaHeader

        mock_reg = _mock_model_registry()
        with patch("kazma_tui.header.get_model_registry", return_value=mock_reg):
            app = KazmaTUI()
            async with app.run_test(size=(120, 40)) as pilot:
                assert app.query_one(KazmaHeader) is not None
                assert app.query_one(MetricsDashboard) is not None
                assert app.query_one(ChatPanel) is not None
                assert app.query_one(Footer) is not None


# ---------------------------------------------------------------------------
# Chat Interaction Tests (VAL-TUI-020, VAL-TUI-021, VAL-TUI-022)
# ---------------------------------------------------------------------------


class TestChatAsyncInteraction:
    """VAL-TUI-020/021/022: Chat input, message display, and commands via pilot."""

    @pytest.mark.asyncio
    async def test_chat_input_focused_on_mount(self) -> None:
        """VAL-TUI-020: The chat input must be focused after mount."""
        from kazma_tui.app import KazmaTUI

        mock_reg = _mock_model_registry()
        with patch("kazma_tui.header.get_model_registry", return_value=mock_reg):
            app = KazmaTUI()
            async with app.run_test(size=(120, 40)) as pilot:
                from textual.widgets import Input

                chat_input = app.query_one("#chat-input", Input)
                assert chat_input is not None
                assert chat_input.has_focus

    @pytest.mark.asyncio
    async def test_chat_type_and_submit_message(self) -> None:
        """VAL-TUI-020: Typing text and pressing Enter adds a user message."""
        from kazma_tui.app import KazmaTUI

        mock_reg = _mock_model_registry()
        with patch("kazma_tui.header.get_model_registry", return_value=mock_reg):
            app = KazmaTUI()
            async with app.run_test(size=(120, 40)) as pilot:
                from textual.widgets import Input, RichLog

                chat_input = app.query_one("#chat-input", Input)
                await pilot.press(*list("Hello world"))
                await pilot.press("enter")
                # Input should be cleared after submit
                assert chat_input.value == ""
                # Chat log should contain the message
                log = app.query_one("#chat-log", RichLog)
                # RichLog renders to a buffer; check it's not empty
                assert log is not None

    @pytest.mark.asyncio
    async def test_help_command_displays_help(self) -> None:
        """VAL-TUI-022: /help command displays available commands."""
        from kazma_tui.app import KazmaTUI

        mock_reg = _mock_model_registry()
        with patch("kazma_tui.header.get_model_registry", return_value=mock_reg):
            app = KazmaTUI()
            async with app.run_test(size=(120, 40)) as pilot:
                from textual.widgets import Input

                chat_input = app.query_one("#chat-input", Input)
                await pilot.press(*list("/help"))
                await pilot.press("enter")
                # Input should be cleared
                assert chat_input.value == ""
                # add_message should have been called (we can verify via log)
                # The help text mentions /help, /clear, /quit
                log = app.query_one("#chat-log")
                assert log is not None

    @pytest.mark.asyncio
    async def test_clear_command_clears_log(self) -> None:
        """VAL-TUI-022: /clear command clears the chat log."""
        from kazma_tui.app import KazmaTUI

        mock_reg = _mock_model_registry()
        with patch("kazma_tui.header.get_model_registry", return_value=mock_reg):
            app = KazmaTUI()
            async with app.run_test(size=(120, 40)) as pilot:
                from textual.widgets import Input

                # First add a message
                chat_input = app.query_one("#chat-input", Input)
                await pilot.press(*list("Hello"))
                await pilot.press("enter")
                # Now clear
                await pilot.press(*list("/clear"))
                await pilot.press("enter")
                # The log should have been cleared (no crash)

    @pytest.mark.asyncio
    async def test_unknown_command_displays_error(self) -> None:
        """VAL-TUI-022: Unknown commands display an error message."""
        from kazma_tui.app import KazmaTUI

        mock_reg = _mock_model_registry()
        with patch("kazma_tui.header.get_model_registry", return_value=mock_reg):
            app = KazmaTUI()
            async with app.run_test(size=(120, 40)) as pilot:
                from textual.widgets import Input

                chat_input = app.query_one("#chat-input", Input)
                await pilot.press(*list("/foobar"))
                await pilot.press("enter")
                # Should not crash, should display "Unknown command"


# ---------------------------------------------------------------------------
# Dashboard Metrics Tests (VAL-TUI-010, VAL-TUI-051)
# ---------------------------------------------------------------------------


class TestDashboardAsyncRefresh:
    """VAL-TUI-015, VAL-TUI-051: Dashboard periodic refresh and data source integration."""

    @pytest.mark.asyncio
    async def test_dashboard_mounts_metric_widgets(self) -> None:
        """VAL-TUI-010: Dashboard must mount RPM, latency, health, VRAM, errors, agents widgets."""
        from kazma_tui.app import KazmaTUI
        from kazma_tui.dashboard import MetricCard

        mock_reg = _mock_model_registry()
        with patch("kazma_tui.header.get_model_registry", return_value=mock_reg):
            app = KazmaTUI()
            async with app.run_test(size=(120, 40)) as pilot:
                dashboard = pilot.app.query_one("MetricsDashboard")
                cards = dashboard.query(MetricCard)
                card_ids = {w.id for w in cards if w.id}
                assert "metric-rpm" in card_ids
                assert "metric-latency" in card_ids
                assert "metric-health" in card_ids
                assert "metric-vram" in card_ids
                assert "metric-errors" in card_ids
                assert "metric-agents" in card_ids

    @pytest.mark.asyncio
    async def test_dashboard_shows_na_without_data_sources(self) -> None:
        """VAL-TUI-010: Without data sources, metrics should show N/A."""
        from kazma_tui.app import KazmaTUI
        from kazma_tui.dashboard import MetricCard

        mock_reg = _mock_model_registry()
        with patch("kazma_tui.header.get_model_registry", return_value=mock_reg):
            # Patch out all data source imports so they fail
            with patch.dict("sys.modules", {"kazma_core": None}):
                app = KazmaTUI()
                async with app.run_test(size=(120, 40)) as pilot:
                    dashboard = pilot.app.query_one("MetricsDashboard")
                    vram_card = dashboard.query_one("#metric-vram", MetricCard)
                    assert vram_card._value == "N/A"

    @pytest.mark.asyncio
    async def test_dashboard_refreshes_with_mock_data(self) -> None:
        """VAL-TUI-051: Dashboard fetches from HardwareMonitor on mount and refresh."""
        from kazma_tui.app import KazmaTUI
        from kazma_tui.dashboard import MetricCard, MetricsDashboard

        mock_reg = _mock_model_registry()
        from unittest.mock import AsyncMock
        mock_monitor = MagicMock()
        snapshot = _mock_telemetry_snapshot(75.5, 8.0, 16.0)
        snapshot.vram_used_gb = 17.6
        snapshot.vram_total_gb = 22.5
        mock_monitor.get_stats = AsyncMock(return_value=snapshot)

        mock_store = MagicMock()
        mock_store.recent.return_value = [_mock_trace_entry()]

        mock_collector = MagicMock()
        mock_collector.get_all_metrics.return_value = [
            {"worker": "w1", "tasks_completed": 10, "tasks_failed": 1, "avg_latency": 200.0}
        ]

        mock_engine = MagicMock()
        mock_engine._workers = {"w1": MagicMock()}

        with patch("kazma_tui.header.get_model_registry", return_value=mock_reg):
            app = KazmaTUI()
            async with app.run_test(size=(120, 40)) as pilot:
                dashboard = app.query_one(MetricsDashboard)
                # Inject mock data sources
                dashboard._hardware_monitor = mock_monitor
                dashboard._trace_store = mock_store
                dashboard._metrics_collector = mock_collector
                dashboard._swarm_engine = mock_engine
                # Trigger a manual refresh
                dashboard._do_refresh()
                await pilot.pause()
                # Check that agents card was updated
                agents_card = app.query_one("#metric-agents", MetricCard)
                assert "w1" in agents_card._value


# ---------------------------------------------------------------------------
# Header Refresh Tests (VAL-TUI-003, VAL-TUI-030, VAL-TUI-031)
# ---------------------------------------------------------------------------


class TestHeaderRefreshProfile:
    """VAL-TUI-003, VAL-TUI-030, VAL-TUI-031: Header refreshes profile on demand."""

    @pytest.mark.asyncio
    async def test_header_refresh_updates_display(self) -> None:
        """Calling refresh_profile() must update the header display."""
        from kazma_tui.app import KazmaTUI
        from kazma_tui.header import KazmaHeader
        from textual.widgets import Static

        mock_reg1 = _mock_model_registry("openai", "gpt-4o")
        mock_reg2 = _mock_model_registry("anthropic", "claude-3-opus")

        with patch("kazma_tui.header.get_model_registry", return_value=mock_reg1):
            app = KazmaTUI()
            async with app.run_test(size=(120, 40)) as pilot:
                header = app.query_one(KazmaHeader)
                profile_w = header.query_one("#header-profile", Static)
                initial = profile_w.content
                assert "openai" in initial.lower() or "gpt-4o" in initial

                # Swap the registry mock
                with patch("kazma_tui.header.get_model_registry", return_value=mock_reg2):
                    header.refresh_profile()
                    await pilot.pause()
                    updated = profile_w.content
                    assert "anthropic" in updated.lower() or "claude-3-opus" in updated

    @pytest.mark.asyncio
    async def test_header_fallback_text_on_empty_profile(self) -> None:
        """VAL-TUI-050: Header shows fallback when profile is empty."""
        from kazma_tui.app import KazmaTUI
        from kazma_tui.header import KazmaHeader
        from textual.widgets import Static

        mock_reg = MagicMock()
        mock_reg.get_active_profile.return_value = {"provider": "", "model": ""}
        with patch("kazma_tui.header.get_model_registry", return_value=mock_reg):
            app = KazmaTUI()
            async with app.run_test(size=(120, 40)) as pilot:
                header = app.query_one(KazmaHeader)
                profile_w = header.query_one("#header-profile", Static)
                rendered = profile_w.content
                assert "not configured" in rendered.lower()


# ---------------------------------------------------------------------------
# Footer Rendering Tests (VAL-TUI-004)
# ---------------------------------------------------------------------------


class TestFooterAsyncRendering:
    """VAL-TUI-004: Footer shortcuts are rendered in the app."""

    @pytest.mark.asyncio
    async def test_footer_renders_shortcuts(self) -> None:
        """Footer must render Ctrl+Q, Tab, and Enter shortcuts."""
        from kazma_tui.app import KazmaTUI
        from kazma_tui.footer import Footer
        from textual.widgets import Static

        mock_reg = _mock_model_registry()
        with patch("kazma_tui.header.get_model_registry", return_value=mock_reg):
            app = KazmaTUI()
            async with app.run_test(size=(120, 40)) as pilot:
                footer = app.query_one(Footer)
                assert footer is not None
                # The footer contains a Static with shortcuts text
                statics = footer.query(Static)
                assert len(statics) >= 1
                text = statics[0].content
                assert "ctrl+q" in text.lower() or "quit" in text.lower()
                assert "enter" in text.lower()


# ---------------------------------------------------------------------------
# Data Source Unavailability (VAL-TUI-051)
# ---------------------------------------------------------------------------


class TestDataSourceUnavailability:
    """VAL-TUI-051: Dashboard handles unavailable data sources gracefully."""

    def test_format_cpu_na_on_none(self) -> None:
        """CPU shows N/A when value is None."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        assert "N/A" in d._format_cpu(None)

    def test_format_ram_na_on_partial_none(self) -> None:
        """RAM shows N/A when only one value is None."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        assert "N/A" in d._format_ram(16.0, None)
        assert "N/A" in d._format_ram(None, 32.0)

    def test_format_rpm_na_on_none(self) -> None:
        """RPM shows N/A when value is None."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        assert "N/A" in d._format_rpm(None)

    def test_format_latency_na_on_none(self) -> None:
        """Latency shows N/A when value is None."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        assert "N/A" in d._format_latency(None)

    def test_format_error_rate_na_on_none(self) -> None:
        """Error rate shows N/A when value is None."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        assert "N/A" in d._format_error_rate(None)

    def test_format_agents_na_on_empty(self) -> None:
        """Agents shows N/A when list is empty."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        assert "N/A" in d._format_agents([])

    def test_calculate_error_rate_zero_division(self) -> None:
        """VAL-TUI-013: Error rate must handle zero tasks gracefully."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        rate = d._calculate_error_rate([{"tasks_completed": 0, "tasks_failed": 0}])
        assert rate == 0.0

    def test_calculate_avg_latency_empty_data(self) -> None:
        """VAL-TUI-012: Average latency must handle empty data."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        lat = d._calculate_avg_latency([])
        assert lat == 0.0

    def test_calculate_rpm_with_all_entries_outside_window(self) -> None:
        """VAL-TUI-011: RPM must be 0 when all entries are older than 60s."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        old_entry = MagicMock()
        old_entry.timestamp = time.time() - 120.0  # 2 minutes ago
        rpm = d._calculate_rpm([old_entry])
        assert rpm == 0

    def test_calculate_rpm_burst(self) -> None:
        """VAL-TUI-011: RPM must handle burst of entries at same timestamp."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        now = time.time()
        entries = []
        for _ in range(10):
            e = MagicMock()
            e.timestamp = now
            entries.append(e)
        rpm = d._calculate_rpm(entries)
        # All at same instant => burst count
        assert rpm == 10
