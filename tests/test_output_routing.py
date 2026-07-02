"""Tests for Phase 5 — Swarm Output Routing to Telegram Group.

Covers:
    * ``_parse_output_target_suffix`` — inline ``-> platform:<id>`` parsing
    * ``_get_output_target_config`` — ConfigStore read + enabled gating
    * ``_maybe_send_to_output_target`` — OutboundMessage construction
    * ``GET/PUT /api/swarm/output-target`` — REST API round-trip

All ConfigStore access is isolated to a temp DB so tests don't touch
real settings.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _parse_output_target_suffix
# ---------------------------------------------------------------------------

class TestParseOutputTargetSuffix:
    """Parse inline ``-> platform:<chat_id>`` routing overrides."""

    def test_explicit_platform_id(self):
        """Valid ``-> telegram:-100123`` suffix yields a concrete override."""
        from kazma_gateway.agent_handler import _parse_output_target_suffix

        clean, override = _parse_output_target_suffix(
            "summarize the news -> telegram:-1001234567890"
        )
        assert clean == "summarize the news"
        assert override == {
            "platform": "telegram",
            "chat_id": -1001234567890,
            "enabled": True,
        }

    def test_no_suffix_returns_untouched(self):
        """Messages without an arrow are returned unchanged with no override."""
        from kazma_gateway.agent_handler import _parse_output_target_suffix

        clean, override = _parse_output_target_suffix("just a plain task")
        assert clean == "just a plain task"
        assert override is None

    def test_named_group_stripped_no_override(self):
        """``-> @GroupName`` is stripped but yields no resolvable override."""
        from kazma_gateway.agent_handler import _parse_output_target_suffix

        clean, override = _parse_output_target_suffix("do thing -> @MyGroup")
        # @GroupName form: stripped from prompt, override is None
        assert override is None

    def test_malformed_chat_id_returns_untouched(self):
        """``-> telegram:abc`` (non-integer) leaves the task untouched."""
        from kazma_gateway.agent_handler import _parse_output_target_suffix

        clean, override = _parse_output_target_suffix("task -> telegram:abc")
        assert clean == "task -> telegram:abc"
        assert override is None

    def test_other_platform_prefix(self):
        """Any ``platform:id`` form works, not just telegram."""
        from kazma_gateway.agent_handler import _parse_output_target_suffix

        clean, override = _parse_output_target_suffix("task -> discord:998877")
        assert clean == "task"
        assert override == {
            "platform": "discord",
            "chat_id": 998877,
            "enabled": True,
        }


# ---------------------------------------------------------------------------
# _get_output_target_config
# ---------------------------------------------------------------------------

class TestGetOutputTargetConfig:
    """Read the configured output target from ConfigStore."""

    def test_returns_none_when_not_configured(self):
        """No stored config → None."""
        from kazma_gateway.agent_handler import _get_output_target_config

        with patch("kazma_core.config_store.get_config_store") as mock_cs_cls:
            cs = MagicMock()
            cs.get.return_value = None
            mock_cs_cls.return_value = cs
            assert _get_output_target_config() is None

    def test_returns_none_when_disabled(self):
        """Config exists but enabled=False → None."""
        from kazma_gateway.agent_handler import _get_output_target_config

        with patch("kazma_core.config_store.get_config_store") as mock_cs_cls:
            cs = MagicMock()
            cs.get.return_value = {
                "platform": "telegram",
                "chat_id": -100123,
                "enabled": False,
            }
            mock_cs_cls.return_value = cs
            assert _get_output_target_config() is None

    def test_returns_config_when_enabled(self):
        """Valid enabled config → returned with platform default."""
        from kazma_gateway.agent_handler import _get_output_target_config

        with patch("kazma_core.config_store.get_config_store") as mock_cs_cls:
            cs = MagicMock()
            cs.get.return_value = {
                "platform": "telegram",
                "chat_id": -1009876543210,
                "enabled": True,
            }
            mock_cs_cls.return_value = cs
            result = _get_output_target_config()
            assert result is not None
            assert result["chat_id"] == -1009876543210
            assert result["platform"] == "telegram"

    def test_returns_none_when_no_chat_id(self):
        """Config exists and enabled but chat_id missing → None."""
        from kazma_gateway.agent_handler import _get_output_target_config

        with patch("kazma_core.config_store.get_config_store") as mock_cs_cls:
            cs = MagicMock()
            cs.get.return_value = {"platform": "telegram", "enabled": True}
            mock_cs_cls.return_value = cs
            assert _get_output_target_config() is None


# ---------------------------------------------------------------------------
# _maybe_send_to_output_target
# ---------------------------------------------------------------------------

class TestMaybeSendToOutputTarget:
    """Best-effort mirror of swarm output to a Telegram group."""

    @pytest.mark.asyncio
    async def test_sends_to_override_target(self):
        """An explicit override dict is used directly."""
        from kazma_gateway.agent_handler import _maybe_send_to_output_target

        manager = MagicMock()
        manager.send = AsyncMock()
        override = {"platform": "telegram", "chat_id": -100555, "enabled": True}

        result = await _maybe_send_to_output_target(manager, "hello", override)

        assert result is True
        manager.send.assert_awaited_once()
        # Verify the OutboundMessage shape
        call_args = manager.send.call_args
        outbound = call_args.args[0]
        assert outbound.target_id == "telegram:-100555"
        assert outbound.text == "hello"
        assert outbound.context_metadata == {"chat_id": -100555}

    @pytest.mark.asyncio
    async def test_returns_false_when_no_target(self):
        """No override and no ConfigStore config → False, no send."""
        from kazma_gateway.agent_handler import _maybe_send_to_output_target

        manager = MagicMock()
        manager.send = AsyncMock()

        with patch(
            "kazma_gateway.agent_handler._get_output_target_config",
            return_value=None,
        ):
            result = await _maybe_send_to_output_target(manager, "hello", None)

        assert result is False
        manager.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_swallows_send_error(self):
        """If manager.send raises, the function returns False (best-effort)."""
        from kazma_gateway.agent_handler import _maybe_send_to_output_target

        manager = MagicMock()
        manager.send = AsyncMock(side_effect=RuntimeError("bot not in group"))
        override = {"platform": "telegram", "chat_id": -100, "enabled": True}

        # Should NOT raise
        result = await _maybe_send_to_output_target(manager, "hello", override)
        assert result is False

    @pytest.mark.asyncio
    async def test_uses_configstore_when_no_override(self):
        """Without an override, falls back to ConfigStore config."""
        from kazma_gateway.agent_handler import _maybe_send_to_output_target

        manager = MagicMock()
        manager.send = AsyncMock()
        config_target = {
            "platform": "telegram",
            "chat_id": -100999,
            "enabled": True,
        }

        with patch(
            "kazma_gateway.agent_handler._get_output_target_config",
            return_value=config_target,
        ):
            result = await _maybe_send_to_output_target(manager, "hi", None)

        assert result is True
        call_args = manager.send.call_args
        outbound = call_args.args[0]
        assert outbound.target_id == "telegram:-100999"


# ---------------------------------------------------------------------------
# GET/PUT /api/swarm/output-target
# ---------------------------------------------------------------------------

class TestOutputTargetAPI:
    """REST API for reading and writing the output routing config."""

    def test_get_returns_defaults_when_unset(self):
        """GET returns safe defaults when no target is configured."""
        from fastapi.testclient import TestClient

        from kazma_ui.swarm_panel import create_swarm_router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(create_swarm_router(templates=None))
        client = TestClient(app)

        with patch("kazma_core.config_store.get_config_store") as mock_cs_cls:
            cs = MagicMock()
            cs.get.return_value = None
            mock_cs_cls.return_value = cs

            resp = client.get("/api/swarm/output-target")

        assert resp.status_code == 200
        data = resp.json()["output_target"]
        assert data["platform"] == "telegram"
        assert data["chat_id"] is None
        assert data["enabled"] is False

    def test_put_sets_target(self):
        """PUT with valid payload stores the config and returns it."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from kazma_ui.swarm_panel import create_swarm_router

        app = FastAPI()
        app.include_router(create_swarm_router(templates=None))
        client = TestClient(app)

        with patch("kazma_core.config_store.get_config_store") as mock_cs_cls:
            cs = MagicMock()
            mock_cs_cls.return_value = cs

            resp = client.put("/api/swarm/output-target", json={
                "platform": "telegram",
                "chat_id": -1001234567890,
                "enabled": True,
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["output_target"]["chat_id"] == -1001234567890
        assert data["output_target"]["enabled"] is True
        # Verify ConfigStore.set was called with the right key + category
        cs.set.assert_called_once()
        args = cs.set.call_args
        assert args.args[0] == "swarm.output_target"
        assert args.kwargs.get("category") == "swarm"

    def test_put_clears_target(self):
        """PUT with {clear: true} deletes the config."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from kazma_ui.swarm_panel import create_swarm_router

        app = FastAPI()
        app.include_router(create_swarm_router(templates=None))
        client = TestClient(app)

        with patch("kazma_core.config_store.get_config_store") as mock_cs_cls:
            cs = MagicMock()
            mock_cs_cls.return_value = cs

            resp = client.put("/api/swarm/output-target", json={"clear": True})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["output_target"]["chat_id"] is None
        cs.delete.assert_called_once_with("swarm.output_target")

    def test_put_rejects_missing_chat_id(self):
        """PUT without chat_id returns 400."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from kazma_ui.swarm_panel import create_swarm_router

        app = FastAPI()
        app.include_router(create_swarm_router(templates=None))
        client = TestClient(app)

        with patch("kazma_core.config_store.get_config_store") as mock_cs_cls:
            cs = MagicMock()
            mock_cs_cls.return_value = cs

            resp = client.put("/api/swarm/output-target", json={
                "platform": "telegram",
                "enabled": True,
            })

        assert resp.status_code == 400
        assert "chat_id" in resp.json()["message"]

    def test_put_rejects_non_integer_chat_id(self):
        """PUT with a non-integer chat_id returns 400."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from kazma_ui.swarm_panel import create_swarm_router

        app = FastAPI()
        app.include_router(create_swarm_router(templates=None))
        client = TestClient(app)

        with patch("kazma_core.config_store.get_config_store") as mock_cs_cls:
            cs = MagicMock()
            mock_cs_cls.return_value = cs

            resp = client.put("/api/swarm/output-target", json={
                "platform": "telegram",
                "chat_id": "not-a-number",
                "enabled": True,
            })

        assert resp.status_code == 400
        assert "integer" in resp.json()["message"]

    def test_get_serializes_chat_id_as_string(self):
        """GET returns chat_id as a string to avoid JS precision loss."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from kazma_ui.swarm_panel import create_swarm_router

        app = FastAPI()
        app.include_router(create_swarm_router(templates=None))
        client = TestClient(app)

        big_id = -1009876543210987  # exceeds Number.MAX_SAFE_INTEGER
        with patch("kazma_core.config_store.get_config_store") as mock_cs_cls:
            cs = MagicMock()
            cs.get.return_value = {
                "platform": "telegram",
                "chat_id": big_id,
                "enabled": True,
            }
            mock_cs_cls.return_value = cs

            resp = client.get("/api/swarm/output-target")

        assert resp.status_code == 200
        data = resp.json()["output_target"]
        # Must be a string, not a number, to survive JSON.parse in JS
        assert isinstance(data["chat_id"], str)
        assert data["chat_id"] == str(big_id)
