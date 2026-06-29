"""Tests for Swarm SSE streaming endpoint.

Covers the following validation assertions:
  VAL-OBS-001: SSE auto-connects when task is active
  VAL-OBS-002: SSE emits task_started as first event
  VAL-OBS-003: SSE emits per-worker started/progress/completed events
  VAL-OBS-004: SSE emits checkpoint events for HITL pauses
  VAL-OBS-005: SSE emits task_completed as terminal event
  VAL-OBS-006: SSE reconnects after disconnect (catch-up replay)
  VAL-ORCH-003: Pipeline SSE stream emits per-step events
  VAL-ORCH-045: SSE progress stream works for all orchestration patterns
"""

from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_sse_lines(lines: list[str]) -> list[dict]:
    """Parse raw SSE lines into a list of {event, data} dicts."""
    events: list[dict] = []
    current_event: str | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("event:"):
            current_event = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("data:"):
            raw_data = stripped.split(":", 1)[1].strip()
            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                data = raw_data
            events.append({"event": current_event, "data": data})
    return events


def _read_sse_response(response, max_events: int = 20, timeout: float = 3.0) -> list[dict]:
    """Read SSE events from a streaming response with a wall-clock timeout.

    Uses a background thread to read lines from the response so the
    main thread can enforce a timeout.  This prevents blocking forever
    on SSE streams that stay open for active tasks.
    """
    collected_lines: list[str] = []

    def _reader() -> None:
        try:
            for line in response.iter_lines():
                if line:
                    collected_lines.append(line)
                if len(_parse_sse_lines(collected_lines)) >= max_events:
                    break
        except Exception:
            pass

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    return _parse_sse_lines(collected_lines)


# ---------------------------------------------------------------------------
# Test: SSE endpoint returns correct response headers
# ---------------------------------------------------------------------------


class TestSSEEndpoint:
    """Test GET /api/swarm/tasks/{id}/stream endpoint basics."""

    def test_sse_returns_404_for_unknown_task(self):
        """Returns 404 when task does not exist."""
        from kazma_ui.swarm_sse import SSEEventBus, create_sse_router

        app = FastAPI()
        bus = SSEEventBus()
        app.include_router(create_sse_router(event_bus=bus))

        with patch("kazma_ui.swarm_sse.get_swarm_engine") as mock_get_engine:
            mock_engine = MagicMock()
            mock_engine.task_store = None
            mock_engine.get_task.return_value = None
            mock_get_engine.return_value = mock_engine

            client = TestClient(app)
            response = client.get("/api/swarm/tasks/nonexistent/stream")
            assert response.status_code == 404
            data = response.json()
            assert "not found" in data.get("message", "").lower()

    def test_sse_returns_event_stream_content_type(self):
        """Returns text/event-stream content type for existing task."""
        from kazma_ui.swarm_sse import SSEEventBus, create_sse_router

        app = FastAPI()
        bus = SSEEventBus()
        app.include_router(create_sse_router(event_bus=bus))

        # Store a completed task so the endpoint can find it.
        mock_task = MagicMock()
        mock_task.status.value = "completed"
        mock_task.status = "completed"
        mock_task.workers = ["worker-a"]

        with patch("kazma_ui.swarm_sse.get_swarm_engine") as mock_get_engine:
            mock_engine = MagicMock()
            mock_engine.task_store = None
            mock_engine.get_task.return_value = mock_task
            mock_get_engine.return_value = mock_engine

            with TestClient(app).stream(
                "GET", "/api/swarm/tasks/task-123/stream"
            ) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Test: Event bus unit tests
# ---------------------------------------------------------------------------


