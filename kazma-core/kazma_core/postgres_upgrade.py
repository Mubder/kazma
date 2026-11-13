"""Move Kazma's database to a newer Postgres major (2026-11-13).

``kazma setup postgres --upgrade`` runs this on an install whose database is
the OS service ``kazma setup postgres`` set up (its record names the major,
16 for every install made before 18). A Postgres major cannot open another
major's data folder, so the new one is a second server, filled with a copy:

1. the new major is installed beside the old one, on a spare port
   (:data:`TEMP_PORT_FIRST` or the next free one), with Kazma's role, the
   password Kazma already uses, and its own WAL archive folder;
2. Kazma is stopped through its guard once no chat turn runs (nothing writes
   while the data is copied), as the move from a container is;
3. :func:`copy_database`: ``pg_dump`` (the new major's) of Kazma's database,
   ``pg_restore`` into the new server as ``kazma_app`` in one transaction,
   then every table's row count and every sequence compared;
4. the switch (:func:`switch_steps`): the new server set to the old port, the
   old one stopped and taken off the boot list (kept, with its data: the way
   back), the new one started on the old port. Every address Kazma knows
   (``.env``, the memory mirror's) stays as it is;
5. the record names the new server, with the old one under ``previous``
   (a restore to a time before the move reads its archive), and Kazma is
   started and seen signed in.

Every stage is written to the record first (``postgres_upgrade``, the
journal). A run that stops anywhere is safe to run again: before the switch
the old server is the one Kazma uses and the new one holds only a copy
(replaced only while Kazma has never started on it, ``backup.pg_move``'s
rule); a switch that did not finish is undone (:func:`undo_steps`: the old
server back on its port and on the boot list) before anything else. The
guard's database watch leaves the service alone while a switch is journaled
(for at most :data:`SWITCH_HOLD_S`), and the record names the server about to
be on the port before any server is stopped, so the watch never starts the
other one.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from kazma_core import postgres_service as _pgs
from kazma_core.postgres_service import (
    PG_MAJOR,
    Platform,
    Step,
    bin_dir_for,
    port_step,
    read_record,
    service_name,
)

__all__ = [
    "JOURNAL_KEY",
    "SWITCH_HOLD_S",
    "TEMP_PORT_FIRST",
    "Upgrade",
    "clear_journal",
    "copy_database",
    "journal",
    "plan_upgrade",
    "server_major",
    "switch_steps",
    "undo_steps",
    "with_port",
    "write_journal",
    "write_postgres_record",
]

#: The journal's key in ``setup_record.json``.
JOURNAL_KEY = "postgres_upgrade"
#: Where the search for a spare port for the new server starts.
TEMP_PORT_FIRST = 55432
#: How long the guard's database watch leaves the service alone while a
#: switch is journaled (a run that died in the switch is not waited for).
SWITCH_HOLD_S = 15 * 60.0


@dataclass(frozen=True)
class Upgrade:
    """One move from the major the record names to *new_major*."""

    platform: Platform
    old: dict[str, Any]
    new_major: int
    port: int
    temp_port: int

    @property
    def old_major(self) -> int:
        return int(self.old.get("major") or 0)

    @property
    def old_service(self) -> str:
        return str(self.old.get("service") or "")

    @property
    def new_service(self) -> str:
        return str(service_name(self.platform.os, self.new_major) or "")

    @property
    def new_bin_dir(self) -> str:
        return bin_dir_for(self.platform, self.new_major)


def plan_upgrade(platform: Platform, old: dict[str, Any], *, new_major: int = PG_MAJOR,
                 temp_port: int | None = None) -> Upgrade:
    """The move from the server *old* (the record) to *new_major*. Raises
    ValueError when there is nothing to move or no way to."""
    if not old.get("service"):
        raise ValueError("Kazma set up no Postgres service here (kazma setup postgres sets one up)")
    major = int(old.get("major") or 0)
    if major >= new_major:
        raise ValueError(f"the database already runs PostgreSQL {major}")
    if platform.manager is None:
        raise ValueError(f"no package manager Kazma knows on this {platform.os} system")
    if platform.os == "debian" and major and service_name("debian", major) != old.get("service"):
        raise ValueError(f"the record names {old.get('service')}, not Debian's postgresql service")
    port = int(old.get("port") or 5432)
    return Upgrade(platform, dict(old), new_major, port, temp_port or _free_port())


def _free_port(first: int = TEMP_PORT_FIRST, *, answers: Callable[[int], bool] | None = None) -> int:
    """The first port from *first* nothing listens on and this machine lets
    a program bind."""
    for port in range(first, first + 200):
        if answers is not None:
            if not answers(port):
                return port
            continue
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                continue
        except OSError:
            pass
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    raise ValueError(f"no free port from {first} to {first + 199}")


# -- the switch ------------------------------------------------------------------------------


def _not_at_boot(p: Platform, service: str, major: int) -> list[Step]:
    if p.os == "windows":
        return [Step(f"PostgreSQL {major} no longer starts at boot (kept: the way back)",
                     ("sc", "config", service, "start=", "demand"), admin=True)]
    if p.os == "debian":
        return [Step(f"PostgreSQL {major} no longer starts at boot (kept: the way back)",
                     ("sudo", "sh", "-c", f"echo manual > /etc/postgresql/{major}/main/start.conf"), admin=True)]
    if p.os == "fedora":
        return [Step(f"PostgreSQL {major} no longer starts at boot (kept: the way back)",
                     ("sudo", "systemctl", "disable", service), admin=True)]
    return []          # macOS: brew services stop takes it off the boot list


def _at_boot(p: Platform, service: str, major: int) -> list[Step]:
    if p.os == "windows":
        return [Step(f"PostgreSQL {major} starts at boot", ("sc", "config", service, "start=", "auto"), admin=True)]
    if p.os == "debian":
        return [Step(f"PostgreSQL {major} starts at boot",
                     ("sudo", "sh", "-c", f"echo auto > /etc/postgresql/{major}/main/start.conf"), admin=True)]
    if p.os == "fedora":
        return [Step(f"PostgreSQL {major} starts at boot", ("sudo", "systemctl", "enable", service), admin=True)]
    return []          # macOS: brew services start puts it on the boot list


def switch_steps(up: Upgrade) -> list[Step]:
    """The new server onto the old port, the old one stopped and kept."""
    p = up.platform
    return [
        port_step(p, up.new_major, up.port),
        Step(f"Stop PostgreSQL {up.new_major} (it moves to port {up.port})", action="stop",
             service=up.new_service, major=up.new_major, admin=True),
        Step(f"Stop PostgreSQL {up.old_major} (Kazma's database until now)", action="stop",
             service=up.old_service, major=up.old_major, admin=True),
        *_not_at_boot(p, up.old_service, up.old_major),
        Step(f"Start PostgreSQL {up.new_major} on port {up.port}", action="start",
             service=up.new_service, major=up.new_major, port=up.port, admin=True),
    ]


def undo_steps(up: Upgrade) -> list[Step]:
    """The old server back on its port and on the boot list; the new one
    stopped, off the boot list and set to its spare port again."""
    p = up.platform
    return [
        Step(f"Stop PostgreSQL {up.new_major}", action="stop", service=up.new_service, major=up.new_major,
             admin=True),
        *_not_at_boot(p, up.new_service, up.new_major),
        port_step(p, up.new_major, up.temp_port),
        *_at_boot(p, up.old_service, up.old_major),
        Step(f"Start PostgreSQL {up.old_major} on port {up.port}", action="start",
             service=up.old_service, major=up.old_major, port=up.port, admin=True),
    ]


# -- the journal -------------------------------------------------------------------------------


def journal(path: Path | None = None) -> dict[str, Any]:
    """The upgrade in progress (empty: none)."""
    entry = read_record(path).get(JOURNAL_KEY)
    return entry if isinstance(entry, dict) else {}


def write_journal(entry: dict[str, Any], path: Path | None = None) -> None:
    """The journal written whole, beside the record (atomically)."""
    from kazma_core.atomic_files import atomic_write_text

    target = path or _pgs._record_path()
    data = read_record(target)
    data[JOURNAL_KEY] = {**entry, "updated": time.time()}
    atomic_write_text(target, json.dumps(data, indent=2))


def clear_journal(path: Path | None = None) -> None:
    from kazma_core.atomic_files import atomic_write_text

    target = path or _pgs._record_path()
    data = read_record(target)
    if data.pop(JOURNAL_KEY, None) is not None:
        atomic_write_text(target, json.dumps(data, indent=2))


def write_postgres_record(rec: dict[str, Any], path: Path | None = None) -> None:
    """The record's ``postgres`` entry replaced by *rec* (whole, atomically)."""
    from kazma_core.atomic_files import atomic_write_text

    target = path or _pgs._record_path()
    data = read_record(target)
    data.update({"version": 1, "postgres": dict(rec)})
    atomic_write_text(target, json.dumps(data, indent=2))


