"""Size, then migrate, a memory database to entities keyed by (tenant_id, id).

Memory audit M21 (2026-10-15). See ``kazma_core/memory/entity_key.py`` for
what changes and why every row keeps its id.

Usage:
    python scripts/memory_entity_key.py size [--db PATH]
    python scripts/memory_entity_key.py apply --db PATH --yes

``size`` only reads. ``apply`` needs the database named and ``--yes``; it
first copies the file to ``<db>.pre_entity_key`` (one SQLite step,
``db.sqlite_snapshot.snapshot_copy``), then migrates it in one transaction.
Run it while Kazma is idle (the server holds the memory database open; the
migration takes its write lock for the rebuild). A database already keyed by
the pair is left as it is.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing


def _copy_first(path: str) -> str:
    """The copy taken before the change, ``<db>.pre_entity_key`` (replacing
    an earlier one): written whole to a temporary name, then renamed."""
    from kazma_core.db.sqlite_snapshot import snapshot_copy

    backup = path + ".pre_entity_key"
    tmp = backup + ".tmp"
    snapshot_copy(path, tmp)
    os.replace(tmp, backup)
    return backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("size", "apply"))
    parser.add_argument("--db", default=None, help="memory_state.db (default: this install's)")
    parser.add_argument("--yes", action="store_true", help="apply: confirm the change")
    args = parser.parse_args(argv)

    from kazma_core.env_files import load_env_files

    load_env_files()
    from kazma_core.memory.entity_key import migrate_entity_key, size_entity_key
    from kazma_core.paths import primary_memory_db

    if args.action == "apply" and (not args.db or not args.yes):
        print("apply needs --db PATH and --yes (the file is copied first)", file=sys.stderr)
        return 2
    path = args.db or str(primary_memory_db())
    if not os.path.exists(path):
        print(f"no database at {path}", file=sys.stderr)
        return 2
    if args.action == "size":
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            print(json.dumps(size_entity_key(conn), indent=1, ensure_ascii=False))
        return 0
    with closing(sqlite3.connect(path, timeout=30)) as conn:
        size = size_entity_key(conn)
        print(json.dumps(size, indent=1, ensure_ascii=False))
        if size.get("keyed_by") != "id":
            print(json.dumps({"migrated": False, "keyed_by": size.get("keyed_by")}, indent=1))
            return 0
    backup = _copy_first(path)
    with closing(sqlite3.connect(path, timeout=30, isolation_level=None)) as conn:
        out = migrate_entity_key(conn)
    print(json.dumps({**out, "copy_before": backup}, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
