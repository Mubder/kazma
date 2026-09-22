#!/usr/bin/env python
"""Prove a Postgres dump RESTORES, not merely that it reads back.

The weekly deep drill streams the whole archive through ``pg_restore
--file=-`` and throws the SQL away. That proves every data block decompresses
and that nothing is truncated -- real integrity, and not the same thing as
"I can bring the system back". The SQL is never executed, so a missing
extension, an absent role, or a server-version mismatch is invisible to it
and would surface for the first time during an actual incident.

This restores into a throwaway database and drops it again.

    python scripts/restore_rehearsal.py              # schema only (seconds)
    python scripts/restore_rehearsal.py --with-data  # full restore (minutes)
    python scripts/restore_rehearsal.py --dump path/to/pg_shared_*.dump

Schema-only is the default on purpose. It is fast and small enough to run
whenever you like, and it catches the failures that actually bite: extensions,
roles, types, constraints, and version skew. Data integrity is already covered
by the drill's full read-back, so the two together answer the whole question
at a fraction of the cost of restoring 1.9 GB.

Why this is a script and not a weekly check
-------------------------------------------
It CREATES and DROPS a database on the live server. Everything the deep drill
does is read-only; this is not, and an unattended job that creates databases
on production is a different risk class from one that reads a file. Run it
deliberately -- before and after a migration, after a Postgres upgrade, or
whenever you want the evidence.

Safety
------
* The scratch database is always ``kazma_rehearsal_<epoch>``, and the drop
  refuses any name that does not match that prefix.
* The drop runs in ``finally``, and leftovers from an earlier killed run are
  swept at startup, so a crash costs disk rather than a manual cleanup.
* The live database is never connected to except to issue CREATE/DROP of the
  scratch one, and is never written.
* The password is never printed; argv is redacted in all output.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in (REPO / "kazma-core", REPO / "kazma-ui"):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

def rehearsal_targets_live_database(live_dsn: str, rehearsal_dsn: str) -> bool:
    """True when both URLs name the same host, port and database.

    An isolated rehearsal must use a disposable server. Matching the live
    URL means CREATE DATABASE would land on production.
    """
    from kazma_core.migration.pg_bridge import _parse_dsn

    if not (live_dsn or "").strip() or not (rehearsal_dsn or "").strip():
        return True
    live = _parse_dsn(live_dsn)
    other = _parse_dsn(rehearsal_dsn)
    return (
        live.host == other.host
        and str(live.port) == str(other.port)
        and live.dbname == other.dbname
    )


SCRATCH_PREFIX = "kazma_rehearsal_"
#: Never drop anything that is not obviously ours.
SCRATCH_RE = re.compile(rf"^{SCRATCH_PREFIX}\d+$")


def _redact(argv: list[str]) -> str:
    """Log-safe argv. Replaces the VALUE only -- the `-e` before it is its
    own element, and emitting "-e PGPASSWORD=***" here printed it twice."""
    return " ".join(
        "PGPASSWORD=***" if a.startswith("PGPASSWORD=") else a for a in argv
    )


class Runner:
    """Builds and runs libpq client commands the way pg_bridge does."""

    def __init__(self, dsn: str | None = None) -> None:
        from kazma_core.db.backend import get_database_url
        from kazma_core.migration import pg_bridge as pb

        self._pb = pb
        self.dsn = dsn or get_database_url()
        if not self.dsn or not self.dsn.startswith("postgres"):
            raise SystemExit(f"not a Postgres deployment (dsn={self.dsn!r})")
        self.parts = pb._parse_dsn(self.dsn)
        self.env = {**os.environ, **pb._libpq_env(self.parts)}

    def _cmd(self, tool: str, args: list[str], dbname: str) -> list[str]:
        pb = self._pb
        prefix = list(pb._resolve_tool(tool))
        conn = pb._docker_adjusted_parts(prefix, self.parts)
        cmd = prefix + [
            "-U", conn.user, "-h", conn.host, "-p", conn.port, "-d", dbname,
        ] + args
        return pb._with_docker_pgpassword(cmd, self.parts)

    def psql(self, sql: str, dbname: str, timeout: float = 120) -> tuple[bool, str]:
        cmd = self._cmd("psql", ["-tAc", sql], dbname)
        proc = subprocess.run(
            cmd, env=self.env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        return proc.returncode == 0, out if proc.returncode == 0 else err

    def pg_restore(self, dump: Path, dbname: str, *, schema_only: bool,
                   timeout: float) -> tuple[bool, str]:
        pb = self._pb
        prefix = list(pb._resolve_tool("pg_restore"))
        conn = pb._docker_adjusted_parts(prefix, self.parts)
        args = [
            "-U", conn.user, "-h", conn.host, "-p", conn.port, "-d", dbname,
            "--no-owner", "--no-privileges", "--exit-on-error",
        ]
        if schema_only:
            args.append("--schema-only")
        cmd = pb._with_docker_pgpassword(prefix + args, self.parts)

        # The same host-path trap the drill hit: a containerised pg_restore
        # cannot see the host's file, so the archive goes over stdin.
        via_docker = bool(prefix) and "docker" in prefix[0].lower()
        print(f"  $ {_redact(cmd)}{'  < ' + dump.name if via_docker else ''}")
        if via_docker:
            with dump.open("rb") as fh:
                proc = subprocess.run(cmd, stdin=fh, env=self.env,
                                      capture_output=True, text=True,
                                      encoding="utf-8", errors="replace",
                                      timeout=timeout)
        else:
            proc = subprocess.run(cmd + [str(dump)], env=self.env,
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=timeout)
        return proc.returncode == 0, (proc.stderr or "").strip()


def _latest_dump() -> Path | None:
    from kazma_core.backup.restore_drill import _latest_pg_dump

    return _latest_pg_dump()


def _sweep_leftovers(r: Runner) -> int:
    ok, out = r.psql(
        "select datname from pg_database where datname like "
        f"'{SCRATCH_PREFIX}%'", "postgres")
    if not ok:
        return 0
    swept = 0
    for name in [ln.strip() for ln in out.splitlines() if ln.strip()]:
        if not SCRATCH_RE.match(name):
            continue
        dropped, _ = r.psql(f'drop database if exists "{name}"', "postgres")
        swept += int(bool(dropped))
        print(f"  swept leftover scratch database {name}")
    return swept


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dump", help="archive to rehearse (default: newest)")
    ap.add_argument("--with-data", action="store_true",
                    help="restore data too, not just the schema (minutes)")
    ap.add_argument("--timeout", type=float, default=3600.0)
    ap.add_argument(
        "--isolated",
        action="store_true",
        help="require KAZMA_REHEARSAL_DATABASE_URL on a different server than the live database",
    )
    args = ap.parse_args()
    if args.isolated:
        from kazma_core.db.backend import get_database_url

        live = get_database_url() or ""
        rehearsal = (os.environ.get("KAZMA_REHEARSAL_DATABASE_URL") or "").strip()
        if not rehearsal or rehearsal_targets_live_database(live, rehearsal):
            print(
                "refusing: --isolated needs KAZMA_REHEARSAL_DATABASE_URL "
                "pointing at a different Postgres server than KAZMA_DATABASE_URL"
            )
            return 2
        isolated_dsn = rehearsal
    else:
        isolated_dsn = None

    dump = Path(args.dump) if args.dump else _latest_dump()
    if dump is None or not dump.is_file():
        print("no Postgres dump found")
        return 1

    r = Runner(dsn=isolated_dsn)
    size_gb = dump.stat().st_size / (1024 ** 3)
    print(f"dump    : {dump}")
    print(f"size    : {size_gb:.2f} GB")
    print(f"mode    : {'schema + DATA' if args.with_data else 'schema only'}")
    print(f"server  : {r.parts.host}:{r.parts.port} db={r.parts.dbname}\n")

    _sweep_leftovers(r)

    scratch = f"{SCRATCH_PREFIX}{int(time.time())}"
    assert SCRATCH_RE.match(scratch), scratch
    print(f"creating scratch database {scratch}")
    ok, err = r.psql(f'create database "{scratch}"', "postgres")
    if not ok:
        print(f"FAILED to create scratch database: {err[:300]}")
        return 1

    t0 = time.time()
    try:
        ok, err = r.pg_restore(dump, scratch, schema_only=not args.with_data,
                               timeout=args.timeout)
        elapsed = time.time() - t0
        if not ok:
            print(f"\nRESTORE FAILED after {elapsed:.0f}s")
            print(err[:1500])
            return 1

        checks: list[tuple[str, str]] = []
        for label, sql in [
            ("tables", "select count(*) from information_schema.tables "
                       "where table_schema not in ('pg_catalog','information_schema')"),
            ("indexes", "select count(*) from pg_indexes "
                        "where schemaname not in ('pg_catalog','information_schema')"),
            ("extensions", "select count(*) from pg_extension"),
        ]:
            good, val = r.psql(sql, scratch)
            checks.append((label, val if good else f"query failed: {val[:80]}"))

        print(f"\nrestored in {elapsed:.0f}s")
        for label, val in checks:
            print(f"  {label:11}: {val}")

        tables = next((v for k, v in checks if k == "tables"), "0")
        if not tables.isdigit() or int(tables) == 0:
            print("\nVERDICT: restore reported success but the database is EMPTY")
            return 1
        # NOTE: no multi-line expression inside an f-string brace. That is
        # PEP 701 (Python 3.12+); this repo's venv is 3.12 but the live
        # install runs 3.11, so it parsed here and was a SyntaxError there.
        scope = (
            "" if args.with_data
            else " (schema only -- data integrity is covered by the drill "
                 "read-back)"
        )
        print(f"\nVERDICT: the dump restores. {tables} tables rebuilt from "
              f"the archive on a live server{scope}.")
        return 0
    finally:
        if SCRATCH_RE.match(scratch):
            dropped, derr = r.psql(f'drop database if exists "{scratch}"',
                                   "postgres")
            print(f"dropped scratch database {scratch}"
                  if dropped else f"WARNING: could not drop {scratch}: {derr[:200]}")
        else:  # unreachable by construction; the guard is the point
            print(f"REFUSED to drop {scratch!r}: not a scratch name")


if __name__ == "__main__":
    raise SystemExit(main())
