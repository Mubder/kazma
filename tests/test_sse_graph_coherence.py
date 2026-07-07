"""Tests for SSE and Graph Coherence.

Covers graph state, event bus stream subscription, and SSE event dispatching.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_ui.swarm_sse import SSEEventBus, create_sse_router


def test_event_bus_subscribe_and_publish():
    """Verify that clients can subscribe to a task's events and receive published events."""
    bus = SSEEventBus()
    queue = bus.subscribe("task-123")

    # Publish an event using emit
    bus.emit("task-123", "task_started", {"message": "hello"})

    # Check if the queue received the event
    assert not queue.empty()
    event_dict = queue.get_nowait()
    assert event_dict["event"] == "task_started"
    assert event_dict["data"] == {"message": "hello"}

    # Unsubscribe
    bus.unsubscribe("task-123", queue)


def test_event_bus_cleanup_on_unsubscribe():
    """Verify that unsubscribing cleans up empty subscriber lists."""
    bus = SSEEventBus()
    queue1 = bus.subscribe("task-abc")
    queue2 = bus.subscribe("task-abc")

    assert len(bus._subscribers["task-abc"]) == 2

    bus.unsubscribe("task-abc", queue1)
    assert len(bus._subscribers["task-abc"]) == 1

    bus.unsubscribe("task-abc", queue2)
    assert "task-abc" not in bus._subscribers


def test_sse_endpoint_coherence_with_active_task():
    """Verify the SSE streaming endpoint behavior when the task is completed."""
    app = FastAPI()
    bus = SSEEventBus()
    # Pre-populate history so the stream yields a chunk immediately and doesn't block
    bus.emit("task-active", "task_started", {"message": "hello"})
    app.include_router(create_sse_router(event_bus=bus))

    with patch("kazma_ui.swarm_sse.get_swarm_engine") as mock_get_engine:
        mock_engine = MagicMock()
        mock_task = MagicMock()
        mock_task.id = "task-active"
        mock_task.status = "completed"
        mock_task.to_dict.return_value = {"id": "task-active", "status": "completed"}
        
        mock_engine.get_task.return_value = mock_task
        mock_engine.task_store.get_task.return_value = mock_task
        mock_get_engine.return_value = mock_engine

        client = TestClient(app)
        
        # Test connecting to the stream and read a single chunk to prevent hanging
        with client.stream("GET", "/api/swarm/tasks/task-active/stream") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            # Just read the first chunk/line and break
            for line in response.iter_lines():
                assert "task_started" in line
                break