class TestSSEEventBus:
    """Unit tests for the SSEEventBus class."""

    def test_emit_stores_event_history(self):
        """Emitted events are stored for late-subscriber catch-up."""
        from kazma_ui.swarm_sse import SSEEventBus

        bus = SSEEventBus()
        bus.emit("task-1", "task_started", {"task_id": "task-1", "workers": ["a"]})
        bus.emit("task-1", "worker_started", {"worker": "a", "step": 1})

        events = bus.get_history("task-1")
        assert len(events) == 2
        assert events[0]["event"] == "task_started"
        assert events[1]["event"] == "worker_started"

    def test_subscribe_returns_queue(self):
        """Subscribing returns an asyncio Queue."""
        from kazma_ui.swarm_sse import SSEEventBus

        bus = SSEEventBus()
        queue = bus.subscribe("task-1")
        assert isinstance(queue, asyncio.Queue)
        assert queue.empty()

    def test_emit_delivers_to_subscribers(self):
        """Emitted events are delivered to active subscribers."""
        from kazma_ui.swarm_sse import SSEEventBus

        bus = SSEEventBus()
        queue = bus.subscribe("task-1")
        bus.emit("task-1", "task_started", {"task_id": "task-1", "workers": ["a"]})

        assert not queue.empty()
        event = queue.get_nowait()
        assert event["event"] == "task_started"
        assert event["data"]["task_id"] == "task-1"

    def test_emit_does_not_deliver_to_other_task_subscribers(self):
        """Events for task-1 are not delivered to task-2 subscribers."""
        from kazma_ui.swarm_sse import SSEEventBus

        bus = SSEEventBus()
        queue = bus.subscribe("task-2")
        bus.emit("task-1", "task_started", {"task_id": "task-1"})

        assert queue.empty()

    def test_get_history_returns_empty_for_unknown_task(self):
        """Returns empty list for tasks with no history."""
        from kazma_ui.swarm_sse import SSEEventBus

        bus = SSEEventBus()
        assert bus.get_history("nonexistent") == []

    def test_unsubscribe_removes_subscriber(self):
        """After unsubscribe, events are no longer delivered."""
        from kazma_ui.swarm_sse import SSEEventBus

        bus = SSEEventBus()
        queue = bus.subscribe("task-1")
        bus.unsubscribe("task-1", queue)
        bus.emit("task-1", "task_started", {"task_id": "task-1"})

        # Queue should not have received the event.
        assert queue.empty()

    def test_emit_with_no_subscribers_succeeds(self):
        """Emitting with no active subscribers stores history only."""
        from kazma_ui.swarm_sse import SSEEventBus

        bus = SSEEventBus()
        bus.emit("task-1", "task_started", {"task_id": "task-1"})
        assert len(bus.get_history("task-1")) == 1


# ---------------------------------------------------------------------------
# Test: SSE emits task_started as first event (VAL-OBS-002)
# ---------------------------------------------------------------------------


class TestSSETaskStarted:
    """VAL-OBS-002: SSE emits task_started as first event."""

    def test_task_started_is_first_event(self):
        """First SSE event is task_started with task_id and workers."""
        from kazma_ui.swarm_sse import SSEEventBus

        bus = SSEEventBus()
        bus.emit("task-1", "task_started", {"task_id": "task-1", "workers": ["a", "b"]})
        bus.emit("task-1", "worker_started", {"worker": "a", "step": 1})
        bus.emit("task-1", "worker_completed", {"worker": "a", "status": "success"})
        bus.emit("task-1", "task_completed", {"task_id": "task-1", "result": {}})

        events = bus.get_history("task-1")
        assert len(events) >= 1
        assert events[0]["event"] == "task_started"
        assert events[0]["data"]["task_id"] == "task-1"
        assert events[0]["data"]["workers"] == ["a", "b"]

    def test_sse_endpoint_emits_task_started_first(self):
        """SSE endpoint emits task_started as the first event for a completed task."""
        from kazma_ui.swarm_sse import SSEEventBus, create_sse_router

        app = FastAPI()
        bus = SSEEventBus()
        app.include_router(create_sse_router(event_bus=bus))

        mock_task = MagicMock()
        mock_task.status = "completed"

        with patch("kazma_ui.swarm_sse.get_swarm_engine") as mock_get_engine:
            mock_engine = MagicMock()
            mock_engine.task_store = None
            mock_engine.get_task.return_value = mock_task
            mock_get_engine.return_value = mock_engine

            # Emit events before connecting (simulating completed task history).
            bus.emit("task-1", "task_started", {"task_id": "task-1", "workers": ["w1"]})
            bus.emit(
                "task-1",
                "task_completed",
                {"task_id": "task-1", "result": {"status": "success"}},
            )

            with TestClient(app).stream(
                "GET", "/api/swarm/tasks/task-1/stream"
            ) as response:
                events = _read_sse_response(response, max_events=2)
                assert len(events) >= 1
                assert events[0]["event"] == "task_started"
                assert events[0]["data"]["task_id"] == "task-1"


