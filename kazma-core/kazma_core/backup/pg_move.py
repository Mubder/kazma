"""Move Kazma's database out of its Docker container (resilience plan A1.4).

``kazma setup postgres --move-from-container kazma-db-win`` runs this after
the native server is set up and before ``.env`` is changed:

1. ``pg_dump -Fc`` inside the container (as today's backups do), streamed to a
   private temporary file on this machine;
2. ``pg_restore`` into the native server as ``kazma_app`` (``--no-owner``:
   every object is then the role's, as ``kazma_app_role.sql`` makes it), in one
   transaction, stopping at the first error;
3. the row count of every Kazma table (``db.pg_backup.KAZMA_PG_TABLES``) in
   the container and in the new server, compared.

Only when every count matches does the caller write the new address. The
container is left as it is: it is the way back (start it, put the old address
back) and is removed by the owner after 30 days. Run with Kazma paused for
maintenance, so nothing writes to the container while it is copied.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlsplit

__all__ = ["move_from_container"]


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


def move_from_container(container: str, dest_url: str, *, port: int,
                        say: Callable[[str], None] = print, run=subprocess.run,
                        bin_dir: str | None = None) -> bool:
    """Copy the container's database into the server at *dest_url*. True when
    the copy finished and every Kazma table holds the same number of rows."""
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
                 capture_output=True, text=True, timeout=120, check=False)
    if listed.returncode != 0:
        say(f"The container's tables could not be listed (exit {listed.returncode}).")
        return False
    present = set(listed.stdout.split())
    tables = [t for t in KAZMA_PG_TABLES if t in present]

    with tempfile.TemporaryDirectory(prefix="kazma-move-") as tmp:
        dump = Path(tmp) / "kazma.dump"
        say(f"-> Dumping {database} from container {container}")
        with dump.open("wb") as fh:
            done = run([docker, "exec", container, "pg_dump", "-U", user, "-d", database, "-Fc",
                        "--no-owner", "--no-privileges"], stdout=fh, stderr=subprocess.PIPE,
                       timeout=3600, check=False)
        if done.returncode != 0 or dump.stat().st_size == 0:
            say(f"The dump failed (exit {done.returncode}).")
            return False
        say(f"-> Restoring it into the new server ({dump.stat().st_size // 1024} KiB)")
        parts = urlsplit(dest_url)
        env = {**os.environ, "PGPASSWORD": unquote(parts.password or "")}
        done = run([pg_restore, "-h", "127.0.0.1", "-p", str(port), "-U", unquote(parts.username or ""),
                    "-d", (parts.path or "/kazma").lstrip("/"), "--no-owner", "--no-privileges",
                    "--single-transaction", "--exit-on-error", str(dump)],
                   capture_output=True, text=True, env=env, timeout=3600, check=False)
        if done.returncode != 0:
            said = ((done.stderr or "").strip().splitlines() or [""])[-1][:300]
            say(f"The restore failed (exit {done.returncode}): {said}")
            return False

    if not tables:
        say("The container holds no Kazma table: nothing to compare.")
        return True
    sql = _count_sql(tables)
    source = run([docker, "exec", container, "psql", "-U", user, "-d", database, "-At", "-F", "|", "-c", sql],
                 capture_output=True, text=True, timeout=600, check=False)
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
