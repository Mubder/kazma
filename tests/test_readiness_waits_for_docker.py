"""Kazma waiting for Docker is up, says so, and is never restarted for it.

2026-11-12: a Windows update restarted the PC at 06:30 and nobody signed in,
so Docker Desktop -- which starts only in a signed-in session -- never
started. The live install requires code to run in a container
(``security.execution.container_required``), so readiness answered 503
"code_execution: No available isolated execution backend". At start the guard
waits 15 minutes for "ready", then stops Kazma and starts it again: it did
that 18 times in six hours and paged each time, over a Kazma that served chat
the whole while. No restart can bring Docker back.

Readiness now marks a failing check that waits for something outside Kazma
(``waits_for``: the Docker engine not installed, not running or not
answering), and says so when every failing required check waits for the
owner or for such a dependency. The guard reads it (its side is held by
``test_guard_dependencies`` and ``test_guard_integration``). And the check
says the outage once, not on every probe: 4,666 WARNING lines that morning.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from kazma_ui import health

_GUARD = Path(__file__).resolve().parents[1] / "scripts" / "service" / "kazma_guard.py"
_CHECKS = ("check_config_store", "check_database", "check_swarm_engine",
           "check_model_registry", "check_agent_runner", "check_mcp",
           "check_cron", "check_schedulers", "check_llm_provider")


@pytest.fixture
def docker(monkeypatch):
    """Every check ok, code execution required (the strict profile), and a
    stand-in ``docker info`` whose answers the test sets."""
    for name in _CHECKS:
        component = name.removeprefix("check_")
        monkeypatch.setattr(health, name, lambda c=component: {"status": "ok", "component": c})
    monkeypatch.setattr(health, "_check_knowledge_vectors", lambda: {"status": "ok"})
    from kazma_core.safety import deployment_policy
    from kazma_core.sandbox import e2b
    from kazma_core.tools import code_exec

    monkeypatch.setattr(deployment_policy, "container_required", lambda: True)
    monkeypatch.setattr(e2b, "e2b_available", lambda: False)
    monkeypatch.setattr(code_exec, "local_exec_forbidden", lambda: True)
    monkeypatch.setattr(code_exec, "_docker_cli", lambda: "fixture-docker")
    monkeypatch.setattr(code_exec, "use_docker_jail", lambda: True)
    monkeypatch.setattr(health, "_docker_answered_at", None)
    monkeypatch.setattr(health, "_docker_down_said", False)
    answers: list[str] = []

    def run(argv, **_kw):
        answer = answers.pop(0) if answers else "down"
        if answer == "slow":
            raise subprocess.TimeoutExpired(argv, 2)
        if answer == "down":
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="failed to connect to the docker API at "
                "npipe:////./pipe/dockerDesktopLinuxEngine")
        return subprocess.CompletedProcess(argv, 0, stdout="29.0.1\n", stderr="")

    monkeypatch.setattr(health.subprocess, "run", run)
    return answers


def _readiness() -> tuple[int, dict]:
    resp = asyncio.run(health._readiness())
    return resp.status_code, json.loads(resp.body)


def test_docker_down_is_a_wait_not_a_failure_to_restart(docker):
    code, body = _readiness()
    assert code == 503 and body["status"] == "not_ready", "the required sandbox is not there"
    assert body["unavailable_capabilities"] == ["code_execution"]
    assert body["checks"]["code_execution"]["waits_for"] == "docker"
    assert body["waits_for"] == {"code_execution": "docker"}


@pytest.mark.parametrize("answer", ["down", "slow"])
def test_each_way_docker_is_away_waits_for_it(docker, answer):
    docker.append(answer)
    assert health._check_code_execution()["waits_for"] == "docker"


def test_no_docker_cli_waits_for_docker_too(docker, monkeypatch):
    from kazma_core.tools import code_exec

    monkeypatch.setattr(code_exec, "_docker_cli", lambda: None)
    assert health._check_code_execution()["waits_for"] == "docker"


def test_a_wait_is_not_said_over_a_real_failure(docker, monkeypatch):
    """Negative control: the database down too is no wait a restart cannot
    help -- the old rules hold."""
    monkeypatch.setattr(health, "check_database",
                        lambda: {"status": "failed", "component": "database", "error": "timed out"})
    code, body = _readiness()
    assert code == 503 and "waits_for" not in body


def test_a_wait_is_not_said_when_only_a_restart_clears_a_failure(docker, monkeypatch):
    monkeypatch.setattr(health, "check_config_store",
                        lambda: {"status": "ok", "component": "config_store", "restart_required": True})
    code, body = _readiness()
    assert body.get("restart_required") is True and "waits_for" not in body


def test_with_docker_answering_it_is_ready(docker):
    docker.append("up")
    code, body = _readiness()
    assert code == 200 and "waits_for" not in body


def test_the_outage_is_said_once_and_its_end_once(docker, caplog):
    caplog.set_level(logging.DEBUG, logger=health.logger.name)
    docker.extend(["down", "down", "slow", "down", "up", "down"])
    for _ in range(6):
        health._check_code_execution()
    warnings = [r for r in caplog.records
                if r.name == health.logger.name and r.levelno == logging.WARNING]
    infos = [r.getMessage() for r in caplog.records
             if r.name == health.logger.name and r.levelno == logging.INFO]
    assert len(warnings) == 2, [r.getMessage() for r in warnings]  # the outage, and the next one
    assert infos.count("[Health] the Docker daemon answers again") == 1
    debug = [r for r in caplog.records if r.name == health.logger.name and r.levelno == logging.DEBUG]
    assert len(debug) == 3, "the repeats are kept, at DEBUG"


# ── the guard reads it ────────────────────────────────────────────────


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("kazma_guard_waits_for_test", _GUARD)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def _serve(code: int, body: dict):
    raw = json.dumps(body).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 -- the stdlib's name
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _probe(guard, code: int, body: dict):
    httpd = _serve(code, body)
    try:
        return guard.probe(f"http://127.0.0.1:{httpd.server_address[1]}/health/ready", 5.0)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_guard_reads_the_real_body(guard, docker):
    code, body = _readiness()
    result = _probe(guard, code, body)
    assert result.answered and not result[0]
    assert result.waits_for == ("docker",)
    assert guard._answered_waits(result) == ("docker",)
    assert "code_execution" in result[1], result[1]
    # Negative control: the morning's body, from before ``waits_for``.
    old = {k: v for k, v in body.items() if k != "waits_for"}
    assert _probe(guard, code, old).waits_for == ()


def test_a_wait_the_answer_says_only_a_restart_clears_is_no_wait(guard):
    result = guard.ProbeResult(False, "code_execution", restart_required=True, waits_for=("docker",))
    assert guard._answered_waits(result) == ()
    assert guard._answered_waits(guard.ProbeResult(False, "unreachable", answered=False,
                                                   waits_for=("docker",))) == ()


def test_status_says_up_and_waiting(guard, monkeypatch, capsys):
    result = guard.ProbeResult(False, "code_execution: No available isolated execution backend",
                               waits_for=("docker",))
    monkeypatch.setattr(guard, "probe", lambda url, timeout: result)
    monkeypatch.setattr(guard, "_port_holder_pid", lambda port: None)
    monkeypatch.setattr(guard, "read_pause", lambda: None)
    started = time.monotonic()
    guard._cmd_status()
    out = capsys.readouterr().out
    assert "server      : up, waiting for docker" in out, out
    assert time.monotonic() - started < 30
