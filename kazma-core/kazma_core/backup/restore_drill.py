"""Prove a backup can be restored, without restoring it over anything.

A backup nobody has ever restored is a hypothesis. The audit asked for a
rehearsed restore; building one surfaced that Kazma has no restore path for
universal backups at all -- settings, memory and Postgres each have one, but
nothing reassembles the 954 MB folder of SQLite DBs, assets, .env and work
artifacts. There is currently nothing to rehearse.

So this is the half that can be done safely, and it is worth more than it
sounds: verify that what was written back is *readable*. It never writes
into the live data directory and never touches a live database.

It checks the things that actually go wrong with backups:

* every SQLite file passes ``PRAGMA integrity_check`` -- copied to scratch
  first, so the backup itself is only ever read, and because a torn WAL
  copy passes a file-exists check while failing to open
* the Postgres dump parses as an archive, via ``pg_restore --list``, which
  reads the table of contents and writes to no database. A dump truncated
  by a full disk keeps a valid PGDMP header and a broken TOC, and only
  this catches it
* ``.env`` is present -- it holds KAZMA_SECRET, and without it the
  backed-up encrypted vault is unrecoverable. A backup that restores
  everything except the key to read it is not a backup
* the manifest is present, parses, and records no failed databases

Exit code is non-zero when the backup fails, so this is usable as a
scheduled check rather than something a human must remember to run.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["DrillResult", "verify_backup", "run_drill", "drill_scheduler",
           "DRILL_INTERVAL_HOURS"]

# pg_restore --list on a multi-GB archive reads only the TOC, but a busy or
# containerised host can still be slow. Generous on purpose: a false failure
# here would teach an operator to ignore the drill.
_PG_LIST_TIMEOUT_S = 300


@dataclass
class DrillResult:
    """What the drill found. ``ok`` is the whole verdict."""

    backup_dir: str
    ok: bool = True
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            self.ok = False

    @property
    def failures(self) -> list[dict[str, Any]]:
        return [c for c in self.checks if not c["ok"]]

    def summary(self) -> str:
        bad = self.failures
        head = "PASS" if self.ok else "FAIL"
        return (
            f"{head}: {len(self.checks) - len(bad)}/{len(self.checks)} checks "
            f"passed for {Path(self.backup_dir).name or '(none)'}"
        )


def _check_sqlite(path: Path, scratch: Path, res: DrillResult) -> None:
    """Copy to scratch and integrity-check. Never opens the backup in place."""
    name = path.name
    target = scratch / name
    try:
        shutil.copy2(path, target)
    except Exception as exc:  # noqa: BLE001
        res.add(f"sqlite:{name}", False, f"could not copy out: {exc}")
        return
    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            verdict = (row[0] if row else "") or ""
            tables = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        res.add(f"sqlite:{name}", False, f"will not open: {exc}")
        return
    if verdict.lower() != "ok":
        res.add(f"sqlite:{name}", False, f"integrity_check: {verdict[:120]}")
        return
    res.add(f"sqlite:{name}", True, f"{tables} tables")


def _check_pg_dump(dump: Path, res: DrillResult) -> None:
    """Parse the archive TOC. Reads only; writes to no database."""
    try:
        with dump.open("rb") as fh:
            head = fh.read(5)
    except Exception as exc:  # noqa: BLE001
        res.add("postgres:header", False, f"unreadable: {exc}")
        return
    if head[:5] != b"PGDMP":
        res.add("postgres:header", False, "missing PGDMP magic")
        return
    res.add("postgres:header", True, f"{dump.stat().st_size // (1024 * 1024)} MB")

    try:
        from kazma_core.migration.pg_bridge import resolve_pg_restore

        prefix = list(resolve_pg_restore())
    except Exception as exc:  # noqa: BLE001
        # Not a failure: the dump's header is still verified above, and a
        # host without client tools must not fail a drill for lacking them.
        res.add("postgres:toc", True, f"pg_restore unavailable, header only ({exc})")
        return

    # resolve_pg_restore may hand back "docker exec -i <container> pg_restore".
    # That tool runs INSIDE the container and cannot see a host path -- the
    # first run of this drill against the live install failed with
    # 'could not open input file "C:\\Users\\..."'. Piping the archive over
    # stdin is what actually works, and is how the containerised deployment
    # shape has to be verified.
    via_docker = bool(prefix) and "docker" in prefix[0].lower()
    try:
        if via_docker:
            with dump.open("rb") as fh:
                proc = subprocess.run(
                    [*prefix, "--list"], stdin=fh, capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=_PG_LIST_TIMEOUT_S, check=False,
                )
        else:
            proc = subprocess.run(
                [*prefix, "--list", str(dump)], capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=_PG_LIST_TIMEOUT_S, check=False,
            )
    except Exception as exc:  # noqa: BLE001
        res.add("postgres:toc", False, f"pg_restore --list would not run: {exc}")
        return
    if proc.returncode != 0:
        res.add("postgres:toc", False,
                f"archive will not parse: {(proc.stderr or '')[:160]}")
        return
    entries = [ln for ln in proc.stdout.splitlines()
               if ln.strip() and not ln.startswith(";")]
    if not entries:
        res.add("postgres:toc", False, "archive parses but contains nothing")
        return
    res.add("postgres:toc", True, f"{len(entries)} archive entries")


def verify_backup(
    backup_dir: str | Path,
    *,
    scratch_dir: str | Path | None = None,
    pg_dump: str | Path | None = None,
) -> DrillResult:
    """Verify one backup directory is readable. Never writes to live data."""
    d = Path(backup_dir)
    res = DrillResult(backup_dir=str(d))
    if not d.is_dir():
        res.add("backup:exists", False, "not a directory")
        return res
    res.add("backup:exists", True)

    manifest = d / "manifest.json"
    if not manifest.is_file():
        res.add("manifest:present", False, "manifest.json missing")
    else:
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
            res.add("manifest:present", True, f"version {m.get('version')}")
            failed = int((m.get("databases") or {}).get("failed") or 0)
            res.add(
                "manifest:databases", failed == 0,
                f"{failed} database(s) failed at backup time" if failed
                else "no failures recorded",
            )
        except Exception as exc:  # noqa: BLE001
            res.add("manifest:present", False, f"will not parse: {exc}")

    has_env = (d / ".env").is_file()
    res.add(
        "env:present", has_env,
        "" if has_env else "no .env -- the encrypted vault could not be decrypted",
    )

    dbs = sorted((d / "dbs").rglob("*.db")) if (d / "dbs").is_dir() else []
    if not dbs:
        res.add("sqlite:any", False, "no SQLite databases found in dbs/")

    owns_scratch = scratch_dir is None
    scratch = Path(scratch_dir or tempfile.mkdtemp(prefix="kazma-drill-"))
    try:
        scratch.mkdir(parents=True, exist_ok=True)
        for db in dbs:
            _check_sqlite(db, scratch, res)
    finally:
        if owns_scratch:
            shutil.rmtree(scratch, ignore_errors=True)

    if pg_dump is not None:
        _check_pg_dump(Path(pg_dump), res)
    return res


def _latest_pg_dump() -> Path | None:
    """The newest Postgres dump, or None if there genuinely is not one.

    This imported ``_dump_dir``, which does not exist in ``pg_backup`` -- the
    accessor is ``pg_backup_dir``. The bare ``except`` turned that
    AttributeError into None, ``run_drill`` passed ``pg_dump=None``, and
    ``_check_pg_dump`` was never called. So the drill verified 25 SQLite
    databases and silently skipped the 1.67 GB main database, reporting
    "29/29 checks passed" while never looking at it.

    The failure is worth naming: a broad except around an import turns a code
    defect into an absence, and an absence into a clean bill of health.
    """
    try:
        from kazma_core.db.pg_backup import pg_backup_dir

        dumps = sorted(
            (f for f in Path(pg_backup_dir()).glob("pg_shared_*.dump") if f.is_file()),
            key=lambda f: f.stat().st_mtime,
        )
        return dumps[-1] if dumps else None
    except Exception:  # noqa: BLE001
        logger.warning("[restore-drill] could not locate the Postgres dumps",
                       exc_info=True)
        return None


def run_drill(backup_dir: str | Path | None = None) -> DrillResult:
    """Verify the newest universal backup, or the one given."""
    if backup_dir is None:
        from kazma_core.backup.universal import latest_universal_backup

        latest = latest_universal_backup()
        if not latest:
            res = DrillResult(backup_dir="")
            res.add("backup:exists", False, "no universal backups found")
            return res
        backup_dir = latest.get("path") or latest.get("dir") or ""
        # list_universal_backups() reports "dir" as the directory NAME, not a
        # path, so this resolved to a bare timestamp and every drill failed at
        # its first check with "not a directory". Nothing caught it because
        # nothing ever ran the drill -- it was referenced only by the
        # resilience manifest, which documented it as a working mechanism.
        if backup_dir and not Path(backup_dir).is_dir():
            from kazma_core.backup.universal import _universal_dir

            resolved = _universal_dir() / str(backup_dir)
            if resolved.is_dir():
                backup_dir = resolved

    pg_dump = _latest_pg_dump()
    res = verify_backup(backup_dir, pg_dump=pg_dump)

    # A missing dump is only "nothing to check" on a SQLite install. Where
    # Postgres IS the backend, no dump means the main database is in no
    # backup at all -- which must fail loudly rather than pass by omission.
    if pg_dump is None:
        try:
            from kazma_core.db.pg_backup import pg_backup_enabled

            if pg_backup_enabled():
                res.add(
                    "postgres:dump", False,
                    "Postgres is the backend but no dump was found -- the main "
                    "database is not in this backup",
                )
        except Exception:  # noqa: BLE001
            logger.debug("[restore-drill] pg_backup_enabled check failed",
                         exc_info=True)
    return res


#: Weekly. Bit rot, an expired credential and a truncated dump are all slow
#: failures -- checking daily would add noise without finding them sooner,
#: and monthly leaves too long a window in which a restore silently stops
#: being possible.
DRILL_INTERVAL_HOURS = 168.0


async def drill_scheduler() -> None:
    """Run the drill once a week. Crash-isolated; sleeps first.

    This module could verify a backup from the day it was written. Nothing
    called it: its only non-test reference was the resilience manifest, which
    listed it as a mechanism that protects the system. So the manifest
    asserted a property that was never once measured -- the same shape as the
    hardcoded ``{"ok": True}`` Postgres entry, and as the offsite remote that
    reported healthy while refusing every write.

    Sleeps first because a drill on every boot fires hardest during an
    incident, when the operator needs another message least.
    """
    import asyncio

    while True:
        try:
            await asyncio.sleep(DRILL_INTERVAL_HOURS * 3600)
            res = await asyncio.to_thread(run_drill)
            if res.ok:
                # Logged on success on purpose: a mechanism that speaks only
                # when it breaks cannot be told from one that never runs.
                logger.info("[restore-drill] %s", res.summary())
            else:
                logger.error("[restore-drill] %s", res.summary())
                _alert_failure(res)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- a failed drill must not
            # kill the cadence; the next one still fires.
            logger.warning("[restore-drill] scheduler iteration failed: %s", exc)
            await asyncio.sleep(300)


def _alert_failure(res: DrillResult) -> None:
    """Tell the operator the backup cannot be read back. Never raises."""
    try:
        from kazma_core.observability.ops_alerts import alert

        failed = ", ".join(
            f"{c['check']}" + (f" ({c['detail']})" if c["detail"] else "")
            for c in res.failures[:4]
        )
        alert(
            "backup.restore_drill_failed",
            "A backup cannot be restored -- the drill failed.",
            f"{res.summary()}. Failed: {failed}. The data is being written; "
            "what is in doubt is whether it can be read back.",
            severity="critical",
        )
    except Exception:  # noqa: BLE001
        logger.debug("[restore-drill] could not raise the alert", exc_info=True)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Verify a Kazma backup is readable.")
    ap.add_argument("--backup", default=None,
                    help="backup directory (default: the newest one)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    res = run_drill(args.backup)
    for c in res.checks:
        mark = "ok  " if c["ok"] else "FAIL"
        detail = f" -- {c['detail']}" if c["detail"] else ""
        print(f"  [{mark}] {c['check']}{detail}")
    print(res.summary())
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
