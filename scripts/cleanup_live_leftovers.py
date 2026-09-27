#!/usr/bin/env python3
"""Remove the test data and copies found in the live install on 2026-09-27.

Run from the install folder with the install's Python. Without ``--apply``
nothing changes: it prints every memory, row and file it would touch.

    .venv\\Scripts\\python.exe scripts\\cleanup_live_leftovers.py
    .venv\\Scripts\\python.exe scripts\\cleanup_live_leftovers.py --apply

``--apply`` first writes ``kazma-data/backups/cleanup-<time>/``: a copy of both
memory databases (WAL-safe), and ``removed.jsonl`` with every row it will
delete from any other store, in full. Then, in this order:

1. The memories of the live-test chats (probe-*, live-*, livetest-*, diag-*,
   fork-*) are forgotten the way a chat's "Forget" does it -- emptied, their
   facts too, and the forget ledger keeps turn reconcile from writing them
   again.
2. Three notes and three facts that test runs stored through the agent's
   memory tool (a self-check, a stress-run marker, a test token) are
   forgotten.
3. The V1 migration copies of turns memory holds in full are retired
   (``legacy_tables.legacy_copies``; 181 on 2026-09-27): emptied, no ledger
   entry, facts kept -- the original stays.
4. The Postgres memory mirror loses the rows test runs wrote into it on
   2026-07-31/08-01 (before the test database shield); a real memory that
   only the mirror still holds is put back into local memory, archived.
5. The live-test chats leave the chat store, with their checkpoints,
   time-travel snapshots, approval rows, task ledgers, the test reminder and
   the test draft; the approval rows a test run wrote on 2026-09-01 go too.
6. The legacy SQLite swarm store (unused under Postgres) loses the task and
   metric rows a test run wrote on 2026-08-01.
7. Stray files (a test chat store's -wal/-shm, two empty databases) move
   into the cleanup folder.

The chats, notes and facts are named here (found by a read-only inventory,
2026-09-27), not matched by pattern, so nothing else can be caught. Reload
the server afterwards (``kazma_guard.py --reload --when-idle``) so its chat
list drops the removed chats.

Running it again is safe: every step reads what is still there, so what was
done is not done twice. A store another program keeps locked for longer than
``LOCK_WAIT_S`` is left unchanged and reported; the other steps still run,
the report is written, and the exit code is 1 until nothing is left. (The
first run on 2026-09-27 stopped at ``cron.db``, whose write lock the server
held -- fixed in the cron store the same day.)
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import os
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

#: The live-test chats (session id = thread id for each).
TEST_CHATS = (
    "probe-93o4411y", "probe-vk38tbux", "probe-vvwh5sk1", "probe-7osu0qd5",
    "diag-b", "diag-now-pin",
    "livetest-1788960104", "livetest-hitl-1788960141", "livetest-mcp-1788960427",
    "live-replay", "fork-5536d55d586c", "live-remind", "live-mcp", "live-email",
    "live-long-rounds", "live-x",
)
#: A task ledger of a live test that had no chat of its own.
TEST_LEDGERS = (*TEST_CHATS, "live-long-20260922")
#: Swarm pipeline test tasks whose approval rows a test run wrote (2026-09-01).
TEST_GATE_THREADS = ("task-hitl-restart", "task-bdd7c62f4f25487595c47d9e9adc4387")
#: Notes test runs stored through the memory tool, and their facts.
TEST_NOTES = (
    "e_808f44e59e10e6d67060aa33",  # "selfcheck ok 2026-09-09 ... test belief"
    "e_cf9671b9ddc4c64b3204a890",  # "stress-run-2026-09-15: the marker string is PELICAN-7734"
    "e_1b9ae26e78a33872870ba7e2",  # "Remembered token: pebble-live."
)
TEST_FACTS = (
    "b_f75fde47e714672b1668_59a8c5_17200f",  # user noted "selfcheck ok 2026-09-09 ..."
    "b_de0f1064757d10716e73_6571fc_93ac34",  # user noted "stress-run-2026-09-15 ... PELICAN-7734"
    "b_d7b7eec0a24139f1402d_24865e_ddfb9e",  # user remembered_token "pebble-live"
)
#: The live-remind test's one-shot reminder (fired 2026-09-22).
TEST_CRON_JOB = "cron-33da17dc"
#: Test runs wrote into the Postgres mirror between these times (UTC).
MIRROR_TEST_WINDOW = (
    _dt.datetime(2026, 7, 31, tzinfo=_dt.UTC).timestamp(),
    _dt.datetime(2026, 8, 2, tzinfo=_dt.UTC).timestamp(),
)
#: The day a test run wrote into the legacy SQLite swarm store.
SWARM_TEST_DAY = "2026-08-01"
#: Stray files in the data folder.
STRAY_FILES = ("chat_sessions_test.db-wal", "chat_sessions_test.db-shm", "kazma.db", "ops.db")

_CHECKPOINT_TABLES = ("checkpoints", "checkpoint_writes", "checkpoint_blobs")
#: How long a store's delete waits for another program's write lock.
LOCK_WAIT_S = 30.0


@dataclass
class Plan:
    chat_memories: dict[str, int] = field(default_factory=dict)
    notes: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    copies: dict[str, str] = field(default_factory=dict)
    mirror_junk: list[dict[str, Any]] = field(default_factory=list)
    mirror_restore: list[dict[str, Any]] = field(default_factory=list)
    pg_rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    sqlite_rows: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    files: list[Path] = field(default_factory=list)


def _safe_dsn(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.username or '?'}@{parts.hostname or '?'}:{parts.port or 5432}{parts.path}"


def _pg(url: str) -> Any:
    import psycopg

    return psycopg.connect(url, connect_timeout=10)


def _rows(cur: Any) -> list[dict[str, Any]]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _json_safe(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"base64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _sqlite_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _sqlite_targets(data: Path) -> list[tuple[str, str, str, tuple[Any, ...]]]:
    """(database file, table, WHERE, params) of every SQLite row to remove."""
    chats = ",".join("?" for _ in TEST_CHATS)
    ledgers = ",".join("?" for _ in TEST_LEDGERS)
    gates = ",".join("?" for _ in TEST_GATE_THREADS)
    return [
        ("snapshots.db", "snapshots", f"thread_id IN ({chats})", TEST_CHATS),
        ("hitl_gates.db", "hitl_gates", f"thread_id IN ({chats}) OR thread_id IN ({gates})",
         (*TEST_CHATS, *TEST_GATE_THREADS)),
        ("task_ledgers.db", "task_ledgers", f"thread_id IN ({ledgers})", TEST_LEDGERS),
        ("cron.db", "cron_jobs", "job_id = ? AND thread_id = 'live-remind' AND status = 'done'", (TEST_CRON_JOB,)),
        ("agent_artifacts.db", "agent_artifacts", "thread_id = 'live-x'", ()),
        ("swarm_tasks.db", "swarm_tasks", "substr(created_at, 1, 10) = ?", (SWARM_TEST_DAY,)),
        ("swarm_tasks.db", "swarm_worker_metrics", "date = ?", (SWARM_TEST_DAY,)),
    ]


def build_plan(data: Path, db_url: str, mirror_url: str) -> Plan:
    """Everything the cleanup would change, read without writing anything."""
    from kazma_core.memory.legacy_tables import legacy_copies
    from kazma_core.paths import primary_memory_db

    plan = Plan()
    mem = _sqlite_ro(Path(primary_memory_db()))
    try:
        for chat in TEST_CHATS:
            plan.chat_memories[chat] = mem.execute(
                "SELECT COUNT(*) FROM episodes WHERE session_id = ? AND tier != 'forgotten'", (chat,)
            ).fetchone()[0]
        marks = ",".join("?" for _ in TEST_NOTES)
        plan.notes = [dict(r) for r in mem.execute(
            f"SELECT id, tenant_id, substr(user_text, 1, 80) AS text FROM episodes "
            f"WHERE id IN ({marks}) AND tier != 'forgotten'", TEST_NOTES)]
        marks = ",".join("?" for _ in TEST_FACTS)
        plan.facts = [dict(r) for r in mem.execute(
            f"SELECT id, tenant_id, subject, predicate, substr(object, 1, 60) AS object FROM beliefs "
            f"WHERE id IN ({marks}) AND invalidated_at IS NULL AND valid_until IS NULL", TEST_FACTS)]
        plan.copies = legacy_copies(mem, "default")
        local_ids = {r[0] for r in mem.execute("SELECT id FROM episodes")}
    finally:
        mem.close()

    if mirror_url:
        with _pg(mirror_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM kazma_episodes")
            for row in _rows(cur):
                if row["id"] in local_ids:
                    continue
                created = float(row.get("created_at") or 0)
                if MIRROR_TEST_WINDOW[0] <= created < MIRROR_TEST_WINDOW[1]:
                    plan.mirror_junk.append(row)
                else:
                    plan.mirror_restore.append(row)

    if db_url:
        with _pg(db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM kazma_chat_sessions WHERE session_id = ANY(%s)", (list(TEST_CHATS),))
            plan.pg_rows["kazma_chat_sessions"] = _rows(cur)
            for table in _CHECKPOINT_TABLES:
                cur.execute(f"SELECT * FROM {table} WHERE thread_id = ANY(%s)", (list(TEST_CHATS),))
                plan.pg_rows[table] = _rows(cur)

    for db_name, table, where, params in _sqlite_targets(data):
        path = data / db_name
        if not path.is_file():
            continue
        conn = _sqlite_ro(path)
        try:
            plan.sqlite_rows[(db_name, table)] = [
                dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE {where}", params)
            ]
        except sqlite3.OperationalError as exc:
            print(f"  (skipped {db_name}/{table}: {exc})")
        finally:
            conn.close()

    plan.files = [data / name for name in STRAY_FILES if (data / name).is_file()]
    return plan


def print_plan(plan: Plan) -> None:
    print("\n1. Memories of the live-test chats (forgotten, with their facts):")
    for chat, n in plan.chat_memories.items():
        print(f"   {chat:<28} {n} memor{'y' if n == 1 else 'ies'}")
    print(f"\n2. Test notes and facts stored through the memory tool: {len(plan.notes)} notes, {len(plan.facts)} facts")
    for n in plan.notes:
        print(f"   note {n['id']}: {n['text']!r}")
    for f in plan.facts:
        print(f"   fact {f['id']}: {f['subject']} {f['predicate']} {f['object']!r}")
    print(f"\n3. V1 migration copies of turns memory holds in full: {len(plan.copies)} retired (originals kept)")
    print(f"\n4. Postgres memory mirror: {len(plan.mirror_junk)} test rows from 2026-07-31/08-01 removed")
    for row in plan.mirror_restore:
        text = (row.get("user_text") or row.get("summary_text") or "")[:80]
        print(f"   restored to local memory (archived): {row['id']} {text!r}")
    total = sum(len(v) for v in plan.pg_rows.values())
    print(f"\n5. Chat store and checkpoints (Postgres): {total} rows")
    for table, rows in plan.pg_rows.items():
        print(f"   {table:<22} {len(rows)}")
    print("\n5-6. Other stores (SQLite), rows removed:")
    for (db_name, table), rows in plan.sqlite_rows.items():
        print(f"   {db_name + '/' + table:<40} {len(rows)}")
    print(f"\n7. Stray files moved into the cleanup folder: {[p.name for p in plan.files]}")


def _write_receipt(folder: Path, plan: Plan) -> None:
    with (folder / "removed.jsonl").open("w", encoding="utf-8") as fh:
        def put(store: str, table: str, row: dict[str, Any]) -> None:
            fh.write(json.dumps({"store": store, "table": table, "row": _json_safe(row)}, ensure_ascii=False) + "\n")

        for row in plan.mirror_junk:
            put("postgres-mirror", "kazma_episodes", row)
        for table, rows in plan.pg_rows.items():
            for row in rows:
                put("postgres", table, row)
        for (db_name, table), rows in plan.sqlite_rows.items():
            for row in rows:
                put(db_name, table, row)


def _delete_rows(path: Path, table: str, where: str, params: tuple[Any, ...]) -> int:
    """Delete in one short write transaction, waiting up to ``LOCK_WAIT_S``
    for another program's write lock; nothing changes unless it commits."""
    conn = sqlite3.connect(str(path), timeout=LOCK_WAIT_S, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        committed = False
        try:
            removed = conn.execute(f"DELETE FROM {table} WHERE {where}", params).rowcount
            conn.execute("COMMIT")
            committed = True
        finally:
            if not committed and conn.in_transaction:
                conn.execute("ROLLBACK")
        return removed
    finally:
        conn.close()


def apply(plan: Plan, data: Path, db_url: str, mirror_url: str) -> tuple[dict[str, Any], dict[str, str]]:
    """Make the changes. Returns what was done and, per step, what was left
    and why."""
    folder, done, left = _backup(plan)
    try:
        done.update(_apply_memory(plan))
    except sqlite3.Error as exc:
        # Nothing further: the stores keep their rows until memory is done,
        # so the next run does every step in order.
        left["memory"] = f"{exc}; no other store was changed"
        _write_report(folder, done, left)
        return done, left
    _apply_postgres(plan, db_url, mirror_url, done, left)
    for db_name, table, where, params in _sqlite_targets(data):
        if not plan.sqlite_rows.get((db_name, table)):
            continue
        key = f"{db_name}/{table}"
        try:
            done[key] = _delete_rows(data / db_name, table, where, params)
        except sqlite3.OperationalError as exc:
            left[key] = f"{exc} after waiting {LOCK_WAIT_S:.0f} s; nothing in it was changed"
    moved = folder / "files"
    moved.mkdir(exist_ok=True)
    done["files_moved"] = []
    for path in plan.files:
        try:
            shutil.move(str(path), str(moved / path.name))
            done["files_moved"].append(path.name)
        except OSError as exc:
            left[path.name] = f"not moved: {exc}"
    _write_report(folder, done, left)
    return done, left


def _write_report(folder: Path, done: dict[str, Any], left: dict[str, str]) -> None:
    report = {"done": done, "left": left}
    (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def _backup(plan: Plan) -> tuple[Path, dict[str, Any], dict[str, str]]:
    from kazma_core.memory.backup import backup_one
    from kazma_core.paths import backups_dir, memory_ops_db, primary_memory_db

    stamp, n = int(time.time()), 1
    while True:  # each run its own folder, even two runs in one second
        folder = backups_dir() / (f"cleanup-{stamp}" if n == 1 else f"cleanup-{stamp}-{n}")
        try:
            folder.mkdir(parents=True)
            break
        except FileExistsError:
            n += 1
    for src in (primary_memory_db(), memory_ops_db()):
        if not backup_one(Path(src), folder / Path(src).name):
            raise SystemExit(f"ABORT: could not back up {src}; nothing was changed")
    _write_receipt(folder, plan)
    print(f"Backup and receipt: {folder}")
    return folder, {"folder": str(folder)}, {}


def _apply_memory(plan: Plan) -> dict[str, Any]:
    """Steps 1-4 in local memory: forget, invalidate, retire, restore."""
    from kazma_core.memory import forget
    from kazma_core.memory.hygiene import invalidate_belief
    from kazma_core.memory.state_backend import remirror_belief_by_id, remirror_episode_by_id
    from kazma_core.paths import primary_memory_db

    done: dict[str, Any] = {}
    mem = sqlite3.connect(primary_memory_db(), timeout=LOCK_WAIT_S)
    mem.row_factory = sqlite3.Row
    try:
        done["chats_forgotten"] = {
            chat: forget.forget_chat(chat, tenant_id="default", conn=mem).get("forgotten", 0)
            for chat in TEST_CHATS
        }
        done["notes_forgotten"] = sum(
            1 for n in plan.notes if forget.forget_episode(n["id"], tenant_id="default", conn=mem).get("ok"))
        for f in plan.facts:
            invalidate_belief(f["id"], conn=mem, tenant_id=f["tenant_id"] or "default")
            remirror_belief_by_id(mem, f["id"])
        done["facts_invalidated"] = len(plan.facts)
        done["copies_retired"] = sum(
            1 for copy_id, original in plan.copies.items()
            if forget.retire_copy(copy_id, original_id=original, conn=mem).get("ok"))
        restored = 0
        for row in plan.mirror_restore:
            if forget.refuses_write(mem, tenant_id=row.get("tenant_id") or "default",
                                    session_id=row.get("session_id") or "",
                                    turn_number=int(row.get("turn_number") or 0),
                                    user_text=row.get("user_text")):
                continue
            meta = json.loads(row.get("metadata_json") or "{}") if isinstance(row.get("metadata_json"), str) else {}
            meta = meta if isinstance(meta, dict) else {}
            meta["restored_from"] = "postgres_mirror"
            cur = mem.execute(
                "INSERT OR IGNORE INTO episodes (id, tenant_id, session_id, turn_number, user_text, "
                "assistant_text, summary_text, tier, structural_importance, created_at, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'archived', ?, ?, ?)",
                (row["id"], row.get("tenant_id") or "default", row.get("session_id"),
                 int(row.get("turn_number") or 0), row.get("user_text"), row.get("assistant_text"),
                 row.get("summary_text"), int(row.get("structural_importance") or 1),
                 float(row.get("created_at") or time.time()), json.dumps(meta, ensure_ascii=False)),
            )
            restored += cur.rowcount
            mem.commit()
            remirror_episode_by_id(mem, row["id"])
        done["mirror_restored"] = restored
    finally:
        mem.close()
    return done


def _apply_postgres(plan: Plan, db_url: str, mirror_url: str, done: dict[str, Any], left: dict[str, str]) -> None:
    """Steps 4-5 in Postgres: each database in one transaction, which a
    failure rolls back whole."""
    import psycopg

    if mirror_url and plan.mirror_junk:
        try:
            with _pg(mirror_url) as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM kazma_episodes WHERE id = ANY(%s)", ([r["id"] for r in plan.mirror_junk],))
                done["mirror_removed"] = cur.rowcount
        except psycopg.Error as exc:
            left["postgres memory mirror"] = f"{exc}; nothing in it was changed"
    if db_url and any(plan.pg_rows.values()):
        try:
            with _pg(db_url) as conn, conn.cursor() as cur:
                removed: dict[str, int] = {}
                for table in _CHECKPOINT_TABLES:
                    cur.execute(f"DELETE FROM {table} WHERE thread_id = ANY(%s)", (list(TEST_CHATS),))
                    removed[table] = cur.rowcount
                cur.execute("DELETE FROM kazma_chat_sessions WHERE session_id = ANY(%s)", (list(TEST_CHATS),))
                removed["kazma_chat_sessions"] = cur.rowcount
            done.update(removed)
        except psycopg.Error as exc:
            left["postgres chat store"] = f"{exc}; nothing in it was changed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="make the changes (a backup is taken first)")
    args = parser.parse_args()

    from kazma_core.env_files import load_env_files

    load_env_files()
    from kazma_core.db.shared_store_peers import install_id
    from kazma_core.memory.backends import get_backends_cfg
    from kazma_core.paths import data_dir, primary_memory_db

    data = Path(data_dir())
    if not install_id(create=False):
        print(f"ABORT: {data} is not a booted install (no install_id). Run from the install folder.")
        return 2
    mem = _sqlite_ro(Path(primary_memory_db()))
    try:
        episodes = mem.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    finally:
        mem.close()
    if episodes < 100:
        print(f"ABORT: {primary_memory_db()} holds {episodes} memories -- not the live install's database.")
        return 2

    from kazma_core.diagnostic_scope import read_only_diagnostic

    # Reading the plan writes nothing: not even a lazy settings migration.
    with read_only_diagnostic("cleanup plan"):
        db_url = os.environ.get("KAZMA_DATABASE_URL") or ""
        state = get_backends_cfg().get("state") or {}
        provider = str(state.get("provider") or "")
        mirror_url = str(state.get("url") or "") if provider in ("postgres", "postgresql", "pg") else ""
        print(f"Install data: {data} ({episodes} memories)")
        print(f"Postgres: {_safe_dsn(db_url) if db_url else 'none'}; "
              f"memory mirror: {_safe_dsn(mirror_url) if mirror_url else 'none'}")
        plan = build_plan(data, db_url, mirror_url)
    print_plan(plan)
    if not args.apply:
        print("\nDry run: nothing was changed. Run again with --apply to make these changes.")
        return 0
    done, left = apply(plan, data, db_url, mirror_url)
    print("\nDone:")
    for key, value in done.items():
        print(f"   {key}: {value}")
    if left:
        print("\nNOT finished -- left unchanged:")
        for key, why in left.items():
            print(f"   {key}: {why}")
        print("\nRun the same command again (when the server is idle): what is done is not repeated.")
        return 1
    print("\nReload the server when idle so its chat list drops the removed chats.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