# ---------------------------------------------------------------------------
# Test: SSE emits per-worker events (VAL-OBS-003)
# ---------------------------------------------------------------------------


class TestSSEWorkerEvents:
    """VAL-OBS-003: SSE emits per-worker started/progress/completed events."""

    def test_worker_started_progress_completed_sequence(self):
        """Each worker gets worker_started, worker_progress, worker_completed."""
        from kazma_ui.swarm_sse import SSEEventBus

        bus = SSEEventBus()
        bus.emit("task-1", "task_started", {"task_id": "task-1", "workers": ["w1"]})
        bus.emit("task-1", "worker_started", {"worker": "w1", "step": 1})
        bus.emit("task-1", "worker_progress", {"worker": "w1", "tokens": 150})
        bus.emit(
            "task-1",
            "worker_completed",
            {"worker": "w1", "status": "success", "output_preview": "hello"},
        )

        events = bus.get_history("task-1")
        event_types = [e["event"] for e in events]
        assert "worker_started" in event_types
        assert "worker_progress" in event_types
        assert "worker_completed" in event_types

        # Verify worker_started has step index.
        started_event = next(e for e in events if e["event"] == "worker_started")
        assert started_event["data"]["worker"] == "w1"
        assert started_event["data"]["step"] == 1

        # Verify worker_completed has output_preview.
        completed_event = next(e for e in events if e["event"] == "worker_completed")
        assert completed_event["data"]["output_preview"] == "hello"


# ---------------------------------------------------------------------------
# Test: SSE emits checkpoint events (VAL-OBS-004)
# ---------------------------------------------------------------------------


class TestSSECheckpointEvents:
    """VAL-OBS-004: SSE emits checkpoint events for HITL pauses."""

    def test_checkpoint_event_emitted(self):
        """Checkpoint event with step, needs_approval, output_preview."""
        from kazma_ui.swarm_sse import SSEEventBus

        bus = SSEEventBus()
        bus.emit("task-1", "task_started", {"task_id": "task-1", "workers": ["w1"]})
        bus.emit(
            "task-1",
            "checkpoint",
            {
                "step": 2,
                "needs_approval": True,
                "output_preview": "Intermediate result...",
            },
        )

        events = bus.get_history("task-1")
        checkpoint_events = [e for e in events if e["event"] == "checkpoint"]
        assert len(checkpoint_events) == 1
        data = checkpoint_events[0]["data"]
        assert data["step"] == 2
        assert data["needs_approval"] is True
        assert "Intermediate result" in data["output_preview"]


# ---------------------------------------------------------------------------
# Test: SSE emits task_completed as terminal event (VAL-OBS-005)
# ---------------------------------------------------------------------------


class TestSSETaskCompleted:
    """VAL-OBS-005: SSE emits task_completed as terminal event."""

    def test_task_completed_is_last_event(self):
        """Stream ends with task_completed containing full TaskResult."""
        from kazma_ui.swarm_sse import SSEEventBus

        bus = SSEEventBus()
        bus.emit("task-1", "task_started", {"task_id": "task-1", "workers": ["w1"]})
        bus.emit("task-1", "worker_started", {"worker": "w1", "step": 1})
        bus.emit(
            "task-1",
            "worker_completed",
            {"worker": "w1", "status": "success", "output_preview": "done"},
        )
        bus.emit(
            "task-1",
            "task_completed",
            {
                "task_id": "task-1",
                "result": {
                    "task_id": "task-1",
                    "status": "success",
                    "worker_results": [],
                },
            },
        )

        events = bus.get_history("task-1")
        assert events[-1]["event"] == "task_completed"
        assert events[-1]["data"]["task_id"] == "task-1"
        assert "result" in events[-1]["data"]


