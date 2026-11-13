"""Move a Docker Compose deployment's database from Postgres 16 to 18
(2026-11-13, ``scripts/pg_compose_upgrade.py``).

``docker-compose.postgres.yml`` and ``docker-compose.ha.yml`` ran
``postgres:16`` with its data in the ``kazma_pgdata`` volume. Postgres 18
cannot open that folder, so the compose files now run 18 on a volume of its
own (``kazma_pgdata18``), and the old volume is mounted read-only in the db
container only so its entrypoint can refuse to start an empty 18 while a 16
database is still there: a ``docker compose up`` after a ``git pull`` stops
there and names this command, instead of starting Kazma on an empty
database.

The move, with Kazma's own container stopped (nothing writes):

1. the 16 database dumped (``pg_dump -Fc``) and every public table's row
   count and sequence read -- from the 16 container compose still runs, or,
   when a ``docker compose up`` already replaced it with the refusing 18 one,
   from a temporary 16 server started on the 16 volume (Postgres writes its
   own pid and log files to start; no row changes);
2. the 16 container's definition kept (``docker inspect``, in a journal
   beside the compose file, this account only: it holds the password):
   compose finds a service's container by its labels, not its name, and
   removes the old one when it makes the new one;
3. Postgres 18 started on its empty volume (``KAZMA_PG_UPGRADING=1``, which
   lets the entrypoint through), the dump restored into it in one
   transaction, and every count and sequence compared;
4. 18 started again without the flag, and Kazma's container started if it
   was running.

Any failure after the dump puts it back: the 18 container and its volume
removed (the volume held only a copy, and while it exists the entrypoint's
check would let an empty or partial 18 start), the 16 container made again
from its kept definition and started, and Kazma started. A run killed in the
middle leaves the journal, and the next run puts it back first. The 16
volume's data is never changed; it stays as the way back.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["JOURNAL_NAME", "OLD_IMAGES", "upgrade_compose"]

#: The journal, beside the compose file.
JOURNAL_NAME = ".kazma-pg-upgrade.json"
#: The image each earlier major ran as in Kazma's compose files (pinned as
#: they pinned it): the temporary server a replaced container's data is read
#: with.
OLD_IMAGES = {16: "postgres:16.16-alpine@sha256:0adbf25a9e96d38823bb813f3ac0ed41fadb665c88f1df7fe6dbfb060cbdc106"}
_MAJOR = re.compile(r"postgres:(\d+)")


class _StepFailedError(Exception):
    """A step that did not work: what to say."""


class _Docker:
    def __init__(self, docker: str, compose_file: Path, project: str | None, run) -> None:
        self.docker, self.file, self.project, self.run = docker, compose_file, project, run

    def compose(self, *args: str, env: dict[str, str] | None = None, timeout: float = 600) -> str:
        argv = [self.docker, "compose", "-f", str(self.file), *(["-p", self.project] if self.project else []), *args]
        done = self.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                        check=False, stdin=subprocess.DEVNULL, env={**os.environ, **env} if env else None)
        if done.returncode != 0:
            raise _StepFailedError(f"docker compose {args[0]}: {(done.stderr or done.stdout or '').strip()[-300:]}")
        return done.stdout or ""

    def __call__(self, *args: str, check: bool = True, timeout: float = 600) -> subprocess.CompletedProcess:
        done = self.run([self.docker, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=timeout, check=False, stdin=subprocess.DEVNULL)
        if check and done.returncode != 0:
            raise _StepFailedError(f"docker {args[0]}: {(done.stderr or done.stdout or '').strip()[-300:]}")
        return done

    def inspect(self, name: str) -> dict[str, Any] | None:
        done = self("inspect", name, check=False)
        if done.returncode != 0:
            return None
        rows = json.loads(done.stdout or "[]")
        return rows[0] if rows else None


def _major(image: str) -> int | None:
    found = _MAJOR.search(image or "")
    return int(found.group(1)) if found else None


def _psql(d: _Docker, name: str, user: str, database: str, sql: str) -> str:
    return d("exec", name, "psql", "-U", user, "-d", database, "-At", "-F", "|", "-c", sql).stdout or ""


def _snapshot(d: _Docker, name: str, user: str, database: str) -> tuple[dict[str, int], dict[str, int]]:
    """Every public table's row count and every public sequence's last value."""
    tables = [t for t in _psql(d, name, user, database,
                               "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1").split() if t]
    counts: dict[str, int] = {}
    if tables:
        sql = " UNION ALL ".join(
            "SELECT '" + t.replace("'", "''") + "', count(*) FROM public.\"" + t.replace('"', '""') + "\""
            for t in tables)
        for line in _psql(d, name, user, database, sql).splitlines():
            table, _, value = line.partition("|")
            if value.strip().isdigit():
                counts[table] = int(value)
    sequences: dict[str, int] = {}
    for line in _psql(d, name, user, database, "SELECT sequencename, coalesce(last_value, 0) FROM pg_sequences "
                      "WHERE schemaname = 'public'").splitlines():
        seq, _, value = line.partition("|")
        if value.strip().lstrip("-").isdigit():
            sequences[seq] = int(value)
    return counts, sequences


def _wait_ready(d: _Docker, name: str, user: str, database: str, *, wait_s: float = 180.0) -> None:
    """A query answers (``pg_isready`` answers during the image's first-start
    server too, before its own restart)."""
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        state = (d.inspect(name) or {}).get("State") or {}
        if state.get("Status") in ("exited", "dead"):
            logs = d("logs", "--tail", "5", name, check=False)
            raise _StepFailedError(f"{name} stopped: {((logs.stdout or '') + (logs.stderr or '')).strip()[-300:]}")
        if d("exec", name, "psql", "-U", user, "-d", database, "-At", "-c", "SELECT 1",
             check=False, timeout=30).returncode == 0:
            return
        time.sleep(2.0)
    raise _StepFailedError(f"{name} did not answer within {wait_s:.0f} s")


def _volume_holds(d: _Docker, volume: str, path: str, image: str) -> bool:
    """Whether *path* is a file in *volume* (read-only, no network)."""
    return d("run", "--rm", "--network", "none", "--entrypoint", "sh", "-v", f"{volume}:/v:ro", image,
             "-c", f"test -f /v/{path}", check=False, timeout=300).returncode == 0


def _write_journal(path: Path, entry: dict[str, Any]) -> None:
    from kazma_core.atomic_files import atomic_write_text
    from kazma_core.security.file_access import restrict_to_owner

    if not path.exists():
        path.write_text("{}", encoding="utf-8")
        restrict_to_owner(path)          # before it holds the kept definition and its password
    atomic_write_text(path, json.dumps(entry, indent=2))
    restrict_to_owner(path)


def _put_back(d: _Docker, journal: dict[str, Any], *, say: Callable[[str], None]) -> bool:
    """The 18 container and its volume removed; the 16 container made again
    from its kept definition (when there was one) and started. True when 16
    answers, or when there was no 16 container to put back."""
    from kazma_core.engine_move import _create_args, _extra_networks

    name = journal["container"]
    say(f"-> Putting Postgres {journal.get('from')} back")
    d("rm", "-f", name, check=False)
    if journal.get("new_volume"):
        d("volume", "rm", journal["new_volume"], check=False)
    row = journal.get("old")
    if not row:
        say("   (there was no 16 container to put back: the compose file's 18 refuses to start until the move "
            "is done)")
        return True
    d(*_create_args(row, str((row.get("Config") or {}).get("Image") or "")))
    for net, aliases in _extra_networks(row):
        d("network", "connect", *[a for alias in aliases for a in ("--alias", alias)], net, name, check=False)
    d("start", name, check=False)
    try:
        _wait_ready(d, name, journal["user"], journal["database"])
    except _StepFailedError as exc:
        say(f"   Postgres {journal.get('from')} did not come back: {exc}")
        return False
    return True


@dataclass
class _Deployment:
    """What the compose file says of its database, and the move's state."""

    d: _Docker
    compose_file: Path
    name: str
    user: str
    database: str
    new_image: str
    new_major: int
    old_volume: str
    new_volume: str
    has_kazma: bool
    old_major: int = 0
    mode: str = ""                 # "container" or "volume": where the 16 data is read
    current: dict[str, Any] | None = None
    kazma_running: bool = False

    @property
    def journal_path(self) -> Path:
        return self.compose_file.parent / JOURNAL_NAME

    @property
    def source(self) -> str:
        return self.name if self.mode == "container" else f"{self.name}-pg{self.old_major}-source"


