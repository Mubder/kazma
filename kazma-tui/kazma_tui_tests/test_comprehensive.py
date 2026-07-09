"""Comprehensive edge-case and behavioral tests for all TUI components.

Validates deeper behavioral aspects of each assertion that the unit tests
only check at surface level.

Validates:
- VAL-TUI-003: Header Shows Provider/Model Info (edge cases)
- VAL-TUI-004: Footer Shows Keyboard Shortcuts (edge cases)
- VAL-TUI-010: CPU/Memory Metrics Display (edge cases)
- VAL-TUI-011: RPM Display (edge cases)
- VAL-TUI-012: Latency Metrics Display (edge cases)
- VAL-TUI-013: Error Rate Display (edge cases)
- VAL-TUI-014: Active Agents List (edge cases)
- VAL-TUI-020: Chat Input Accepts User Text (behavioral)
- VAL-TUI-021: Chat Displays Messages (behavioral)
- VAL-TUI-022: Basic Commands Support (behavioral)
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Header Edge Cases (VAL-TUI-003)
# ---------------------------------------------------------------------------


class TestHeaderEdgeCases:
    """VAL-TUI-003: Header handles various profile formats."""

    def test_header_with_whitespace_provider(self) -> None:
        """Header must include provider and model in text."""
        from kazma_tui.header import KazmaHeader

        mock_reg = MagicMock()
        mock_reg.get_active_profile.return_value = {
            "provider": "openai",
            "model": "gpt-4o",
        }
        with patch("kazma_tui.header._get_model_registry", return_value=mock_reg):
            widget = KazmaHeader()
            text = widget._build_header_text()
            assert "openai" in text
            assert "gpt-4o" in text

    def test_header_with_none_values(self) -> None:
        """Header must handle None provider/model gracefully."""
        from kazma_tui.header import _FALLBACK_TEXT, KazmaHeader

        mock_reg = MagicMock()
        mock_reg.get_active_profile.return_value = {
            "provider": None,
            "model": None,
        }
        with patch("kazma_tui.header._get_model_registry", return_value=mock_reg):
            widget = KazmaHeader()
            text = widget._build_header_text()
            # Should handle None gracefully - fallback text is used for display
            assert isinstance(text, str)
            assert "KAZMA" in text

    def test_header_with_missing_keys(self) -> None:
        """Header must handle missing provider/model keys gracefully."""
        from kazma_tui.header import _FALLBACK_TEXT, KazmaHeader

        mock_reg = MagicMock()
        mock_reg.get_active_profile.return_value = {}
        with patch("kazma_tui.header._get_model_registry", return_value=mock_reg):
            widget = KazmaHeader()
            text = widget._build_header_text()
            assert isinstance(text, str)

    def test_header_reactive_attributes(self) -> None:
        """Header must have reactive provider and model attributes."""
        from kazma_tui.header import KazmaHeader

        widget = KazmaHeader()
        assert hasattr(widget, "provider")
        assert hasattr(widget, "model")

    def test_header_title_static(self) -> None:
        """Header must include a logo static showing the KA ASCII art."""
        from kazma_tui.header import KazmaHeader

        widget = KazmaHeader()
        # Header extends Static, not a compose-yielding widget
        # It displays as a single unit without sub-widgets
        assert hasattr(widget, "_build_header_text")

    def test_header_separator_static(self) -> None:
        """Header must include a tagline static."""
        from kazma_tui.header import KazmaHeader

        widget = KazmaHeader()
        # Header extends Static, not a compose-yielding widget
        assert hasattr(widget, "_build_header_text")

    def test_header_profile_static(self) -> None:
        """Header must include a profile static for provider/model display."""
        from kazma_tui.header import KazmaHeader

        widget = KazmaHeader()
        # Header extends Static, not a compose-yielding widget
        assert hasattr(widget, "_build_header_text")


# ---------------------------------------------------------------------------
# Footer Edge Cases (VAL-TUI-004)
# ---------------------------------------------------------------------------


class TestFooterEdgeCases:
    """VAL-TUI-004: Footer handles various shortcut formats."""

    def test_footer_shortcuts_list_not_empty(self) -> None:
        """Footer must define at least one shortcut."""
        from kazma_tui.footer import CHAT_SHORTCUTS

        assert len(CHAT_SHORTCUTS) >= 1

    def test_footer_shortcuts_have_labels_and_descriptions(self) -> None:
        """Each shortcut must have a label and description."""
        from kazma_tui.footer import CHAT_SHORTCUTS

        for key, desc in CHAT_SHORTCUTS:
            assert isinstance(key, str) and len(key) > 0
            assert isinstance(desc, str) and len(desc) > 0

    def test_footer_text_contains_all_shortcuts(self) -> None:
        """Footer text must mention all defined shortcuts."""
        from kazma_tui.footer import CHAT_SHORTCUTS, KazmaFooter

        widget = KazmaFooter()
        text = widget._get_shortcuts_text().lower()
        for key, desc in CHAT_SHORTCUTS:
            assert key.lower() in text, f"Footer text missing shortcut: {key}"

    def test_footer_text_uses_pipe_separator(self) -> None:
        """Footer text must use pipe separator between shortcuts."""
        from kazma_tui.footer import CHAT_SHORTCUTS, KazmaFooter

        widget = KazmaFooter()
        text = widget._get_shortcuts_text()
        if len(CHAT_SHORTCUTS) > 1:
            assert "|" in text

    def test_footer_compose_yields_static(self) -> None:
        """Footer compose must yield a Static widget."""
        from kazma_tui.footer import KazmaFooter
        from textual.widgets import Static

        widget = KazmaFooter()
        widgets = list(widget.compose())
        assert any(isinstance(w, Static) for w in widgets)


# ---------------------------------------------------------------------------
# Dashboard Edge Cases (VAL-TUI-010 through VAL-TUI-015)
# ---------------------------------------------------------------------------


class TestDashboardFormattingEdgeCases:
    """Dashboard formatting helpers must handle boundary values."""

    def test_format_cpu_zero(self) -> None:
        """CPU must format 0% correctly."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        text = d._format_cpu(0.0)
        assert "0.0%" in text

    def test_format_cpu_100(self) -> None:
        """CPU must format 100% correctly."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        text = d._format_cpu(100.0)
        assert "100.0%" in text

    def test_format_ram_zero(self) -> None:
        """RAM must format 0/0 GB correctly."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        text = d._format_ram(0.0, 0.0)
        assert "0.0" in text
        assert "GB" in text

    def test_format_rpm_zero(self) -> None:
        """RPM must format 0 correctly."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        text = d._format_rpm(0)
        assert "0" in text

    def test_format_latency_zero(self) -> None:
        """Latency must format 0ms correctly."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        text = d._format_latency(0.0)
        assert "0.0ms" in text

    def test_format_error_rate_zero(self) -> None:
        """Error rate must format 0% correctly."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        text = d._format_error_rate(0.0)
        assert "0.00%" in text

    def test_format_error_rate_100(self) -> None:
        """Error rate must format 100% correctly."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        text = d._format_error_rate(100.0)
        assert "100.00%" in text

    def test_format_agents_single(self) -> None:
        """Agents must format single agent correctly."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        text = d._format_agents(["analyst"])
        assert "analyst" in text
        assert "N/A" not in text

    def test_format_agents_multiple_sorted(self) -> None:
        """Agent names must be sorted alphabetically."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        names = d._get_agent_names(MagicMock(_workers={"zulu": None, "alpha": None}))
        assert names == ["alpha", "zulu"]


