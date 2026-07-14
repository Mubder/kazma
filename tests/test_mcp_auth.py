"""Tests for MCP manager stdio authentication."""

import pytest


class TestMCPStdioAuth:
    """Tests for MCP stdio authentication support."""

    @pytest.mark.asyncio
    async def test_stdio_env_auth_injected(self):
        """Auth of type 'env' should inject token into environment."""
        from unittest.mock import AsyncMock, patch, MagicMock
        from kazma_core.mcp.manager import AsyncMCPManager, MCPBridgeError
        import asyncio

        manager = AsyncMCPManager()

        # Config with env auth
        cfg = {
            "name": "test-server",
            "transport": "stdio",
            "command": ["echo", "test"],
            "auth": {
                "type": "env",
                "name": "API_TOKEN",
                "value": "secret-token-123",
            },
        }

        # Mock the subprocess creation to capture env
        captured_env = {}

        async def mock_create_subprocess(*args, **kwargs):
            proc = MagicMock()
            proc.stdin = MagicMock()
            proc.stdout = MagicMock()
            proc.stderr = MagicMock()
            proc.pid = 12345
            captured_env.update(kwargs.get("env", {}))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess):
            with patch.object(manager, "_send", return_value={"tools": []}):
                try:
                    await manager._connect_stdio("test-server", cfg)
                except Exception:
                    pass  # May fail on handshake, we just need env check

        # Check that API_TOKEN was injected
        assert captured_env.get("API_TOKEN") == "secret-token-123"

    @pytest.mark.asyncio
    async def test_stdio_arg_auth_injected(self):
        """Auth of type 'arg' should inject token as command-line argument."""
        from unittest.mock import AsyncMock, patch, MagicMock, call
        from kazma_core.mcp.manager import AsyncMCPManager
        import asyncio

        manager = AsyncMCPManager()

        # Config with arg auth
        cfg = {
            "name": "test-server",
            "transport": "stdio",
            "command": ["mcp-server", "--config", "config.json"],
            "auth": {
                "type": "arg",
                "name": "--api-key",
                "value": "secret-key-456",
            },
        }

        captured_cmd = []

        async def mock_create_subprocess(*args, **kwargs):
            captured_cmd.extend(args)
            proc = MagicMock()
            proc.stdin = MagicMock()
            proc.stdout = MagicMock()
            proc.stderr = MagicMock()
            proc.pid = 12345
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess):
            with patch.object(manager, "_send", return_value={"tools": []}):
                try:
                    await manager._connect_stdio("test-server", cfg)
                except Exception:
                    pass

        # Check that --api-key was injected into command
        assert "--api-key" in captured_cmd
        assert "secret-key-456" in captured_cmd