def _read_deployment(d: _Docker, compose_file: Path) -> _Deployment | str:
    """The deployment, or what is wrong with the compose file."""
    try:
        config = json.loads(d.compose("config", "--format", "json"))
    except (_StepFailedError, ValueError) as exc:
        return f"The compose file could not be read: {exc}"
    services = config.get("services") or {}
    db = services.get("db") or {}
    env = db.get("environment") or {}
    volumes = config.get("volumes") or {}
    dep = _Deployment(d, compose_file, str(db.get("container_name") or ""),
                      str(env.get("POSTGRES_USER") or "postgres"), str(env.get("POSTGRES_DB") or "kazma"),
                      str(db.get("image") or ""), _major(str(db.get("image") or "")) or 0,
                      str((volumes.get("kazma_pgdata") or {}).get("name") or ""),
                      str((volumes.get("kazma_pgdata18") or {}).get("name") or ""), "kazma" in services)
    if not dep.name or not dep.new_major or not dep.old_volume or not dep.new_volume:
        return ("This compose file has no Postgres 18 db service with its own volume (container_name, kazma_pgdata, "
                "kazma_pgdata18).")
    return dep


def _find_source(dep: _Deployment) -> str | None:
    """Where the 16 data is read (sets ``mode``), or why there is nothing to
    move (None: there is something)."""
    d = dep.d
    dep.current = d.inspect(dep.name)
    current_major = _major(str(((dep.current or {}).get("Config") or {}).get("Image") or ""))
    if dep.current is not None and current_major is not None and current_major < dep.new_major:
        dep.mode, dep.old_major = "container", current_major
        return None
    if _volume_holds(d, dep.new_volume, f"{dep.new_major}/docker/PG_VERSION", dep.new_image):
        return f"Postgres {dep.new_major} already holds Kazma's database: nothing to move."
    if not _volume_holds(d, dep.old_volume, "PG_VERSION", dep.new_image):
        return (f"There is no Postgres 16 database in {dep.old_volume}: nothing to move (a new install starts on "
                f"Postgres {dep.new_major}).")
    dep.mode, dep.old_major = "volume", 16
    return None