# -- the copy ------------------------------------------------------------------------------------


def with_port(url: str, port: int) -> str:
    """*url* with its port replaced."""
    parts = urlsplit(url)
    userinfo, _, _host = parts.netloc.rpartition("@")
    host = parts.hostname or "127.0.0.1"
    netloc = f"{userinfo + '@' if userinfo else ''}{host}:{int(port)}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def server_major(url: str) -> int | None:
    """The major of the server at *url* (None: it could not be asked)."""
    import psycopg

    try:
        with psycopg.connect(url, connect_timeout=10, autocommit=True) as conn:
            return int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000
    except (psycopg.Error, ValueError, TypeError):
        return None


def _snapshot(url: str) -> tuple[dict[str, int], dict[str, int]]:
    """Every public table's row count and every public sequence's last value."""
    import psycopg

    with psycopg.connect(url, connect_timeout=10, autocommit=True) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename").fetchall()]
        counts = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            sql = f"SELECT count(*) FROM public.{quoted}"  # noqa: S608  # nosec B608 - a name from pg_tables, quoted
            counts[table] = int(conn.execute(sql).fetchone()[0])
        sequences = {r[0]: int(r[1] or 0) for r in conn.execute(
            "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'public'").fetchall()}
    return counts, sequences


def _tool(bin_dir: str, name: str) -> list[str]:
    return [str(Path(bin_dir) / (f"{name}.exe" if os.name == "nt" else name))]


