"""Move Kazma's database out of its Docker container (resilience plan A1.4).

``kazma setup postgres --move-from-container kazma-db-win`` runs this after
the native server is set up, with Kazma stopped through its guard (nothing
writes to the container while it is copied), and before ``.env`` names the
new server:

1. the new server is checked: when it already holds Kazma's tables (an
   earlier copy), it may be replaced only if Kazma never started on it --
   its last start (``system.lifecycle.last_boot_epoch``, written at every
   boot) is not newer than the container's -- and nothing is connected to it;
2. ``pg_dump -Fc`` inside the container (as today's backups do), streamed to a
   private temporary file on this machine;
3. the earlier copy, if any, emptied by the caller (``empty_target``: the
   database dropped and made again, as the superuser);
4. ``pg_restore`` into the native server as ``kazma_app`` (``--no-owner``:
   every object is then the role's, as ``kazma_app_role.sql`` makes it), in one
   transaction, stopping at the first error;
5. the row count of every Kazma table (``db.pg_backup.KAZMA_PG_TABLES``) in
   the container and in the new server, compared.

Only when every count matches does the caller write the new address. The
container is left as it is: it is the way back (start it, put the old address
back) and is removed by the owner when they are sure.

Live 2026-11-06 the first move ran while Kazma kept writing to the container,
and the setup then said to reload: a reload would have started Kazma on a
copy missing everything written since, and a second run failed on the tables
the first had made. The checks in step 1 are what make a second run safe: an
earlier copy is replaced, a database Kazma has started on never is.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlsplit

__all__ = ["move_from_container"]

#: The setting every Kazma boot writes (``lifecycle_notifier``): the newest
#: start a database has seen.
BOOT_KEY = "system.lifecycle.last_boot_epoch"


def _container_login() -> tuple[str, str]:
    """The user and database of the address Kazma uses today (the container's)."""
    from kazma_core.db.backend import get_database_url

    parts = urlsplit(get_database_url() or "")
    return unquote(parts.username or "kazma"), (parts.path or "/kazma").lstrip("/") or "kazma"


def _count_sql(tables: list[str]) -> str:
    return " UNION ALL ".join(
        f"SELECT '{t}', count(*) FROM {t}" for t in tables) + ";"


def _parse_counts(text: str) -> dict[str, int]:
    counts = {}
    for line in text.splitlines():
        name, _, value = line.partition("|")
        if name and value.strip().isdigit():
            counts[name.strip()] = int(value)
    return counts


def _boot_time(text: str | None) -> float | None:
    """A stored boot stamp (a JSON number in a text column), or None."""
    try:
        return float(str(text).strip().strip('"'))
    except (TypeError, ValueError):
        return None


def _replace_refusal(container_boot: float | None, target_boot: float | None, others: int) -> str | None:
    """Why an earlier copy on the new server must not be replaced, or None.

    Replaced only when Kazma never started on it: its newest start is not
    newer than the container's (the copy carries the container's stamp, and
    every start after the switch writes a newer one), and nothing is
    connected to it."""
    if others:
        return (f"{others} session(s) are connected to Kazma's database on the new server: something "
                "uses it. Stop it, then run this again. Nothing was changed.")
    if container_boot is None or target_boot is None:
        return ("The new server already holds Kazma's tables, and it cannot be told whether Kazma has "
                f"started on them (no {BOOT_KEY} to compare). Nothing was changed. If they are only an "
                "earlier copy, drop the database yourself, then run this again.")
    if target_boot > container_boot:
        return ("Kazma has started on the new server since it was copied (its last start there is newer "
                "than the container's): it is in use, and copying the container over it would lose what "
                "Kazma wrote there. Nothing was changed.")
    return None


def _target_state(dest_url: str) -> tuple[set[str], float | None, int]:
    """The new server's public tables, its boot stamp and how many other
    sessions are connected to its database, read as ``kazma_app``. Raises
    OSError when the server cannot be read."""
    import psycopg

    try:
        with psycopg.connect(dest_url, connect_timeout=10, autocommit=True) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()}
            boot = None
            if "kazma_settings" in tables:
                row = conn.execute("SELECT value FROM kazma_settings WHERE key = %s", (BOOT_KEY,)).fetchone()
                boot = _boot_time(row[0]) if row else None
            others = int(conn.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND pid <> pg_backend_pid()").fetchone()[0])
    except psycopg.Error as exc:
        raise OSError(type(exc).__name__) from exc
    return tables, boot, others


def move_from_container(container: str, dest_url: str, *, port: int,
                        say: Callable[[str], None] = print, run=subprocess.run,
                        bin_dir: str | None = None,
                        empty_target: Callable[[], bool] | None = None) -> bool:
    """Copy the container's database into the server at *dest_url*. True when
    the copy finished and every Kazma table holds the same number of rows.

    An earlier copy on the new server is replaced through *empty_target*
    (which drops and makes the database again) only when
    :func:`_replace_refusal` allows it; without *empty_target* it is refused."""
    from kazma_core.db.pg_backup import KAZMA_PG_TABLES
    from kazma_core.docker_cli import find_docker_cli
    from kazma_core.postgres_service import bin_dir_for, detect_platform

    docker = find_docker_cli()
    if not docker:
        say("The docker command was not found: the container cannot be read.")
        return False
    user, database = _container_login()
    bin_dir = bin_dir or bin_dir_for(detect_platform())
    pg_restore = str(Path(bin_dir) / ("pg_restore.exe" if os.name == "nt" else "pg_restore"))

    # Which Kazma tables the container holds (a table a feature never made is not compared).
    listed = run([docker, "exec", container, "psql", "-U", user, "-d", database, "-At", "-c",
                  "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"],
                 stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, check=False)
    if listed.returncode != 0:
        say(f"The container's tables could not be listed (exit {listed.returncode}).")
        return False
    present = set(listed.stdout.split())
    tables = [t for t in KAZMA_PG_TABLES if t in present]

    # An earlier copy on the new server: replaced only if Kazma never started on it.
    try:
        held, target_boot, others = _target_state(dest_url)
    except (OSError, ImportError) as exc:  # the new server could not be read: say so, change nothing
        say(f"The new server could not be read ({exc}): nothing was changed.")
        return False
    replace = bool(held & set(KAZMA_PG_TABLES))
    if replace:
        container_boot = None
        if "kazma_settings" in present:
            stamp = run([docker, "exec", container, "psql", "-U", user, "-d", database, "-At", "-c",
                         f"SELECT value FROM kazma_settings WHERE key = '{BOOT_KEY}'"],
                        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, check=False)
            container_boot = _boot_time(stamp.stdout) if stamp.returncode == 0 else None
        refusal = _replace_refusal(container_boot, target_boot, others)
        if refusal:
            say(refusal)
            return False
        if empty_target is None:
            say("The new server holds an earlier copy, and nothing was given to empty it: nothing was changed.")
            return False

    with tempfile.TemporaryDirectory(prefix="kazma-move-") as tmp:
        dump = Path(tmp) / "kazma.dump"
        say(f"-> Dumping {database} from container {container}")
        with dump.open("wb") as fh:
            done = run([docker, "exec", container, "pg_dump", "-U", user, "-d", database, "-Fc",
                        "--no-owner", "--no-privileges"], stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.PIPE,
                       timeout=3600, check=False)
        if done.returncode != 0 or dump.stat().st_size == 0:
            say(f"The dump failed (exit {done.returncode}).")
            return False
        if replace:
            say("-> The new server holds an earlier copy Kazma never started on: replacing it")
            if not empty_target():
                say("Emptying the new server's database failed.")
                return False
        say(f"-> Restoring it into the new server ({dump.stat().st_size // 1024} KiB)")
        parts = urlsplit(dest_url)
        env = {**os.environ, "PGPASSWORD": unquote(parts.password or "")}
        done = run([pg_restore, "-h", "127.0.0.1", "-p", str(port), "-U", unquote(parts.username or ""),
                    "-d", (parts.path or "/kazma").lstrip("/"), "--no-owner", "--no-privileges",
                    "--single-transaction", "--exit-on-error", "--no-password", str(dump)],
                   stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=3600, check=False)
        if done.returncode != 0:
            said = ((done.stderr or "").strip().splitlines() or [""])[-1][:300]
            say(f"The restore failed (exit {done.returncode}): {said}")
            return False

    if not tables:
        say("The container holds no Kazma table: nothing to compare.")
        return True
    sql = _count_sql(tables)
    source = run([docker, "exec", container, "psql", "-U", user, "-d", database, "-At", "-F", "|", "-c", sql],
                 stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600, check=False)
    import psycopg

    with psycopg.connect(dest_url, connect_timeout=10, autocommit=True) as conn:
        dest = {name: int(n) for name, n in conn.execute(sql.rstrip(";")).fetchall()}
    src = _parse_counts(source.stdout)
    differ = [t for t in tables if src.get(t) != dest.get(t)]
    for t in tables:
        mark = "ok" if t not in differ else "DIFFERS"
        say(f"   {mark:7} {t}: {src.get(t)} -> {dest.get(t)}")
    if differ:
        say(f"{len(differ)} table(s) differ: the move is not complete.")
        return False
    say(f"-> Every one of {len(tables)} Kazma tables holds the same rows in the new server.")
    return True
