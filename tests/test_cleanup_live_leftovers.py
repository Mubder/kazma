"""The live cleanup script removes what it names, and only that
(``scripts/cleanup_live_leftovers.py``, 2026-09-27).

Run on a synthetic install -- every store the script touches, holding one of
its targets and one row that must survive -- through the script's own plan
and apply (the Postgres parts are off: no URL). Held: the dry run changes
nothing; apply backs up first, writes every removed row to the receipt,
forgets the test chats and notes the product's way, retires the copy, and
leaves the real rows alone.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from kazma_core.memory.schema_v2 import ensure_primary_schema

REPO = Path(__file__).resolve().parents[1]
FULL = "You have fifteen private repos: kazma, shipx, kca, cortexswarm and eleven more, all on GitHub."


def _script():
    spec = importlib.util.spec_from_file_location("cleanup_live_leftovers", REPO / "scripts" / "cleanup_live_leftovers.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cleanup_live_leftovers"] = mod
    spec.loader.exec_module(mod)
    return mod


def _db(path: Path, ddl: str, rows: list[tuple]) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(ddl)
    marks = ",".join("?" for _ in rows[0])
    table = ddl.split()[2]
    conn.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)
    conn.commit()
    conn.close()


@pytest.fixture()
def install(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write

    data = tmp_path / "kazma-data"
    data.mkdir()
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(data / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(data / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    dual_write._reset_mirror()
    mem = sqlite3.connect(str(data / "memory_state.db"))
    ensure_primary_schema(mem)
    now = time.time()
    episodes = [
        ("ep_probe", "probe-93o4411y", 1, "reply with the single word OK", "OK", "episodic"),
        ("ep_real", "real-chat", 1, "what private repos do I have", FULL, "episodic"),
        ("ep_copy", "legacy-aaaa1111", 0, f"User: What private repos do I have\nAssistant: {FULL[:30]}…", None,
         "archived"),
        ("e_808f44e59e10e6d67060aa33", "memory_store", 0, "selfcheck ok 2026-09-09 test belief", None, "episodic"),
    ]
    for eid, session, turn, user, answer, tier in episodes:
        mem.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
                    "created_at, metadata_json) VALUES (?, 'default', ?, ?, ?, ?, ?, ?, '{}')",
                    (eid, session, turn, user, answer, tier, now))
    for bid, obj, session, turn in (("b_probe", "OK", "probe-93o4411y", 1),
                                    ("b_f75fde47e714672b1668_59a8c5_17200f", "selfcheck ok", None, None),
                                    ("b_real", "fifteen repos", "real-chat", 1)):
        mem.execute("INSERT INTO beliefs (id, tenant_id, subject, predicate, object, predicate_type, valid_from, "
                    "ingested_at, confidence, source_session, source_turn) VALUES (?, 'default', 'user', 'noted', ?, "
                    "'set', ?, ?, 0.9, ?, ?)", (bid, obj, now, now, session, turn))
    mem.commit()
    mem.close()
    _db(data / "snapshots.db", "CREATE TABLE snapshots (id TEXT, thread_id TEXT, state_json TEXT)",
        [("s1", "probe-93o4411y", "{}"), ("s2", "real-chat", "{}")])
    _db(data / "hitl_gates.db", "CREATE TABLE hitl_gates (gate_id TEXT, thread_id TEXT, state TEXT)",
        [("g1", "live-remind", "settled"), ("g2", "task-hitl-restart", "settled"), ("g3", "real-chat", "settled")])
    _db(data / "task_ledgers.db", "CREATE TABLE task_ledgers (thread_id TEXT, status TEXT)",
        [("live-long-20260922", "active"), ("real-chat", "active")])
    _db(data / "cron.db", "CREATE TABLE cron_jobs (job_id TEXT, thread_id TEXT, status TEXT)",
        [("cron-33da17dc", "live-remind", "done"), ("cron-real", "real-chat", "pending")])
    _db(data / "agent_artifacts.db", "CREATE TABLE agent_artifacts (thread_id TEXT, key TEXT)",
        [("live-x", "proposal:p1"), ("real-chat", "proposal:p2")])
    _db(data / "swarm_tasks.db", "CREATE TABLE swarm_tasks (id TEXT, created_at TEXT)",
        [("task-a", "2026-08-01T18:05:19+00:00"), ("task-real", "2026-09-20T10:00:00+00:00")])
    conn = sqlite3.connect(str(data / "swarm_tasks.db"))
    conn.execute("CREATE TABLE swarm_worker_metrics (worker TEXT, date TEXT)")
    conn.executemany("INSERT INTO swarm_worker_metrics VALUES (?, ?)", [("alpha", "2026-08-01"), ("coder", "2026-09-20")])
    conn.commit()
    conn.close()
    for name in ("kazma.db", "ops.db", "chat_sessions_test.db-wal"):
        (data / name).write_bytes(b"")
    yield data
    dual_write._reset_mirror()


def _count(path: Path, table: str) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _data_files(folder: Path) -> dict[str, bytes]:
    """Every file's bytes, except the -wal/-shm SQLite makes for any reader
    of a WAL database (on live the server holds them open anyway)."""
    return {p.name: p.read_bytes() for p in folder.iterdir()
            if p.is_file() and not p.name.endswith(("-wal", "-shm")) or p.name == "chat_sessions_test.db-wal"}


def test_the_dry_run_changes_nothing(install):
    mod = _script()
    before = _data_files(install)
    plan = mod.build_plan(install, "", "")
    assert plan.copies == {"ep_copy": "ep_real"} and plan.chat_memories["probe-93o4411y"] == 1
    assert plan.sqlite_rows[("hitl_gates.db", "hitl_gates")] and [p.name for p in plan.files] == [
        "chat_sessions_test.db-wal", "kazma.db", "ops.db"]
    assert _data_files(install) == before


def test_apply_removes_what_it_names_and_only_that(install):
    mod = _script()
    done = mod.apply(mod.build_plan(install, "", ""), install, "", "")
    folder = Path(done["folder"])
    assert (folder / "memory_state.db").is_file()  # the backup was taken
    receipt = [json.loads(line) for line in (folder / "removed.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {(r["store"], r["row"].get("thread_id") or r["row"].get("id") or r["row"].get("worker"))
            for r in receipt} >= {("hitl_gates.db", "live-remind"), ("swarm_tasks.db", "task-a")}

    mem = sqlite3.connect(str(install / "memory_state.db"))
    mem.row_factory = sqlite3.Row
    tiers = {r["id"]: r["tier"] for r in mem.execute("SELECT id, tier FROM episodes")}
    assert tiers["ep_probe"] == tiers["ep_copy"] == tiers["e_808f44e59e10e6d67060aa33"] == "forgotten"
    assert tiers["ep_real"] == "episodic"
    live = {r["id"]: r["invalidated_at"] is None for r in mem.execute("SELECT id, invalidated_at FROM beliefs")}
    assert live == {"b_probe": False, "b_f75fde47e714672b1668_59a8c5_17200f": False, "b_real": True}
    assert mem.execute("SELECT COUNT(*) FROM memory_forgotten WHERE session_key = 'probe-93o4411y'").fetchone()[0]
    mem.close()

    for db_name, table in (("snapshots.db", "snapshots"), ("hitl_gates.db", "hitl_gates"),
                           ("task_ledgers.db", "task_ledgers"), ("cron.db", "cron_jobs"),
                           ("agent_artifacts.db", "agent_artifacts"), ("swarm_tasks.db", "swarm_tasks"),
                           ("swarm_tasks.db", "swarm_worker_metrics")):
        assert _count(install / db_name, table) == 1, f"{db_name}/{table}: the real row must stay"
    assert not (install / "kazma.db").exists() and (folder / "files" / "kazma.db").exists()
