#!/usr/bin/env python3
"""Full SQLite → Postgres migration for Kazma stores.

Migrates:
  * settings.db        → kazma_settings
  * chat_sessions.db   → kazma_chat_sessions
  * swarm_tasks.db     → kazma_swarm_tasks (+ worker metrics)

Prerequisites:
  pip install -e ".[postgres]"
  export KAZMA_DATABASE_URL=postgresql://kazma:pass@localhost:5432/kazma

Usage:
  python scripts/migrate_sqlite_to_postgres.py
  python scripts/migrate_sqlite_to_postgres.py --dry-run
  python scripts/migrate_sqlite_to_postgres.py --data-dir kazma-data
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any


def _connect(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate all Kazma SQLite stores → Postgres")
    parser.add_argument("--data-dir", default="kazma-data")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    url = (os.environ.get("KAZMA_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        print("ERROR: set KAZMA_DATABASE_URL", file=sys.stderr)
        return 1
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]

    data = Path(args.data_dir)
    settings_path = data / "settings.db"
    chat_path = data / "chat_sessions.db"
    swarm_path = data / "swarm_tasks.db"

    if args.dry_run:
        for p in (settings_path, chat_path, swarm_path):
            c = _connect(p)
            if c is None:
                print(f"  skip missing {p}")
                continue
            try:
                n = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
                print(f"  {p}: {n} tables")
            finally:
                c.close()
        return 0

    os.environ["KAZMA_DATABASE_URL"] = url
    os.environ["KAZMA_DB_BACKEND"] = "postgres"
    sys.path.insert(0, str(Path.cwd() / "kazma-core"))
    from kazma_core.db.postgres_pool import get_postgres_pool, reset_postgres_pool

    reset_postgres_pool()
    pool = get_postgres_pool()
    if pool is None:
        print("ERROR: could not open Postgres pool", file=sys.stderr)
        return 1

    # Force TaskStore schema extras
    from kazma_core.swarm.task_store import TaskStore

    TaskStore()  # ensures ALTER columns on pg

    # ── settings ───────────────────────────────────────────────────
    n_settings = 0
    sc = _connect(settings_path)
    if sc is not None:
        try:
            rows = sc.execute(
                "SELECT key, value, category, updated_at FROM settings"
            ).fetchall()
        except Exception:
            rows = []
        with pool.connection() as pg:
            with pg.cursor() as cur:
                for r in rows:
                    cur.execute(
                        """
                        INSERT INTO kazma_settings (key, value, category, updated_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (key) DO UPDATE SET
                          value = EXCLUDED.value,
                          category = EXCLUDED.category,
                          updated_at = EXCLUDED.updated_at
                        """,
                        (
                            r["key"],
                            r["value"] if isinstance(r["value"], str) else json.dumps(r["value"]),
                            r["category"] or "general",
                            r["updated_at"] or "",
                        ),
                    )
                    n_settings += 1
            pg.commit()
        sc.close()
    print(f"OK settings → {n_settings} keys")

    # ── chat sessions ──────────────────────────────────────────────
    n_chat = 0
    cc = _connect(chat_path)
    if cc is not None:
        try:
            rows = cc.execute(
                "SELECT tenant_id, session_id, messages, created_at, total_cost, "
                "total_tokens, thread_id, updated_at, title, archived FROM sessions"
            ).fetchall()
        except Exception:
            # older schema without optional cols
            rows = cc.execute(
                "SELECT tenant_id, session_id, messages, created_at, total_cost, "
                "total_tokens, thread_id FROM sessions"
            ).fetchall()
        with pool.connection() as pg:
            with pg.cursor() as cur:
                for r in rows:
                    keys = r.keys()
                    msgs = r["messages"] if "messages" in keys else "[]"
                    cur.execute(
                        """
                        INSERT INTO kazma_chat_sessions (
                            tenant_id, session_id, messages, created_at,
                            total_cost, total_tokens, thread_id, updated_at,
                            title, archived
                        ) VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (tenant_id, session_id) DO UPDATE SET
                            messages = EXCLUDED.messages,
                            total_cost = EXCLUDED.total_cost,
                            total_tokens = EXCLUDED.total_tokens,
                            thread_id = EXCLUDED.thread_id,
                            updated_at = EXCLUDED.updated_at,
                            title = EXCLUDED.title,
                            archived = EXCLUDED.archived
                        """,
                        (
                            r["tenant_id"] or "default",
                            r["session_id"],
                            msgs if isinstance(msgs, str) else json.dumps(msgs),
                            r["created_at"] if "created_at" in keys else "",
                            r["total_cost"] if "total_cost" in keys else 0,
                            r["total_tokens"] if "total_tokens" in keys else 0,
                            r["thread_id"] if "thread_id" in keys else "",
                            r["updated_at"] if "updated_at" in keys else "",
                            r["title"] if "title" in keys else "",
                            bool(r["archived"]) if "archived" in keys else False,
                        ),
                    )
                    n_chat += 1
            pg.commit()
        cc.close()
    print(f"OK chat sessions → {n_chat}")

    # ── swarm tasks ────────────────────────────────────────────────
    n_tasks = 0
    n_metrics = 0
    tc = _connect(swarm_path)
    if tc is not None:
        try:
            rows = tc.execute("SELECT * FROM swarm_tasks").fetchall()
        except Exception:
            rows = []
        with pool.connection() as pg:
            with pg.cursor() as cur:
                for r in rows:
                    keys = set(r.keys())
                    def col(name: str, default: Any = None) -> Any:
                        return r[name] if name in keys else default

                    workers = col("workers", "[]")
                    result = col("result")
                    meta = col("metadata", "{}")
                    deps = col("dependencies", "[]")
                    fb = col("fallback_chain", "[]")
                    cur.execute(
                        """
                        INSERT INTO kazma_swarm_tasks (
                            id, type, prompt, status, workers, result, context,
                            dependencies, fallback_chain, validation_schema, aggregation,
                            timeout, created_at, started_at, completed_at, cost, tokens, metadata
                        ) VALUES (
                            %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s,
                            %s::jsonb, %s::jsonb, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s::jsonb
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            status = EXCLUDED.status,
                            result = EXCLUDED.result,
                            completed_at = EXCLUDED.completed_at,
                            cost = EXCLUDED.cost,
                            tokens = EXCLUDED.tokens,
                            metadata = EXCLUDED.metadata
                        """,
                        (
                            col("id"),
                            col("type"),
                            col("prompt"),
                            col("status"),
                            workers if isinstance(workers, str) else json.dumps(workers),
                            result if result else "null",
                            col("context") or "",
                            deps if isinstance(deps, str) else json.dumps(deps or []),
                            fb if isinstance(fb, str) else json.dumps(fb or []),
                            col("validation_schema") or "",
                            col("aggregation") or "",
                            col("timeout"),
                            col("created_at") or "",
                            col("started_at"),
                            col("completed_at"),
                            col("cost") or 0,
                            col("tokens") or 0,
                            meta if isinstance(meta, str) else json.dumps(meta or {}),
                        ),
                    )
                    n_tasks += 1
                try:
                    mrows = tc.execute("SELECT * FROM swarm_worker_metrics").fetchall()
                except Exception:
                    mrows = []
                for r in mrows:
                    cur.execute(
                        """
                        INSERT INTO kazma_swarm_worker_metrics (
                            worker, date, tasks_completed, tasks_failed,
                            avg_latency, total_tokens, total_cost
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (worker, date) DO UPDATE SET
                            tasks_completed = EXCLUDED.tasks_completed,
                            tasks_failed = EXCLUDED.tasks_failed,
                            avg_latency = EXCLUDED.avg_latency,
                            total_tokens = EXCLUDED.total_tokens,
                            total_cost = EXCLUDED.total_cost
                        """,
                        (
                            r["worker"], r["date"],
                            r["tasks_completed"], r["tasks_failed"],
                            r["avg_latency"], r["total_tokens"], r["total_cost"],
                        ),
                    )
                    n_metrics += 1
            pg.commit()
        tc.close()
    print(f"OK swarm tasks → {n_tasks}, metrics → {n_metrics}")
    print("Done. Set KAZMA_DB_BACKEND=postgres and restart Kazma.")
    print("Checkpoints: use AsyncPostgresSaver automatically when URL is set.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
