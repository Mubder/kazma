"""One-shot mirror reconciliation: make kazma_beliefs (PG) match the SQLite SoT.

Usage:
    python scripts/reconcile_memory_mirror.py [--dry-run] [--db PATH]

Reads KAZMA_DATABASE_URL / data-dir from the environment (.env honored).
Safe to run while the server is up — short transactions per row.
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report only")
    parser.add_argument("--db", default=None, help="override primary memory DB path")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass

    import sqlite3

    from kazma_core.memory.state_backend import reconcile_state_beliefs
    from kazma_core.paths import primary_memory_db

    db_path = args.db or primary_memory_db()
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        stats = reconcile_state_beliefs(conn, dry_run=args.dry_run)
    finally:
        conn.close()

    print("mirror reconciliation " + ("(dry-run) " if args.dry_run else "") + "->")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return 1 if stats.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
