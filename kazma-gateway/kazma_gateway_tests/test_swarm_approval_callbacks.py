"""Regression tests for Sprint 14 dead seam fix.

Verifies that swarm_approve_ and swarm_reject_ callbacks are properly
handled by all three platform bus adapters. Prevents reintroduction of
the "dead seam" where callbacks were not routed.
"""

import time
import uuid

import pytest


def _open() -> str:
    """A pending swarm-bus request, as SafetyMiddleware.check opens it.

    The dead-seam lock now reads the shared decision row: since the Swarm
    audit's stage 1 a button decides that row (``shared_approvals``), not an
    in-process event of the adapter that posted the card.
    """
    from kazma_core.swarm import shared_approvals

    approval_id = "task-" + uuid.uuid4().hex[:12]
    shared_approvals.open_request(approval_id, tool="file_write", args="{}", deadline=time.time() + 60)
    return approval_id


def _status(approval_id: str) -> str | None:
    from kazma_core.swarm import shared_approvals

    row = shared_approvals.get(approval_id)
    return row.get("status") if row else None


class TestTelegramBusAdapterCallbacks:
    """Test TelegramBusAdapter.handle_callback() for swarm approvals."""
    
    def test_handle_callback_approve(self):
        """swarm_approve_<task_id> marks task as approved."""
        from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter
        
        adapter = TelegramBusAdapter(bot_token="test_token", chat_id="12345")
        approval_id = _open()

        task_id = adapter.handle_callback("swarm_approve_" + approval_id)

        assert task_id == approval_id
        assert _status(approval_id) == "approved"
    
    def test_handle_callback_reject(self):
        """swarm_reject_<task_id> marks task as rejected."""
        from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter
        
        adapter = TelegramBusAdapter(bot_token="test_token", chat_id="12345")
        approval_id = _open()

        task_id = adapter.handle_callback("swarm_reject_" + approval_id)

        assert task_id == approval_id
        assert _status(approval_id) == "denied"
    
    def test_handle_callback_unknown_returns_none(self):
        """Unknown callback prefix returns None (doesn't crash)."""
        from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter
        
        adapter = TelegramBusAdapter(bot_token="test_token", chat_id="12345")
        
        result = adapter.handle_callback("unknown_callback")
        
        assert result is None
    
    def test_handle_callback_for_unknown_task(self):
        """A button for a request nobody opened returns its id and decides
        nothing: no row appears (Swarm audit SW-S02 -- a click on an unknown
        id used to create a decided row a later request inherited)."""
        from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter

        adapter = TelegramBusAdapter(bot_token="test_token", chat_id="12345")
        unknown = "nonexistent-" + uuid.uuid4().hex[:8]

        result = adapter.handle_callback("swarm_approve_" + unknown)

        assert result == unknown
        assert _status(unknown) is None


class TestDiscordBusAdapterCallbacks:
    """Test DiscordBusAdapter.handle_callback() for swarm approvals."""
    
    def test_handle_callback_approve(self):
        """swarm_approve_<task_id> marks task as approved."""
        from kazma_gateway.adapters.discord_bus import DiscordBusAdapter
        
        adapter = DiscordBusAdapter(bot_token="test_token", channel_id="12345")
        approval_id = _open()

        task_id = adapter.handle_callback("swarm_approve_" + approval_id)

        assert task_id == approval_id
        assert _status(approval_id) == "approved"
    
    def test_handle_callback_reject(self):
        """swarm_reject_<task_id> marks task as rejected."""
        from kazma_gateway.adapters.discord_bus import DiscordBusAdapter
        
        adapter = DiscordBusAdapter(bot_token="test_token", channel_id="12345")
        approval_id = _open()

        task_id = adapter.handle_callback("swarm_reject_" + approval_id)

        assert task_id == approval_id
        assert _status(approval_id) == "denied"
    
    def test_handle_callback_unknown_returns_none(self):
        """Unknown callback prefix returns None."""
        from kazma_gateway.adapters.discord_bus import DiscordBusAdapter
        
        adapter = DiscordBusAdapter(bot_token="test_token", channel_id="12345")
        
        result = adapter.handle_callback("unknown_callback")
        
        assert result is None


