"""Tests for the swarm handoff mechanism.

Covers VAL-HAND-001 through VAL-HAND-006 and VAL-ORCH-051:
- Worker A can delegate to Worker B mid-execution
- Full context transfers on handoff
- HandoffRecord logged with from, to, context, timestamp
- Multi-hop handoffs (A -> B -> C) work
- Handoff back to original worker works (A -> B -> A)
- Failed handoff to nonexistent worker returns clear error
- Handoff chain visible in WorkerResult.handoffs array
"""

from __future__ import annotations

import pytest
from kazma_core.swarm import (
    SwarmConfig,
    SwarmTask,
    WorkerConfig,
)
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.handoff import HandoffRequest, request_handoff
from kazma_core.swarm.task import HandoffRecord

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_config() -> SwarmConfig:
    return SwarmConfig(enabled=True, workers=[])


@pytest.fixture
def three_worker_engine(empty_config: SwarmConfig) -> SwarmEngine:
    """Engine with workers alpha, beta, gamma registered."""
    engine = SwarmEngine(empty_config)
    for name in ("alpha", "beta", "gamma"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))
    return engine


# ---------------------------------------------------------------------------
# HandoffRequest exception tests
# ---------------------------------------------------------------------------


class TestHandoffRequestException:
    """request_handoff() raises HandoffRequest with correct fields."""

    def test_request_handoff_raises_handoff_request(self):
        with pytest.raises(HandoffRequest) as exc_info:
            request_handoff("beta", "do something", "extra context")

        assert exc_info.value.target_worker == "beta"
        assert exc_info.value.task == "do something"
        assert exc_info.value.context == "extra context"

    def test_request_handoff_default_context_is_empty(self):
        with pytest.raises(HandoffRequest) as exc_info:
            request_handoff("beta", "task")

        assert exc_info.value.context == ""


# ---------------------------------------------------------------------------
# VAL-HAND-001: Worker A can delegate to Worker B mid-execution
# ---------------------------------------------------------------------------


class TestHandoffBasic:
    """VAL-HAND-001: Worker A delegates to B, both in worker_results."""

    @pytest.mark.asyncio
    async def test_handoff_a_to_b_both_in_results(self, three_worker_engine: SwarmEngine):
        engine = three_worker_engine

        async def alpha_dispatch(task: str, context: str = "") -> dict:
            request_handoff("beta", "continue from alpha", "alpha intermediate data")
            return {"worker": "alpha", "task_id": "a", "status": "success", "output": "done", "error": None}

        async def beta_dispatch(task: str, context: str = "") -> dict:
            return {"worker": "beta", "task_id": "b", "status": "success", "output": "beta done", "error": None}

        engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
        engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]

        result = await engine.dispatch(
            SwarmTask(prompt="main task", context="base ctx", workers=["alpha"])
        )

        assert result.status == "success"
        worker_names = [wr.worker for wr in result.worker_results]
        assert "alpha" in worker_names
        assert "beta" in worker_names

    @pytest.mark.asyncio
    async def test_handoff_aggregated_output_from_target(self, three_worker_engine: SwarmEngine):
        engine = three_worker_engine

        async def alpha_dispatch(task: str, context: str = "") -> dict:
            request_handoff("beta", "finish it", "data from alpha")
            return {"worker": "alpha", "task_id": "a", "status": "success", "output": "alpha", "error": None}

        async def beta_dispatch(task: str, context: str = "") -> dict:
            return {"worker": "beta", "task_id": "b", "status": "success", "output": "final output", "error": None}

        engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
        engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]

        result = await engine.dispatch(
            SwarmTask(prompt="main", workers=["alpha"])
        )

        assert result.aggregated_output == "final output"


# ---------------------------------------------------------------------------
# VAL-HAND-002: Full context transfers on handoff
# ---------------------------------------------------------------------------


