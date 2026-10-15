"""Size, then relabel, the facts the memory tools stored as the user's.

Memory audit M03 (relabelling, 2026-10-15). See
``kazma_core/memory/provenance_relabel.py`` for which facts change, which are
kept and why nothing is guessed.

Usage:
    python scripts/memory_relabel_provenance.py size [--db PATH] [--ops-db PATH]
    python scripts/memory_relabel_provenance.py apply --db PATH --yes [--ops-db PATH]

``size`` only reads, and names facts by id, never by value. ``apply`` needs
the database named and ``--yes``; it first copies the file to
``<db>.pre_relabel`` (one SQLite step), then relabels in one transaction and
writes one ``memory_audit_log`` row per fact to the operational database
(``--ops-db``, default the ``memory_ops.db`` beside ``--db``). A chat's other
key (session or thread id) is read from this install's chat store. Run it
while Kazma is idle.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path


def _copy_first(path: str) -> str:
    """The copy taken before the change, ``<db>.pre_relabel`` (replacing an
    earlier one): written whole to a temporary name, then renamed."""
    from kazma_core.db.sqlite_snapshot import snapshot_copy

    backup = path + ".pre_relabel"
    tmp = backup + ".tmp"
    snapshot_copy(path, tmp)
    os.replace(tmp, backup)
    return backup


def main(argv: list[str] | None = None, *, keys_of=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("size", "apply"))
    parser.add_argument("--db", default=None, help="memory_state.db (default: this install's)")
    parser.add_argument("--ops-db", default=None, help="memory_ops.db (default: beside --db)")
    parser.add_argument("--yes", action="store_true", help="apply: confirm the change")
    args = parser.parse_args(argv)

    from kazma_core.env_files import load_env_files

    load_env_files()
    from kazma_core.memory.provenance_relabel import apply_relabel, size_relabel
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
    if args.action == "size":
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn, \
                closing(sqlite3.connect(f"file:{ops_path}?mode=ro", uri=True)) as ops:
            print(json.dumps(size_relabel(conn, ops, keys_of=keys_of), indent=1, ensure_ascii=False))
        return 0
    backup = _copy_first(path)
    with closing(sqlite3.connect(path, timeout=30)) as conn, closing(sqlite3.connect(ops_path, timeout=30)) as ops:
        ensure_ops_schema(ops)
        print(json.dumps(size_relabel(conn, ops, keys_of=keys_of), indent=1, ensure_ascii=False))
        out = apply_relabel(conn, ops, keys_of=keys_of)
    print(json.dumps({**out, "copy_before": backup}, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
