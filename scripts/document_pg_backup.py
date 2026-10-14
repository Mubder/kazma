#!/usr/bin/env python3
"""Coordinated backup, verification and restore of a Postgres document store.

The document tables and the content files they reference are captured at ONE
point in time (``kazma_core.documents.backup_pg``): the scheduled backup does
this every six hours; this script is for the two manual moments.

Commands:
  backup   Back up the configured store now (KAZMA_DATABASE_URL + the
           installed document storage root, or --storage-root).
  verify   Check a backup directory: every required object present, every
           checksum matching, a real custom-format dump.
  restore  Restore a backup into an EMPTY database (--target-url) and an
           EMPTY storage root (--storage-root). Never into the live store:
           a target that already has document tables or files is refused.
           After restoring the whole-database dump into the same database,
           pass --replace-document-tables: that dump's document tables are
           older than this backup's files and are dropped first (the drilled
           order, tests/test_document_pg_restore_order.py).
           Afterwards point the install at the restored store and restart;
           the service resumes queued work and rolls back intakes that had
           not finished at the backup's snapshot, and re-publishes to the
           knowledge libraries every document it records as indexed
           ("reindex_required"; the knowledge index is a separate store).
  index    Compare what the document store records as searchable with what
           the knowledge libraries hold, and report the difference. With
           --apply, re-publish what is missing; with --apply --prune, also
           remove chunks of documents the store does not record as indexed
           (deleted, unknown or unindexed). Run --apply only while Kazma is
           stopped: the running server repairs the missing ones itself at
           every start, and only it serializes with live unindexing.

Usage:
  python scripts/document_pg_backup.py backup --dest <dir>
  python scripts/document_pg_backup.py verify <backup dir>
  python scripts/document_pg_backup.py restore <backup dir> --target-url <url> --storage-root <dir> [--replace-document-tables]
  python scripts/document_pg_backup.py index [--tenant <id>] [--apply [--prune]]

Prerequisites: pip install -e ".[postgres]"; pg_dump/pg_restore on PATH or in
the database container (KAZMA_DB_CONTAINER).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_backup = sub.add_parser("backup", help="back up the configured store now")
    p_backup.add_argument("--dest", required=True, help="directory to write the backup into")
    p_backup.add_argument("--storage-root", default=None, help="document storage root (default: installed)")
    p_verify = sub.add_parser("verify", help="verify a backup directory")
    p_verify.add_argument("backup")
    p_restore = sub.add_parser("restore", help="restore into an empty database and storage root")
    p_restore.add_argument("backup")
    p_restore.add_argument("--target-url", required=True, help="postgresql:// URL of an EMPTY database")
    p_restore.add_argument("--storage-root", required=True, help="an EMPTY directory for the content")
    p_restore.add_argument(
        "--replace-document-tables", action="store_true",
        help="after restoring the whole-database dump (scripts/pg_backup.py restore) into the same "
             "database: drop its older document tables, then restore this backup's",
    )
    p_index = sub.add_parser("index", help="compare the knowledge index with the document store")
    p_index.add_argument("--apply", action="store_true", help="re-publish what is missing (Kazma stopped)")
    p_index.add_argument("--prune", action="store_true", help="with --apply: also remove what is not recorded")
    p_index.add_argument("--tenant", default=None, help="one tenant only")
    args = parser.parse_args(argv)

    from kazma_core.env_files import load_env_files

    load_env_files()

    from kazma_core.documents.backup_pg import (
        perform_pg_document_backup,
        restore_pg_document_backup,
        verify_pg_document_backup,
    )

    if args.cmd == "index":
        from kazma_core.documents.ingestion import create_default_ingestion_service

        service = create_default_ingestion_service()
        try:
            report = service.reconcile_index(apply=args.apply, prune=args.prune, tenant_id=args.tenant)
        finally:
            service.close()
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return 0 if report["ok"] and not report["failed"] else 1
    if args.cmd == "verify":
        result = verify_pg_document_backup(args.backup)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["ok"] else 1
    if args.cmd == "backup":
        from kazma_core.db.backend import get_database_url
        from kazma_core.documents.config import get_document_storage_root

        dsn = get_database_url()
        if not dsn:
            print("KAZMA_DATABASE_URL is not set", file=sys.stderr)
            return 2
        root = Path(args.storage_root) if args.storage_root else get_document_storage_root()
        report = perform_pg_document_backup(dsn=dsn, storage_root=root, dest_dir=args.dest)
        report.pop("checksums", None)
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return 0 if report.get("ok") else 1
    report = restore_pg_document_backup(
        args.backup, target_dsn=args.target_url, storage_root=args.storage_root,
        replace_document_tables=args.replace_document_tables,
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