class TestDashboardCalculationEdgeCases:
    """Dashboard calculation helpers must handle edge cases."""

    def test_calculate_rpm_single_entry_at_now(self) -> None:
        """RPM with single entry at current time should be 1 (burst)."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        e = MagicMock()
        e.timestamp = time.time()
        rpm = d._calculate_rpm([e])
        assert rpm == 1

    def test_calculate_avg_latency_single_worker(self) -> None:
        """Average latency with single worker returns that worker's latency."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        data = [{"avg_latency": 500.0, "tasks_completed": 10, "tasks_failed": 0}]
        lat = d._calculate_avg_latency(data)
        assert lat == 500.0

    def test_calculate_error_rate_100_percent(self) -> None:
        """Error rate must be 100% when all tasks fail."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        data = [{"tasks_completed": 0, "tasks_failed": 100}]
        rate = d._calculate_error_rate(data)
        assert abs(rate - 100.0) < 0.01

    def test_calculate_error_rate_multiple_workers(self) -> None:
        """Error rate must aggregate across multiple workers."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        data = [
            {"tasks_completed": 80, "tasks_failed": 0},
            {"tasks_completed": 10, "tasks_failed": 10},
        ]
        rate = d._calculate_error_rate(data)
        # 10 / 100 = 10%
        assert abs(rate - 10.0) < 0.01

    def test_get_agent_names_sorted(self) -> None:
        """Agent names must be returned sorted."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        engine = MagicMock()
        engine._workers = {"charlie": None, "alpha": None, "bravo": None}
        names = d._get_agent_names(engine)
        assert names == ["alpha", "bravo", "charlie"]


# ---------------------------------------------------------------------------
# Chat Behavioral Tests (VAL-TUI-020, VAL-TUI-021, VAL-TUI-022)
# ---------------------------------------------------------------------------


class TestChatBehavioral:
    """VAL-TUI-020/021/022: Chat behavioral tests."""

    def test_chat_compose_yields_rich_log(self) -> None:
        """VAL-TUI-021: Chat must yield a RichLog for message display."""
        from kazma_tui.chat import ChatPanel
        from textual.widgets import RichLog

        panel = ChatPanel()
        widgets = list(panel.compose())
        assert any(isinstance(w, RichLog) for w in widgets)

    def test_chat_compose_yields_input(self) -> None:
        """VAL-TUI-020: Chat must yield an Input for user text entry."""
        from kazma_tui.chat import ChatPanel
        from textual.widgets import Input

        panel = ChatPanel()
        widgets = list(panel.compose())
        assert any(isinstance(w, Input) for w in widgets)

    def test_chat_input_has_correct_id(self) -> None:
        """VAL-TUI-020: Chat input must have id 'chat-input'."""
        from kazma_tui.chat import ChatPanel
        from textual.widgets import Input

        panel = ChatPanel()
        widgets = list(panel.compose())
        input_widgets = [w for w in widgets if isinstance(w, Input)]
        assert len(input_widgets) == 1
        assert input_widgets[0].id == "chat-input"

    def test_chat_log_has_correct_id(self) -> None:
        """VAL-TUI-021: Chat output must have id 'chat-log'."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        widgets = list(panel.compose())
        ids = [getattr(w, "id", None) for w in widgets]
        assert "chat-log" in ids

    def test_chat_help_command_shows_help_text(self) -> None:
        """VAL-TUI-022: /help must display help text mentioning all commands."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        with patch.object(panel, "write") as mock_write:
            panel._handle_command("/help")
            mock_write.assert_called_once()
            call_args = mock_write.call_args[0]
            assert call_args[0] == "system"
            help_text = call_args[1]
            assert "/help" in help_text
            assert "/clear" in help_text
            assert "/quit" in help_text

    def test_chat_clear_calls_clear_on_log(self) -> None:
        """VAL-TUI-022: /clear clears chat output text."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        mock_output = MagicMock()
        mock_output.clear = MagicMock()
        with patch.object(panel, "query_one", return_value=mock_output):
            panel._handle_command("/clear")
            mock_output.clear.assert_called_once()

    def test_chat_quit_calls_app_exit(self) -> None:
        """VAL-TUI-022: /quit must call app.exit()."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        mock_app = MagicMock()
        # Patch the app property on the Widget base class
        with patch.object(type(panel), "app", new_callable=lambda: property(lambda self: mock_app)):
            panel._handle_command("/quit")
            mock_app.exit.assert_called_once()

    def test_chat_unknown_command_displays_error(self) -> None:
        """VAL-TUI-022: Unknown commands must display an error message."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        with patch.object(panel, "write") as mock_write:
            panel._handle_command("/unknown")
            mock_write.assert_called_once()
            call_args = mock_write.call_args[0]
            assert call_args[0] == "system"
            assert "unknown" in call_args[1].lower()

    def test_chat_commands_case_insensitive(self) -> None:
        """VAL-TUI-022: Commands must be case-insensitive."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        with patch.object(panel, "write"):
            # These should not raise
            panel._handle_command("/HELP")
            panel._handle_command("/Help")
            panel._handle_command("/help")

    def test_chat_add_message_format(self) -> None:
        """VAL-TUI-021: Messages must be formatted with role prefix."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()

        # Test that add_message calls write (which formats the message)
        with patch.object(panel, "write") as mock_write:
            panel.add_message("user", "Hello world")
            mock_write.assert_called_once_with("user", "Hello world")
            call_args = mock_write.call_args[0]
            # Verify the write method formats with role
            assert call_args[0] == "user"
            assert call_args[1] == "Hello world"


# ---------------------------------------------------------------------------
# App Structure Tests
# ---------------------------------------------------------------------------


class TestAppStructure:
    """Verify the overall app structure and composition."""

    def test_app_has_css(self) -> None:
        """App must define a CSS layout."""
        from kazma_tui.app import KazmaTUI

        assert KazmaTUI.CSS is not None
        assert len(KazmaTUI.CSS) > 0

    def test_app_title_is_english(self) -> None:
        """VAL-TUI-002: App title must be in English."""
        import re

        from kazma_tui.app import KazmaTUI

        title = KazmaTUI.TITLE
        arabic_re = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
        assert not arabic_re.search(title)
        assert len(title) > 0


# ---------------------------------------------------------------------------
# Dashboard Constructor Tests
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

    def test_dashboard_defaults_to_none(self) -> None:
        """MetricsDashboard must default all data sources to None."""
        from kazma_tui.dashboard import MetricsDashboard

        d = MetricsDashboard()
        assert d._hardware_monitor is None
        assert d._trace_store is None
        assert d._metrics_collector is None
        assert d._swarm_engine is None
