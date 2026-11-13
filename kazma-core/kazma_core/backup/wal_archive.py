"""WAL archiving and point-in-time restore (resilience plan A2).

Postgres is dumped every 6 hours (``native_pg_backup``), so a lost disk could
cost up to 6 hours of chats, settings and checkpoints. With write-ahead-log
archiving every change since the last base backup is kept, and a restore can
stop at any minute.

* ``kazma setup postgres`` turns it on (:func:`archive_command`,
  :func:`prepare_steps`): ``archive_mode = on``, ``archive_timeout = 300`` (a
  segment at least every 5 minutes while anything is written), each finished
  segment copied into the archive folder with the OS's own copy (the server
  runs as its own service account, which cannot run Kazma's Python). A copy
  that fails fails the command, so Postgres keeps the segment and tries again.
* A weekly base backup (:func:`_base_backup`, ``pg_basebackup -Ft -z -X fetch``
  into ``<data dir>/backups/pg/base/<epoch>``), two kept, and the archived
  WAL older than the older one pruned (:func:`prune`).
* Offsite (:func:`_push_offsite`): new segments and each new base backup go to
  the configured backup provider (the same one the universal backup uses),
  named ``pgwal-<segment>`` and ``pgbase-<epoch>.tar.gz``.
* The maintenance sweep "WAL archive" (:func:`wal_sweep`): the archiver's own
  numbers (``pg_stat_archiver``); a failing archiver, or none archived for 15
  minutes, pages ``backup.wal_archive_stale``. It also takes the weekly base
  backup when one is due and pushes offsite.
* :func:`restore_to` lays a base backup and the WAL up to a time into a new
  data folder and says how to start it: the live database is never touched.

Every function blocks: off the loop. Nothing happens on an install whose
setup record names no WAL archive (``kazma setup postgres`` was not run).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any

from kazma_core.child_stop import StoppedByKazma, ended_by_kazmas_stop

logger = logging.getLogger(__name__)

__all__ = [
    "archive_command",
    "default_archive_dir",
    "prepare_steps",
    "prune",
    "restore_to",
    "wal_sweep",
]

#: No segment archived for this long while the server writes: a page.
STALE_AFTER_S = 15 * 60.0
#: A base backup is taken this often; two are kept.
BASE_EVERY_S = 7 * 86400.0
BASE_KEEP = 2
_SEGMENT = re.compile(r"^[0-9A-F]{24}$")
_START = re.compile(r"START WAL LOCATION: [0-9A-F/]+ \(file ([0-9A-F]{24})\)")


# -- setting it up ------------------------------------------------------------------------


def default_archive_dir(platform: Any) -> Path:
    """Where the server copies finished segments: a folder its service
    account may write and Kazma's account may read and prune."""
    if platform.os == "windows":
        return Path(os.environ.get("ProgramData") or r"C:\ProgramData") / "Kazma" / "pg_wal_archive"
    if platform.os == "debian":
        return Path("/var/lib/postgresql/16/wal_archive")
    if platform.os == "fedora":
        return Path("/var/lib/pgsql/16/wal_archive")
    from kazma_core.paths import data_dir

    return data_dir() / "backups" / "pg" / "wal"     # macOS: the server runs as this account


def archive_command(platform: Any, archive_dir: Path) -> str:
    """The ``archive_command`` for *platform*: the OS's copy, a temporary name
    and a rename, so a half-copied segment is never taken for a whole one."""
    d = str(archive_dir) if platform.os == "windows" else Path(archive_dir).as_posix()
    if platform.os == "windows":
        return f'copy /Y "%p" "{d}\\%f.part" && move /Y "{d}\\%f.part" "{d}\\%f"'
    return f'cp "%p" "{d}/%f.part" && chmod 660 "{d}/%f.part" && mv "{d}/%f.part" "{d}/%f"'


