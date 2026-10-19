"""Size, then reopen, the list entries a single-valued memory write closed.

Live 2026-10-19 (see ``kazma_core/memory/list_reopen.py``): one wrongly
labelled fact closed 57 of the owner's notes and superseded one more.

Usage:
    python scripts/memory_reopen_lists.py size [--db PATH] [--written-after ISO_OR_EPOCH]
    python scripts/memory_reopen_lists.py apply --db PATH --yes [--ops-db PATH] [--written-after ...]

``size`` only reads, and names entries by id, never by value. ``apply`` needs
the database named and ``--yes``; it first copies the file to
``<db>.pre_reopen`` (one SQLite step), then reopens in one transaction and
writes one ``memory_audit_log`` row per entry to the operational database
(``--ops-db``, default the ``memory_ops.db`` beside ``--db``).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def _copy_first(path: str) -> str:
    """The copy taken before the change, ``<db>.pre_reopen``: written whole to
    a temporary name, then renamed."""
    from kazma_core.db.sqlite_snapshot import snapshot_copy

    backup = path + ".pre_reopen"
    tmp = backup + ".tmp"
    snapshot_copy(path, tmp)
    os.replace(tmp, backup)
    return backup


def _epoch(value: str) -> float:
    if not value:
        return 0.0
    try:
        return float(value)
    except ValueError:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.timestamp()


def _report(groups: list[dict]) -> dict:
    return {
        "writes": len(groups),
        "entries": sum(len(g["closed"]) for g in groups),
        "groups": [{**g, "written_at": datetime.fromtimestamp(g["written_at"], timezone.utc).isoformat(),
                    "closed": len(g["closed"]), "ids": g["closed"]} for g in groups],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("size", "apply"))
    parser.add_argument("--db", default=None, help="memory_state.db (default: this install's)")
    parser.add_argument("--ops-db", default=None, help="memory_ops.db (default: beside --db)")
    parser.add_argument("--written-after", default="", help="only writes after this time (ISO or epoch)")
    parser.add_argument("--yes", action="store_true", help="apply: confirm the change")
    args = parser.parse_args(argv)

    from kazma_core.env_files import load_env_files

    load_env_files()
    from kazma_core.memory.list_reopen import find_closed_entries, reopen_entries
    from kazma_core.memory.schema_v2 import ensure_ops_schema
    from kazma_core.paths import memory_ops_db, primary_memory_db

    if args.action == "apply" and (not args.db or not args.yes):
        print("apply needs --db PATH and --yes (the file is copied first)", file=sys.stderr)
        return 2
    path = args.db or str(primary_memory_db())
    ops_path = args.ops_db or (str(Path(path).with_name("memory_ops.db")) if args.db else str(memory_ops_db()))
    for name in (path, ops_path):
        if not os.path.exists(name):
            print(f"no database at {name}", file=sys.stderr)
            return 2
    after = _epoch(args.written_after)
    if args.action == "size":
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            print(json.dumps(_report(find_closed_entries(conn, written_after=after)), indent=1))
        return 0
    backup = _copy_first(path)
    with closing(sqlite3.connect(path, timeout=30, isolation_level=None)) as conn, \
            closing(sqlite3.connect(ops_path, timeout=30)) as ops:
        ensure_ops_schema(ops)
        groups = find_closed_entries(conn, written_after=after)
        print(json.dumps(_report(groups), indent=1))
        out = reopen_entries(conn, ops, groups)
    print(json.dumps({**out, "copy_before": backup}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