class TestHandoffContextTransfer:
    """VAL-HAND-002: Transferred context includes prompt, intermediate, blackboard."""

    @pytest.mark.asyncio
    async def test_handoff_context_includes_original_prompt(self, three_worker_engine: SwarmEngine):
        engine = three_worker_engine
        received_contexts: list[str] = []

        async def alpha_dispatch(task: str, context: str = "") -> dict:
            request_handoff("beta", "continue", "intermediate results")
            return {"worker": "alpha", "task_id": "a", "status": "success", "output": "alpha out", "error": None}

        async def beta_dispatch(task: str, context: str = "") -> dict:
            received_contexts.append(str(context))
            return {"worker": "beta", "task_id": "b", "status": "success", "output": "beta out", "error": None}

        engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
        engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]

        await engine.dispatch(
            SwarmTask(prompt="original prompt here", context="base context", workers=["alpha"])
        )

        assert len(received_contexts) == 1
        ctx = received_contexts[0]
        assert "original prompt here" in ctx

    @pytest.mark.asyncio
    async def test_handoff_context_includes_intermediate_results(self, three_worker_engine: SwarmEngine):
        engine = three_worker_engine
        received_contexts: list[str] = []

        async def alpha_dispatch(task: str, context: str = "") -> dict:
            request_handoff("beta", "continue", "alpha intermediate work")
            return {"worker": "alpha", "task_id": "a", "status": "success", "output": "alpha out", "error": None}

        async def beta_dispatch(task: str, context: str = "") -> dict:
            received_contexts.append(str(context))
            return {"worker": "beta", "task_id": "b", "status": "success", "output": "beta out", "error": None}

        engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
        engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]

        await engine.dispatch(
            SwarmTask(prompt="task", context="ctx", workers=["alpha"])
        )

        ctx = received_contexts[0]
        assert "alpha intermediate work" in ctx


# ---------------------------------------------------------------------------
# VAL-HAND-003: HandoffRecord logged with from, to, context, timestamp
# ---------------------------------------------------------------------------


class TestHandoffRecord:
    """VAL-HAND-003: HandoffRecord has all 4 fields populated."""

    @pytest.mark.asyncio
    async def test_handoff_record_has_all_fields(self, three_worker_engine: SwarmEngine):
        engine = three_worker_engine

        async def alpha_dispatch(task: str, context: str = "") -> dict:
            request_handoff("beta", "task for beta", "handoff context")
            return {"worker": "alpha", "task_id": "a", "status": "success", "output": "a", "error": None}

        async def beta_dispatch(task: str, context: str = "") -> dict:
            return {"worker": "beta", "task_id": "b", "status": "success", "output": "b", "error": None}

        engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
        engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]

        result = await engine.dispatch(
            SwarmTask(prompt="p", workers=["alpha"])
        )

        alpha_result = next(wr for wr in result.worker_results if wr.worker == "alpha")
        assert len(alpha_result.handoffs) >= 1
        record = alpha_result.handoffs[0]
        assert record.from_worker == "alpha"
        assert record.to_worker == "beta"
        assert isinstance(record.context_transferred, str)
        assert len(record.context_transferred) > 0
        assert isinstance(record.timestamp, str)
        assert len(record.timestamp) > 0

    @pytest.mark.asyncio
    async def test_handoff_record_serialization(self):
        record = HandoffRecord(
            from_worker="alpha",
            to_worker="beta",
            context_transferred="some context",
            timestamp="2025-01-01T00:00:00",
        )
        d = record.to_dict()
        assert d["from_worker"] == "alpha"
        assert d["to_worker"] == "beta"
        assert d["context_transferred"] == "some context"
        assert d["timestamp"] == "2025-01-01T00:00:00"

        restored = HandoffRecord.from_dict(d)
        assert restored.from_worker == "alpha"
        assert restored.to_worker == "beta"


# ---------------------------------------------------------------------------
# VAL-HAND-004: Multi-hop handoffs (A -> B -> C) work
# ---------------------------------------------------------------------------


