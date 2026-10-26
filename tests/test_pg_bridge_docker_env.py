"""docker exec must receive PGPASSWORD (audit M-9)."""

from __future__ import annotations

from kazma_core.migration.pg_bridge import (
    _DsnParts,
    _redact_cmd,
    _with_docker_pgpassword,
)

import pytest

# Verified against a real Postgres; the CI Postgres job runs every test
# carrying this marker (scripts/postgres_suite.py).
pytestmark = pytest.mark.postgres


def test_docker_exec_injects_pgpassword() -> None:
    parts = _DsnParts(
        host="127.0.0.1",
        port="5433",
        user="kazma",
        password="s3cret",
        dbname="kazma",
    )
    cmd = ["docker", "exec", "-i", "kazma-db", "pg_dump", "-U", "kazma"]
    out = _with_docker_pgpassword(cmd, parts)
    # The name alone: docker forwards the value from its own environment
    # (_libpq_env), so the password is never in the argv (audit 2026-10-26
    # AUD-091: a process listing, and a timeout's message, showed it).
    assert out[:5] == ["docker", "exec", "-i", "-e", "PGPASSWORD"]
    assert "s3cret" not in " ".join(out)
    assert "kazma-db" in out
    assert "pg_dump" in out


def test_a_timeout_carries_no_command(monkeypatch, tmp_path) -> None:
    import subprocess

    from kazma_core.migration import pg_bridge

    def hang(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(pg_bridge.subprocess, "run", hang)
    cmd = ["docker", "exec", "-i", "-e", "PGPASSWORD=s3cret", "kazma-db", "pg_dump"]
    with pytest.raises(pg_bridge.PgBridgeError) as err:
        pg_bridge._run_bounded(cmd, what="pg_dump", timeout=1800)
    assert "s3cret" not in str(err.value) and "docker" not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_negative_control_the_raw_timeout_prints_the_argv() -> None:
    import subprocess

    cmd = ["docker", "exec", "-e", "PGPASSWORD=s3cret", "kazma-db", "pg_dump"]
    assert "s3cret" in str(subprocess.TimeoutExpired(cmd, 1800))


def test_local_binary_is_untouched() -> None:
    parts = _DsnParts("127.0.0.1", "5432", "u", "pw", "db")
    cmd = ["pg_dump", "-U", "u", "db"]
    assert _with_docker_pgpassword(cmd, parts) == cmd


def test_redact_cmd_hides_password() -> None:
    cmd = ["docker", "exec", "-i", "-e", "PGPASSWORD=s3cret", "kazma-db", "pg_restore"]
    logged = " ".join(_redact_cmd(cmd))
    assert "s3cret" not in logged
    assert "PGPASSWORD=***" in logged