def _say_plan(dep: _Deployment, say: Callable[[str], None]) -> None:
    say(f"Postgres {dep.old_major} -> {dep.new_major} for {dep.compose_file.name}:")
    say(f"  1. stop Kazma's container{'' if dep.kazma_running else ' (not running)'}; dump {dep.database} from "
        + (dep.name if dep.mode == "container" else
           f"a temporary Postgres {dep.old_major} on {dep.old_volume} (compose already replaced its container)"))
    say(f"  2. start Postgres {dep.new_major} on its own volume ({dep.new_volume}), restore the dump into it, and "
        "compare every table and sequence")
    say(f"  3. start it again for good{', then Kazma' if dep.kazma_running else ''}")
    say(f"  The data in {dep.old_volume} is not changed: it is the way back.")


def _start_kazma(dep: _Deployment, say: Callable[[str], None]) -> None:
    if dep.kazma_running:
        say("-> Starting Kazma's container")
        dep.d.compose("start", "kazma")


def _open_source(dep: _Deployment, say: Callable[[str], None]) -> tuple[dict[str, int], dict[str, int]]:
    """Kazma stopped and the 16 data answering; its counts and sequences."""
    d = dep.d
    if dep.kazma_running:
        say("-> Stopping Kazma's container")
        d.compose("stop", "kazma")
    if dep.mode == "volume":
        d("rm", "-f", dep.source, check=False)
        d("run", "-d", "--name", dep.source, "--network", "none", "-e", f"POSTGRES_USER={dep.user}",
          "-e", "POSTGRES_PASSWORD=unused-local-socket-only", "-e", f"POSTGRES_DB={dep.database}",
          "-v", f"{dep.old_volume}:/var/lib/postgresql/data", OLD_IMAGES[dep.old_major])
    elif ((dep.current or {}).get("State") or {}).get("Status") != "running":
        d("start", dep.source)
    _wait_ready(d, dep.source, dep.user, dep.database)
    return _snapshot(d, dep.source, dep.user, dep.database)


def _dump(dep: _Deployment, dump: Path, run) -> int:
    """``pg_dump -Fc`` of the 16 data into *dump*; the temporary server gone after."""
    with dump.open("wb") as fh:
        done = run([dep.d.docker, "exec", dep.source, "pg_dump", "-U", dep.user, "-d", dep.database, "-Fc",
                    "--no-owner", "--no-privileges"], stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.PIPE,
                   timeout=3600, check=False)
    if dep.mode == "volume":
        dep.d("stop", dep.source, check=False)
        dep.d("rm", "-f", dep.source, check=False)
    return done.returncode if dump.stat().st_size else (done.returncode or 1)


