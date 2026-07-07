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
    mock_engine.list_workers.return_value = [{"name": "brain", "status": "online"}]

    with patch("kazma_ui.services.get_swarm_engine", return_value=mock_engine):
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

    with patch("kazma_ui.services.get_swarm_engine", return_value=mock_engine):
        svc = get_swarm_service()
        svc._engine = None
        task = svc.get_active_task("t1")
        assert task == mock_task
        mock_engine.get_active_task.assert_called_with("t1")


def test_register_and_get_task_handle():
    mock_engine = MagicMock()
    handle = MagicMock()

    with patch("kazma_ui.services.get_swarm_engine", return_value=mock_engine):
        svc = get_swarm_service()
        svc._engine = None
        svc.register_task_handle("t1", handle)
        mock_engine.register_task_handle.assert_called_with("t1", handle)

        retrieved = svc.get_task_handle("t1")
        mock_engine.get_task_handle.assert_called_with("t1")


def test_set_sse_bus():
    mock_engine = MagicMock()
    bus = MagicMock()

    with patch("kazma_ui.services.get_swarm_engine", return_value=mock_engine):
        svc = get_swarm_service()
        svc._engine = None
        svc.set_sse_bus(bus)
        mock_engine.set_sse_bus.assert_called_with(bus)


def test_fallback_when_no_public_methods():
    mock_engine = MagicMock(spec=[])  # no methods
    mock_engine._workers = {"brain": MagicMock(name="brain", _running=True, model="gpt")}

    with patch("kazma_ui.services.get_swarm_engine", return_value=mock_engine):
        svc = get_swarm_service()
        svc._engine = None
        workers = svc.list_workers()
        assert len(workers) == 1
        assert workers[0]["name"] == "brain"
