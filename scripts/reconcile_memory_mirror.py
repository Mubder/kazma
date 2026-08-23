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
    parser.add_argument(
        "--force",
        action="store_true",
        help="skip the small-SoT safety guard (see below)",
    )
    args = parser.parse_args()

    # Resolve env from THIS repo's root (script lives in <root>/scripts), not
    # the process CWD — a wrong-CWD .env once pointed this script at an
    # unrelated near-empty DB and it dutifully deleted 433 good mirror rows.
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(repo_root, ".env"))
    except Exception:
        pass

    import sqlite3

    from kazma_core.memory.state_backend import reconcile_state_beliefs
    from kazma_core.paths import primary_memory_db

    db_path = args.db or primary_memory_db()
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        n = conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0]
    except Exception as exc:
        print(f"ABORT: cannot read beliefs table at {db_path}: {exc}")
        conn.close()
        return 2
    if n < 10 and not args.force:
        print(
            f"ABORT: {db_path} contains only {n} belief row(s). That looks like "
            "the WRONG data dir (empty clone / fresh install) — reconciling "
            "against it would delete the real mirror rows. Point --db at the "
            "production memory_state.db or pass --force to override."
        )
        conn.close()
        return 2
    print(f"SoT: {db_path} ({n} beliefs)")

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