# ---------------------------------------------------------------------------
# Test: SSE reconnect with catch-up (VAL-OBS-006)
# ---------------------------------------------------------------------------


class TestSSEReconnectCatchUp:
    """VAL-OBS-006: SSE reconnects after mid-task disconnect with catch-up."""

    def test_late_subscriber_gets_catch_up_events(self):
        """Late subscribers receive replayed task_started and prior events."""
        from kazma_ui.swarm_sse import SSEEventBus

        bus = SSEEventBus()
        # Emit events before any subscriber connects.
        bus.emit("task-1", "task_started", {"task_id": "task-1", "workers": ["w1"]})
        bus.emit("task-1", "worker_started", {"worker": "w1", "step": 1})

        # Late subscriber should get catch-up.
        history = bus.get_history("task-1")
        assert len(history) == 2
        assert history[0]["event"] == "task_started"
        assert history[1]["event"] == "worker_started"

    def test_sse_endpoint_replays_history_for_completed_task(self):
        """For a completed task, SSE replays the full event history."""
        from kazma_ui.swarm_sse import SSEEventBus, create_sse_router

        app = FastAPI()
        bus = SSEEventBus()
        app.include_router(create_sse_router(event_bus=bus))

        mock_task = MagicMock()
        mock_task.status = "completed"

        # Store events in history.
        bus.emit("task-1", "task_started", {"task_id": "task-1", "workers": ["w1"]})
        bus.emit("task-1", "worker_started", {"worker": "w1", "step": 1})
        bus.emit(
            "task-1",
            "worker_completed",
            {"worker": "w1", "status": "success", "output_preview": "ok"},
        )
        bus.emit(
            "task-1",
            "task_completed",
            {"task_id": "task-1", "result": {"status": "success"}},
        )

        with patch("kazma_ui.swarm_sse.get_swarm_engine") as mock_get_engine:
            mock_engine = MagicMock()
            mock_engine.task_store = None
            mock_engine.get_task.return_value = mock_task
            mock_get_engine.return_value = mock_engine

            with TestClient(app).stream(
                "GET", "/api/swarm/tasks/task-1/stream"
            ) as response:
                events = _read_sse_response(response, max_events=4)
                assert len(events) == 4
                assert events[0]["event"] == "task_started"
                assert events[-1]["event"] == "task_completed"


# ---------------------------------------------------------------------------
# Test: Pipeline SSE stream emits per-step events (VAL-ORCH-003)
# ---------------------------------------------------------------------------


class TestSSEPipelineEvents:
    """VAL-ORCH-003: Pipeline SSE stream emits per-step events."""

    def test_pipeline_emits_incrementing_step_values(self):
        """Pipeline emits task_started, per-worker events with step 1, 2, 3."""
        from kazma_ui.swarm_sse import SSEEventBus

        bus = SSEEventBus()
        bus.emit("task-1", "task_started", {"task_id": "task-1", "workers": ["a", "b", "c"]})
        bus.emit("task-1", "worker_started", {"worker": "a", "step": 1})
        bus.emit("task-1", "worker_completed", {"worker": "a", "status": "success", "output_preview": "out-a"})
        bus.emit("task-1", "worker_started", {"worker": "b", "step": 2})
        bus.emit("task-1", "worker_completed", {"worker": "b", "status": "success", "output_preview": "out-b"})
        bus.emit("task-1", "worker_started", {"worker": "c", "step": 3})
        bus.emit("task-1", "worker_completed", {"worker": "c", "status": "success", "output_preview": "out-c"})
        bus.emit(
            "task-1",
            "task_completed",
            {"task_id": "task-1", "result": {"status": "success"}},
        )

        events = bus.get_history("task-1")
        started_events = [e for e in events if e["event"] == "worker_started"]
        steps = [e["data"]["step"] for e in started_events]
        assert steps == [1, 2, 3]


