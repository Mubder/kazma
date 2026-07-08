"""Unit and integration tests for Phase 2: LT-1 AlertDispatcher Upgrade.

Verifies:
1. AlertPayload dataclass attributes, dict-style access, and to_dict serialization.
2. AlertDispatcher registration, unregistration, and custom channels.
3. PassThroughAlertChannel delivery to synchronous and asynchronous callbacks.
4. SseAlertChannel integration with SwarmEngine and SSEEventBus.
5. Severity levels propagation and api/alerts/recent serialization.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from kazma_core.observability.alerts import (
    AlertDispatcher,
    AlertPayload,
    LogAlertChannel,
    BusAlertChannel,
    SseAlertChannel,
    PassThroughAlertChannel,
    trigger_system_alert,
)


@pytest.fixture
def client() -> TestClient:
    from kazma_ui.app import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_alert_payload_dataclass_and_dict_compatibility() -> None:
    """Test that AlertPayload acts like a dataclass and maintains dict-like compatibility."""
    payload = AlertPayload(
        id="test-id",
        title="Test Alert",
        subsystem="Memory",
        status="DEGRADED",
        reason="Missing packages",
        callback_id="mem-init",
        button_text="Resolve",
        timestamp=123456.78,
        severity="WARNING",
    )

    # 1. Direct attribute access
    assert payload.id == "test-id"
    assert payload.severity == "WARNING"

    # 2. Dict-like __getitem__ access
    assert payload["subsystem"] == "Memory"
    assert payload["status"] == "DEGRADED"

    with pytest.raises(KeyError):
        _ = payload["non_existent"]

    # 3. Dict-like get access
    assert payload.get("reason") == "Missing packages"
    assert payload.get("non_existent", "default") == "default"

    # 4. to_dict serialization
    d = payload.to_dict()
    assert isinstance(d, dict)
    assert d["id"] == "test-id"
    assert d["severity"] == "WARNING"


@pytest.mark.asyncio
async def test_alert_dispatcher_registration() -> None:
    """Test registration and unregistration of alert channels."""
    AlertDispatcher.clear_alerts()
    # Reset channels
    AlertDispatcher._initialized = False
    AlertDispatcher._init_default_channels()

    initial_count = len(AlertDispatcher.get_channels())
    assert initial_count >= 3  # Bus, Log, Sse

    class MockChannel:
        def name(self) -> str:
            return "custom_mock"
        async def deliver(self, alert: AlertPayload) -> None:
            pass

    mock_chan = MockChannel()
    AlertDispatcher.register_channel(mock_chan)

    assert len(AlertDispatcher.get_channels()) == initial_count + 1
    assert any(c.name() == "custom_mock" for c in AlertDispatcher.get_channels())

    AlertDispatcher.unregister_channel("custom_mock")
    assert len(AlertDispatcher.get_channels()) == initial_count


@pytest.mark.asyncio
async def test_pass_through_channel_sync_and_async_callbacks() -> None:
    """Test delivering alerts through the PassThroughAlertChannel."""
    AlertDispatcher.clear_alerts()
    
    sync_called_with = None
    async_called_with = None

    def sync_callback(alert: AlertPayload) -> None:
        nonlocal sync_called_with
        sync_called_with = alert

    async def async_callback(alert: AlertPayload) -> None:
        nonlocal async_called_with
        async_called_with = alert

    chan_sync = PassThroughAlertChannel(sync_callback)
    chan_async = PassThroughAlertChannel(async_callback)

    AlertPayload_obj = AlertPayload(
        id="p-id",
        title="PassThrough",
        subsystem="System",
        status="OK",
        reason="test",
        callback_id="none",
        button_text="none",
        timestamp=100.0,
        severity="INFO",
    )

    await chan_sync.deliver(AlertPayload_obj)
    await chan_async.deliver(AlertPayload_obj)

    assert sync_called_with is not None
    assert sync_called_with.id == "p-id"
    assert async_called_with is not None
    assert async_called_with.id == "p-id"


@pytest.mark.asyncio
async def test_sse_alert_channel_delivery() -> None:
    """Test SseAlertChannel broadcasts to the active SwarmEngine's SSEEventBus."""
    mock_bus = MagicMock()
    mock_engine = MagicMock()
    mock_engine._sse_bus = mock_bus

    payload = AlertPayload(
        id="sse-id",
        title="SSE Test",
        subsystem="AutoScaler",
        status="DEGRADED",
        reason="Too busy",
        callback_id="as",
        button_text="Fix",
        timestamp=200.0,
        severity="CRITICAL",
    )

    chan = SseAlertChannel()

    with patch("kazma_core.swarm.engine.get_swarm_engine", return_value=mock_engine):
        await chan.deliver(payload)

    # Assert event bus emit called with correctly serialized payload dict
    mock_bus.emit.assert_called_once_with(
        task_id="system",
        event="system_alert",
        data=payload.to_dict()
    )


@pytest.mark.asyncio
async def test_severity_levels_and_endpoint_serialization(client: TestClient) -> None:
    """Test that trigger_system_alert accepts custom severity and GET /api/alerts/recent serializes correctly."""
    AlertDispatcher.clear_alerts()

    # Trigger a custom alert with CRITICAL severity
    await trigger_system_alert(
        subsystem="Memory",
        status="DEGRADED",
        message="Total failure",
        severity="CRITICAL",
    )

    recent = AlertDispatcher.get_recent_alerts()
    assert len(recent) == 1
    assert recent[0].severity == "CRITICAL"

    # Call endpoint and verify JSON serialization
    resp = client.get("/api/alerts/recent")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["subsystem"] == "Memory"
    assert data[0]["severity"] == "CRITICAL"
    assert "status" in data[0]
