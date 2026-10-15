"""E2B sandbox + Temporal durable swarm (industry stack part 8).

Default remains Docker/local python_exec and in-process swarm. These
adapters are opt-in and must not run against a real cloud in tests.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from kazma_core.sandbox.e2b import e2b_available, e2b_enabled, run_python
from kazma_core.swarm.durable import (
    durable_enabled,
    in_durable_activity,
    run_via_durable,
)
from kazma_core.tools.code_exec import python_exec


def test_e2b_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAZMA_E2B_API_KEY", raising=False)
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    monkeypatch.delenv("KAZMA_E2B", raising=False)
    assert e2b_enabled() is False
    assert e2b_available() is False


def test_e2b_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2B_API_KEY", "e2b_test_key")
    monkeypatch.setenv("KAZMA_E2B", "0")
    assert e2b_enabled() is False


def test_e2b_enabled_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_E2B_API_KEY", "e2b_test_key")
    monkeypatch.delenv("KAZMA_E2B", raising=False)
    assert e2b_enabled() is True


@pytest.mark.asyncio
async def test_python_exec_routes_to_e2b(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_E2B_API_KEY", "e2b_test_key")
    monkeypatch.delenv("KAZMA_E2B", raising=False)

    async def _fake(code: str, timeout: int = 30) -> str:
        return f"[sandbox: e2b timeout={timeout}s]\n[Exit code: 0]\n[stdout]\n{code}"

    monkeypatch.setattr("kazma_core.sandbox.e2b.run_python", _fake)
    monkeypatch.setattr("kazma_core.sandbox.e2b.e2b_enabled", lambda: True)
    result = await python_exec("print(1)")
    assert "[sandbox: e2b" in result
    assert "print(1)" in result


@pytest.mark.asyncio
async def test_e2b_run_python_uses_interpreter_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E2B_API_KEY", "e2b_test_key")

    class _Logs:
        stdout = ["hello from microvm\n"]
        stderr = ""

    class _Exec:
        logs = _Logs()
        text = "hello from microvm"
        error = None

    class _Sandbox:
        @classmethod
        def create(cls, **kwargs: Any) -> _Sandbox:
            return cls()

        def run_code(self, code: str) -> _Exec:
            assert "print" in code
            return _Exec()

        def kill(self) -> None:
            return None

    monkeypatch.setattr(
        "kazma_core.sandbox.e2b._sdk", lambda: ("interpreter", _Sandbox)
    )
    out = await run_python("print('x')", timeout=12)
    assert "hello from microvm" in out
    assert "[sandbox: e2b" in out
    assert "[Exit code: 0]" in out


def test_temporal_off_without_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAZMA_TEMPORAL_HOST", raising=False)
    monkeypatch.delenv("TEMPORAL_ADDRESS", raising=False)
    monkeypatch.delenv("KAZMA_TEMPORAL", raising=False)
    assert durable_enabled() is False


def test_temporal_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_TEMPORAL_HOST", "localhost:7233")
    monkeypatch.setenv("KAZMA_TEMPORAL", "0")
    assert durable_enabled() is False


def test_temporal_enabled_with_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_TEMPORAL_HOST", "localhost:7233")
    monkeypatch.delenv("KAZMA_TEMPORAL", raising=False)
    assert durable_enabled() is True


@pytest.mark.asyncio
async def test_durable_missing_sdk_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_TEMPORAL_HOST", "localhost:7233")
    monkeypatch.delenv("KAZMA_TEMPORAL_REQUIRED", raising=False)

    async def _run_admitted(task: Any, started: float, span: Any) -> str:
        return "in-process"

    engine = MagicMock()
    engine._run_admitted = _run_admitted
    monkeypatch.setattr("kazma_core.swarm.durable._sdk_available", lambda: False)
    out = await run_via_durable(engine, MagicMock(id="t1"), 0.0, None)
    assert out == "in-process"


@pytest.mark.asyncio
async def test_durable_required_fails_without_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAZMA_TEMPORAL_HOST", "localhost:7233")
    monkeypatch.setenv("KAZMA_TEMPORAL_REQUIRED", "1")
    monkeypatch.setattr("kazma_core.swarm.durable._sdk_available", lambda: False)
    engine = MagicMock()
    task = MagicMock(id="t-req")
    result = await run_via_durable(engine, task, 0.0, None)
    assert result.status == "failed"
    assert "temporalio" in (result.error or "")


@pytest.mark.asyncio
async def test_the_run_body_is_marked_in_durable_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The activity runs the run's own body with the durable mark set (a
    swarm dispatch started inside it runs in process, never a nested
    workflow), in a copy of the run's context."""
    import contextvars

    from kazma_core.swarm import durable
    from kazma_core.swarm.task import SwarmTask, TaskResult

    seen: list[bool] = []

    async def _run_admitted(task: Any, started: float, span: Any) -> TaskResult:
        seen.append(in_durable_activity())
        return TaskResult(task_id=task.id, status="success", aggregated_output="ok")

    engine = MagicMock()
    engine._run_admitted = _run_admitted
    task = SwarmTask(prompt="hi", id="act-1")
    entry = durable._DurableRun(engine=engine, task=task, started=0.0, task_span=None,
                                context=contextvars.copy_context(), workflow_id="kazma-swarm-act-1")
    import asyncio

    body = asyncio.get_running_loop().create_task(durable._run_body(entry), context=entry.context.copy())
    result = await body
    assert result.status == "success" and seen == [True]
    assert in_durable_activity() is False  # only the activity's own copy of the context
