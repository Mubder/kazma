"""Kazma names the container engine it waits for, and watches that engine's disk.

Kazma's own container engine shipped on 2026-11-12 (AGENTS.md section 63):
Docker Engine in the ``kazma-engine`` WSL distro, kept up from boot by the
``KazmaEngine`` task, beside Docker Desktop, which starts only at a sign-in.
The two call for different steps when they do not answer -- the guard brings
Kazma's engine back by itself, Docker Desktop waits for someone to sign in --
yet three places still spoke of "Docker" for either:

* readiness's code sandbox check said "No available isolated execution
  backend", which the guard quotes in its page and in ``--status``;
* the document isolation report's step said "Docker does not answer: start
  Docker" whatever the engine;
* the guard's disk watch read Docker Desktop's disk image only, so a
  ``kazma-engine`` distro disk filling up (%LOCALAPPDATA%\\Kazma\\engine) was
  never seen.

Now ``docker_cli.engine_in_force`` is the one answer (from the context in
force, no process started), readiness and the document report name the
engine, and the disk watch follows the engine in force. Each test fails on
the code before the change; the negative controls keep the other engines'
answers as they were.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core import docker_cli
from kazma_ui import health

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "service" / "kazma_guard.py"


@pytest.fixture
def context(tmp_path, monkeypatch):
    """Point the docker CLI's files at an empty folder and return a setter
    for the context in force (``None``: the CLI's current one)."""
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / "docker-config"))
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)

    def use(name: str | None, *, current: str | None = None) -> None:
        if name is None:
            monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
        else:
            monkeypatch.setenv("DOCKER_CONTEXT", name)
        if current is not None:
            cfg = tmp_path / "docker-config"
            cfg.mkdir(parents=True, exist_ok=True)
            (cfg / "config.json").write_text(json.dumps({"currentContext": current}), encoding="utf-8")

    return use


# ── the one answer ────────────────────────────────────────────────────


@pytest.mark.parametrize("name, engine, label", [
    ("kazma-engine", "kazma", "Kazma's container engine"),
    ("desktop-linux", "desktop", "Docker Desktop"),
    ("colima", "other", "the Docker engine of the 'colima' context"),
])
def test_the_engine_in_force_is_read_from_the_context(context, name, engine, label):
    context(name)
    assert docker_cli.engine_in_force() == engine
    assert docker_cli.engine_label() == label


def test_the_cli_s_current_context_counts_when_nothing_is_pinned(context):
    """The owner's PC: the docker CLI's default context is kazma-engine and
    no setting chose it yet."""
    context(None, current="kazma-engine")
    assert docker_cli.engine_in_force() == "kazma"


@pytest.mark.parametrize("platform, engine", [("linux", "system"), ("win32", "desktop"),
                                              ("darwin", "desktop")])
def test_the_default_context_is_the_platform_s_own_engine(context, monkeypatch, platform, engine):
    context(None)
    monkeypatch.setattr(docker_cli, "sys", SimpleNamespace(platform=platform))
    assert docker_cli.engine_in_force() == engine


def test_docker_host_is_the_operator_s_own_engine(context, monkeypatch):
    context("kazma-engine")
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:2375")
    assert docker_cli.engine_in_force() == "other"
    assert docker_cli.engine_label() == "the Docker engine DOCKER_HOST names"


def test_the_engine_folder_is_the_one_setup_installs_into(monkeypatch, tmp_path):
    from kazma_core import container_engine_setup as setup

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert docker_cli.kazma_engine_dir() == setup._engine_dir() == tmp_path / "Kazma" / "engine"
    assert docker_cli.kazma_engine_disk_dir() == setup._engine_dir() / setup.DISTRO
    assert setup.DISTRO == docker_cli.KAZMA_DISTRO and setup.CONTEXT == docker_cli.KAZMA_CONTEXT


# ── readiness: the code sandbox ───────────────────────────────────────

_CHECKS = ("check_config_store", "check_database", "check_swarm_engine",
           "check_model_registry", "check_agent_runner", "check_mcp",
           "check_cron", "check_schedulers", "check_llm_provider")


@pytest.fixture
def docker(context, monkeypatch):
    """Every check ok, code execution required, and a stand-in ``docker
    info`` whose answers the test sets (as test_readiness_waits_for_docker)."""
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
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="cannot connect")
        return subprocess.CompletedProcess(argv, 0, stdout="29.9.3\n", stderr="")

    monkeypatch.setattr(health.subprocess, "run", run)
    return SimpleNamespace(answers=answers, use=context)


