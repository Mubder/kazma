"""Option A — safe maintenance cleanup for V2 primary memory.

1. WAL-safe backup to kazma-data/backups/
2. Archive + hard-delete soft-invalidated beliefs
3. Archive + hard-delete near-duplicate set-valued ``noted`` pairs
   (keep newest by valid_from; near-match = same first 160 normalized chars)
4. Episodes are NEVER deleted

Dry-run by default. Pass --apply to commit.

  python scripts/_memory_maint_a.py --db PATH
  python scripts/_memory_maint_a.py --db PATH --apply
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import time
from pathlib import Path


def _connect(db: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def backup_db(db_path: Path) -> Path:
    backups = db_path.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = backups / f"memory_state.maint-a-{stamp}.db"
    # sqlite3.backup is WAL-safe
    src = sqlite3.connect(str(db_path), timeout=120)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    # also copy -wal/-shm if present (optional; backup API is enough)
    return dest


def ensure_archive(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS beliefs_archive (
          id                  TEXT PRIMARY KEY,
          tenant_id           TEXT NOT NULL,
          original_belief_json TEXT NOT NULL,
          archived_at         REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_beliefs_archive_tenant
          ON beliefs_archive(tenant_id, archived_at);
        """
    )


def archive_and_delete(conn: sqlite3.Connection, ids: list[str], *, apply: bool) -> int:
    if not ids:
        return 0
    now = time.time()
    n = 0
    for bid in ids:
        row = conn.execute("SELECT * FROM beliefs WHERE id=?", (bid,)).fetchone()
        if not row:
            continue
        if not apply:
            n += 1
            continue
        payload = {k: row[k] for k in row.keys()}
        tenant = str(payload.get("tenant_id") or "default")
        conn.execute(
            """
            INSERT OR IGNORE INTO beliefs_archive
              (id, tenant_id, original_belief_json, archived_at)
            VALUES (?, ?, ?, ?)
            """,
            (bid, tenant, json.dumps(payload, default=str), now),
        )
        # drop FTS if present
        try:
            conn.execute("DELETE FROM beliefs_fts WHERE rowid IN (SELECT rowid FROM beliefs WHERE id=?)", (bid,))
        except Exception:
            try:
                conn.execute("DELETE FROM beliefs_fts WHERE id=?", (bid,))
            except Exception:
                pass
        conn.execute("DELETE FROM beliefs WHERE id=?", (bid,))
        n += 1
    if apply and n:
        conn.commit()
    return n


def invalidated_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT id FROM beliefs
        WHERE invalidated_at IS NOT NULL OR valid_until IS NOT NULL
        """
    ).fetchall()
    return [r["id"] for r in rows]


def near_dup_noted_ids(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return [(id_to_drop, reason), ...] for near-duplicate noted beliefs."""
    rows = conn.execute(
        """
        SELECT id, object, valid_from, confidence, structural_importance
        FROM beliefs
        WHERE valid_until IS NULL AND invalidated_at IS NULL
          AND lower(predicate) = 'noted'
        ORDER BY valid_from DESC
        """
    ).fetchall()
    groups: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        key = re.sub(r"\s+", " ", (r["object"] or "").strip().lower())[:160]
        if not key:
            continue
        groups.setdefault(key, []).append(r)

    out: list[tuple[str, str]] = []
    for key, members in groups.items():
        if len(members) <= 1:
            continue
        # keep newest
        members_sorted = sorted(
            members,
            key=lambda r: float(r["valid_from"] or 0),
            reverse=True,
        )
        keep = members_sorted[0]["id"]
        for r in members_sorted[1:]:
            out.append(
                (
                    r["id"],
                    f"near-dup noted (keep {keep[:20]}…): {key[:50]}…",
                )
            )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return 1

    print(f"DB: {db_path}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")

    # Counts before
    conn = _connect(str(db_path))
    ensure_archive(conn)
    live_b = conn.execute(
        "SELECT COUNT(*) FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL"
    ).fetchone()[0]
    dead_b = conn.execute(
        "SELECT COUNT(*) FROM beliefs WHERE invalidated_at IS NOT NULL OR valid_until IS NOT NULL"
    ).fetchone()[0]
    try:
        live_e = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    except Exception:
        live_e = -1
    arch = conn.execute("SELECT COUNT(*) FROM beliefs_archive").fetchone()[0]
    print(f"Before: live_beliefs={live_b} dead_beliefs={dead_b} episodes={live_e} archive={arch}")

    inv = invalidated_ids(conn)
    dups = near_dup_noted_ids(conn)
    print(f"\nInvalidated to archive+delete: {len(inv)}")
    print(f"Near-dup noted to archive+delete: {len(dups)}")
    for bid, reason in dups[:20]:
        print(f"  {bid[:36]}  {reason}")

    # Episodes policy
    print("\nEpisodes: LEFT UNTOUCHED (conversation diary).")

    if not args.apply:
        print("\nRe-run with --apply to backup + commit.")
        conn.close()
        return 0

    conn.close()

    # Backup first
    print("\n=== BACKUP ===")
    dest = backup_db(db_path)
    print(f"  WAL-safe backup -> {dest} ({dest.stat().st_size} bytes)")

    conn = _connect(str(db_path))
    ensure_archive(conn)
    inv = invalidated_ids(conn)
    dups = near_dup_noted_ids(conn)
    dup_ids = [d[0] for d in dups]

    n1 = archive_and_delete(conn, inv, apply=True)
    print(f"\nArchived+deleted invalidated: {n1}")
    n2 = archive_and_delete(conn, dup_ids, apply=True)
    print(f"Archived+deleted near-dup noted: {n2}")

    # VACUUM optional (rewrite) — skip if locked; ANALYZE only
    try:
        conn.execute("ANALYZE")
        conn.commit()
    except Exception as exc:
        print(f"ANALYZE skipped: {exc}")

    live_b = conn.execute(
        "SELECT COUNT(*) FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL"
    ).fetchone()[0]
    dead_b = conn.execute(
        "SELECT COUNT(*) FROM beliefs WHERE invalidated_at IS NOT NULL OR valid_until IS NOT NULL"
    ).fetchone()[0]
    arch = conn.execute("SELECT COUNT(*) FROM beliefs_archive").fetchone()[0]
    print(f"\nAfter: live_beliefs={live_b} dead_beliefs={dead_b} archive={arch}")

    # Functional SoT still present
    print("\nFunctional SoT:")
    for r in conn.execute(
        """
        SELECT predicate, substr(object,1,90) AS o FROM beliefs
        WHERE valid_until IS NULL AND invalidated_at IS NULL
          AND predicate IN ('grok_next_reset','zcode_next_reset')
        """
    ):
        print(f"  {r['predicate']}: {r['o']}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