class TestHandoffMultiHop:
    """VAL-HAND-004: Chain A -> B -> C executes fully."""

    @pytest.mark.asyncio
    async def test_multi_hop_a_b_c_all_in_results(self, three_worker_engine: SwarmEngine):
        engine = three_worker_engine

        async def alpha_dispatch(task: str, context: str = "") -> dict:
            request_handoff("beta", "step 2", "from alpha")
            return {"worker": "alpha", "task_id": "a", "status": "success", "output": "a out", "error": None}

        async def beta_dispatch(task: str, context: str = "") -> dict:
            request_handoff("gamma", "step 3", "from beta")
            return {"worker": "beta", "task_id": "b", "status": "success", "output": "b out", "error": None}

        async def gamma_dispatch(task: str, context: str = "") -> dict:
            return {"worker": "gamma", "task_id": "c", "status": "success", "output": "c out", "error": None}

        engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
        engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]
        engine.get_worker("gamma").dispatch = gamma_dispatch  # type: ignore[assignment]

        result = await engine.dispatch(
            SwarmTask(prompt="chain task", workers=["alpha"])
        )

        worker_names = [wr.worker for wr in result.worker_results]
        assert worker_names == ["alpha", "beta", "gamma"]
        assert result.status == "success"
        assert result.aggregated_output == "c out"

    @pytest.mark.asyncio
    async def test_multi_hop_handoff_records_chain(self, three_worker_engine: SwarmEngine):
        engine = three_worker_engine

        async def alpha_dispatch(task: str, context: str = "") -> dict:
            request_handoff("beta", "step 2", "ctx-a")
            return {"worker": "alpha", "task_id": "a", "status": "success", "output": "a", "error": None}

        async def beta_dispatch(task: str, context: str = "") -> dict:
            request_handoff("gamma", "step 3", "ctx-b")
            return {"worker": "beta", "task_id": "b", "status": "success", "output": "b", "error": None}

        async def gamma_dispatch(task: str, context: str = "") -> dict:
            return {"worker": "gamma", "task_id": "c", "status": "success", "output": "c", "error": None}

        engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
        engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]
        engine.get_worker("gamma").dispatch = gamma_dispatch  # type: ignore[assignment]

        result = await engine.dispatch(
            SwarmTask(prompt="chain", workers=["alpha"])
        )

        by_name = {wr.worker: wr for wr in result.worker_results}
        assert len(by_name["alpha"].handoffs) >= 1
        assert by_name["alpha"].handoffs[0].to_worker == "beta"
        assert len(by_name["beta"].handoffs) >= 1
        assert by_name["beta"].handoffs[0].to_worker == "gamma"
        assert len(by_name["gamma"].handoffs) == 0


# ---------------------------------------------------------------------------
# VAL-HAND-005: Handoff back to original worker works (A -> B -> A)
# ---------------------------------------------------------------------------


class TestHandoffReturn:
    """VAL-HAND-005: Return handoff A -> B -> A works without false cycle detection."""

    @pytest.mark.asyncio
    async def test_return_handoff_a_b_a(self, three_worker_engine: SwarmEngine):
        engine = three_worker_engine
        call_count = {"alpha": 0, "beta": 0}

        async def alpha_dispatch(task: str, context: str = "") -> dict:
            call_count["alpha"] += 1
            if call_count["alpha"] == 1:
                request_handoff("beta", "delegate to beta", "from alpha first call")
            return {"worker": "alpha", "task_id": "a", "status": "success", "output": f"alpha call {call_count['alpha']}", "error": None}

        async def beta_dispatch(task: str, context: str = "") -> dict:
            call_count["beta"] += 1
            request_handoff("alpha", "return to alpha", "from beta")
            return {"worker": "beta", "task_id": "b", "status": "success", "output": "beta out", "error": None}

        engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
        engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]

        result = await engine.dispatch(
            SwarmTask(prompt="return handoff", workers=["alpha"])
        )

        worker_names = [wr.worker for wr in result.worker_results]
        assert worker_names == ["alpha", "beta", "alpha"]
        assert call_count["alpha"] == 2
        assert call_count["beta"] == 1
        assert result.status == "success"


# ---------------------------------------------------------------------------
# VAL-HAND-006: Failed handoff to nonexistent worker returns clear error
# ---------------------------------------------------------------------------


class TestHandoffNonexistentTarget:
    """VAL-HAND-006: Unknown target returns error with worker name."""

    @pytest.mark.asyncio
    async def test_handoff_to_nonexistent_worker_returns_error(self, three_worker_engine: SwarmEngine):
        engine = three_worker_engine

        async def alpha_dispatch(task: str, context: str = "") -> dict:
            request_handoff("ghost", "task", "ctx")
            return {"worker": "alpha", "task_id": "a", "status": "success", "output": "a", "error": None}

        engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]

        result = await engine.dispatch(
            SwarmTask(prompt="test", workers=["alpha"])
        )

        alpha_result = next(wr for wr in result.worker_results if wr.worker == "alpha")
        assert alpha_result.status == "error"
        assert "ghost" in (alpha_result.error or "")
        assert "not found" in (alpha_result.error or "").lower()


# ---------------------------------------------------------------------------
# VAL-ORCH-051: Handoff mechanism transfers context mid-execution
# ---------------------------------------------------------------------------