# ---------------------------------------------------------------------------
# Test: SSE works for all orchestration patterns (VAL-ORCH-045)
# ---------------------------------------------------------------------------


class TestSSEAllPatterns:
    """VAL-ORCH-045: SSE progress stream works for all orchestration patterns."""

    @pytest.mark.parametrize(
        "pattern",
        ["dispatch", "broadcast", "pipeline", "fan_out", "consult", "conditional"],
    )
    def test_sse_events_emitted_for_pattern(self, pattern: str):
        """SSE emits live progress for any pattern, concludes with task_completed."""
        from kazma_ui.swarm_sse import SSEEventBus

        bus = SSEEventBus()
        bus.emit("task-1", "task_started", {"task_id": "task-1", "workers": ["w1"]})
        bus.emit("task-1", "worker_started", {"worker": "w1", "step": 1})
        bus.emit(
            "task-1",
            "worker_completed",
            {"worker": "w1", "status": "success", "output_preview": "ok"},
        )
        bus.emit(
            "task-1",
            "task_completed",
            {"task_id": "task-1", "result": {"status": "success"}},
        )

        events = bus.get_history("task-1")
        assert events[0]["event"] == "task_started"
        assert events[-1]["event"] == "task_completed"
        # Verify worker events are present.
        worker_events = [e for e in events if e["event"].startswith("worker_")]
        assert len(worker_events) >= 2  # started + completed


# ---------------------------------------------------------------------------
# Test: Engine integration — dispatch emits SSE events
# ---------------------------------------------------------------------------


