"""Memory V2 Phase 1 tests — storage infrastructure.

Covers:
  - schema_v2: DDL execution, idempotency, nullable provenance, MiniLM default
  - config: v2 sub-block defaults, type coercion, memory_v2_enabled gating
  - dual_write: belief + episode mirror, predicate-type inference, idempotency
  - backup: streaming copy content parity, retention pruning
  - paths: new V2 path helpers resolve under overridden data dir

All tests use tmp_path + KAZMA_DATA_DIR override so nothing touches the
real kazma-data/ directory.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture()
def isolated_data(tmp_path: Path, monkeypatch):
    """Redirect KAZMA_DATA_DIR to a temp dir + reset all singletons."""
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    # Reset singletons that may have cached the old data dir
    from kazma_core.memory import dual_write

    dual_write.reset_mirror()
    yield tmp_path
    dual_write.reset_mirror()


# ── 1. Schema tests ────────────────────────────────────────────────────────


def test_primary_schema_creates_all_tables(isolated_data):
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    conn = sqlite3.connect(primary_memory_db())
    ensure_primary_schema(conn)
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    expected = {
        "beliefs",
        "episodes",
        "entities",
        "entity_merges",
        "procedural_dags",
        "beliefs_archive",
    }
    assert expected <= tables, f"missing tables: {expected - tables}"


def test_ops_schema_creates_queue_and_audit(isolated_data):
    from kazma_core.memory.schema_v2 import ensure_ops_schema
    from kazma_core.paths import memory_ops_db

    conn = sqlite3.connect(memory_ops_db())
    ensure_ops_schema(conn)
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert "memory_task_queue" in tables
    assert "memory_audit_log" in tables


def test_schema_idempotent(isolated_data):
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    conn = sqlite3.connect(primary_memory_db())
    ensure_primary_schema(conn)
    ensure_primary_schema(conn)  # second call must not raise
    ensure_primary_schema(conn)  # third call neither
    conn.close()


def test_source_session_and_turn_nullable(isolated_data):
    """Resolution #3: source_session/source_turn must accept NULL."""
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    conn = sqlite3.connect(primary_memory_db())
    ensure_primary_schema(conn)
    now = time.time()
    conn.execute(
        """INSERT INTO beliefs
           (id, subject, predicate, predicate_type, object, valid_from, ingested_at)
           VALUES ('b1', 'user', 'lives_in', 'functional', 'Paris', ?, ?)""",
        (now, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT source_session, source_turn FROM beliefs WHERE id='b1'"
    ).fetchone()
    conn.close()
    assert row == (None, None)


def test_embedding_model_default_is_minilm(isolated_data):
    """Resolution #2: default stays all-MiniLM-L6-v2 (no re-index needed)."""
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    conn = sqlite3.connect(primary_memory_db())
    ensure_primary_schema(conn)
    cols = {r[1]: r[4] for r in conn.execute("PRAGMA table_info(beliefs)")}
    conn.close()
    default = cols["embedding_model_version"].strip("'\"")
    assert default == "all-MiniLM-L6-v2"


# ── 2. Config tests ────────────────────────────────────────────────────────


def test_v2_defaults_present():
    from kazma_core.memory.config import DEFAULT_MEMORY_CFG

    v2 = DEFAULT_MEMORY_CFG["v2"]
    # V2 is now the active stack by default (the V1→V2 cutover landed).
    # Flip to False to roll back to the legacy 4-layer RRF stack.
    assert v2["use_new_stack"] is True
    assert v2["trust_weight_user"] == 1.0
    assert v2["trust_weight_tool"] == 0.85
    assert v2["trust_weight_llm"] == 0.60
    assert v2["decay_lambda_identity"] == 0.0001
    assert v2["ppr_alpha"] == 0.15
    assert v2["procedural_quarantine_threshold"] == 0.40


def test_memory_v2_enabled_by_default():
    from kazma_core.memory.config import memory_v2_enabled, read_memory_cfg

    cfg = read_memory_cfg()
    assert memory_v2_enabled(cfg) is True


def test_memory_v2_enabled_after_flip():
    from kazma_core.memory.config import memory_v2_enabled, read_memory_cfg

    cfg = read_memory_cfg()
    cfg["v2"]["use_new_stack"] = True
    assert memory_v2_enabled(cfg) is True


def test_memory_v2_gated_by_master_switch():
    from kazma_core.memory.config import memory_v2_enabled, read_memory_cfg

    cfg = read_memory_cfg()
    cfg["v2"]["use_new_stack"] = True
    cfg["enabled"] = False
    assert memory_v2_enabled(cfg) is False


# ── 3. Dual-write tests ────────────────────────────────────────────────────


def test_dual_write_mirror_belief_functional(isolated_data):
    from kazma_core.memory import dual_write

    m = dual_write.get_mirror()
    bid = m.mirror_belief(
        "John Smith", "lives_in", "Paris",
        importance=4, source_session="s1", source_turn=1,
    )
    assert bid is not None
    row = m._primary.execute(
        "SELECT predicate_type, object, structural_importance FROM beliefs WHERE id=?",
        (bid,),
    ).fetchone()
    assert row["predicate_type"] == "functional"
    assert row["object"] == "Paris"
    assert row["structural_importance"] == 4


def test_dual_write_mirror_belief_set_valued(isolated_data):
    from kazma_core.memory import dual_write

    m = dual_write.get_mirror()
    bid = m.mirror_belief("user", "uses_tool", "git")
    assert bid is not None
    row = m._primary.execute(
        "SELECT predicate_type FROM beliefs WHERE id=?", (bid,)
    ).fetchone()
    assert row["predicate_type"] == "set"


def test_dual_write_mirror_episode(isolated_data):
    from kazma_core.memory import dual_write

    m = dual_write.get_mirror()
    eid = m.mirror_episode(
        session_id="s1", turn_number=1,
        user_text="I live in Paris", assistant_text="Noted.",
    )
    assert eid is not None
    row = m._primary.execute(
        "SELECT session_id, turn_number, user_text, tier FROM episodes WHERE id=?",
        (eid,),
    ).fetchone()
    assert row["session_id"] == "s1"
    assert row["turn_number"] == 1
    assert row["tier"] == "episodic"


def test_dual_write_idempotent(isolated_data):
    """Same content + turn must produce the same episode id (INSERT OR IGNORE)."""
    from kazma_core.memory import dual_write

    m = dual_write.get_mirror()
    eid1 = m.mirror_episode(
        session_id="s1", turn_number=1, user_text="hello", assistant_text="hi"
    )
    eid2 = m.mirror_episode(
        session_id="s1", turn_number=1, user_text="hello", assistant_text="hi"
    )
    assert eid1 == eid2
    count = m._primary.execute(
        "SELECT COUNT(*) FROM episodes WHERE id=?", (eid1,)
    ).fetchone()[0]
    assert count == 1


def test_dual_write_best_effort_no_raise(isolated_data):
    """Mirror must never raise — failures are logged, not propagated."""
    from kazma_core.memory import dual_write

    m = dual_write.get_mirror()
    # Close the connection to force a failure path
    m.close()
    # These should return None, not raise
    bid = m.mirror_belief("x", "y", "z")
    eid = m.mirror_episode(session_id="s", turn_number=0)
    # After close, _ensure reopens — so these may succeed or fail, but
    # the contract is: never raise. Either outcome is acceptable.
    assert bid is None or isinstance(bid, str)
    assert eid is None or isinstance(eid, str)


# ── 4. Backup tests ────────────────────────────────────────────────────────


def test_backup_content_parity(isolated_data):
    from kazma_core.memory.backup import perform_native_backups
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.paths import memory_ops_db, primary_memory_db

    # Seed both DBs
    now = time.time()
    c = sqlite3.connect(primary_memory_db())
    ensure_primary_schema(c)
    c.execute(
        """INSERT INTO beliefs
           (id, subject, predicate, predicate_type, object, valid_from, ingested_at)
           VALUES ('b1', 'user', 'name_is', 'functional', 'Alice', ?, ?)""",
        (now, now),
    )
    c.commit()
    c.close()
    c = sqlite3.connect(memory_ops_db())
    ensure_ops_schema(c)
    c.execute(
        """INSERT INTO memory_task_queue
           (id, task_type, payload_json, created_at, updated_at)
           VALUES ('t1', 'macro_sleep', '{}', ?, ?)""",
        (now, now),
    )
    c.commit()
    c.close()

    written = perform_native_backups(retention=5)
    assert len(written) == 2

    # Verify the belief survived the streaming copy
    prim_backup = [p for p in written if "memory_state" in p.name][0]
    bc = sqlite3.connect(str(prim_backup))
    row = bc.execute("SELECT object FROM beliefs WHERE id='b1'").fetchone()
    bc.close()
    assert row and row[0] == "Alice"


def test_backup_retention_pruning(isolated_data):
    from kazma_core.memory.backup import perform_native_backups
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import backups_dir, primary_memory_db

    # Create the primary DB so backup has something to copy
    c = sqlite3.connect(primary_memory_db())
    ensure_primary_schema(c)
    c.close()

    for _ in range(6):
        perform_native_backups(retention=3)
        time.sleep(0.02)  # distinct timestamps

    files = list(backups_dir().glob("memory_state_*.db"))
    assert len(files) <= 3, f"retention=3 but found {len(files)} backups"


def test_backup_skips_missing_db(isolated_data):
    """If a DB file doesn't exist yet, backup skips it without error."""
    from kazma_core.memory.backup import perform_native_backups

    written = perform_native_backups(retention=3)
    # Both DBs absent → nothing written, no crash
    assert written == []


# ── 5. Paths tests ─────────────────────────────────────────────────────────


def test_paths_resolve_under_data_dir(isolated_data):
    from kazma_core.paths import (
        backups_dir,
        exports_dir,
        memory_ops_db,
        primary_memory_db,
    )

    assert primary_memory_db().endswith("memory_state.db")
    assert memory_ops_db().endswith("memory_ops.db")
    assert str(backups_dir()).endswith("backups")
    assert str(exports_dir()).endswith("exports")
    # exports_dir must create itself
    assert exports_dir().exists()


def test_paths_env_override(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "custom_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "custom_ops.db"))
    from kazma_core.paths import memory_ops_db, primary_memory_db

    assert primary_memory_db().endswith("custom_state.db")
    assert memory_ops_db().endswith("custom_ops.db")
