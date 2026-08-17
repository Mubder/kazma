"""Scale foundation: state dual-mirror, graph backend, LLM entity tier-3 gate."""

from __future__ import annotations

import sqlite3

import pytest


def test_state_backend_null_default():
    from kazma_core.memory.state_backend import (
        NullStateBackend,
        get_state_backend,
        state_capability,
    )

    cap = state_capability(
        {"state": {"provider": "sqlite", "url": ""}, "failover": {}}
    )
    assert cap["status"] == "local"
    be = get_state_backend()
    assert isinstance(be, NullStateBackend) or be.name in ("null", "postgres")


def test_state_capability_postgres_needs_url():
    from kazma_core.memory.state_backend import state_capability

    cap = state_capability(
        {"state": {"provider": "postgres", "url": ""}, "failover": {}}
    )
    assert cap["status"] == "needs_url"
    assert cap["write_ready"] is False


def test_state_capability_postgres_primary():
    from kazma_core.memory.state_backend import state_capability

    cap = state_capability(
        {
            "state": {
                "provider": "postgres",
                "url": "postgresql://localhost/kazma",
                "role": "primary",
                "conflict_policy": "fail_closed",
                "region": "eu-1",
            }
        }
    )
    assert cap["status"] == "postgres_primary"
    assert cap["role"] == "primary"
    assert cap["conflict_policy"] == "fail_closed"
    assert "fail-closed" in cap["detail"]


def test_conflict_policy_origin_and_fail_closed():
    from kazma_core.memory.state_backend import should_apply_remote_write

    ok, _ = should_apply_remote_write(None, region="a", policy="fail_closed")
    assert ok is True
    ok, reason = should_apply_remote_write(
        {"region": "eu"}, region="us", policy="origin_wins"
    )
    assert ok is False
    assert "origin_wins" in reason
    ok, reason = should_apply_remote_write(
        {"region": "eu"}, region="us", policy="fail_closed"
    )
    assert ok is False
    assert "fail_closed" in reason
    ok, _ = should_apply_remote_write(
        {"region": "eu"}, region="us", policy="last_write_wins"
    )
    assert ok is True


def test_recall_primary_fail_closed_when_backend_down(monkeypatch):
    from kazma_core.memory.recall import recall
    from kazma_core.memory.state_backend import NullStateBackend

    monkeypatch.setattr(
        "kazma_core.memory.state_backend.is_state_primary", lambda cfg=None: True
    )
    monkeypatch.setattr(
        "kazma_core.memory.state_backend.get_state_backend",
        lambda: NullStateBackend(),
    )
    result = recall("where do I live")
    assert result.beliefs == []
    assert result.episodes == []


def test_graph_sqlite_neighbors(tmp_path, monkeypatch):
    from kazma_core.memory.graph_backend import SqliteGraphBackend, graph_capability
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    db = tmp_path / "g.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    import time

    now = time.time()
    conn.execute(
        """INSERT INTO beliefs
           (id, tenant_id, subject, predicate, predicate_type, object,
            confidence, structural_importance, source_trust_weight,
            valid_from, ingested_at)
           VALUES ('b1','default','user','likes','functional','teal',0.9,3,1.0,?,?)""",
        (now, now),
    )
    conn.commit()
    g = SqliteGraphBackend(conn)
    n = g.neighbors("user", tenant_id="default")
    assert n and n[0]["object"] == "teal"
    cap = graph_capability({"graph": {"provider": "sqlite"}})
    assert cap["status"] == "local"
    conn.close()


def test_graph_capability_neo4j_needs_url():
    from kazma_core.memory.graph_backend import graph_capability

    cap = graph_capability({"graph": {"provider": "neo4j", "url": ""}})
    assert cap["status"] == "needs_url"


def test_tier3_llm_gated_off():
    from kazma_core.memory.entity_resolution import _tier3_llm_disambiguate

    assert (
        _tier3_llm_disambiguate(
            "John", "john_smith", entity_type="person", distance=0.05, cfg={"v2": {}}
        )
        == "skip"
    )
    assert (
        _tier3_llm_disambiguate(
            "John",
            "john_smith",
            entity_type="person",
            distance=0.05,
            cfg={"v2": {"entity_llm_disambiguate": False}},
        )
        == "skip"
    )


def test_tier3_llm_merge_when_enabled(monkeypatch):
    from kazma_core.memory import entity_resolution as er

    class _FakeClient:
        def chat(self, messages=None, temperature=0, max_tokens=8):
            return {"choices": [{"message": {"content": "MERGE"}}]}

    class _FakeReg:
        def get_client(self, model=None):
            return _FakeClient()

    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry", lambda: _FakeReg()
    )
    assert (
        er._tier3_llm_disambiguate(
            "J Smith",
            "john_smith",
            entity_type="person",
            distance=0.05,
            cfg={"v2": {"entity_llm_disambiguate": True}},
        )
        == "merge"
    )
