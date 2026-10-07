"""Real IDE requests and worker failures cannot repeat an admitted effect."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.agent.effect_journal import _EffectJournal
from kazma_core.agent.tool_registry import LocalToolRegistry
from kazma_core.ide.service import IdeService
from kazma_core.llm_provider import LLMResponse, ToolCall
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.reliability import FallbackChain, RetryPolicy
from kazma_core.swarm.task import WorkerResult
from kazma_core.swarm.worker import InProcessWorker, SwarmWorker
from kazma_ui.ide_api import create_ide_router


@pytest.fixture
def fixture_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("KAZMA_WORKSPACE", str(root))
    monkeypatch.setattr("kazma_core.workspace.binding._WORKSPACE_ROOT", root)
    monkeypatch.setattr("kazma_core.stores.get_workspace_store", lambda: Mock(get_active_workspace=lambda: None))
    registry = LocalToolRegistry(include_builtins=False)
    monkeypatch.setattr("kazma_core.agent.tool_registry.get_tool_registry", lambda: registry)
    service = IdeService()
    monkeypatch.setattr("kazma_core.ide.get_ide_service", lambda: service)
    safety = Mock(enabled=True)
    safety.is_danger_tool.return_value = True
    safety.check = AsyncMock(return_value=True)
    monkeypatch.setattr("kazma_core.swarm.safety.get_safety", lambda: safety)
    monkeypatch.setattr("kazma_ui.auth.get_request_principal", lambda request: {
        "user_id": "fixture-owner", "role": "admin", "source": "fixture",
    })
    return root, registry, safety


def _client():
    app = FastAPI()
    app.include_router(create_ide_router())
    return TestClient(app)


def test_http_retry_after_lost_response_reuses_result_and_gate(fixture_tools):
    root, registry, safety = fixture_tools
    calls = []

    @registry.register(name="file_write")
    async def write(path: str, content: str):
        calls.append(Path(path).name)
        (root / path).write_text(content, encoding="utf-8")
        return "written"

    with _client() as client:
        headers = {"Idempotency-Key": "save-1"}
        first = client.post("/api/ide/write", json={"path": "a.txt", "content": "one"}, headers=headers)
        # Discard the first response: the user's retry carries the same ID.
        second = client.post("/api/ide/write", json={"path": "a.txt", "content": "one"}, headers=headers)
        assert first.json() == second.json() and second.json()["ok"]
        changed = client.post("/api/ide/write", json={"path": "a.txt", "content": "two"}, headers=headers)
        assert changed.status_code == 409
        assert (root / "a.txt").read_text() == "one"
        assert calls == ["a.txt"]
        safety.check.assert_awaited_once()
        # A distinct deliberate operation is allowed and needs its own gate.
        response = client.post("/api/ide/write", json={"path": "a.txt", "content": "two"},
                               headers={"Idempotency-Key": "save-2"})
        assert response.json()["ok"] and calls == ["a.txt", "a.txt"]
        assert safety.check.await_count == 2


def test_http_unknown_effect_is_held_without_another_gate_or_dispatch(fixture_tools):
    root, registry, safety = fixture_tools

    @registry.register(name="file_write")
    async def write(path: str, content: str):
        with (root / path).open("a", encoding="utf-8") as stream:
            stream.write(content)
        raise TimeoutError("acknowledgement lost")

    with _client() as client:
        for _ in range(2):
            response = client.post("/api/ide/write", json={"path": "a.txt", "content": "once"},
                                   headers={"Idempotency-Key": "uncertain-1"})
            assert response.status_code == 409 and response.json()["effect_uncertain"]
    assert (root / "a.txt").read_text() == "once"
    safety.check.assert_awaited_once()


def test_cached_http_result_still_requires_authenticated_operator(fixture_tools, monkeypatch):
    _, registry, _ = fixture_tools

    @registry.register(name="file_write")
    async def write(path: str, content: str):
        return "written"

    with _client() as client:
        args = {"path": "a.txt", "content": "one"}
        headers = {"Idempotency-Key": "auth-1"}
        assert client.post("/api/ide/write", json=args, headers=headers).json()["ok"]
        monkeypatch.setattr("kazma_ui.auth.get_request_principal", lambda request: None)
        assert client.post("/api/ide/write", json=args, headers=headers).status_code == 401
        monkeypatch.setattr("kazma_ui.auth.get_request_principal", lambda request: {
            "user_id": "fixture-owner", "role": "viewer", "source": "fixture",
        })
        assert client.post("/api/ide/write", json=args, headers=headers).status_code == 403


@pytest.mark.parametrize("opaque", [False, True])
def test_bodyless_restore_and_new_post_operations_are_receipted(fixture_tools, opaque):
    from fastapi import APIRouter
    from kazma_ui.ide_effects import IdeEffectRoute

    router = APIRouter(prefix="/api/ide", route_class=IdeEffectRoute)
    calls = []

    @router.post("/checkpoints/fixture/restore")
    @router.post("/future-mutation")
    async def mutate():
        calls.append("changed")
        return {"ok": True}

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        for path in ("/api/ide/checkpoints/fixture/restore", "/api/ide/future-mutation"):
            headers = {"Idempotency-Key": "key-" + str(len(calls))}
            content = b"opaque operation" if opaque else b""
            assert client.post(path, headers=headers, content=content).json()["ok"]
            assert client.post(path, headers=headers, content=content).json()["ok"]
    assert calls == ["changed", "changed"]


def test_readonly_git_is_not_cached(fixture_tools, monkeypatch):
    service = IdeService()
    read = AsyncMock(side_effect=[{"ok": True, "output": "before"}, {"ok": True, "output": "after"}])
    monkeypatch.setattr(service, "git", read)
    monkeypatch.setattr("kazma_core.ide.get_ide_service", lambda: service)
    with _client() as client:
        responses = [client.post("/api/ide/git", json={"subcommand": "status"},
                                 headers={"Idempotency-Key": "read-1"}).json() for _ in range(2)]
    assert [response["output"] for response in responses] == ["before", "after"]
    assert read.await_count == 2


def test_denied_http_request_remains_denied_on_retry(fixture_tools):
    root, registry, safety = fixture_tools
    safety.check.return_value = False

    @registry.register(name="file_write")
    async def write(path: str, content: str):
        pytest.fail("denied request executed")

    with _client() as client:
        for _ in range(2):
            response = client.post("/api/ide/write", json={"path": "a.txt", "content": "one"},
                                   headers={"Idempotency-Key": "deny-1"})
            assert response.status_code == 200 and response.json()["ok"] is False
    safety.check.assert_awaited_once()
    assert not (root / "a.txt").exists()


@pytest.mark.asyncio
async def test_inflight_http_retry_cannot_redirect_a_waiting_approval(fixture_tools, monkeypatch, tmp_path):
    import httpx
    from kazma_core.workspace.binding import resolve_active_root

    root, registry, safety = fixture_tools
    entered, release = asyncio.Event(), asyncio.Event()

    async def approve(**kwargs):
        entered.set()
        await release.wait()
        return True

    safety.check.side_effect = approve

    @registry.register(name="file_write")
    async def write(path: str, content: str):
        assert resolve_active_root() == root
        (resolve_active_root() / "a.txt").write_text(content, encoding="utf-8")
        return "written"

    app = FastAPI()
    app.include_router(create_ide_router())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        payload = {"path": "a.txt", "content": "once"}
        headers = {"Idempotency-Key": "waiting-1"}
        first = asyncio.create_task(client.post("/api/ide/write", json=payload, headers=headers))
        await asyncio.wait_for(entered.wait(), timeout=5)
        try:
            concurrent = await client.post("/api/ide/write", json=payload, headers=headers)
            assert concurrent.status_code == 409
            other = tmp_path / "other"
            other.mkdir()
            monkeypatch.setattr("kazma_core.workspace.binding._WORKSPACE_ROOT", other)
            changed = await client.post("/api/ide/write", json=payload, headers=headers)
            assert changed.status_code == 409
        finally:
            release.set()
            result = await first
        assert result.json()["ok"]
        assert (root / "a.txt").read_text() == "once" and not (other / "a.txt").exists()
        safety.check.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutating", [True, False])
async def test_whole_worker_retry_stops_after_mutator_but_reads_retry(fixture_tools, mutating, monkeypatch):
    _, registry, safety = fixture_tools
    calls = []
    name = "file_append" if mutating else "file_read"

    @registry.register(name=name)
    async def tool():
        calls.append("invoked")
        return "done"

    class Worker(SwarmWorker):
        async def start(self):
            pass

        async def stop(self):
            pass

        async def dispatch(self, task, context=""):
            await registry.execute(name, {})
            return {"worker": self.name, "status": "error", "output": "", "error": "model connection lost"}

    engine = SwarmEngine()
    monkeypatch.setattr(engine, "get_retry_policy", lambda name: RetryPolicy(max_retries=2, base_delay=0))
    worker = Worker("fixture")
    result = (await engine._dispatch_worker(worker, "fixture", ""))[0]
    assert result.status == "error" and not worker.busy
    assert len(calls) == (1 if mutating else 3)
    if mutating:
        assert "retry withheld" in result.error
        assert result.retry_safe is False
        fallback = AsyncMock()
        assert await FallbackChain(["replacement"]).execute(result, dispatch_worker=fallback) is result
        fallback.assert_not_called()
    assert safety.check.await_count == len(calls)


@pytest.mark.asyncio
async def test_uncertain_swarm_tool_stops_without_model_synthesis(fixture_tools, monkeypatch):
    root, registry, safety = fixture_tools

    @registry.register(name="file_append")
    async def append(path: str, content: str):
        with (root / path).open("a", encoding="utf-8") as stream:
            stream.write(content)
        raise TimeoutError("lost acknowledgement")

    provider = Mock()
    provider.chat = AsyncMock(return_value=LLMResponse(
        content="I will write it", tool_calls=[ToolCall(id="call-1", name="file_append",
                                                        arguments={"path": "a.txt", "content": "once"})],
    ))
    model_registry = Mock()
    model_registry.get_client.return_value = provider
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: model_registry)
    monkeypatch.setattr("kazma_core.models.selection.select_provider_for_task", lambda *args, **kwargs: None)
    worker = InProcessWorker("fixture")
    result = await worker.dispatch("write once")
    assert result["status"] == "error" and result["output"] == ""
    assert result["retry_safe"] is False
    assert provider.chat.await_count == 1
    assert (root / "a.txt").read_text() == "once"
    safety.check.assert_awaited_once()
    from kazma_core.paths import data_dir

    rows = _EffectJournal(data_dir() / "tool_effects.db").inspect(result["task_id"])
    assert len(rows) == 1 and rows[0]["result_uncertain"] == 1


@pytest.mark.asyncio
async def test_fallback_stops_after_an_uncertain_alternative():
    primary = WorkerResult("primary", "task", "error", "", error="read failed")
    held = WorkerResult("first", "task", "error", "", error="effects unknown", retry_safe=False)
    fallback = AsyncMock(return_value=held)
    result = await FallbackChain(["first", "second"]).execute(primary, dispatch_worker=fallback)
    assert result is held
    fallback.assert_awaited_once_with("first")
