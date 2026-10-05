"""Required functionality must be usable before traffic is admitted."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kazma_ui import health


@pytest.fixture
def healthy(monkeypatch):
    for name in ("config_store", "database", "swarm_engine", "model_registry", "agent_runner", "mcp", "cron", "schedulers", "llm_provider"):
        monkeypatch.setattr(health, "check_" + name, lambda n=name: {"status": "ok", "component": n})


@pytest.mark.asyncio
@pytest.mark.parametrize("component", ["agent_runner", "model_registry", "llm_provider"])
async def test_core_failure_refuses_traffic(healthy, monkeypatch, component):
    monkeypatch.setattr(health, "check_" + component, lambda: {"status": "failed"})
    response = await health._readiness()
    assert response.status_code == 503
    assert component in json.loads(response.body)["unavailable_capabilities"]


@pytest.mark.asyncio
async def test_graph_without_saver_is_not_ready(healthy):
    runtime = SimpleNamespace(_graph_holder={"graph": object()}, _checkpointer=None)
    response = await health._readiness(runtime=runtime)
    assert response.status_code == 503
    assert "chat_runtime" in json.loads(response.body)["unavailable_capabilities"]
    runtime._checkpointer = object()
    assert (await health._readiness(runtime=runtime)).status_code == 503
    runtime._graph_holder["graph"] = SimpleNamespace(checkpointer=runtime._checkpointer)
    assert (await health._readiness(runtime=runtime)).status_code == 200


@pytest.mark.asyncio
async def test_optional_outage_is_visible_but_required_outage_refuses(healthy, monkeypatch):
    monkeypatch.setattr(health, "check_mcp", lambda: {"status": "degraded"})
    optional = await health._readiness()
    assert optional.status_code == 200
    assert json.loads(optional.body)["status"] == "degraded"
    assert (await health._readiness(required=["mcp"])).status_code == 503


@pytest.mark.asyncio
async def test_unknown_requirement_cannot_report_ready(healthy):
    response = await health._readiness(required=["misspelled-service"])
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_malformed_requirement_cannot_drop_the_requirement(healthy):
    response = await health._readiness(required="mcp")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_required_temporal_failure_cannot_hide_behind_ready_chat(healthy, monkeypatch):
    from kazma_core.swarm import durable
    monkeypatch.setenv("KAZMA_TEMPORAL_REQUIRED", "1")
    monkeypatch.setattr(durable, "_worker_task", None)
    response = await health._readiness()
    assert response.status_code == 503
    assert "temporal" in json.loads(response.body)["unavailable_capabilities"]


def test_docker_cli_without_a_working_daemon_is_not_execution_ready(monkeypatch):
    from kazma_core.tools import code_exec
    from kazma_core.sandbox import e2b
    import subprocess
    monkeypatch.setattr(e2b, "e2b_available", lambda: False)
    monkeypatch.setattr(code_exec, "local_exec_forbidden", lambda: True)
    monkeypatch.setattr(code_exec, "_docker_cli", lambda: "fixture-docker")
    monkeypatch.setattr(code_exec, "use_docker_jail", lambda: True)
    monkeypatch.setattr(health.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1, stdout="", stderr="daemon down"))
    assert health._check_code_execution()["status"] == "failed"