@pytest.mark.parametrize("name, engine, said", [
    ("kazma-engine", "kazma", "Kazma's container engine is not running"),
    ("desktop-linux", "desktop", "Docker Desktop is not running"),
])
def test_the_code_sandbox_check_names_the_engine_it_waits_for(docker, name, engine, said):
    docker.use(name)
    check = health._check_code_execution()
    assert check["waits_for"] == "docker", "still a wait no restart helps"
    assert check["engine"] == engine
    assert said in check["error"], check["error"]


@pytest.mark.parametrize("answer, said", [("slow", "does not answer")])
def test_a_silent_engine_is_named_too(docker, answer, said):
    docker.use("kazma-engine")
    docker.answers.append(answer)
    check = health._check_code_execution()
    assert check["error"].endswith(f"Kazma's container engine {said}"), check["error"]


def test_no_docker_cli_names_the_engine(docker, monkeypatch):
    from kazma_core.tools import code_exec

    docker.use("kazma-engine")
    monkeypatch.setattr(code_exec, "_docker_cli", lambda: None)
    check = health._check_code_execution()
    assert "Kazma's container engine has no docker CLI" in check["error"], check["error"]


def test_an_engine_that_answers_is_named_in_the_ok_answer(docker):
    docker.use("kazma-engine")
    docker.answers.append("up")
    check = health._check_code_execution()
    assert check["status"] == "ok" and check["engine"] == "kazma"


def test_the_outage_and_its_end_are_said_with_the_engine(docker, caplog):
    import logging

    caplog.set_level(logging.INFO, logger=health.logger.name)
    docker.use("desktop-linux")
    docker.answers.extend(["down", "up"])
    health._check_code_execution()
    health._check_code_execution()
    said = [r.getMessage() for r in caplog.records if r.name == health.logger.name]
    assert any(m.startswith("[Health] Docker Desktop is not running") for m in said), said
    assert "[Health] Docker Desktop answers again" in said, said


# ── the guard quotes it ───────────────────────────────────────────────


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("kazma_guard_engine_named_test", GUARD)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def _probe(guard, code: int, body: dict):
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
    try:
        return guard.probe(f"http://127.0.0.1:{httpd.server_address[1]}/health/ready", 5.0)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_guard_s_status_and_page_name_the_engine(guard, docker):
    """What the guard says -- the ``--status`` server line, the waiting page
    -- is the readiness body's failing check, read over HTTP."""
    docker.use("kazma-engine")
    resp = asyncio.run(health._readiness())
    body = json.loads(resp.body)
    result = _probe(guard, resp.status_code, body)
    assert result.waits_for == ("docker",)
    assert "Kazma's container engine is not running" in result[1], result[1]
    # Negative control: the body before the change named no engine.
    old = json.loads(json.dumps(body))
    old["checks"]["code_execution"]["error"] = "No available isolated execution backend"
    assert "engine" not in _probe(guard, resp.status_code, old)[1]


# ── the document isolation report ─────────────────────────────────────


@pytest.fixture
def documents(tmp_path, monkeypatch, context):
    from kazma_core.config_store import ConfigStore, peek_config_store, reset_config_store, set_config_store
    from kazma_core.documents import worker_container
    from kazma_core.safety.deployment_policy import DOCUMENT_WORKER_ISOLATION_KEY

    previous = peek_config_store()
    store = ConfigStore(db_path=str(tmp_path / "settings.db"))
    set_config_store(store)
    monkeypatch.delenv("KAZMA_DOCUMENT_WORKER_ISOLATION", raising=False)
    monkeypatch.setattr(worker_container, "_status_cache", None)
    store.set(DOCUMENT_WORKER_ISOLATION_KEY, "container", category="security")
    url = "tcp://127.0.0.1:24567"
    monkeypatch.setenv("KAZMA_DOCUMENT_BROKER_URL", url)
    state = {"heartbeat": time.time(), "guard_pid": 4242,
             "guard_features": ["pause_held", "document_broker"],
             "document_broker": {"url": url, "log": str(tmp_path / "document_broker.log"),
                                 "state": "docker_unavailable", "failures": 0}}
    path = tmp_path / "guard.state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setenv("KAZMA_GUARD_STATE_FILE", str(path))
    unreachable = {"status": "unreachable", "ready": False, "error": "ConnectionRefusedError"}
    monkeypatch.setattr(worker_container, "_broker_status", lambda **_: dict(unreachable))

    def report() -> dict[str, Any]:
        return worker_container.isolation_report()

    try:
        yield SimpleNamespace(report=report, use=context)
    finally:
        if previous is not None:
            set_config_store(previous)
        else:
            reset_config_store()