def prepare_steps(platform: Any, archive_dir: Path) -> list[Any]:
    """Create the archive folder with the rights the server and Kazma need."""
    from kazma_core.postgres_service import Step

    d = str(archive_dir) if platform.os == "windows" else Path(archive_dir).as_posix()
    if platform.os == "windows":
        user = os.environ.get("USERNAME") or ""
        grants = ["/grant", "*S-1-5-20:(OI)(CI)M"]          # NETWORK SERVICE: the server
        if user:
            grants += ["/grant", f"{user}:(OI)(CI)M"]       # Kazma's account: read and prune
        return [Step("Create the WAL archive folder", ("cmd", "/c", "if", "not", "exist", d, "mkdir", d),
                     admin=True),
                Step("", ("icacls", d, "/inheritance:r", *grants, "/grant", "*S-1-5-32-544:(OI)(CI)F"),
                     admin=True)]
    if platform.os in ("debian", "fedora"):
        user = os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
        steps = [Step("Create the WAL archive folder",
                      ("sudo", "install", "-d", "-o", "postgres", "-g", "postgres", "-m", "2770", d), admin=True)]
        if user and user != "root":
            steps.append(Step("Let Kazma's account read and prune it",
                              ("sudo", "usermod", "-aG", "postgres", user), admin=True))
        return steps
    return [Step("Create the WAL archive folder", ("mkdir", "-p", d))]


# -- what the server says ----------------------------------------------------------------------


def archiver_status(dsn: str) -> dict[str, Any]:
    """``pg_stat_archiver`` and ``archive_mode``, read with Kazma's own sign-in."""
    import psycopg

    with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as conn:
        mode = conn.execute("SHOW archive_mode").fetchone()[0]
        row = conn.execute(
            "SELECT archived_count, last_archived_wal, extract(epoch FROM last_archived_time), "
            "failed_count, last_failed_wal, extract(epoch FROM last_failed_time) FROM pg_stat_archiver"
        ).fetchone()
    return {
        "mode": mode, "archived_count": int(row[0] or 0), "last_archived_wal": row[1],
        "last_archived_at": float(row[2]) if row[2] is not None else None,
        "failed_count": int(row[3] or 0), "last_failed_wal": row[4],
        "last_failed_at": float(row[5]) if row[5] is not None else None,
    }


def archive_problem(status: dict[str, Any], now: float | None = None) -> str | None:
    """Why the archive is not current, in words, or None."""
    now = time.time() if now is None else now
    if status.get("mode") != "on":
        return "archive_mode is off: the server keeps no WAL"
    failed_at, done_at = status.get("last_failed_at"), status.get("last_archived_at")
    if failed_at and (not done_at or failed_at > done_at):
        return f"the last copy failed (segment {status.get('last_failed_wal')})"
    if done_at is None:
        return "nothing has been archived yet"
    if now - done_at > STALE_AFTER_S:
        return f"no segment archived for {int((now - done_at) // 60)} minutes"
    return None


# -- base backups and retention -------------------------------------------------------------------


def _base_root() -> Path:
    from kazma_core.paths import data_dir

    return data_dir() / "backups" / "pg" / "base"


def _base_backups(root: Path | None = None) -> list[Path]:
    """Finished base backups, newest first (a folder named by its epoch with
    a ``base.tar.gz``)."""
    root = root or _base_root()
    found = [p for p in root.iterdir() if p.is_dir() and p.name.isdigit() and (p / "base.tar.gz").is_file()] \
        if root.is_dir() else []
    return sorted(found, key=lambda p: int(p.name), reverse=True)


def _start_segment(base: Path) -> str | None:
    """The first WAL segment a base backup needs (its ``backup_label``)."""
    try:
        with tarfile.open(base / "base.tar.gz", "r:gz") as tar:
            member = tar.extractfile("backup_label")
            label = member.read().decode("utf-8", "replace") if member else ""
    except (OSError, tarfile.TarError, KeyError):
        return None
    found = _START.search(label)
    return found.group(1) if found else None


