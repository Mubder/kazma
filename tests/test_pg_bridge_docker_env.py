"""docker exec must receive PGPASSWORD (audit M-9)."""

from __future__ import annotations

from kazma_core.migration.pg_bridge import (
    _DsnParts,
    _redact_cmd,
    _with_docker_pgpassword,
)


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
    assert out[:5] == ["docker", "exec", "-i", "-e", "PGPASSWORD=s3cret"]
    assert "kazma-db" in out
    assert "pg_dump" in out


def test_local_binary_is_untouched() -> None:
    parts = _DsnParts("127.0.0.1", "5432", "u", "pw", "db")
    cmd = ["pg_dump", "-U", "u", "db"]
    assert _with_docker_pgpassword(cmd, parts) == cmd


def test_redact_cmd_hides_password() -> None:
    cmd = ["docker", "exec", "-i", "-e", "PGPASSWORD=s3cret", "kazma-db", "pg_restore"]
    logged = " ".join(_redact_cmd(cmd))
    assert "s3cret" not in logged
    assert "PGPASSWORD=***" in logged
