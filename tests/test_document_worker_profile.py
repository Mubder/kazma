"""Where untrusted documents run is the admin's setting, and Kazma tells the
guard (2026-10-15).

Live 2026-10-15: the install required containers
(``security.execution.container_required``), the profile could only be chosen
with ``KAZMA_DOCUMENT_WORKER_ISOLATION`` in ``.env``, no broker ran, and
readiness answered "degraded" with a sentence about environment variables.
Now:

* the profile is ``security.execution.document_worker_isolation`` (Settings
  -> Documents; protected from the agent's config tools), the environment
  still wins, and a worker process never reads it;
* Kazma writes the guard a request when it needs the broker
  (``broker_supervision._publish_broker_request``), at boot and on a change;
* readiness says which profile runs, who runs the broker, and the one step
  that fixes what is not right; an image older than the code is alerted once
  per build.

Each test carries a negative control: the old behaviour fails it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from kazma_core.documents import broker_supervision, worker_container, worker_process
from kazma_core.documents.errors import DocumentSandboxError
from kazma_core.safety.deployment_policy import (
    CONTAINER_REQUIRED_KEY,
    DOCUMENT_WORKER_ISOLATION_KEY,
)

ENV = "KAZMA_DOCUMENT_WORKER_ISOLATION"


@pytest.fixture
def store(tmp_path, monkeypatch):
    from kazma_core.config_store import ConfigStore, peek_config_store, reset_config_store, set_config_store

    previous = peek_config_store()
    fresh = ConfigStore(db_path=str(tmp_path / "settings.db"))
    set_config_store(fresh)
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.setattr(worker_container, "_status_cache", None)
    try:
        yield fresh
    finally:
        if previous is not None:
            set_config_store(previous)
        else:
            reset_config_store()


def _old_worker_isolation_mode() -> str:
    """worker_container.worker_isolation_mode before 2026-10-15, verbatim but
    for the names: the environment only."""
    raw = (os.environ.get(ENV) or "").strip().lower()
    if raw in ("", "process"):
        return "process"
    if raw == "container":
        return "container"
    raise DocumentSandboxError("invalid", code="worker_isolation_invalid")


# ── the setting ─────────────────────────────────────────────────────────


def test_the_setting_chooses_the_profile_and_the_environment_wins(store, monkeypatch):
    assert worker_container.worker_isolation_mode() == "process"
    store.set(DOCUMENT_WORKER_ISOLATION_KEY, "container", category="security")
    assert worker_container.worker_isolation_mode() == "container"
    selection = worker_container.isolation_selection()
    assert (selection["mode"], selection["source"]) == ("container", "setting")
    monkeypatch.setenv(ENV, "process")
    assert worker_container.worker_isolation_mode() == "process"
    assert worker_container.isolation_selection()["source"] == "environment"
    monkeypatch.delenv(ENV)
    # Negative control: the old resolver never saw the setting.
    assert _old_worker_isolation_mode() == "process"


def test_a_worker_process_never_reads_the_settings_store(store, monkeypatch):
    store.set(DOCUMENT_WORKER_ISOLATION_KEY, "container", category="security")
    reads: list[str] = []

    def spy() -> str | None:
        reads.append("read")
        return "container"

    monkeypatch.setattr("kazma_core.safety.deployment_policy.document_worker_isolation_setting", spy)
    monkeypatch.setattr(worker_process, "_worker", True)
    assert worker_container.worker_isolation_mode() == "process"
    assert worker_container.isolation_selection()["source"] == "worker"
    assert reads == []
    # Negative control: Kazma's own process does read it.
    monkeypatch.setattr(worker_process, "_worker", False)
    assert worker_container.worker_isolation_mode() == "container" and reads


def test_every_worker_entry_point_marks_itself_first() -> None:
    """The four programs a worker runs as (the local profile's three, and the
    image's entry) call worker_process.mark() before anything else."""
    import ast

    root = Path(worker_container.__file__).resolve().parent
    for name in ("parser_worker.py", "mutation_worker.py", "renderer_worker.py", "worker_entry.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        calls = [n for n in main.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
        assert calls and getattr(calls[0].value.func, "id", None) == "mark", name


def test_an_unreadable_setting_refuses_instead_of_guessing(store, monkeypatch):
    def unreadable() -> str:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("kazma_core.safety.deployment_policy.document_worker_isolation_setting", unreadable)
    with pytest.raises(DocumentSandboxError) as caught:
        worker_container.worker_isolation_mode()
    assert caught.value.code == "document_policy_unavailable"
    assert worker_container.isolation_report()["action"]["code"] == "settings_unavailable"
    # Negative control: the old resolver answered "process" -- the less
    # isolated profile -- whatever the setting said.
    assert _old_worker_isolation_mode() == "process"


def test_the_profile_is_protected_validated_and_never_restored() -> None:
    from kazma_core.safety.protected_config import is_protected_config_key
    from kazma_core.settings_restore import CREDENTIAL, classify
    from kazma_core.settings_validation import SettingRejected, validate_setting

    assert is_protected_config_key(DOCUMENT_WORKER_ISOLATION_KEY)
    assert is_protected_config_key(f"tenant.acme.{DOCUMENT_WORKER_ISOLATION_KEY}")
    assert validate_setting(DOCUMENT_WORKER_ISOLATION_KEY, " Container ") == ("container", "security")
    for bad in ("docker", "", None, True, 1):
        with pytest.raises(SettingRejected):
            validate_setting(DOCUMENT_WORKER_ISOLATION_KEY, bad)
    with pytest.raises(SettingRejected):  # a parent mapping save goes through the same rule
        validate_setting("security.execution", {"document_worker_isolation": "jail"})
    assert classify(DOCUMENT_WORKER_ISOLATION_KEY) == CREDENTIAL


def test_the_agent_s_config_tool_cannot_change_the_profile(store) -> None:
    from kazma_core.agent.tool_builtins.system import register_system_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    reg = LocalToolRegistry()
    register_system_tools(reg)
    out = reg._tools["config_save"].func(key=DOCUMENT_WORKER_ISOLATION_KEY, value="process")
    assert out.startswith("Error: Cannot modify restricted key"), out
    assert store.get(DOCUMENT_WORKER_ISOLATION_KEY) is None


# ── the Settings page's route ───────────────────────────────────────────


@pytest.fixture
def client(store, tmp_path):
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient
    from kazma_ui.settings import create_settings_router

    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "settings.html").write_text("ok")
    app = FastAPI()
    app.include_router(create_settings_router(
        MagicMock(), store, Jinja2Templates(directory=str(tmp_path / "templates"))))
    return TestClient(app)


def test_settings_saves_the_profile_and_says_what_the_install_requires(client, store):
    got = client.get("/api/settings/documents").json()["worker_isolation"]
    assert (got["mode"], got["source"], got["container_required"]) == ("process", "default", False)
    store.set(CONTAINER_REQUIRED_KEY, True, category="security")
    assert client.get("/api/settings/documents").json()["worker_isolation"]["container_required"] is True
    ok = client.put("/api/settings/documents", json={"worker_isolation": "container"})
    assert ok.status_code == 200
    assert store.get(DOCUMENT_WORKER_ISOLATION_KEY) == "container"
    got = client.get("/api/settings/documents").json()["worker_isolation"]
    assert (got["mode"], got["source"]) == ("container", "setting")
    bad = client.put("/api/settings/documents", json={"worker_isolation": "docker"})
    assert bad.status_code == 400
    assert store.get(DOCUMENT_WORKER_ISOLATION_KEY) == "container"


# ── the request to the guard ────────────────────────────────────────────


@pytest.fixture
def guard_files(tmp_path, monkeypatch):
    request = tmp_path / "document_broker.request.json"
    monkeypatch.setenv(broker_supervision.REQUEST_FILE_ENV, str(request))
    monkeypatch.setenv("KAZMA_DOCUMENT_BROKER_URL", "tcp://127.0.0.1:24567")
    monkeypatch.setenv("KAZMA_DOCUMENT_BROKER_TOKEN_FILE", str(tmp_path / "token"))
    return request


def test_kazma_asks_the_guard_for_the_broker_when_documents_need_it(store, guard_files):
    assert broker_supervision._publish_broker_request() is True
    first = json.loads(guard_files.read_text(encoding="utf-8"))
    assert first["want"] is False and first["url"] == "tcp://127.0.0.1:24567"
    assert broker_supervision._publish_broker_request() is False, "rewrote an unchanged request"
    store.set(DOCUMENT_WORKER_ISOLATION_KEY, "container", category="security")
    assert broker_supervision._publish_broker_request() is True
    assert json.loads(guard_files.read_text(encoding="utf-8"))["want"] is True


def test_an_unreadable_profile_leaves_the_request_standing(store, guard_files, monkeypatch):
    store.set(DOCUMENT_WORKER_ISOLATION_KEY, "container", category="security")
    broker_supervision._publish_broker_request()

    def unreadable() -> str:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("kazma_core.safety.deployment_policy.document_worker_isolation_setting", unreadable)
    assert broker_supervision._publish_broker_request() is False
    assert json.loads(guard_files.read_text(encoding="utf-8"))["want"] is True


def test_a_profile_change_reaches_the_guard_without_a_restart(store, guard_files):
    broker_supervision.start_broker_requests()
    assert json.loads(guard_files.read_text(encoding="utf-8"))["want"] is False
    store.set(DOCUMENT_WORKER_ISOLATION_KEY, "container", category="security")
    broker_supervision._drain_publishes()
    assert json.loads(guard_files.read_text(encoding="utf-8"))["want"] is True


def test_without_the_watch_a_change_waits_for_the_next_pass(store, guard_files):
    # Negative control for the test above: no listener, the file stays.
    broker_supervision._publish_broker_request()
    store.set(DOCUMENT_WORKER_ISOLATION_KEY, "container", category="security")
    broker_supervision._drain_publishes()
    assert json.loads(guard_files.read_text(encoding="utf-8"))["want"] is False


def test_no_guard_no_request(store, tmp_path, monkeypatch):
    monkeypatch.delenv(broker_supervision.REQUEST_FILE_ENV, raising=False)
    assert broker_supervision._publish_broker_request() is False
    assert list(tmp_path.glob("*.request.json")) == []


# ── readiness: what runs, and what to do ────────────────────────────────


def _guard_state(tmp_path: Path, monkeypatch, *, features=("pause_held", "document_broker"),
                 broker: dict[str, Any] | None = None, url="tcp://127.0.0.1:24567") -> None:
    state = {"heartbeat": time.time(), "guard_pid": 4242, "guard_features": list(features)}
    if broker is not None:
        state["document_broker"] = {"url": url, "log": str(tmp_path / "document_broker.log"), **broker}
    path = tmp_path / "guard.state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setenv("KAZMA_GUARD_STATE_FILE", str(path))


def _status(**extra: Any) -> dict[str, Any]:
    return {"status": "ok", "ready": True, "image": "kazma-document-worker:src-x",
            "image_ref": "kazma-document-worker:src-x", "image_fallback": False,
            "attestation": {"verified": True, "problems": []},
            "capabilities": {"documents_code_sha256": worker_container._local_code_fingerprint()},
            **extra}


def _report(monkeypatch, status: dict[str, Any] | None) -> dict[str, Any]:
    unreachable = {"status": "unreachable", "ready": False, "error": "ConnectionRefusedError"}
    monkeypatch.setattr(worker_container, "_broker_status", lambda **_: status or unreachable)
    return worker_container.isolation_report()


@pytest.mark.parametrize("case, expected", [
    ("required_process", "use_container"),
    ("required_env_process", "remove_environment_override"),
    ("no_guard_no_url", "run_broker"),
    ("old_guard", "restart_guard"),
    ("image_missing", "build_image"),
    ("docker_down", "start_docker"),
    ("failing", "check_broker_log"),
    ("starting", "wait"),
    ("guard_off", "enable_guard_broker"),
    ("ready_outdated", "build_image"),
    ("ready_fallback", "build_image"),
    ("ready_current", None),
])
def test_readiness_names_the_one_step_that_fixes_it(store, tmp_path, monkeypatch, case, expected):
    if case.startswith("required"):
        store.set(CONTAINER_REQUIRED_KEY, True, category="security")
        if case == "required_env_process":
            monkeypatch.setenv(ENV, "process")
        report = _report(monkeypatch, None)
    else:
        store.set(DOCUMENT_WORKER_ISOLATION_KEY, "container", category="security")
        status = None
        if case == "no_guard_no_url":
            monkeypatch.delenv("KAZMA_GUARD_STATE_FILE", raising=False)
            monkeypatch.delenv("KAZMA_DOCUMENT_BROKER_URL", raising=False)
        else:
            monkeypatch.setenv("KAZMA_DOCUMENT_BROKER_URL", "tcp://127.0.0.1:24567")
            broker = {
                "old_guard": {"state": "idle"},
                "image_missing": {"state": "image_missing", "failures": 0},
                "docker_down": {"state": "docker_unavailable", "failures": 0},
                "failing": {"state": "backoff", "failures": 3},
                "starting": {"state": "starting", "failures": 0},
                "guard_off": {"state": "off"},
            }.get(case, {"state": "running", "failures": 0})
            features = ("pause_held",) if case == "old_guard" else ("pause_held", "document_broker")
            _guard_state(tmp_path, monkeypatch, features=features, broker=broker)
            if case.startswith("ready"):
                status = _status(
                    image_source="0" * 64 if case == "ready_outdated" else broker_supervision.local_build_fingerprint(),
                    image_fallback=case == "ready_fallback",
                )
        report = _report(monkeypatch, status)
    action = report["action"]
    assert (action or {}).get("code") == expected, report
    if expected == "build_image":
        assert "scripts" in action["command"] and "document_worker_image.py build" in action["command"]
    if expected == "check_broker_log":
        assert action["command"].endswith("document_broker.log")


def test_readiness_reports_the_profile_the_supervisor_and_the_action(store, tmp_path, monkeypatch):
    from dataclasses import replace

    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.ingestion import DocumentIngestionService

    store.set(CONTAINER_REQUIRED_KEY, True, category="security")
    service = DocumentIngestionService(config=replace(
        DocumentConfig(storage_root=tmp_path / "ingest"), ocr_enabled=False, security_malware_scan="off"))
    try:
        readiness = service.readiness()
    finally:
        service.close()
    isolation = readiness["worker_isolation"]
    assert isolation["selection"]["mode"] == "process"
    assert isolation["action"]["code"] == "use_container"
    assert "document_worker_isolation_unavailable" in readiness["degraded_reasons"]
    # The reason no longer tells the owner to edit .env for a setting.
    assert "Settings" in isolation["reason"] and "container_required" in isolation["reason"]


def test_no_broker_address_names_the_guard_that_must_be_restarted(store, tmp_path, monkeypatch):
    store.set(DOCUMENT_WORKER_ISOLATION_KEY, "container", category="security")
    monkeypatch.delenv("KAZMA_DOCUMENT_BROKER_URL", raising=False)
    _guard_state(tmp_path, monkeypatch, features=("pause_held",))
    with pytest.raises(DocumentSandboxError) as caught:
        worker_container._endpoint()
    assert "restart the guard" in caught.value.safe_message


# ── the outdated image, once per build ──────────────────────────────────


def test_an_image_older_than_the_code_is_alerted_once_per_build(store, monkeypatch):
    from kazma_core.observability import ops_alerts

    monkeypatch.setenv(ENV, "container")
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(ops_alerts, "alert", lambda key, title, detail="", **kw: alerts.append((key, detail)))
    monkeypatch.setattr(worker_container, "isolation_report", lambda: {
        "action": {"code": "build_image", "command": "python scripts/document_worker_image.py build"},
        "supervisor": {"broker": {"state": "running"}},
    })
    assert broker_supervision._report_outdated_image() is True
    assert broker_supervision._report_outdated_image() is False
    assert broker_supervision._report_outdated_image() is False
    assert len(alerts) == 1 and "document_worker_image.py build" in alerts[0][1]
    # A new build (an update) is a new alert.
    broker_supervision.local_build_fingerprint.cache_clear()
    monkeypatch.setattr(broker_supervision, "local_build_fingerprint", lambda: "f" * 64)
    assert broker_supervision._report_outdated_image() is True
    assert len(alerts) == 2
    # Negative control: without the stamp every maintenance pass alerts again.
    store.delete(broker_supervision.ALERTED_KEY)
    assert broker_supervision._report_outdated_image() is True


def test_a_current_image_is_never_alerted(store, monkeypatch):
    from kazma_core.observability import ops_alerts

    monkeypatch.setenv(ENV, "container")
    alerts: list[str] = []
    monkeypatch.setattr(ops_alerts, "alert", lambda *a, **k: alerts.append("x"))
    monkeypatch.setattr(worker_container, "isolation_report", lambda: {"action": None, "supervisor": {}})
    assert broker_supervision._report_outdated_image() is False and not alerts


def test_the_maintenance_cadence_runs_the_broker_pass() -> None:
    from kazma_core.memory import worker_bootstrap

    labels = [label for label, _ in worker_bootstrap._MAINTENANCE_SWEEPS]
    assert "document worker broker" in labels


def test_the_request_is_published_on_its_own_thread(store, guard_files, monkeypatch):
    seen: list[str] = []

    def record() -> None:
        seen.append(threading.current_thread().name)

    monkeypatch.setattr(broker_supervision, "_publish_broker_request", record)
    broker_supervision._watch_profile_setting(store)
    store.set(DOCUMENT_WORKER_ISOLATION_KEY, "container", category="security")
    broker_supervision._drain_publishes()
    assert seen == ["document-broker-request"], "the writer's thread did the publish itself"
