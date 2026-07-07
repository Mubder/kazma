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
            "kazma_gateway.agent_handler.swarm_dispatch._get_output_target_config",
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
            "kazma_gateway.agent_handler.swarm_dispatch._get_output_target_config",
            return_value=config_target,
        ):
            result = await _maybe_send_to_output_target(manager, "hi", None)

        assert result is True
        call_args = manager.send.call_args
        outbound = call_args.args[0]
        assert outbound.target_id == "telegram:-100999"

    @pytest.mark.asyncio
    async def test_direct_send_success(self):
        """If explicit bot_token is set, tries direct API call first and returns True if successful."""
        from kazma_gateway.agent_handler import _maybe_send_to_output_target

        manager = MagicMock()
        manager.send = AsyncMock()
        override = {
            "platform": "telegram",
            "chat_id": -100555,
            "enabled": True,
            "bot_token": "mock_dedicated_token",
        }

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}

        # Mock httpx.AsyncClient
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _maybe_send_to_output_target(manager, "hello direct", override)

        assert result is True
        mock_client.post.assert_awaited()
        # Direct send succeeded, so manager should NOT have been called
        manager.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_direct_send_fails_and_falls_back_to_manager(self):
        """If direct send returns ok=False, falls back to gateway manager."""
        from kazma_gateway.agent_handler import _maybe_send_to_output_target

        manager = MagicMock()
        manager.send = AsyncMock()
        override = {
            "platform": "telegram",
            "chat_id": -100555,
            "enabled": True,
            "bot_token": "mock_dedicated_token",
        }

        mock_resp_fail = MagicMock()
        mock_resp_fail.json.return_value = {"ok": False, "description": "Chat not found"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp_fail)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _maybe_send_to_output_target(manager, "hello fallback", override)

        # Direct send failed, so it fell back to manager.send and succeeded
        assert result is True
        mock_client.post.assert_awaited()
        manager.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_explicit_bot_token_tries_manager_first(self):
        """Without explicit token, tries gateway manager first, avoiding direct send if manager succeeds."""
        from kazma_gateway.agent_handler import _maybe_send_to_output_target

        manager = MagicMock()
        manager.send = AsyncMock()
        override = {
            "platform": "telegram",
            "chat_id": -100555,
            "enabled": True,
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _maybe_send_to_output_target(manager, "hello manager first", override)

        assert result is True
        manager.send.assert_awaited_once()
        # Since manager succeeded, direct send should NOT occur
        mock_client.post.assert_not_awaited()


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


# ---------------------------------------------------------------------------
# Web UI Dispatch / Retry Output Routing
# ---------------------------------------------------------------------------

class TestWebUIDispatchOutputRouting:
    """Tests for Output Routing triggered via Web UI/API dispatch and retry endpoints."""

    @pytest.mark.asyncio
    async def test_dispatch_foreground_output_routing(self):
        """Foreground dispatch with output routing enabled formats and delivers results to the gateway manager."""
        import asyncio
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from kazma_core.service_container import get_container, reset_container
        from kazma_gateway import GatewayManager
        from kazma_core.swarm import SwarmTask, TaskResult, WorkerResult

        reset_container()
        container = get_container()

        mock_gateway = MagicMock()
        mock_gateway.send = AsyncMock()
        container.register(GatewayManager, mock_gateway)

        # Mock the engine
        mock_engine = MagicMock()
        mock_worker = MagicMock()
        mock_engine.get_worker.return_value = mock_worker
        
        # Mock TaskResult
        mock_result = TaskResult(
            task_id="task-123",
            status="success",
            worker_results=[
                WorkerResult(worker="worker1", task_id="task-123", status="success", output="Final analysis completed.")
            ],
            aggregated_output="Final analysis completed.",
        )
        mock_engine.dispatch = AsyncMock(return_value=mock_result)

        from kazma_ui.swarm_panel import create_swarm_router
        app = FastAPI()
        app.include_router(create_swarm_router(templates=None))

        config_target = {
            "platform": "telegram",
            "chat_id": -100999,
            "enabled": True,
        }

        with patch("kazma_ui.services.SwarmService.resolve_engine", return_value=mock_engine), \
             patch("kazma_gateway.agent_handler.swarm_dispatch._get_output_target_config", return_value=config_target):
            
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/api/swarm/dispatch", json={
                    "workers": ["worker1"],
                    "task": "run analysis",
                    "background": False,
                })

            assert resp.status_code == 200
            # Gateway manager should have been called with the aggregated output!
            mock_gateway.send.assert_awaited_once()
            outbound = mock_gateway.send.call_args.args[0]
            assert outbound.target_id == "telegram:-100999"
            assert "Final analysis completed." in outbound.text

    @pytest.mark.asyncio
    async def test_dispatch_background_output_routing(self):
        """Background dispatch formats and delivers results to the gateway manager asynchronously on completion."""
        import asyncio
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from kazma_core.service_container import get_container, reset_container
        from kazma_gateway import GatewayManager
        from kazma_core.swarm import SwarmTask, TaskResult, WorkerResult

        reset_container()
        container = get_container()

        mock_gateway = MagicMock()
        mock_gateway.send = AsyncMock()
        container.register(GatewayManager, mock_gateway)

        # Mock the engine
        mock_engine = MagicMock()
        if hasattr(mock_engine, "register_task_handle"):
            del mock_engine.register_task_handle
        if hasattr(mock_engine, "get_task_handle"):
            del mock_engine.get_task_handle
        mock_worker = MagicMock()
        mock_engine.get_worker.return_value = mock_worker
        mock_engine._task_handles = {}

        # Set up a future for mock_engine.dispatch so we can await it
        fut = asyncio.Future()
        mock_result = TaskResult(
            task_id="task-456",
            status="success",
            worker_results=[
                WorkerResult(worker="worker1", task_id="task-456", status="success", output="Background result")
            ],
            aggregated_output="Background result",
        )
        # Keep the future unresolved until background task is verified in mock_engine._task_handles
        mock_engine.dispatch = MagicMock(return_value=fut)

        from kazma_ui.swarm_panel import create_swarm_router
        app = FastAPI()
        app.include_router(create_swarm_router(templates=None))

        config_target = {
            "platform": "telegram",
            "chat_id": -100999,
            "enabled": True,
        }

        from kazma_ui.services import get_swarm_service, reset_swarm_service
        reset_swarm_service()
        get_swarm_service()._engine = mock_engine

        with patch("kazma_ui.services.SwarmService.resolve_engine", return_value=mock_engine), \
             patch("kazma_gateway.agent_handler.swarm_dispatch._get_output_target_config", return_value=config_target):
            
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/api/swarm/dispatch", json={
                    "workers": ["worker1"],
                    "task": "background task",
                    "background": True,
                })

            assert resp.status_code == 200
            # Await the created task handle in _task_handles to make sure the background task is fully executed
            task_id = resp.json().get("task_id")
            assert task_id is not None
            handle = mock_engine._task_handles.get(task_id)
            assert handle is not None
            
            # Now resolve the future to let the background task complete
            fut.set_result(mock_result)
            await handle

            # Gateway manager should have been called with the aggregated output!
            mock_gateway.send.assert_awaited_once()
            outbound = mock_gateway.send.call_args.args[0]
            assert outbound.target_id == "telegram:-100999"
            assert "Background result" in outbound.text

    @pytest.mark.asyncio
    async def test_dispatch_retry_output_routing(self):
        """Retrying a task also triggers background dispatch that routes its output on completion."""
        import asyncio
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from kazma_core.service_container import get_container, reset_container
        from kazma_gateway import GatewayManager
        from kazma_core.swarm import SwarmTask, TaskResult, WorkerResult

        reset_container()
        container = get_container()

        mock_gateway = MagicMock()
        mock_gateway.send = AsyncMock()
        container.register(GatewayManager, mock_gateway)

        # Mock the engine
        mock_engine = MagicMock()
        if hasattr(mock_engine, "register_task_handle"):
            del mock_engine.register_task_handle
        if hasattr(mock_engine, "get_task_handle"):
            del mock_engine.get_task_handle
        mock_engine._task_handles = {}

        # Mock engine.retry_task to return a new task
        new_task = SwarmTask(id="task-new", prompt="retry prompt", workers=["worker1"])
        mock_engine.retry_task = AsyncMock(return_value=new_task)

        fut = asyncio.Future()
        mock_result = TaskResult(
            task_id="task-new",
            status="success",
            worker_results=[
                WorkerResult(worker="worker1", task_id="task-new", status="success", output="Retry successful output")
            ],
            aggregated_output="Retry successful output",
        )
        # Keep the future unresolved until background task is verified in mock_engine._task_handles
        mock_engine.dispatch = MagicMock(return_value=fut)

        from kazma_ui.swarm_panel import create_swarm_router
        app = FastAPI()
        app.include_router(create_swarm_router(templates=None))

        config_target = {
            "platform": "telegram",
            "chat_id": -100999,
            "enabled": True,
        }

        from kazma_ui.services import get_swarm_service, reset_swarm_service
        reset_swarm_service()
        get_swarm_service()._engine = mock_engine

        with patch("kazma_ui.services.SwarmService.resolve_engine", return_value=mock_engine), \
             patch("kazma_gateway.agent_handler.swarm_dispatch._get_output_target_config", return_value=config_target):
            
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/api/swarm/tasks/task-old/retry")

            assert resp.status_code == 200
            # Await the created task handle in _task_handles to make sure the background task is fully executed
            task_id = resp.json().get("new_task_id")
            assert task_id == "task-new"
            handle = mock_engine._task_handles.get(task_id)
            assert handle is not None
            
            # Now resolve the future to let the background task complete
            fut.set_result(mock_result)
            await handle

            # Gateway manager should have been called with the aggregated output!
            mock_gateway.send.assert_awaited_once()
            outbound = mock_gateway.send.call_args.args[0]
            assert outbound.target_id == "telegram:-100999"
            assert "Retry successful output" in outbound.text