class TestSlackBusAdapterCallbacks:
    """Test SlackBusAdapter.handle_callback() for swarm approvals."""
    
    def test_handle_callback_approve(self):
        """swarm_approve_<task_id> marks task as approved."""
        from kazma_gateway.adapters.slack_bus import SlackBusAdapter
        
        adapter = SlackBusAdapter(bot_token="test_token", channel_id="12345")
        approval_id = _open()

        task_id = adapter.handle_callback("swarm_approve_" + approval_id)

        assert task_id == approval_id
        assert _status(approval_id) == "approved"
    
    def test_handle_callback_reject(self):
        """swarm_reject_<task_id> marks task as rejected."""
        from kazma_gateway.adapters.slack_bus import SlackBusAdapter
        
        adapter = SlackBusAdapter(bot_token="test_token", channel_id="12345")
        approval_id = _open()

        task_id = adapter.handle_callback("swarm_reject_" + approval_id)

        assert task_id == approval_id
        assert _status(approval_id) == "denied"
    
    def test_handle_callback_unknown_returns_none(self):
        """Unknown callback prefix returns None."""
        from kazma_gateway.adapters.slack_bus import SlackBusAdapter
        
        adapter = SlackBusAdapter(bot_token="test_token", channel_id="12345")
        
        result = adapter.handle_callback("unknown_callback")
        
        assert result is None


class TestCallbackRoutingIntegration:
    """Test that platform adapters route callbacks to bus adapters."""
    
    def test_telegram_adapter_routes_swarm_callbacks(self):
        """TelegramAdapter._handle_callback_query routes swarm approvals to bus.

        The literal ``swarm_approve_``/``swarm_reject_`` prefixes live in the
        extracted ``parse_callback_data`` helper now, so we verify the wiring
        (parse -> get_message_bus -> handle_callback) plus the parser
        recognizing the prefixes, rather than grepping the adapter method.
        """
        from kazma_gateway.adapters.telegram import TelegramAdapter
        from kazma_gateway.adapters.telegram_callbacks import parse_callback_data
        import inspect

        src = inspect.getsource(TelegramAdapter._handle_callback_query)
        assert "parse_callback_data" in src
        # The swarm button decides the shared request directly, with the user
        # who pressed it (no bus adapter lookup to fail under FanOut).
        assert "decide_from_button" in src and "actor=_approver" in src

        # The extracted parser recognizes the swarm approval prefixes.
        assert parse_callback_data("swarm_approve_task-1").kind == "swarm"
        assert parse_callback_data("swarm_reject_task-1").kind == "swarm"
    
    def test_discord_adapter_routes_swarm_callbacks(self):
        """DiscordAdapter._handle_interaction routes via discord_callbacks module."""
        from kazma_gateway.adapters.discord import DiscordAdapter
        from kazma_gateway.adapters.discord_callbacks import route_swarm_bus
        from kazma_gateway.adapters.platform_callbacks import parse_callback_data
        import inspect

        src = inspect.getsource(DiscordAdapter._handle_interaction)
        assert "route_swarm_bus" in src
        assert "discord_callbacks" in src
        assert parse_callback_data("swarm_approve_task-1").kind == "swarm"
        assert parse_callback_data("swarm_reject_task-1").kind == "swarm"
        assert callable(route_swarm_bus)

    def test_slack_adapter_routes_swarm_callbacks(self):
        """SlackAdapter routes swarm approvals via slack_callbacks module."""
        from kazma_gateway.adapters.slack import SlackAdapter
        from kazma_gateway.adapters.slack_callbacks import route_swarm_bus
        from kazma_gateway.adapters.platform_callbacks import parse_callback_data
        import inspect

        src = inspect.getsource(SlackAdapter)
        assert "slack_callbacks" in src
        assert "route_swarm_bus" in src or "iter_block_actions" in src
        assert parse_callback_data("swarm_approve_task-1").kind == "swarm"
        assert callable(route_swarm_bus)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])