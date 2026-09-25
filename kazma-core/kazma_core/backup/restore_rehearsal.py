"""Restore the newest Postgres dump into a scratch database, check it, drop it.

The restore drill proves the archive READS: its table of contents parses
(daily) and every data block decompresses (weekly). Neither proves it
RESTORES -- that the schema recreates, the rows load, and the tables Kazma
needs come back queryable. The only proof of that is a restore, so this does
one, into a database of its own.

It is the one backup check that writes to the database server, so:

* **Opt-in, default OFF.** ``backups.pg.restore_rehearsal`` (ConfigStore) or
  ``KAZMA_PG_RESTORE_REHEARSAL=1``; ``KAZMA_PG_RESTORE_REHEARSAL=0`` vetoes the
  setting. Postgres installs only.
* **Scratch only.** It creates ``kazma_restore_rehearsal_<epoch>`` on the same
  server, restores into that, and drops it. Every CREATE and DROP re-checks the
  name against a strict pattern and refuses one equal to the live database.
* **Self-cleaning.** A crash between CREATE and DROP leaves a database the next
  run removes -- one matching the pattern and older than a day, nothing else.
* **Honest about permissions.** A user without CREATEDB is reported as
  UNVERIFIED with the grant to add, not as a failed backup.

Runs inside the weekly deep drill when enabled (``restore_drill.run_deep_drill``).
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

if TYPE_CHECKING:
    from kazma_core.backup.restore_drill import DrillResult

logger = logging.getLogger(__name__)

__all__ = [
    "SCRATCH_PREFIX",
    "rehearsal_enabled",
    "rehearse_pg_restore",
]

SCRATCH_PREFIX = "kazma_restore_rehearsal_"
_SCRATCH_RE = re.compile(rf"^{SCRATCH_PREFIX}(\d{{10}})$")
_CONFIG_KEY = "backups.pg.restore_rehearsal"
_ENV = "KAZMA_PG_RESTORE_REHEARSAL"
_STALE_AFTER_S = 86400
_CONNECT_TIMEOUT_S = 10
_CHECK = "postgres:restore"


class _RehearsalRefused(RuntimeError):
    """A guard rail said no. Never caught to proceed anyway."""


def rehearsal_enabled() -> bool:
    """Opt-in. The env var decides when set; otherwise the setting; default off."""
    from kazma_core.db.backend import is_postgres

    if not is_postgres():
        return False
    raw = (os.environ.get(_ENV) or "").strip().lower()
    if raw in ("0", "false", "off", "no"):
        return False
    if raw in ("1", "true", "on", "yes"):
        return True
    try:
        from kazma_core.config_store import get_config_store

        return bool(get_config_store().get(_CONFIG_KEY, False))
    except (RuntimeError, OSError, ValueError, TypeError):
        logger.debug("[restore-rehearsal] setting unreadable -- treated as off", exc_info=True)
        return False


def _scratch_name(now: float) -> str:
    return f"{SCRATCH_PREFIX}{int(now):010d}"


def _dbname(dsn: str) -> str:
    return (urlparse(dsn).path or "/").lstrip("/")


def _with_dbname(dsn: str, dbname: str) -> str:
    u = urlparse(dsn)
    return urlunparse(u._replace(path="/" + dbname))


def _assert_scratch(name: str, live: str) -> None:
    """Every CREATE and DROP passes through here first."""
    if not _SCRATCH_RE.match(name):
        raise _RehearsalRefused(f"refusing to touch {name!r}: not a rehearsal database name")
    if name == live:
        raise _RehearsalRefused(f"refusing to touch {name!r}: it is the live database")


@contextmanager
def _admin(dsn: str) -> Iterator[Any]:
    """Autocommit connection to the live server (CREATE/DROP DATABASE need it)."""
    import psycopg

    conn = psycopg.connect(dsn, autocommit=True, connect_timeout=_CONNECT_TIMEOUT_S)
    try:
        yield conn
    finally:
        conn.close()


def _drop(conn: Any, name: str, live: str) -> None:
    from psycopg import sql

    _assert_scratch(name, live)
    force = sql.SQL(" WITH (FORCE)") if conn.info.server_version >= 130000 else sql.SQL("")
    conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}{}").format(sql.Identifier(name), force))


def _drop_stale(conn: Any, live: str, now: float) -> list[str]:
    """Remove rehearsal databases a crashed run left behind -- and nothing else."""
    rows = conn.execute(
        "SELECT datname FROM pg_database WHERE datname LIKE %s", (SCRATCH_PREFIX + "%",),
    ).fetchall()
    dropped: list[str] = []
    for (name,) in rows:
        m = _SCRATCH_RE.match(name)
        if m and name != live and now - int(m.group(1)) > _STALE_AFTER_S:
            _drop(conn, name, live)
            dropped.append(name)
    return dropped


def _verify(scratch_dsn: str) -> tuple[bool, str]:
    """The tables Kazma owns came back, and the settings are not empty."""
    import psycopg

    from kazma_core.db.pg_backup import KAZMA_PG_TABLES

    with psycopg.connect(scratch_dsn, connect_timeout=_CONNECT_TIMEOUT_S) as conn:
        present = {
            r[0] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            ).fetchall()
        }
        restored = [t for t in KAZMA_PG_TABLES if t in present]
        settings = 0
        if "kazma_settings" in present:
            settings = int(conn.execute("SELECT count(*) FROM kazma_settings").fetchone()[0])
    if not restored:
        return False, "the restore produced none of Kazma's tables"
    if "kazma_settings" not in present:
        return False, f"{len(restored)} tables restored, but not kazma_settings"
    if settings == 0:
        return False, f"{len(restored)} tables restored, but kazma_settings is empty"
    return True, f"{len(restored)} tables restored, {settings} settings rows"


def rehearse_pg_restore(dump: Path, dsn: str, res: DrillResult, *, now: float | None = None) -> None:
    """Restore *dump* into a scratch database on *dsn*'s server; record the verdict."""
    import psycopg
    from psycopg import sql

    from kazma_core.migration.pg_bridge import PgBridgeError, PgToolNotFound, restore_database

    now = time.time() if now is None else now
    live = _dbname(dsn)
    name = _scratch_name(now)
    try:
        _assert_scratch(name, live)
        with _admin(dsn) as conn:
            stale = _drop_stale(conn, live, now)
            if stale:
                logger.warning("[restore-rehearsal] removed %d leftover scratch database(s): %s",
                               len(stale), ", ".join(stale))
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    except psycopg.errors.InsufficientPrivilege:
        user = urlparse(dsn).username or "<user>"
        res.add(_CHECK, None, f"the database user cannot CREATE DATABASE; grant it "
                              f"(ALTER ROLE {user} CREATEDB) or turn the rehearsal off")
        return
    except (psycopg.Error, _RehearsalRefused) as exc:
        res.add(_CHECK, False, f"could not create the scratch database: {exc}")
        return

    started = time.time()
    try:
        restore_database(dump, _with_dbname(dsn, name))
        ok, detail = _verify(_with_dbname(dsn, name))
        res.add(_CHECK, ok, f"{detail} into {name} in {time.time() - started:.0f}s")
    except PgToolNotFound as exc:
        res.add(_CHECK, None, f"pg_restore unavailable ({exc})")
    except (PgBridgeError, psycopg.Error, OSError) as exc:
        res.add(_CHECK, False, f"the dump did not restore: {str(exc)[:200]}")
    finally:
        try:
            with _admin(dsn) as conn:
                _drop(conn, name, live)
        except (psycopg.Error, _RehearsalRefused):
            logger.warning("[restore-rehearsal] could not drop %s -- the next run removes it",
                           name, exc_info=True)
