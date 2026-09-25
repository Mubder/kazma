"""Postgres dumps must not stop because Docker's CLI fell off PATH.

Live 2026-09-25 07:53Z: "[Ops] Postgres dump failed — native_pg_backup produced
no dump". Docker Desktop had updated itself to 4.91.0 overnight (docker.exe
rewritten 01:35, reboot 01:38) and left its ``resources\\bin`` folder off the
system PATH. The KazmaAgent task, the guard and the server all start from the
registry environment, so ``shutil.which("docker")`` failed and pg_dump (which
runs inside the database container through ``docker exec``) could not be
reached. Docker and the container were fine the whole time. The alert said
nothing about any of that; the reason was one line in a traceback.

Three fixes, tested here: docker is found off PATH (``kazma_core.docker_cli``),
the alert names the reason, and boot checks the dump tool instead of the first
dump discovering it six hours later.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import subprocess
import textwrap
from pathlib import Path

import pytest
from kazma_core import docker_cli
from kazma_core.db import pg_backup
from kazma_core.migration import pg_bridge

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    """A docker CLI that exists only at an install location, not on PATH."""
    exe = tmp_path / "Docker" / "resources" / "bin" / "docker.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.delenv("KAZMA_DOCKER_BIN", raising=False)
    monkeypatch.setattr(docker_cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(docker_cli, "_candidates", lambda: [tmp_path / "missing.exe", exe])
    monkeypatch.setattr(docker_cli, "_reported", set())
    return exe


# ── finding the CLI ───────────────────────────────────────────────────────


def test_an_off_path_install_is_found_and_said_once(fake_docker, caplog):
    caplog.set_level(logging.WARNING, logger=docker_cli.__name__)
    assert docker_cli.find_docker_cli() == str(fake_docker)
    assert docker_cli.find_docker_cli() == str(fake_docker)
    lines = [r.getMessage() for r in caplog.records if "not on PATH" in r.getMessage()]
    assert len(lines) == 1 and "KAZMA_DOCKER_BIN" in lines[0]


def test_path_wins_over_the_install_folders(fake_docker, monkeypatch):
    monkeypatch.setattr(docker_cli.shutil, "which", lambda name: "/opt/bin/docker")
    assert docker_cli.find_docker_cli() == "/opt/bin/docker"


def test_an_explicit_path_wins_over_everything(fake_docker, tmp_path, monkeypatch):
    mine = tmp_path / "my-docker"
    mine.write_bytes(b"")
    monkeypatch.setenv("KAZMA_DOCKER_BIN", str(mine))
    monkeypatch.setattr(docker_cli.shutil, "which", lambda name: "/opt/bin/docker")
    assert docker_cli.find_docker_cli() == str(mine)


def test_a_wrong_explicit_path_falls_back_loudly(fake_docker, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("KAZMA_DOCKER_BIN", str(tmp_path / "nope"))
    caplog.set_level(logging.WARNING, logger=docker_cli.__name__)
    assert docker_cli.find_docker_cli() == str(fake_docker)
    assert any("does not exist" in r.getMessage() for r in caplog.records)


def test_nothing_found_is_none(monkeypatch, tmp_path):
    monkeypatch.delenv("KAZMA_DOCKER_BIN", raising=False)
    monkeypatch.setattr(docker_cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(docker_cli, "_candidates", lambda: [tmp_path / "missing.exe"])
    assert docker_cli.find_docker_cli() is None


# ── the dump path uses it ─────────────────────────────────────────────────


def _probe_ok(monkeypatch, seen):
    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"/usr/bin/pg_dump", b"")

    monkeypatch.setattr(pg_bridge.subprocess, "run", fake_run)
    monkeypatch.setattr(pg_bridge.shutil, "which", lambda name: None)  # no host pg_dump
    monkeypatch.setenv("KAZMA_DB_CONTAINER", "kazma-db-win")


def test_pg_dump_is_reached_through_an_off_path_docker(fake_docker, monkeypatch):
    seen: list = []
    _probe_ok(monkeypatch, seen)
    prefix = pg_bridge.resolve_pg_dump()
    assert prefix == [str(fake_docker), "exec", "-i", "kazma-db-win", "pg_dump"]
    assert seen[0][:4] == [str(fake_docker), "exec", "kazma-db-win", "which"]


def test_negative_control_the_path_only_lookup_was_the_incident(fake_docker, monkeypatch):
    """With the old lookup the same machine cannot dump -- the 07:53Z alert."""
    _probe_ok(monkeypatch, [])
    monkeypatch.setattr(docker_cli, "find_docker_cli", lambda: docker_cli.shutil.which("docker"))
    with pytest.raises(pg_bridge.PgToolNotFound) as exc:
        pg_bridge.resolve_pg_dump()
    assert "KAZMA_DOCKER_BIN" in str(exc.value), "the hint names the override"


def test_the_python_exec_jail_uses_the_same_lookup(fake_docker):
    from kazma_core.tools import code_exec

    assert code_exec._docker_cli() == str(fake_docker)


# ── the alert says why ────────────────────────────────────────────────────


@pytest.fixture
def pg_on(tmp_path, monkeypatch):
    monkeypatch.setattr(pg_backup, "pg_backup_enabled", lambda: True)
    monkeypatch.setattr(pg_backup, "pg_backup_dir", lambda: tmp_path / "pg")
    monkeypatch.setattr("kazma_core.db.backend.get_database_url",
                        lambda: "postgresql://kazma:s3cr3t@localhost:5433/kazma")
    pg_backup._record_failure(None)
    yield
    pg_backup._record_failure(None)


def test_a_failed_dump_records_its_reason_and_a_good_one_clears_it(pg_on, monkeypatch):
    def refuse(dsn, out, *, tables=None, progress=None):
        raise pg_bridge.PgToolNotFound(
            "'pg_dump' not found on PATH and not reachable via docker exec kazma-db-win "
            "(connect postgresql://kazma:s3cr3t@localhost:5433/kazma)"
        )

    monkeypatch.setattr(pg_bridge, "dump_database", refuse)
    assert pg_backup.perform_pg_backup() is None
    reason = pg_backup.last_pg_backup_failure()
    assert reason and reason.startswith("PgToolNotFound:") and "docker exec kazma-db-win" in reason
    assert "s3cr3t" not in reason, "a DSN password must never reach an alert"

    def write_dump(dsn, out, *, tables=None, progress=None):
        Path(out).write_bytes(b"PGDMP" + b"\0" * 2048)
        return Path(out)

    monkeypatch.setattr(pg_bridge, "dump_database", write_dump)
    assert pg_backup.perform_pg_backup() is not None
    assert pg_backup.last_pg_backup_failure() is None


def test_the_ops_alert_carries_the_reason(pg_on, monkeypatch):
    from kazma_core.memory import worker_bootstrap

    sent: list[tuple] = []
    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert",
                        lambda key, title, detail, **kw: sent.append((key, title, detail)))

    def failing_backup(**kwargs):
        pg_backup._record_failure("PgToolNotFound: 'pg_dump' not found on PATH")
        return None

    monkeypatch.setattr(pg_backup, "perform_pg_backup", failing_backup)
    assert asyncio.run(worker_bootstrap._handle_native_pg_backup({})) is False
    ((key, title, detail),) = sent
    assert key == "backup.pg_dump" and title == "Postgres dump failed"
    assert "produced no dump: PgToolNotFound: 'pg_dump' not found on PATH" in detail


# ── boot checks the tool ──────────────────────────────────────────────────


def test_the_boot_check_names_a_missing_tool(monkeypatch):
    def missing():
        raise pg_bridge.PgToolNotFound("'pg_dump' not found on PATH")

    monkeypatch.setattr(pg_bridge, "resolve_pg_dump", missing)
    assert "pg_dump" in (pg_backup.pg_dump_tool_problem() or "")
    monkeypatch.setattr(pg_bridge, "resolve_pg_dump", lambda: ["pg_dump"])
    assert pg_backup.pg_dump_tool_problem() is None


def test_boot_runs_the_tool_check_off_the_loop():
    src = (REPO / "kazma-ui" / "kazma_ui" / "app.py").read_text(encoding="utf-8")
    assert "await _aio.to_thread(pg_dump_tool_problem)" in src
    assert '"backup.pg_tools"' in src


# ── one lookup for the docker CLI ─────────────────────────────────────────


def _which_docker_calls(sources: dict[str, str]) -> list[str]:
    hits: list[str] = []
    for rel, text in sources.items():
        if rel.endswith("kazma_core/docker_cli.py"):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == "which"
                and node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in ("docker", "docker.exe")
            ):
                hits.append(f"{rel}:{node.lineno}")
    return hits


def test_nothing_else_looks_docker_up_on_path_alone():
    sources = {}
    for pkg in ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-skills", "kazma-tui"):
        for p in (REPO / pkg).rglob("*.py"):
            if "tests" in p.parts or "__pycache__" in p.parts:
                continue
            sources[p.relative_to(REPO).as_posix()] = p.read_text(encoding="utf-8", errors="replace")
    hits = _which_docker_calls(sources)
    assert not hits, (
        "shutil.which('docker') alone misses a CLI a Docker Desktop update took off\n"
        "PATH (2026-09-25). Use kazma_core.docker_cli.find_docker_cli():\n  " + "\n  ".join(hits)
    )


def test_the_docker_lookup_gate_sees_a_path_only_call():
    planted = {"x.py": textwrap.dedent('''
        import shutil
        def docker():
            return shutil.which("docker")
    ''')}
    assert _which_docker_calls(planted) == ["x.py:4"]