def _base_backup(record: dict[str, Any], superuser_password: str, *, run=subprocess.run,
                now: float | None = None, root: Path | None = None) -> Path:
    """Take a base backup (``pg_basebackup -Ft -z -X fetch``) and keep BASE_KEEP."""
    now = time.time() if now is None else now
    root = root or _base_root()
    target = root / str(int(now))
    partial = root / f"{int(now)}.partial"
    root.mkdir(parents=True, exist_ok=True)
    exe = str(Path(record["bin_dir"]) / ("pg_basebackup.exe" if record["os"] == "windows" else "pg_basebackup"))
    argv = [exe, "-h", "127.0.0.1", "-p", str(record["port"]), "-U", "postgres",
            "-D", str(partial), "-Ft", "-z", "-X", "fetch", "-c", "fast", "--no-password"]
    from kazma_core.security.child_env import tool_child_env

    env = {**tool_child_env(), "PGPASSWORD": superuser_password}
    done = run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=3600, check=False, env=env)
    if done.returncode != 0:
        shutil.rmtree(partial, ignore_errors=True)
        said = ((done.stderr or done.stdout or "").strip().splitlines() or [""])[-1][:300]
        if ended_by_kazmas_stop(done.returncode):
            raise StoppedByKazma(f"pg_basebackup was stopped by Kazma's own stop: {said}")
        raise RuntimeError(f"pg_basebackup exited {done.returncode}: {said}")
    partial.rename(target)
    for old in _base_backups(root)[BASE_KEEP:]:
        shutil.rmtree(old, ignore_errors=True)
    return target


def prune(archive_dir: Path, root: Path | None = None) -> int:
    """Remove archived segments older than the oldest kept base backup needs."""
    kept = _base_backups(root)
    if len(kept) < 1:
        return 0
    first = _start_segment(kept[-1])
    if not first:
        return 0
    removed = 0
    for path in archive_dir.iterdir() if archive_dir.is_dir() else []:
        name = path.name
        if _SEGMENT.match(name) and name[8:] < first[8:] and name[:8] == first[:8]:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


# -- offsite -----------------------------------------------------------------------------------------


_PUSHED_KEY = "backups.pg.wal_offsite_pushed"


def _push_offsite(archive_dir: Path, *, root: Path | None = None, provider: Any = None,
                 pushed: set[str] | None = None) -> dict[str, Any]:
    """Upload the segments and base backups not yet offsite. Returns what it did."""
    import asyncio

    if provider is None:
        from kazma_core.backup.cloud_sync import get_sync_provider

        provider = get_sync_provider()
    if provider is None:
        return {"ok": True, "skipped": "no offsite provider"}
    if pushed is None:
        from kazma_core.config_store import get_config_store

        pushed = set(get_config_store().get(_PUSHED_KEY, []) or [])
    todo: list[tuple[Path, str]] = []
    for path in sorted(archive_dir.iterdir()) if archive_dir.is_dir() else []:
        if _SEGMENT.match(path.name) and f"pgwal-{path.name}" not in pushed:
            todo.append((path, f"pgwal-{path.name}"))
    for base in _base_backups(root):
        name = f"pgbase-{base.name}.tar.gz"
        if name not in pushed:
            todo.append((base / "base.tar.gz", name))
    sent, failed = [], []
    for local, remote in todo:
        result = asyncio.run(provider.upload_file(local, remote))
        (sent if result.get("ok") else failed).append(remote)
        if not result.get("ok"):
            break                         # keep the order: the next pass starts here
    pushed.update(sent)
    live = {f"pgwal-{p.name}" for p in archive_dir.iterdir()} if archive_dir.is_dir() else set()
    live |= {f"pgbase-{b.name}.tar.gz" for b in _base_backups(root)}
    from kazma_core.config_store import get_config_store

    get_config_store().set(_PUSHED_KEY, sorted(pushed & live), category="backups")
    return {"ok": not failed, "sent": len(sent), "failed": failed[:1]}


# -- the sweep -----------------------------------------------------------------------------------------