class TestHandoffOrchestration:
    """VAL-ORCH-051: HandoffRecord logged, target executes with transferred context."""

    @pytest.mark.asyncio
    async def test_handoff_emits_sse_event_logged(self, three_worker_engine: SwarmEngine):
        """SSE event swarm.handoff.{from}->{to} is logged."""
        engine = three_worker_engine
        log_messages: list[str] = []

        import logging

        class LogCapture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                log_messages.append(record.getMessage())

        handler = LogCapture()
        engine_logger = logging.getLogger("kazma_core.swarm.engine")
        prev_level = engine_logger.level
        engine_logger.setLevel(logging.DEBUG)
        engine_logger.addHandler(handler)

        async def alpha_dispatch(task: str, context: str = "") -> dict:
            request_handoff("beta", "go", "ctx")
            return {"worker": "alpha", "task_id": "a", "status": "success", "output": "a", "error": None}

        async def beta_dispatch(task: str, context: str = "") -> dict:
            return {"worker": "beta", "task_id": "b", "status": "success", "output": "b", "error": None}

        engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
        engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]

        await engine.dispatch(SwarmTask(prompt="p", workers=["alpha"]))

        engine_logger.removeHandler(handler)
        engine_logger.setLevel(prev_level)

        sse_events = [m for m in log_messages if "swarm.handoff" in m]
        assert len(sse_events) >= 1
        assert "alpha" in sse_events[0]
        assert "beta" in sse_events[0]

    @pytest.mark.asyncio
    async def test_handoff_target_receives_accumulated_context(self, three_worker_engine: SwarmEngine):
        """Target worker receives prompt, intermediate results, blackboard snapshot."""
        engine = three_worker_engine
        received_contexts: list[str] = []

        async def alpha_dispatch(task: str, context: str = "") -> dict:
            if hasattr(context, "blackboard"):
                await context.blackboard.set("alpha_key", "alpha_value")
            request_handoff("beta", "continue", "alpha intermediate")
            return {"worker": "alpha", "task_id": "a", "status": "success", "output": "a", "error": None}

        async def beta_dispatch(task: str, context: str = "") -> dict:
            received_contexts.append(str(context))
            return {"worker": "beta", "task_id": "b", "status": "success", "output": "b", "error": None}

        engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
        engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]

        await engine.dispatch(
            SwarmTask(prompt="original prompt", context="base", workers=["alpha"])
        )

        assert len(received_contexts) == 1
        ctx = received_contexts[0]
        assert "original prompt" in ctx
        assert "alpha intermediate" in ctx

    @pytest.mark.asyncio
    async def test_handoff_visible_in_worker_result_handoffs_array(self, three_worker_engine: SwarmEngine):
        """Handoff chain is visible in WorkerResult.handoffs array."""
        engine = three_worker_engine

        async def alpha_dispatch(task: str, context: str = "") -> dict:
            request_handoff("beta", "go", "ctx")
            return {"worker": "alpha", "task_id": "a", "status": "success", "output": "a", "error": None}

        async def beta_dispatch(task: str, context: str = "") -> dict:
            return {"worker": "beta", "task_id": "b", "status": "success", "output": "b", "error": None}

        engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
        engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]

        result = await engine.dispatch(SwarmTask(prompt="p", workers=["alpha"]))

        alpha_result = next(wr for wr in result.worker_results if wr.worker == "alpha")
        assert len(alpha_result.handoffs) >= 1
        assert alpha_result.handoffs[0].from_worker == "alpha"
        assert alpha_result.handoffs[0].to_worker == "beta"

    @pytest.mark.asyncio
    async def test_handoff_target_failure_propagates(self, three_worker_engine: SwarmEngine):
        """When the target worker fails, the error propagates to the caller's result."""
        engine = three_worker_engine

        async def alpha_dispatch(task: str, context: str = "") -> dict:
            request_handoff("beta", "go", "ctx")
            return {"worker": "alpha", "task_id": "a", "status": "success", "output": "a", "error": None}

        async def beta_dispatch(task: str, context: str = "") -> dict:
            return {"worker": "beta", "task_id": "b", "status": "error", "output": "", "error": "beta exploded"}

        engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]
        engine.get_worker("beta").dispatch = beta_dispatch  # type: ignore[assignment]

        result = await engine.dispatch(SwarmTask(prompt="p", workers=["alpha"]))

        alpha_result = next(wr for wr in result.worker_results if wr.worker == "alpha")
        assert alpha_result.status == "error"
        assert "beta exploded" in (alpha_result.error or "")

    @pytest.mark.asyncio
    async def test_no_handoff_worker_result_has_empty_handoffs(self, three_worker_engine: SwarmEngine):
        """Workers that don't hand off have empty handoffs list."""
        engine = three_worker_engine

        async def alpha_dispatch(task: str, context: str = "") -> dict:
            return {"worker": "alpha", "task_id": "a", "status": "success", "output": "done", "error": None}

        engine.get_worker("alpha").dispatch = alpha_dispatch  # type: ignore[assignment]

        result = await engine.dispatch(SwarmTask(prompt="p", workers=["alpha"]))

        alpha_result = result.worker_results[0]
        assert alpha_result.handoffs == []
