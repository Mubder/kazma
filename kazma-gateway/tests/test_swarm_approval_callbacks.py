"""Regression tests for Sprint 14 dead seam fix.

Verifies that swarm_approve_ and swarm_reject_ callbacks are properly
handled by all three platform bus adapters. Prevents reintroduction of
the "dead seam" where callbacks were not routed.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestTelegramBusAdapterCallbacks:
    """Test TelegramBusAdapter.handle_callback() for swarm approvals."""
    
    def test_handle_callback_approve(self):
        """swarm_approve_<task_id> marks task as approved."""
        from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter
        
        adapter = TelegramBusAdapter(bot_token="test_token", chat_id="12345")
        adapter._pending_approvals["task-123"] = MagicMock()
        
        task_id = adapter.handle_callback("swarm_approve_task-123")
        
        assert task_id == "task-123"
        assert adapter._pending_results["task-123"] is True
        # Note: approve() doesn't remove from _pending_approvals, only sets result
    
    def test_handle_callback_reject(self):
        """swarm_reject_<task_id> marks task as rejected."""
        from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter
        
        adapter = TelegramBusAdapter(bot_token="test_token", chat_id="12345")
        adapter._pending_approvals["task-456"] = MagicMock()
        
        task_id = adapter.handle_callback("swarm_reject_task-456")
        
        assert task_id == "task-456"
        assert adapter._pending_results["task-456"] is False
    
    def test_handle_callback_unknown_returns_none(self):
        """Unknown callback prefix returns None (doesn't crash)."""
        from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter
        
        adapter = TelegramBusAdapter(bot_token="test_token", chat_id="12345")
        
        result = adapter.handle_callback("unknown_callback")
        
        assert result is None
    
    def test_handle_callback_for_unknown_task(self):
        """Callback for non-existent task still returns task_id but doesn't set result (no pending approval)."""
        from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter
        
        adapter = TelegramBusAdapter(bot_token="test_token", chat_id="12345")
        
        # Even if task_id wasn't in pending, it still returns the task_id
        result = adapter.handle_callback("swarm_approve_nonexistent")
        
        assert result == "nonexistent"
        # But _pending_results is not set because there was no pending approval
        assert "nonexistent" not in adapter._pending_results


class TestDiscordBusAdapterCallbacks:
    """Test DiscordBusAdapter.handle_callback() for swarm approvals."""
    
    def test_handle_callback_approve(self):
        """swarm_approve_<task_id> marks task as approved."""
        from kazma_gateway.adapters.discord_bus import DiscordBusAdapter
        
        adapter = DiscordBusAdapter(bot_token="test_token", channel_id="12345")
        adapter._pending_approvals["task-123"] = MagicMock()
        
        task_id = adapter.handle_callback("swarm_approve_task-123")
        
        assert task_id == "task-123"
        assert adapter._pending_results["task-123"] is True
    
    def test_handle_callback_reject(self):
        """swarm_reject_<task_id> marks task as rejected."""
        from kazma_gateway.adapters.discord_bus import DiscordBusAdapter
        
        adapter = DiscordBusAdapter(bot_token="test_token", channel_id="12345")
        adapter._pending_approvals["task-456"] = MagicMock()
        
        task_id = adapter.handle_callback("swarm_reject_task-456")
        
        assert task_id == "task-456"
        assert adapter._pending_results["task-456"] is False
    
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
        adapter._pending_approvals["task-123"] = MagicMock()
        
        task_id = adapter.handle_callback("swarm_approve_task-123")
        
        assert task_id == "task-123"
        assert adapter._pending_results["task-123"] is True
    
    def test_handle_callback_reject(self):
        """swarm_reject_<task_id> marks task as rejected."""
        from kazma_gateway.adapters.slack_bus import SlackBusAdapter
        
        adapter = SlackBusAdapter(bot_token="test_token", channel_id="12345")
        adapter._pending_approvals["task-456"] = MagicMock()
        
        task_id = adapter.handle_callback("swarm_reject_task-456")
        
        assert task_id == "task-456"
        assert adapter._pending_results["task-456"] is False
    
    def test_handle_callback_unknown_returns_none(self):
        """Unknown callback prefix returns None."""
        from kazma_gateway.adapters.slack_bus import SlackBusAdapter
        
        adapter = SlackBusAdapter(bot_token="test_token", channel_id="12345")
        
        result = adapter.handle_callback("unknown_callback")
        
        assert result is None


class TestCallbackRoutingIntegration:
    """Test that platform adapters route callbacks to bus adapters."""
    
    def test_telegram_adapter_routes_swarm_callbacks(self):
        """TelegramAdapter._handle_callback_query routes to bus."""
        from kazma_gateway.adapters.telegram import TelegramAdapter
        import inspect
        
        src = inspect.getsource(TelegramAdapter._handle_callback_query)
        assert "swarm_approve_" in src
        assert "swarm_reject_" in src
        assert "get_message_bus" in src
        assert "handle_callback" in src
    
    def test_discord_adapter_routes_swarm_callbacks(self):
        """DiscordAdapter._handle_interaction routes to bus."""
        from kazma_gateway.adapters.discord import DiscordAdapter
        import inspect
        
        src = inspect.getsource(DiscordAdapter._handle_interaction)
        assert "swarm_approve_" in src
        assert "swarm_reject_" in src
        assert "get_message_bus" in src
        assert "handle_callback" in src
    
    def test_slack_adapter_routes_swarm_callbacks(self):
        """SlackAdapter routes swarm_approve_/swarm_reject_ to bus."""
        from kazma_gateway.adapters.slack import SlackAdapter
        import inspect
        
        src = inspect.getsource(SlackAdapter)
        assert "swarm_approve_" in src
        assert "swarm_reject_" in src
        assert "get_message_bus" in src
        assert "handle_callback" in src


if __name__ == "__main__":
    pytest.main([__file__, "-v"])