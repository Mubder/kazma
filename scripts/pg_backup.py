#!/usr/bin/env python3
"""Manual control for Kazma's Postgres shared-state backups.

Commands:
  backup   Dump exactly Kazma's PG tables now (same path as the nightly job).
  restore  Restore a dump back into the configured Postgres DB
           (--file <name> | --latest; --dry-run to preview).
  list     Show existing dumps, newest first.

The nightly scheduler normally runs ``backup`` every 24h (first sweep ~2 min
after boot, see kazma_core/memory/worker_bootstrap.py). This script exists for
the two manual moments: taking an immediate dump after big changes, and
restoring after a disaster (e.g. the 2026-08-14 incident where another app
dropped Kazma's tables from a shared database).

Usage:
  python scripts/pg_backup.py backup
  python scripts/pg_backup.py list
  python scripts/pg_backup.py restore --latest
  python scripts/pg_backup.py restore --file pg_shared_1712345678.dump --dry-run

Prerequisites: pip install -e ".[postgres]" and KAZMA_DATABASE_URL set
(.env in the working directory is loaded automatically).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manual Postgres backup/restore for Kazma shared-state tables."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("backup", help="dump Kazma's PG tables now")
    sub.add_parser("list", help="list existing dumps (newest first)")
    p_restore = sub.add_parser("restore", help="restore a dump into the configured PG")
    p_restore.add_argument("--file", default=None, help="dump filename in the backups dir")
    p_restore.add_argument("--latest", action="store_true", help="restore the newest dump")
    p_restore.add_argument("--dry-run", action="store_true", help="preview only")

    args = parser.parse_args()

    # Load .env from CWD if present — the server loads it itself, but a
    # manual script run from the install root must too (KAZMA_DATABASE_URL,
    # KAZMA_DB_CONTAINER, KAZMA_DB_BACKEND all live there).
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    sys.path.insert(0, str(Path.cwd() / "kazma-core"))

    if args.cmd == "backup":
        from kazma_core.db.pg_backup import perform_pg_backup, pg_backup_enabled

        if not pg_backup_enabled():
            print(
                "ERROR: Postgres backend not configured (KAZMA_DATABASE_URL) "
                "or backups.pg.enabled is off.",
                file=sys.stderr,
            )
            return 1
        path = perform_pg_backup()
        if path is None:
            print("ERROR: backup FAILED — check the server logs.", file=sys.stderr)
            return 1
        print(f"OK -> {path}")
        return 0

    if args.cmd == "list":
        from kazma_core.db.pg_backup import pg_backup_dir

        files = sorted(
            pg_backup_dir().glob("pg_shared_*.dump"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            print("(no dumps yet)")
            return 0
        for f in files:
            print(f"{f.stat().st_size / (1024 * 1024):9.1f} MB  {f.name}")
        return 0

    if args.cmd == "restore":
        from kazma_core.db.backend import get_database_url
        from kazma_core.db.pg_backup import latest_pg_backup, pg_backup_dir
        from kazma_core.migration.pg_bridge import PgBridgeError, PgToolNotFound, restore_database

        dsn = get_database_url()
        if not dsn:
            print("ERROR: KAZMA_DATABASE_URL not set.", file=sys.stderr)
            return 1
        if args.file:
            dump = pg_backup_dir() / args.file
        elif args.latest:
            dump = latest_pg_backup()
        else:
            print("ERROR: pass --file <name> or --latest.", file=sys.stderr)
            return 1
        if dump is None or not dump.exists():
            print(
                "ERROR: dump not found. Run 'python scripts/pg_backup.py list' "
                "to see what exists.",
                file=sys.stderr,
            )
            return 1
        size_mb = dump.stat().st_size / (1024 * 1024)
        target = dsn.split("@")[-1]  # never print the userinfo (password)
        if args.dry_run:
            print(f"Would restore {dump.name} ({size_mb:.1f} MB) into {target}")
            return 0
        print(f"Restoring {dump.name} ({size_mb:.1f} MB) into {target} ...")
        try:
            warnings = restore_database(dump, dsn)
        except (PgToolNotFound, PgBridgeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"Done ({warnings} warning line(s)). Only Kazma's own tables are touched.")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