def copy_database(source_url: str, dest_url: str, *, bin_dir: str, say: Callable[[str], None] = print,
                  run=subprocess.run, empty_target: Callable[[], bool] | None = None,
                  tools: Callable[[str], list[str]] | None = None) -> bool:
    """Copy Kazma's database from *source_url* into *dest_url* (both as
    ``kazma_app``) with the new major's ``pg_dump`` and ``pg_restore``. True
    when every public table holds the same rows and every sequence the same
    value on both. An earlier copy on the destination is replaced through
    *empty_target* only when Kazma never started on it (``backup.pg_move``)."""
    import psycopg

    from kazma_core.backup import pg_move
    from kazma_core.db.pg_backup import KAZMA_PG_TABLES
    from kazma_core.security.child_env import tool_child_env

    tools = tools or (lambda name: _tool(bin_dir, name))
    try:
        source_counts, source_sequences = _snapshot(source_url)
    except (psycopg.Error, OSError) as exc:
        say(f"Kazma's database could not be read ({type(exc).__name__}): nothing was changed.")
        return False
    try:
        held, target_boot, others = pg_move._target_state(dest_url)
    except (OSError, ImportError) as exc:
        say(f"The new server could not be read ({exc}): nothing was changed.")
        return False
    replace = bool(held & (set(KAZMA_PG_TABLES) | set(source_counts)))
    if replace:
        source_boot = None
        try:
            with psycopg.connect(source_url, connect_timeout=10, autocommit=True) as conn:
                row = conn.execute("SELECT value FROM kazma_settings WHERE key = %s", (pg_move.BOOT_KEY,)).fetchone()
            source_boot = pg_move._boot_time(row[0]) if row else None
        except (psycopg.Error, OSError):
            source_boot = None          # no stamp: the refusal below says so
        refusal = pg_move._replace_refusal(source_boot, target_boot, others)
        if refusal:
            say(refusal)
            return False
        if empty_target is None:
            say("The new server holds an earlier copy, and nothing was given to empty it: nothing was changed.")
            return False

    src, dst = urlsplit(source_url), urlsplit(dest_url)
    with tempfile.TemporaryDirectory(prefix="kazma-pg-upgrade-") as tmp:
        dump = Path(tmp) / "kazma.dump"
        say(f"-> Dumping Kazma's database from PostgreSQL on port {src.port}")
        env = {**tool_child_env(), "PGPASSWORD": unquote(src.password or "")}
        done = run([*tools("pg_dump"), "-h", src.hostname or "127.0.0.1", "-p", str(src.port or 5432),
                    "-U", unquote(src.username or ""), "-d", (src.path or "/kazma").lstrip("/"), "-Fc",
                    "--no-owner", "--no-privileges", "--no-password", "-f", str(dump)],
                   stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=3600, check=False)
        if done.returncode != 0 or not dump.is_file() or dump.stat().st_size == 0:
            said = ((done.stderr or "").strip().splitlines() or [""])[-1][:300]
            say(f"The dump failed (exit {done.returncode}): {said}")
            return False
        if replace:
            say("-> The new server holds an earlier copy Kazma never started on: replacing it")
            if not empty_target():
                say("Emptying the new server's database failed.")
                return False
        say(f"-> Restoring it into PostgreSQL on port {dst.port} ({dump.stat().st_size // 1024} KiB)")
        env = {**tool_child_env(), "PGPASSWORD": unquote(dst.password or "")}
        done = run([*tools("pg_restore"), "-h", dst.hostname or "127.0.0.1", "-p", str(dst.port or 5432),
                    "-U", unquote(dst.username or ""), "-d", (dst.path or "/kazma").lstrip("/"),
                    "--no-owner", "--no-privileges", "--single-transaction", "--exit-on-error",
                    "--no-password", str(dump)],
                   stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=3600, check=False)
        if done.returncode != 0:
            said = ((done.stderr or "").strip().splitlines() or [""])[-1][:300]
            say(f"The restore failed (exit {done.returncode}): {said}")
            return False

    try:
        dest_counts, dest_sequences = _snapshot(dest_url)
    except (psycopg.Error, OSError) as exc:
        say(f"The new server could not be read after the restore ({type(exc).__name__}).")
        return False
    differ = sorted(t for t in source_counts if source_counts[t] != dest_counts.get(t))
    seq_differ = sorted(s for s in source_sequences if source_sequences[s] != dest_sequences.get(s))
    for t in differ:
        say(f"   DIFFERS {t}: {source_counts[t]} -> {dest_counts.get(t)}")
    for s in seq_differ:
        say(f"   DIFFERS sequence {s}: {source_sequences[s]} -> {dest_sequences.get(s)}")
    if differ or seq_differ:
        say(f"{len(differ)} table(s) and {len(seq_differ)} sequence(s) differ: the copy is not whole.")
        return False
    rows = sum(source_counts.values())
    say(f"-> Every one of {len(source_counts)} tables ({rows} rows) and {len(source_sequences)} sequences "
        "is the same on the new server.")
    return True
