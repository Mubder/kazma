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


# A slow docker info is not a missing daemon (live 2026-10-15: three probes
# over the 2 s limit, while the machine was busy, made readiness 503 for 90 s
# and paged the owner over a running daemon).


@pytest.fixture
def docker_jail(monkeypatch):
    from kazma_core.sandbox import e2b
    from kazma_core.tools import code_exec

    monkeypatch.setattr(e2b, "e2b_available", lambda: False)
    monkeypatch.setattr(code_exec, "local_exec_forbidden", lambda: True)
    monkeypatch.setattr(code_exec, "_docker_cli", lambda: "fixture-docker")
    monkeypatch.setattr(code_exec, "use_docker_jail", lambda: True)
    monkeypatch.setattr(health, "_docker_answered_at", None)
    answers: list = []

    def run(argv, **_kw):
        import subprocess

        answer = answers.pop(0)
        if answer == "slow":
            raise subprocess.TimeoutExpired(argv, 2)
        if answer == "down":
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="cannot connect to the daemon")
        return subprocess.CompletedProcess(argv, 0, stdout="29.0.1\n", stderr="")

    monkeypatch.setattr(health.subprocess, "run", run)
    return answers


def test_a_slow_answer_soon_after_a_good_one_keeps_execution_ready(docker_jail):
    docker_jail += ["up", "slow", "slow", "slow"]
    assert health._check_code_execution()["status"] == "ok"
    for _ in range(3):
        result = health._check_code_execution()
        assert result["status"] == "ok" and result["probe"] == "slow", result


def test_a_daemon_that_never_answered_is_not_ready_when_slow(docker_jail):
    docker_jail += ["slow"]
    assert health._check_code_execution()["status"] == "failed"


def test_a_daemon_slow_past_the_grace_is_not_ready(docker_jail, monkeypatch):
    docker_jail += ["up", "slow"]
    assert health._check_code_execution()["status"] == "ok"
    monkeypatch.setattr(health, "_docker_answered_at", health.time.monotonic() - health._DOCKER_SLOW_GRACE_S - 1)
    assert health._check_code_execution()["status"] == "failed"


def test_a_daemon_that_answers_down_fails_at_once_and_is_not_trusted_after(docker_jail):
    docker_jail += ["up", "down", "slow"]
    assert health._check_code_execution()["status"] == "ok"
    assert health._check_code_execution()["status"] == "failed"
    assert health._check_code_execution()["status"] == "failed", "a slow probe after a down answer passed"


def test_negative_control_without_the_grace_one_slow_answer_fails_readiness(docker_jail, monkeypatch):
    """The rule before 2026-10-15: every probe over 2 s failed."""
    monkeypatch.setattr(health, "_DOCKER_SLOW_GRACE_S", 0.0)
    docker_jail += ["up", "slow"]
    assert health._check_code_execution()["status"] == "ok"
    assert health._check_code_execution()["status"] == "failed"