def _move_in(dep: _Deployment, dump: Path, before, journal: dict[str, Any], run, say) -> None:
    """18 started on its volume, the dump restored and compared, 18 started
    again for good. Raises :class:`_StepFailedError`."""
    d = dep.d
    say(f"-> Starting Postgres {dep.new_major} on {dep.new_volume}")
    d.compose("up", "-d", "--no-deps", "--force-recreate", "db", env={"KAZMA_PG_UPGRADING": "1"})
    _write_journal(dep.journal_path, {**journal, "stage": "new_started"})
    _wait_ready(d, dep.name, dep.user, dep.database)
    say(f"-> Restoring the dump ({dump.stat().st_size // 1024} KiB)")
    with dump.open("rb") as fh:
        done = run([d.docker, "exec", "-i", dep.name, "pg_restore", "-U", dep.user, "-d", dep.database,
                    "--no-owner", "--no-privileges", "--single-transaction", "--exit-on-error"],
                   stdin=fh, capture_output=True, timeout=3600, check=False)
    if done.returncode != 0:
        said = (done.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        raise _StepFailedError(f"the restore failed (exit {done.returncode}): {said[0][:300]}")
    after = _snapshot(d, dep.name, dep.user, dep.database)
    differ = sorted(t for t in before[0] if before[0][t] != after[0].get(t))
    differ += sorted(f"sequence {s}" for s in before[1] if before[1][s] != after[1].get(s))
    if differ:
        raise _StepFailedError("the copy is not whole: " + ", ".join(differ[:10]))
    say(f"-> Every one of {len(before[0])} tables and {len(before[1])} sequences is the same in "
        f"Postgres {dep.new_major}")
    d.compose("up", "-d", "--no-deps", "--force-recreate", "db")
    _wait_ready(d, dep.name, dep.user, dep.database)


def _after_failure(dep: _Deployment, journal: dict[str, Any], say) -> None:
    if not _put_back(dep.d, journal, say=say):
        say(f"Postgres {dep.old_major} could not be started again: run this again (the journal "
            f"{dep.journal_path} says what to put back). The data in {dep.old_volume} was not changed.")
        return
    dep.journal_path.unlink(missing_ok=True)
    if journal["old"]:
        _start_kazma(dep, say)
        say(f"Postgres {dep.old_major} is back in place and Kazma runs on it, unchanged.")
    else:
        say(f"Postgres {dep.new_major}'s copy was removed; the data in {dep.old_volume} is unchanged. Run this again.")


def upgrade_compose(compose_file: str | Path, *, project: str | None = None, yes: bool = False,
                    say: Callable[[str], None] = print, ask: Callable[[str], str] = input,
                    run=subprocess.run, docker: str | None = None) -> int:
    """The whole move (see the module docstring). 0 done or nothing to do,
    1 failed with 16 put back, 2 refused before anything changed."""
    from kazma_core.docker_cli import find_docker_cli

    compose_file = Path(compose_file).resolve()
    docker = docker or find_docker_cli()
    if not docker:
        say("No docker CLI was found.")
        return 2
    dep = _read_deployment(_Docker(docker, compose_file, project, run), compose_file)
    if isinstance(dep, str):
        say(dep)
        return 2
    if dep.journal_path.is_file():
        journal = json.loads(dep.journal_path.read_text(encoding="utf-8"))
        say(f"An earlier run did not finish (stage {journal.get('stage')}).")
        if not _put_back(dep.d, journal, say=say):
            return 1
        dep.journal_path.unlink()
    nothing = _find_source(dep)
    if nothing:
        say(nothing)
        return 0
    if dep.has_kazma:
        ids = dep.d.compose("ps", "-q", "kazma").split()
        dep.kazma_running = bool(ids) and bool(((dep.d.inspect(ids[0]) or {}).get("State") or {}).get("Running"))
    _say_plan(dep, say)
    if not yes and ask("Go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
        say("Nothing was done.")
        return 1
    try:
        say("-> Reading Kazma's database from Postgres " + str(dep.old_major))
        before = _open_source(dep, say)
    except (_StepFailedError, subprocess.SubprocessError, OSError) as exc:
        say(f"Nothing was moved: {exc}")
        if dep.mode == "volume":
            dep.d("rm", "-f", dep.source, check=False)
        _start_kazma(dep, say)
        return 1
    with tempfile.TemporaryDirectory(prefix="kazma-pg-compose-") as tmp:
        dump = Path(tmp) / "kazma.dump"
        say(f"-> Dumping {dep.database} from Postgres {dep.old_major}")
        code = _dump(dep, dump, run)
        if code != 0:
            say(f"The dump failed (exit {code}); nothing was moved.")
            _start_kazma(dep, say)
            return 1
        journal = {"stage": "dumped", "container": dep.name, "user": dep.user, "database": dep.database,
                   "old": dep.current if dep.mode == "container" else None, "new_volume": dep.new_volume,
                   "kazma_running": dep.kazma_running, "from": dep.old_major, "to": dep.new_major,
                   "started_at": time.time()}
        _write_journal(dep.journal_path, journal)
        try:
            _move_in(dep, dump, before, journal, run, say)
        except (_StepFailedError, subprocess.SubprocessError, OSError) as exc:
            say(f"The move failed: {exc}")
            _after_failure(dep, journal, say)
            return 1
    dep.journal_path.unlink(missing_ok=True)
    _start_kazma(dep, say)
    say(f"Done. Kazma's database runs on Postgres {dep.new_major}. {dep.old_volume} is kept as it was; remove it "
        "with docker volume rm once you are sure."
        + ("" if dep.kazma_running else f" Start the rest: docker compose -f {dep.compose_file.name} up -d"))
    return 0