def _superuser_password() -> str:
    from kazma_core.paths import user_home

    try:
        return (user_home() / "postgres_superuser.pass").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def wal_sweep(now: float | None = None) -> str:
    """The maintenance sweep "WAL archive" (see the module docstring)."""
    from kazma_core.db.backend import get_database_url
    from kazma_core.postgres_service import read_record

    record = read_record().get("postgres") or {}
    archive_dir = Path(record.get("wal_archive_dir") or "")
    dsn = get_database_url() or ""
    if not record.get("wal_archive_dir") or not dsn.startswith(("postgres://", "postgresql://")):
        return "not set up"
    now = time.time() if now is None else now
    import psycopg

    from kazma_core.observability.ops_alerts import alert

    try:
        status = archiver_status(dsn)
    except (psycopg.Error, OSError) as exc:
        return f"not read ({type(exc).__name__})"
    problem = archive_problem(status, now)
    if problem:
        logger.warning("[backup] WAL archive: %s", problem)
        alert("backup.wal_archive_stale", "The database's WAL archive is not current",
              f"{problem}. Point-in-time restore covers only up to the last archived segment.",
              severity="warn", cooldown_s=6 * 3600.0)
    newest = _base_backups()
    if not newest or now - int(newest[0].name) >= BASE_EVERY_S:
        password = _superuser_password()
        if password:
            try:
                base = _base_backup(record, password, now=now)
                logger.info("[backup] base backup taken: %s", base.name)
            except StoppedByKazma as exc:
                logger.info("[backup] %s; the next sweep takes it", exc)
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                logger.warning("[backup] base backup failed: %s", exc)
                alert("backup.pg_base_backup", "The weekly database base backup failed", str(exc)[:500],
                      severity="warn", cooldown_s=6 * 3600.0)
    removed = prune(archive_dir)
    pushed = _push_offsite(archive_dir)
    logger.info("[backup] WAL archive: %s; %d segment(s) pruned; offsite %s",
                problem or "current", removed, json.dumps(pushed))
    return problem or "current"


# -- restore ------------------------------------------------------------------------------------------


def _base_before(target_time: str, bases: list[Path]) -> Path:
    """The newest base backup taken before *target_time* (a time without a
    zone is this machine's, as the service reads ``recovery_target_time``).
    A base taken after it cannot replay back to it: Postgres refuses a
    recovery target before the backup's own end."""
    from datetime import datetime

    try:
        when = datetime.fromisoformat(target_time.strip()).astimezone().timestamp()
    except ValueError as exc:
        raise ValueError(f"{target_time!r} is not a time: write it like 2026-11-20 14:05") from exc
    earlier = [b for b in bases if int(b.name) < when]
    if not earlier:
        if not bases:
            raise ValueError("there is no base backup to restore from")
        oldest = time.strftime("%Y-%m-%d %H:%M", time.localtime(min(int(b.name) for b in bases)))
        raise ValueError(f"the oldest base backup is from {oldest}: a restore can go back to then, not before")
    return max(earlier, key=lambda b: int(b.name))


def restore_to(target_time: str, new_data_dir: Path, archive_dir: Path, *, base: Path | None = None,
               os_name: str = "windows") -> list[str]:
    """Lay the newest base backup older than *target_time* into *new_data_dir*
    and set it to replay the archived WAL up to that time. Returns the steps
    left to the operator (start it on a spare port, check, switch). The live
    data folder is never touched."""
    if new_data_dir.exists() and any(new_data_dir.iterdir()):
        raise ValueError(f"{new_data_dir} is not empty: a restore goes into a new folder")
    base = base or _base_before(target_time, _base_backups())
    new_data_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(base / "base.tar.gz", "r:gz") as tar:
        tar.extractall(new_data_dir, filter="data")
    copy = (f'copy "{archive_dir}\\%f" "%p"' if os_name == "windows"
            else f'cp "{Path(archive_dir).as_posix()}/%f" "%p"')
    with (new_data_dir / "postgresql.auto.conf").open("a", encoding="utf-8") as fh:
        fh.write(f"\nrestore_command = '{copy}'\n"
                 f"recovery_target_time = '{target_time}'\n"
                 "recovery_target_action = 'promote'\n"
                 "archive_mode = 'off'\n")
    (new_data_dir / "recovery.signal").write_text("", encoding="utf-8")
    return [
        f"Start it on a spare port: pg_ctl -D \"{new_data_dir}\" -o \"-p 55432\" start",
        "Check it: psql -p 55432 -U postgres -d kazma (the rows you expect are there)",
        "Then stop Kazma, stop the service, point the service's data folder at it (or copy it over), start.",
    ]