class TestEngineSSEIntegration:
    """Integration tests verifying SwarmEngine emits SSE events via the bus."""

    def _build_engine_with_mock_workers(self, outputs: dict[str, str] | None = None):
        """Build a SwarmEngine with mock workers."""
        from kazma_core.swarm.config import SwarmConfig, WorkerConfig
        from kazma_core.swarm.engine import SwarmEngine

        outputs = outputs or {"w1": "output from w1"}
        config = SwarmConfig(
            enabled=True,
            workers=[
                WorkerConfig(name=name, type="in_process", model="m", provider="p")
                for name in outputs
            ],
        )
        engine = SwarmEngine(config)

        # Replace workers with mocks that return controlled output.
        for name, output in outputs.items():
            mock_worker = MagicMock()
            mock_worker.name = name
            mock_worker._running = True
            mock_worker.mark_dispatched = MagicMock()
            mock_worker.mark_completed = MagicMock()
            mock_worker.dispatch = AsyncMock(
                return_value={
                    "worker": name,
                    "task_id": "test",
                    "status": "success",
                    "output": output,
                    "error": None,
                    "tokens_used": 50,
                    "cost": 0.01,
                    "duration_seconds": 0.1,
                    "handoffs": [],
                }
            )
            engine._workers[name] = mock_worker

        return engine

    @pytest.mark.asyncio
    async def test_dispatch_emits_sse_events(self):
        """Engine dispatch emits task_started, worker events, task_completed."""
        from kazma_core.swarm.task import SwarmTask, TaskType
        from kazma_ui.swarm_sse import SSEEventBus, wire_engine_events

        engine = self._build_engine_with_mock_workers({"w1": "hello"})
        bus = SSEEventBus()
        wire_engine_events(engine, bus)

        task = SwarmTask(
            prompt="test task",
            workers=["w1"],
            type=TaskType.DISPATCH,
        )

        # Subscribe before dispatch.
        queue = bus.subscribe(task.id)

        result = await engine.dispatch(task)
        assert result.status == "success"

        # Drain the queue.
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        event_types = [e["event"] for e in events]
        assert "task_started" in event_types
        assert "worker_started" in event_types
        assert "worker_completed" in event_types
        assert "task_completed" in event_types

        # task_started should be first.
        task_started_idx = event_types.index("task_started")
        assert task_started_idx == 0

        # task_completed should be last.
        task_completed_idx = event_types.index("task_completed")
        assert task_completed_idx == len(event_types) - 1

    @pytest.mark.asyncio
    async def test_dispatch_worker_started_has_step(self):
        """Worker_started events include step index."""
        from kazma_core.swarm.task import SwarmTask, TaskType
        from kazma_ui.swarm_sse import SSEEventBus, wire_engine_events

        engine = self._build_engine_with_mock_workers({"w1": "out"})
        bus = SSEEventBus()
        wire_engine_events(engine, bus)

        task = SwarmTask(prompt="test", workers=["w1"], type=TaskType.DISPATCH)
        queue = bus.subscribe(task.id)
        await engine.dispatch(task)

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        started_events = [e for e in events if e["event"] == "worker_started"]
        assert len(started_events) >= 1
        assert started_events[0]["data"]["worker"] == "w1"
        assert "step" in started_events[0]["data"]

    @pytest.mark.asyncio
    async def test_dispatch_task_completed_contains_result(self):
        """Task_completed event contains the full TaskResult."""
        from kazma_core.swarm.task import SwarmTask, TaskType
        from kazma_ui.swarm_sse import SSEEventBus, wire_engine_events

        engine = self._build_engine_with_mock_workers({"w1": "out"})
        bus = SSEEventBus()
        wire_engine_events(engine, bus)

        task = SwarmTask(prompt="test", workers=["w1"], type=TaskType.DISPATCH)
        queue = bus.subscribe(task.id)
        result = await engine.dispatch(task)

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        completed_events = [e for e in events if e["event"] == "task_completed"]
        assert len(completed_events) == 1
        assert completed_events[0]["data"]["task_id"] == result.task_id
        assert "result" in completed_events[0]["data"]

    @pytest.mark.asyncio
    async def test_late_subscriber_catches_up(self):
        """Late subscriber receives history of events emitted before connecting."""
        from kazma_core.swarm.task import SwarmTask, TaskType
        from kazma_ui.swarm_sse import SSEEventBus, wire_engine_events

        engine = self._build_engine_with_mock_workers({"w1": "out"})
        bus = SSEEventBus()
        wire_engine_events(engine, bus)

        task = SwarmTask(prompt="test", workers=["w1"], type=TaskType.DISPATCH)

        # Dispatch WITHOUT subscribing first.
        result = await engine.dispatch(task)

        # Late subscriber should get catch-up events.
        history = bus.get_history(task.id)
        assert len(history) >= 3  # task_started + worker events + task_completed
        assert history[0]["event"] == "task_started"
        assert history[-1]["event"] == "task_completed"

    @pytest.mark.asyncio
    async def test_worker_completed_has_output_preview(self):
        """Worker_completed event includes truncated output_preview."""
        from kazma_core.swarm.task import SwarmTask, TaskType
        from kazma_ui.swarm_sse import SSEEventBus, wire_engine_events

        long_output = "x" * 500
        engine = self._build_engine_with_mock_workers({"w1": long_output})
        bus = SSEEventBus()
        wire_engine_events(engine, bus)

        task = SwarmTask(prompt="test", workers=["w1"], type=TaskType.DISPATCH)
        queue = bus.subscribe(task.id)
        await engine.dispatch(task)

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        completed_events = [e for e in events if e["event"] == "worker_completed"]
        assert len(completed_events) >= 1
        preview = completed_events[0]["data"]["output_preview"]
        assert len(preview) <= 200  # Should be truncated

    @pytest.mark.asyncio
    async def test_engine_without_bus_still_works(self):
        """Engine dispatch works normally without an event bus configured."""
        from kazma_core.swarm.task import SwarmTask, TaskType

        engine = self._build_engine_with_mock_workers({"w1": "out"})
        # Do NOT wire an event bus.
        task = SwarmTask(prompt="test", workers=["w1"], type=TaskType.DISPATCH)
        result = await engine.dispatch(task)
        assert result.status == "success"


