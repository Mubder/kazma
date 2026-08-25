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
    run_activity_payload,
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

    inner = MagicMock()

    async def _inner(task: Any, started: float, span: Any) -> str:
        return "in-process"

    engine = MagicMock()
    engine._dispatch_inner = _inner
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
async def test_activity_payload_uses_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.swarm.task import SwarmTask, TaskResult

    task = SwarmTask(prompt="hi", id="act-1")
    engine = MagicMock()
    engine._active_tasks = {"act-1": task}

    async def _inner(t: Any, started: float, span: Any) -> TaskResult:
        assert in_durable_activity() is True
        return TaskResult(task_id=t.id, status="success", aggregated_output="ok")

    engine._dispatch_inner = _inner
    monkeypatch.setattr(
        "kazma_core.swarm.engine.get_swarm_engine", lambda: engine
    )
    out = await run_activity_payload(
        {"task_id": "act-1", "task": task.to_dict(), "started": 1.0}
    )
    assert out["status"] == "success"
    assert out["aggregated_output"] == "ok"
    assert in_durable_activity() is False
