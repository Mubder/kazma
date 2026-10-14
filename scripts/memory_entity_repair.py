"""Size, then repair, facts the old entity-id rule collapsed into ``entity``.

Memory audit M02 (2026-10-13). See ``kazma_core/memory/entity_repair.py`` for
what is repaired and why nothing is guessed.

Usage:
    python scripts/memory_entity_repair.py size [--db PATH]
    python scripts/memory_entity_repair.py apply --db PATH --yes

``size`` only reads. ``apply`` needs the database named and ``--yes``; run it
on a copy first. It quarantines the collapsed facts and queues their turns for
the deep pass of the install whose data directory this process resolves (with
``--no-queue`` nothing is queued: a repair of a copy). Run it while Kazma is
idle; the server's memory worker extracts the queued turns.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("size", "apply"))
    parser.add_argument("--db", default=None, help="memory_state.db (default: this install's)")
    parser.add_argument("--yes", action="store_true", help="apply: confirm the change")
    parser.add_argument("--no-queue", action="store_true", help="apply: queue no turn (a copy)")
    args = parser.parse_args(argv)

    from kazma_core.env_files import load_env_files

    load_env_files()
    from kazma_core.memory.entity_repair import repair_collapsed, size_collapsed
    from kazma_core.paths import primary_memory_db

    if args.action == "apply" and (not args.db or not args.yes):
        print("apply needs --db PATH and --yes (run it on a copy first)", file=sys.stderr)
        return 2
    path = args.db or str(primary_memory_db())
    if args.action == "size":
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            print(json.dumps(size_collapsed(conn), indent=1, ensure_ascii=False))
        finally:
            conn.close()
        return 0
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        print(json.dumps(size_collapsed(conn), indent=1, ensure_ascii=False))
        out = repair_collapsed(conn, queue=not args.no_queue)
        print(json.dumps(out, indent=1, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