def test_a_broker_waiting_on_kazma_s_engine_names_it_and_its_repair(documents):
    documents.use("kazma-engine")
    report = documents.report()
    assert report["engine"] == "kazma"
    action = report["action"]
    assert action["code"] == "start_kazma_engine", action
    assert "Kazma's container engine" in action["message"]
    assert action["command"].endswith("-m kazma_cli setup engine"), action["command"]


def test_a_broker_waiting_on_docker_desktop_says_it_waits_for_a_sign_in(documents):
    documents.use("desktop-linux")
    action = documents.report()["action"]
    assert action["code"] == "start_docker_desktop", action
    assert "signs in" in action["message"]


def test_another_engine_keeps_the_general_step(documents, monkeypatch):
    """Negative control: an engine Kazma did not set up keeps "start Docker"."""
    documents.use(None)
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:2375")
    action = documents.report()["action"]
    assert action["code"] == "start_docker", action


def test_every_step_the_report_names_is_worded_in_both_languages():
    from kazma_core.documents.broker_supervision import _MESSAGES
    from kazma_ui.i18n.catalog.documents import TRANSLATIONS

    for code in _MESSAGES:
        entry = TRANSLATIONS.get(f"documents.isolation_action.{code}")
        assert entry and entry.get("en") and entry.get("ar"), code


# ── the guard's disk watch ────────────────────────────────────────────


class _Guard:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.pages: list[tuple[str, str, str]] = []

    def log(self, _level: str, event: str, **fields: Any) -> None:
        self.events.append((event, fields))

    def _page(self, severity: str, title: str, detail: str = "", **_kw: Any) -> bool:
        self.pages.append((severity, title, detail))
        return True


@pytest.fixture
def machine(tmp_path, monkeypatch, guard):
    """A data folder and both engines' disk folders, each "on its own drive"."""
    local = tmp_path / "LocalAppData"
    engine_disk = local / "Kazma" / "engine" / "kazma-engine"
    engine_disk.mkdir(parents=True)
    desktop_disk = local / "Docker" / "wsl"
    desktop_disk.mkdir(parents=True)
    data = tmp_path / "kazma-data"
    data.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    monkeypatch.setattr(guard, "_same_volume", lambda a, b: False)
    monkeypatch.setattr(guard, "_docker_disk_dir", lambda: desktop_disk)
    return SimpleNamespace(engine_disk=engine_disk, desktop_disk=desktop_disk, data=data)


def _watch(guard, engine: str):
    return guard.DiskWatch(_Guard(), reader=lambda p: None, engine=lambda: engine)


def test_kazma_s_engine_in_use_watches_its_distro_disk(guard, machine):
    from kazma_core import container_engine_setup as setup

    watched = {role: path for _key, role, path in _watch(guard, "kazma").watched()}
    assert watched.get("engine") == machine.engine_disk == setup._engine_dir() / setup.DISTRO
    assert "docker" not in watched, "Docker Desktop's image holds no Kazma container then"
    assert watched.get("data") == machine.data


def test_docker_desktop_in_use_watches_its_image_as_before(guard, machine):
    """Negative control: with Docker Desktop in force the watch is as it was."""
    watched = {role: path for _key, role, path in _watch(guard, "desktop").watched()}
    assert watched.get("docker") == machine.desktop_disk and "engine" not in watched


def test_the_engine_in_force_is_read_from_the_guard_s_own_context(guard, machine, context):
    """The guard pins the server's choice in its DOCKER_CONTEXT (Dependencies);
    the watch reads it at every check, so a change while it runs counts."""
    watch = guard.DiskWatch(_Guard(), reader=lambda p: None)
    context("kazma-engine")
    assert watch.container_disk() == ("engine", machine.engine_disk)
    context("desktop-linux")
    assert watch.container_disk() == ("docker", machine.desktop_disk)


def test_a_filling_engine_disk_is_paged_naming_it(guard, monkeypatch):
    gb = 1024 ** 3
    usage = SimpleNamespace(total=1000 * gb, free=10 * gb)
    owner = _Guard()
    watch = guard.DiskWatch(owner, reader=lambda p: usage,
                            paths=[("E:", "engine", Path("E:/Kazma/engine/kazma-engine"))])
    monkeypatch.setenv("KAZMA_GUARD_DISK_WATCH", "auto")
    monkeypatch.setattr(guard, "_update_state", lambda **_kw: None)
    watch.tick()
    severity, title, detail = owner.pages[-1]
    assert severity == "critical" and "E:" in title
    assert "Kazma's container engine's disk" in detail
    assert "private search, the document worker and the code sandbox" in detail
    assert guard._disk_status_lines({"disk": watch.record()})[0].endswith("on E:, engine)")
