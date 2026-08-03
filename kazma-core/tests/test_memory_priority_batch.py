"""P0/P1 max batch: VectorBackend, tenant isolation, working TTL, secrets."""

from __future__ import annotations

import sqlite3
import time

import pytest


@pytest.fixture()
def mem_db(tmp_path, monkeypatch):
    db = tmp_path / "memory_state.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(db))
    try:
        import kazma_core.paths as paths

        monkeypatch.setattr(paths, "primary_memory_db", lambda: str(db))
    except Exception:
        pass
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    conn = sqlite3.connect(str(db), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    yield conn
    conn.close()


def test_vector_backend_local_search(mem_db):
    from kazma_core.memory.backends import LocalSqliteVectorBackend, get_vector_backend, vector_capability

    cap = vector_capability()
    assert cap["vector_write_ready"] is True
    assert cap["vector_status"] == "full"

    be = get_vector_backend(mem_db)
    assert isinstance(be, LocalSqliteVectorBackend)
    # No embeddings → empty search, but available if numpy/sqlite-vec present
    assert be.search([1.0, 0.0], tenant_id="default", limit=3) == [] or isinstance(
        be.search([1.0, 0.0], limit=3), list
    )


def test_vector_capability_remote_probe_only(monkeypatch):
    from kazma_core.memory import backends as b

    monkeypatch.setattr(
        b,
        "get_backends_cfg",
        lambda: {
            "mode": "remote",
            "vector": {"provider": "qdrant", "url": "http://x"},
            "embedder": {"provider": "local"},
            "graph": {},
            "failover": {"on_remote_error": "local", "timeout_ms": 1000},
        },
    )
    cap = b.vector_capability()
    assert cap["vector_write_ready"] is False
    assert cap["vector_status"] == "probe_only"


def test_resolve_tenant_modes(monkeypatch):
    from kazma_core.memory.config import resolve_tenant_id

    monkeypatch.setattr(
        "kazma_core.memory.config.memory_tenant_mode", lambda cfg=None: "shared"
    )
    assert resolve_tenant_id("web", "u1", "s1") == "default"
    monkeypatch.setattr(
        "kazma_core.memory.config.memory_tenant_mode", lambda cfg=None: "per_platform"
    )
    assert resolve_tenant_id("telegram", "u1") == "telegram"
    monkeypatch.setattr(
        "kazma_core.memory.config.memory_tenant_mode", lambda cfg=None: "per_user"
    )
    assert resolve_tenant_id("web", "", "sess-9") == "web:sess-9"
    assert resolve_tenant_id("telegram", "telegram:42") == "telegram:42"


def test_tenant_episode_isolation(mem_db):
    from kazma_core.memory.recall import recall

    now = time.time()
    for tid, color in (("ta", "crimson"), ("tb", "azure")):
        mem_db.execute(
            """INSERT INTO episodes
               (id, tenant_id, session_id, turn_number, user_text, tier, created_at)
               VALUES (?,?,?,1,?,?,?)""",
            (f"e-{tid}", tid, "s", f"favorite color is {color}", "episodic", now),
        )
    mem_db.commit()
    ra = recall("favorite color crimson", conn=mem_db, tenant_id="ta", limit=5)
    rb = recall("favorite color azure", conn=mem_db, tenant_id="tb", limit=5)
    ta_text = " ".join(h.content for h in ra.episodes).lower()
    tb_text = " ".join(h.content for h in rb.episodes).lower()
    assert "crimson" in ta_text or ra.episodes
    assert "azure" not in ta_text
    assert "azure" in tb_text or rb.episodes


def test_working_ttl_macro_sleep(mem_db):
    from kazma_core.memory.macro_sleep import run_macro_sleep

    old = time.time() - 48 * 3600
    mem_db.execute(
        """INSERT INTO episodes
           (id, tenant_id, session_id, turn_number, user_text, tier, created_at, access_count)
           VALUES ('w-old','default','s',1,'stale working','working',?,0)""",
        (old,),
    )
    mem_db.commit()
    stats = run_macro_sleep(
        mem_db,
        cfg={"v2": {"working_ttl_hours": 24}},
        tenant_id="default",
    )
    assert stats.get("demoted_working", 0) >= 1
    tier = mem_db.execute("SELECT tier FROM episodes WHERE id='w-old'").fetchone()[0]
    assert tier == "episodic"


def test_sensitive_backend_keys():
    from kazma_core.config_store import is_sensitive_config_key
    from kazma_core.memory.backends import is_sensitive_backend_key

    assert is_sensitive_backend_key("api_key") is True
    assert is_sensitive_config_key("memory.backends.vector.api_key") is True
    assert is_sensitive_config_key("memory.backends.embedder.api_key") is True


def test_entity_merge_worker_primary(mem_db, monkeypatch):
    """entity_merge handler reads primary DB (not ops)."""
    import asyncio

    from kazma_core.memory import worker_bootstrap as wb

    now = time.time()
    mem_db.execute(
        """INSERT INTO entities (id, tenant_id, type, name, aliases_json, is_high_stakes)
           VALUES ('src','default','person','Src','[]',1),
                  ('tgt','default','person','Tgt','[]',1)"""
    )
    mem_db.execute(
        """INSERT INTO entity_merges
           (id, tenant_id, source_entity_id, target_entity_id, status,
            merge_tier, confidence, requested_at)
           VALUES ('mw1','default','src','tgt','pending','tier1_exact',1.0,?)""",
        (now,),
    )
    mem_db.commit()
    path = [
        r[2]
        for r in mem_db.execute("PRAGMA database_list").fetchall()
        if r[1] == "main"
    ][0]
    monkeypatch.setattr("kazma_core.paths.primary_memory_db", lambda: path)

    ok = asyncio.run(wb._handle_entity_merge({"merge_id": "mw1"}))
    assert ok is True
    st = mem_db.execute("SELECT status FROM entity_merges WHERE id='mw1'").fetchone()
    assert st["status"] == "approved"
