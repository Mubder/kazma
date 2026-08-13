"""Postgres dump/restore bridge for migration bundles (v2).

Automates the ``pg_dump`` / ``pg_restore`` round-trip that v1 left manual.
Used by the exporter (dump the source DB into the bundle) and the importer
(restore the dump into the target DB).

Design — discover the binary, don't assume an install:
- ``_resolve_pg_dump()`` / ``_resolve_pg_restore()`` try, in order:
  (1) the binary on PATH (``shutil.which``);
  (2) ``docker exec ${KAZMA_DB_CONTAINER:-kazma-db} <bin>`` — the robust
      default for the common Docker-based deployment, where the host has
      no local pg client tools but the DB container always does;
  (3) raise ``PgToolNotFound`` with a clear install hint if neither works.
- The DSN (``KAZMA_DATABASE_URL``) is parsed for connection params so the
  dump/restore connect to the same DB the app uses — no separate config.

Output format: custom (``-Fc``), the only format ``pg_restore`` reads.
~7× smaller than plain text and handles ``bytea`` blobs natively (the
LangGraph ``checkpoint_blobs`` table is ~834 MB of binary).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

__all__ = [
    "PgToolNotFound",
    "PgBridgeError",
    "resolve_pg_dump",
    "resolve_pg_restore",
    "dump_database",
    "restore_database",
]


class PgToolNotFound(RuntimeError):
    """Raised when neither a local pg client nor a docker container is found."""


class PgBridgeError(RuntimeError):
    """Raised when pg_dump/pg_restore runs but exits non-zero."""


@dataclass
class _DsnParts:
    """Parsed components of a Postgres DSN for pg_dump/restore args."""

    host: str
    port: str
    user: str
    password: str
    dbname: str


def _parse_dsn(dsn: str) -> _DsnParts:
    """Parse a ``postgresql://user:pass@host:port/dbname?opts`` DSN.

    libpq accepts the full DSN directly, but we also split out the parts so
    we can pass them as explicit ``--host``/``--port`` etc. to the docker
    invocation (where the DSN's host would be wrong — the container connects
    to localhost, not the host's forwarded port).
    """
    u = urlparse(dsn)
    return _DsnParts(
        host=u.hostname or "localhost",
        port=str(u.port or 5432),
        user=u.username or "kazma",
        password=u.password or "",
        dbname=(u.path or "/kazma").lstrip("/") or "kazma",
    )


# ── Binary discovery ─────────────────────────────────────────────────────


def resolve_pg_dump() -> Sequence[str]:
    """Return the command prefix for invoking ``pg_dump``.

    Tries PATH first, then ``docker exec <container> pg_dump``.
    Raises :class:`PgToolNotFound` if neither is available.
    """
    return _resolve_tool("pg_dump")


def resolve_pg_restore() -> Sequence[str]:
    """Return the command prefix for invoking ``pg_restore``.

    Tries PATH first, then ``docker exec <container> pg_restore``.
    Raises :class:`PgToolNotFound` if neither is available.
    """
    return _resolve_tool("pg_restore")


def _resolve_tool(tool: str) -> Sequence[str]:
    # (1) Local binary on PATH.
    if shutil.which(tool):
        return [tool]
    # (2) Docker container (the common Kazma deployment shape: the DB runs
    # in a container that HAS the client tools, even when the host doesn't).
    container = os.environ.get("KAZMA_DB_CONTAINER", "").strip() or "kazma-db"
    docker = shutil.which("docker")
    if docker:
        # Verify the container is reachable before committing to it (avoids
        # a confusing "docker: not found" deep inside the dump call).
        probe = subprocess.run(
            [docker, "exec", container, "which", tool],
            capture_output=True,
            timeout=15,
        )
        if probe.returncode == 0:
            return [docker, "exec", "-i", container, tool]
    # (3) Neither — clear error.
    hint_path = f"install PostgreSQL client tools (so '{tool}' is on PATH)"
    hint_docker = (
        f"or set KAZMA_DB_CONTAINER to a running container that has '{tool}'"
        if docker
        else "or install Docker so the DB container's client tools can be used"
    )
    raise PgToolNotFound(
        f"'{tool}' not found on PATH and not reachable via docker exec {container}. "
        f"Either {hint_path}, {hint_docker}."
    )


# ── Dump / restore ───────────────────────────────────────────────────────


def dump_database(
    dsn: str,
    out_path: str | Path,
    *,
    progress=None,
    tables: Sequence[str] | None = None,
) -> Path:
    """Dump the database at ``dsn`` to ``out_path`` in custom format (-Fc).

    Args:
        dsn: the source ``postgresql://...`` URL (from KAZMA_DATABASE_URL).
        out_path: destination file (overwritten).
        progress: optional callback ``(phase: str)`` for status messages.
        tables: optional explicit table names to dump (one ``-t`` per name).
            None (default) dumps the whole database. The nightly PG backup
            passes its SoT list so a shared DB never leaks a foreign app's
            tables into Kazma's backups.

    Returns:
        The Path to the written dump.

    Raises:
        PgToolNotFound: if pg_dump can't be found.
        PgBridgeError: if pg_dump exits non-zero.

    Note: when pg_dump is invoked via ``docker exec`` (the common case), the
    command runs INSIDE the DB container, so ``dsn``'s host/port (which point
    at the HOST's forwarded port, e.g. 127.0.0.1:5433) are wrong — the
    container's Postgres listens on localhost:5432 internally. We detect the
    docker case and override host/port to the container defaults.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    parts = _parse_dsn(dsn)
    dump_cmd = resolve_pg_dump()
    conn_parts = _docker_adjusted_parts(dump_cmd, parts)
    cmd = list(dump_cmd) + [
        "--no-owner",
        "--no-privileges",
        "-Fc",  # custom format — the only format pg_restore reads
    ]
    for table in tables or ():
        cmd += ["-t", table]
    cmd += [
        "-U", conn_parts.user,
        "-h", conn_parts.host,
        "-p", conn_parts.port,
        conn_parts.dbname,
    ]
    env = _libpq_env(parts)
    if progress:
        progress(f"pg_dump {conn_parts.dbname}@{conn_parts.host}:{conn_parts.port} -> {out_path.name}")
    logger.info("[pg_bridge] dump: %s", " ".join(cmd[:-1] + [_redact(cmd[-1])]))
    with open(out_path, "wb") as f:
        proc = subprocess.run(
            cmd, env=env, stdout=f, stderr=subprocess.PIPE, timeout=1800
        )
    if proc.returncode != 0:
        out_path.unlink(missing_ok=True)
        raise PgBridgeError(
            f"pg_dump failed (exit {proc.returncode}): "
            f"{proc.stderr.decode(errors='replace').strip()[:500]}"
        )
    size_mb = out_path.stat().st_size / (1024 * 1024)
    logger.info("[pg_bridge] dump wrote %s (%.1f MB)", out_path.name, size_mb)
    return out_path


def restore_database(
    dump_path: str | Path,
    dsn: str,
    *,
    progress=None,
) -> int:
    """Restore a custom-format dump into the database at ``dsn``.

    Uses ``--clean --if-exists`` so the restore is idempotent (safe to
    re-run). ``pg_restore`` recreates the schema itself, so the target DB
    can be empty (no manual table creation needed).

    Args:
        dump_path: the ``-Fc`` dump file.
        dsn: the target ``postgresql://...`` URL.
        progress: optional callback ``(phase: str)``.

    Returns:
        The number of warnings pg_restore emitted (0 = clean restore).

    Raises:
        PgToolNotFound: if pg_restore can't be found.
        PgBridgeError: if pg_restore exits non-zero (warnings are NOT fatal;
            pg_restore returns 1 for non-fatal warnings like "relation does
            not exist" during --clean on a fresh DB).
    """
    dump_path = Path(dump_path)
    if not dump_path.exists():
        raise PgBridgeError(f"dump file not found: {dump_path}")
    parts = _parse_dsn(dsn)
    restore_cmd = resolve_pg_restore()
    conn_parts = _docker_adjusted_parts(restore_cmd, parts)
    cmd = list(restore_cmd) + [
        "--no-owner",
        "--no-privileges",
        "--clean",
        "--if-exists",
        "-U", conn_parts.user,
        "-h", conn_parts.host,
        "-p", conn_parts.port,
        "-d", conn_parts.dbname,
    ]
    env = _libpq_env(parts)
    if progress:
        progress(f"pg_restore {dump_path.name} -> {conn_parts.dbname}@{conn_parts.host}:{conn_parts.port}")
    logger.info("[pg_bridge] restore: %s", " ".join(cmd[:-1] + [_redact(cmd[-1])]))
    with open(dump_path, "rb") as f:
        proc = subprocess.run(
            cmd, env=env, stdin=f, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=3600,
        )
    stderr = proc.stderr.decode(errors="replace")
    # pg_restore returns 0 (clean), 1 (non-fatal warnings, e.g. "relation
    # does not exist" during --clean), or 2+ (fatal). Treat 0 and 1 as
    # success; only 2+ is a real failure.
    if proc.returncode >= 2:
        raise PgBridgeError(
            f"pg_restore failed (exit {proc.returncode}): {stderr.strip()[:500]}"
        )
    warning_count = sum(1 for line in stderr.splitlines() if line.strip())
    logger.info(
        "[pg_bridge] restore complete (exit %d, %d stderr lines)",
        proc.returncode,
        warning_count,
    )
    return warning_count


# ── Helpers ──────────────────────────────────────────────────────────────


def _libpq_env(parts: _DsnParts) -> dict[str, str]:
    """Build the environment for pg_dump/restore, injecting the password.

    libpq reads PGPASSWORD so we don't expose it on the command line (which
    would leak via process listings). Inherits the rest of os.environ.
    """
    env = dict(os.environ)
    if parts.password:
        env["PGPASSWORD"] = parts.password
    return env


def _docker_adjusted_parts(tool_cmd: Sequence[str], parts: _DsnParts) -> _DsnParts:
    """Return connection params adjusted for whether the tool runs in Docker.

    When pg_dump/pg_restore are invoked via ``docker exec``, the process runs
    INSIDE the DB container, where Postgres listens on ``localhost:5432`` —
    NOT the host's forwarded port (e.g. ``127.0.0.1:5433``) that the DSN
    points at. Connecting to the DSN's host/port from inside the container
    fails with "connection refused". So when the tool command starts with
    ``docker exec``, override host→localhost and port→5432 (the container
    internal default); leave a local-binary invocation untouched.

    Override the internal port with ``KAZMA_DB_INTERNAL_PORT`` if your
    container listens elsewhere.
    """
    if len(tool_cmd) >= 2 and "docker" in tool_cmd[0].lower() and tool_cmd[1] == "exec":
        internal_port = os.environ.get("KAZMA_DB_INTERNAL_PORT", "5432").strip() or "5432"
        return _DsnParts(
            host="localhost",
            port=internal_port,
            user=parts.user,
            password=parts.password,
            dbname=parts.dbname,
        )
    return parts


def _redact(s: str) -> str:
    """Redact a potential secret in a command-line token for log safety."""
    return s if len(s) <= 4 else s[:2] + "…" if any(c in s for c in ":/@") else s
