"""Unit tests for the public services.py facade (SwarmService).

Covers list_workers, get_active_task, registration routines, etc.
Mocks the underlying engine to verify clean access without private attrs.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kazma_ui.services import get_swarm_service, reset_swarm_service, SwarmService


@pytest.fixture(autouse=True)
def reset_facade():
    reset_swarm_service()
    yield
    reset_swarm_service()


def test_get_swarm_service_returns_singleton():
    s1 = get_swarm_service()
    s2 = get_swarm_service()
    assert s1 is s2
    assert isinstance(s1, SwarmService)


def test_list_workers_uses_public_api():
    mock_engine = MagicMock()
    mock_worker = MagicMock()
    del mock_worker.to_dict
    mock_worker.name = "brain"
    mock_worker._running = True
    mock_worker.busy = False
    mock_worker.model = "gpt"
    mock_worker.provider = "openai"
    mock_worker.worker_type = "in_process"
    mock_worker.role = "assistant"
    mock_worker.bot_token = None
    mock_worker.added_at = None
    mock_worker.last_task = None
    mock_worker.last_heartbeat = None
    mock_worker.logs = []
    mock_worker.capabilities = None
    mock_engine.list_workers.return_value = [mock_worker]

    with patch("kazma_core.swarm.get_swarm_engine", return_value=mock_engine):
        svc = get_swarm_service()
        # Force refresh
        svc._engine = None
        workers = svc.list_workers()
        assert len(workers) == 1
        assert workers[0]["name"] == "brain"
        mock_engine.list_workers.assert_called_once()


def test_get_active_task_uses_public():
    mock_engine = MagicMock()
    mock_task = {"id": "t1", "prompt": "test"}
    mock_engine.get_active_task.return_value = mock_task

    with patch("kazma_core.swarm.get_swarm_engine", return_value=mock_engine):
        svc = get_swarm_service()
        svc._engine = None
        task = svc.get_active_task("t1")
        assert task == mock_task
        mock_engine.get_active_task.assert_called_with("t1")


def test_register_and_get_task_handle():
    mock_engine = MagicMock()
    handle = MagicMock()

    with patch("kazma_core.swarm.get_swarm_engine", return_value=mock_engine):
        svc = get_swarm_service()
        svc._engine = None
        svc.register_task_handle("t1", handle)
        mock_engine.register_task_handle.assert_called_with("t1", handle)

        retrieved = svc.get_task_handle("t1")
        mock_engine.get_task_handle.assert_called_with("t1")


def test_set_sse_bus():
    mock_engine = MagicMock()
    bus = MagicMock()

    with patch("kazma_core.swarm.get_swarm_engine", return_value=mock_engine):
        svc = get_swarm_service()
        svc._engine = None
        svc.set_sse_bus(bus)
        mock_engine.set_sse_bus.assert_called_with(bus)


def test_fallback_when_no_public_methods():
    mock_engine = MagicMock(spec=[])  # no methods
    mock_worker = MagicMock()
    del mock_worker.to_dict
    mock_worker.name = "brain"
    mock_worker._running = True
    mock_worker.busy = False
    mock_worker.model = "gpt"
    mock_worker.provider = "openai"
    mock_worker.worker_type = "in_process"
    mock_worker.role = "assistant"
    mock_worker.bot_token = None
    mock_worker.added_at = None
    mock_worker.last_task = None
    mock_worker.last_heartbeat = None
    mock_worker.logs = []
    mock_worker.capabilities = None
    mock_engine._workers = {"brain": mock_worker}

    with patch("kazma_core.swarm.get_swarm_engine", return_value=mock_engine):
        svc = get_swarm_service()
        svc._engine = None
        workers = svc.list_workers()
        assert len(workers) == 1
        assert workers[0]["name"] == "brain"


def test_is_started():
    svc = get_swarm_service()
    mock_engine = MagicMock()
    mock_worker = MagicMock()
    del mock_worker.to_dict
    mock_worker.name = "brain"
    mock_worker._running = True
    mock_worker.busy = False
    mock_worker.model = "gpt"
    mock_worker.provider = "openai"
    mock_worker.worker_type = "in_process"
    mock_worker.role = "assistant"
    mock_worker.bot_token = None
    mock_worker.added_at = None
    mock_worker.last_task = None
    mock_worker.last_heartbeat = None
    mock_worker.logs = []
    mock_worker.capabilities = None
    mock_engine.list_workers.return_value = [mock_worker]

    with patch("kazma_core.swarm.get_swarm_engine", return_value=mock_engine):
        svc._engine = None
        assert svc.is_started() is True


def test_resolve_engine():
    svc = get_swarm_service()
    mock_engine = MagicMock()
    with patch("kazma_core.swarm.get_swarm_engine", return_value=mock_engine):
        svc._engine = None
        engine = svc.resolve_engine()
        assert engine is mock_engine


def test_get_and_set_output_target():
    svc = get_swarm_service()
    mock_cs = MagicMock()
    mock_cs.get.return_value = {
        "platform": "telegram",
        "chat_id": 12345,
        "enabled": True,
        "bot_token": "token123",
    }

    with patch.object(svc, "get_config_store", return_value=mock_cs):
        target = svc.get_output_target()
        assert target["platform"] == "telegram"
        assert target["chat_id"] == 12345
        assert target["enabled"] is True
        assert target["bot_token"] == "token123"

        svc.set_output_target({
            "platform": "telegram",
            "chat_id": 67890,
            "enabled": False,
            "bot_token": "token456",
        })
        mock_cs.set.assert_called_with(
            "swarm.output_target",
            {
                "platform": "telegram",
                "chat_id": 67890,
                "enabled": False,
                "bot_token": "token456",
            },
            category="swarm",
        )


def test_get_autoscaler():
    svc = get_swarm_service()
    mock_engine = MagicMock()
    mock_scaler = MagicMock()
    mock_engine.get_autoscaler.return_value = mock_scaler

    with patch("kazma_core.swarm.get_swarm_engine", return_value=mock_engine):
        svc._engine = None
        assert svc.get_autoscaler() is mock_scaler


def test_get_circuit_breaker_status():
    svc = get_swarm_service()
    mock_engine = MagicMock()
    mock_engine.get_circuit_breaker_status.return_value = {"state": "open", "consecutive_failures": 3}

    with patch("kazma_core.swarm.get_swarm_engine", return_value=mock_engine):
        svc._engine = None
        status = svc.get_circuit_breaker_status("worker1")
        assert status["state"] == "open"
        assert status["consecutive_failures"] == 3
        mock_engine.get_circuit_breaker_status.assert_called_with("worker1")