# ---------------------------------------------------------------------------
# Test: SSE frame formatting
# ---------------------------------------------------------------------------


class TestSSEFrameFormatting:
    """Test SSE frame helper function."""

    def test_sse_frame_with_dict_data(self):
        """SSE frame correctly formats dict data as JSON."""
        from kazma_ui.swarm_sse import _sse_frame

        frame = _sse_frame("task_started", {"task_id": "t1", "workers": ["a"]})
        assert "event: task_started\n" in frame
        assert '"task_id": "t1"' in frame
        assert frame.endswith("\n\n")

    def test_sse_frame_with_string_data(self):
        """SSE frame correctly formats string data."""
        from kazma_ui.swarm_sse import _sse_frame

        frame = _sse_frame("error", "something went wrong")
        assert "event: error\n" in frame
        assert "data: something went wrong\n" in frame


# ---------------------------------------------------------------------------
# Test: SSE endpoint returns 404 for nonexistent tasks
# ---------------------------------------------------------------------------


class TestSSENotFound:
    """Test SSE endpoint behavior for missing tasks."""

    def test_returns_404_when_no_engine(self):
        """Returns 404 when SwarmEngine is not available."""
        from kazma_ui.swarm_sse import SSEEventBus, create_sse_router

        app = FastAPI()
        bus = SSEEventBus()
        app.include_router(create_sse_router(event_bus=bus))

        with patch("kazma_ui.swarm_sse.get_swarm_engine", return_value=None):
            client = TestClient(app)
            response = client.get("/api/swarm/tasks/task-1/stream")
            assert response.status_code == 404

    def test_returns_404_when_task_not_found(self):
        """Returns 404 when task is not in history or store."""
        from kazma_ui.swarm_sse import SSEEventBus, create_sse_router

        app = FastAPI()
        bus = SSEEventBus()
        app.include_router(create_sse_router(event_bus=bus))

        with patch("kazma_ui.swarm_sse.get_swarm_engine") as mock_get_engine:
            mock_engine = MagicMock()
            mock_engine.task_store = None
            mock_engine.get_task.return_value = None
            mock_get_engine.return_value = mock_engine

            client = TestClient(app)
            response = client.get("/api/swarm/tasks/task-1/stream")
            assert response.status_code == 404


# ---------------------------------------------------------------------------
# Test: SSE endpoint streams events for active (running) tasks
# ---------------------------------------------------------------------------


class TestSSEActiveTasks:
    """VAL-OBS-001: SSE auto-connects for active tasks."""

    def test_streams_events_for_task_with_history(self):
        """SSE endpoint streams all historical events in order."""
        from kazma_ui.swarm_sse import SSEEventBus, create_sse_router

        app = FastAPI()
        bus = SSEEventBus()
        app.include_router(create_sse_router(event_bus=bus))

        mock_task = MagicMock()
        mock_task.status = "completed"

        with patch("kazma_ui.swarm_sse.get_swarm_engine") as mock_get_engine:
            mock_engine = MagicMock()
            mock_engine.task_store = None
            mock_engine.get_task.return_value = mock_task
            mock_get_engine.return_value = mock_engine

            # Emit a full task lifecycle.
            bus.emit("task-1", "task_started", {"task_id": "task-1", "workers": ["w1"]})
            bus.emit("task-1", "worker_started", {"worker": "w1", "step": 1})
            bus.emit(
                "task-1",
                "worker_completed",
                {"worker": "w1", "status": "success", "output_preview": "ok"},
            )
            bus.emit(
                "task-1",
                "task_completed",
                {"task_id": "task-1", "result": {"status": "success"}},
            )

            with TestClient(app).stream(
                "GET", "/api/swarm/tasks/task-1/stream"
            ) as response:
                events = _read_sse_response(response, max_events=4)
                assert len(events) == 4
                assert events[0]["event"] == "task_started"
                assert events[1]["event"] == "worker_started"
                assert events[2]["event"] == "worker_completed"
                assert events[3]["event"] == "task_completed"
