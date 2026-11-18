"""The Memory page's backups say what they did, and restore only a memory backup.

Found reviewing the Memory page (2026-11-18):

* the backup list showed the operations database's copies
  (``memory_ops_<ts>.db``) beside the memory database's, with the same
  Restore button, and the restore copied whichever was chosen over the memory
  database: restoring an operations copy replaced every memory with the task
  queue and the audit log;
* **Back up now** answered "completed successfully" when nothing was copied
  (no database yet), and its manifest listed full server paths;
* **Optimise** answered "success" when a database's VACUUM failed.

Each check runs the real routes, and each has its negative control.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    state = sqlite3.connect(str(tmp_path / "memory_state.db"))
    ensure_primary_schema(state)
    now = time.time()
    state.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence,"
        " structural_importance, valid_from, ingested_at)"
        " VALUES ('b1', 'default', 'user', 'lives_in', 'functional', 'Lisbon', 0.9, 1, ?, ?)",
        (now, now),
    )
    state.commit()
    state.close()
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    return tmp_path


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui.routes_direct.system import register_system_routes

    app = FastAPI()
    register_system_routes(SimpleNamespace(app=app))
    return TestClient(app)


def _backups(mem_dir: Path) -> tuple[Path, Path]:
    from kazma_core.memory.backup import perform_native_backups

    written = perform_native_backups(retention=10)
    state = next(p for p in written if p.name.startswith("memory_state_"))
    ops = next(p for p in written if p.name.startswith("memory_ops_"))
    return state, ops


def _beliefs(mem_dir: Path) -> int:
    conn = sqlite3.connect(str(mem_dir / "memory_state.db"))
    try:
        return conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0]
    finally:
        conn.close()


def test_the_list_shows_only_the_memory_databases_backups(mem):
    state, ops = _backups(mem)
    names = [b["name"] for b in _client().get("/api/system/memory/backups").json()["backups"]]
    assert names == [state.name]
    # Negative control: the old list (every memory_*_*.db) held the
    # operations copy too, with the same Restore button.
    old = sorted(p.name for p in state.parent.glob("memory_*_*.db"))
    assert ops.name in old


def test_restoring_the_operations_copy_is_refused_and_memory_stays(mem):
    _state, ops = _backups(mem)
    resp = _client().post("/api/system/memory/restore", json={"backup_name": ops.name})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "not_a_memory_backup"
    assert _beliefs(mem) == 1


def test_a_copy_renamed_like_a_memory_backup_is_refused_by_its_tables(mem):
    from kazma_core.memory.backup import NotAMemoryBackup, is_memory_backup, restore_memory_backup

    state, ops = _backups(mem)
    disguised = ops.with_name("memory_state_1.db")
    shutil.copy2(ops, disguised)
    assert is_memory_backup(state) and not is_memory_backup(disguised)
    with pytest.raises(NotAMemoryBackup):
        restore_memory_backup(disguised)
    resp = _client().post("/api/system/memory/restore", json={"backup_name": disguised.name})
    assert resp.status_code == 400 and resp.json()["detail"] == "not_a_memory_backup"
    assert _beliefs(mem) == 1


def test_negative_control_the_old_restore_took_every_memory(mem):
    """What the restore did before the check: the operations copy over the
    memory database, and no fact left."""
    from kazma_core.db.sqlite_snapshot import snapshot_copy

    _state, ops = _backups(mem)
    snapshot_copy(ops, mem / "memory_state.db")
    conn = sqlite3.connect(str(mem / "memory_state.db"))
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "beliefs" not in tables


def test_back_up_now_says_when_nothing_was_copied(mem, monkeypatch):
    monkeypatch.setattr("kazma_core.memory.backup.perform_native_backups", lambda **_k: [])
    body = _client().post("/api/system/memory/backup").json()
    assert body == {"status": "failed", "detail": "nothing_backed_up"}


def test_back_up_now_names_files_not_server_paths(mem):
    body = _client().post("/api/system/memory/backup").json()
    assert body["status"] == "success"
    files = body["manifest"]["files"]
    assert files and all("/" not in f and "\\" not in f for f in files)
    assert any(f.startswith("memory_state_") for f in files)


def test_optimise_says_which_database_failed(mem, monkeypatch):
    real = sqlite3.connect

    def connect(db, *a, **k):
        if str(db).endswith("memory_ops.db"):
            raise sqlite3.OperationalError("database is locked")
        return real(db, *a, **k)

    monkeypatch.setattr(sqlite3, "connect", connect)
    body = _client().post("/api/system/memory/maintenance").json()
    assert body["status"] == "failed"
    assert body["failed"] == ["ops"]
    assert body["details"]["primary"] == "VACUUM + ANALYZE ok"


def test_optimise_succeeds_when_both_do(mem):
    body = _client().post("/api/system/memory/maintenance").json()
    assert body["status"] == "success" and body["failed"] == []
