"""pgvector as memory/search primary when Postgres is already on (industry part 6).

sqlite-vec stays the one-node default. A process Postgres DSN auto-selects
pgvector (hybrid dual-write, or remote-first when state.role=primary).
Postgres-primary recall fuses ILIKE sparse with dense — not ILIKE-only.
"""

from __future__ import annotations

from typing import Any

import pytest

from kazma_core.memory.backends import (
    HybridVectorBackend,
    _apply_pgvector_scale_defaults,
    get_backends_cfg,
)
from kazma_core.memory.recall import RecallHit, _rrf_fuse, recall


def _cfg(**kwargs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "mode": "local",
        "vector": {
            "provider": "sqlite_vec",
            "url": "",
            "api_key": "",
            "collection": "kazma_memory",
            "dimension": 1024,
        },
        "embedder": {"provider": "local"},
        "graph": {"provider": "sqlite"},
        "state": {"provider": "sqlite", "url": "", "role": "mirror"},
        "failover": {"on_remote_error": "local", "timeout_ms": 5000},
    }
    for k, v in kwargs.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k].update(v)
        else:
            base[k] = v
    return base


def test_pgvector_not_forced_without_dsn(monkeypatch) -> None:
    monkeypatch.delenv("KAZMA_PGVECTOR", raising=False)
    out = _cfg()
    _apply_pgvector_scale_defaults(out)
    assert out["vector"]["provider"] == "sqlite_vec"
    assert out["mode"] == "local"


def test_pgvector_auto_selects_hybrid_when_postgres_dsn(monkeypatch) -> None:
    """Pytest strips KAZMA_DATABASE_URL; the production helper still reads it.
    Here the DSN comes from memory.backends.state.url (Settings / same DSN).
    """
    monkeypatch.delenv("KAZMA_PGVECTOR", raising=False)
    out = _cfg(
        state={
            "provider": "postgres",
            "url": "postgresql://kazma:x@localhost:5432/kazma",
            "role": "mirror",
        }
    )
    _apply_pgvector_scale_defaults(out)
    assert out["vector"]["provider"] == "pgvector"
    assert out["vector"]["url"].startswith("postgresql://")
    assert out["mode"] == "hybrid"


def test_pgvector_primary_role_is_remote_first(monkeypatch) -> None:
    monkeypatch.delenv("KAZMA_PGVECTOR", raising=False)
    out = _cfg(
        state={
            "provider": "postgres",
            "url": "postgresql://localhost/kazma",
            "role": "primary",
        }
    )
    _apply_pgvector_scale_defaults(out)
    assert out["vector"]["provider"] == "pgvector"
    assert out["mode"] == "remote"


def test_pgvector_kill_switch(monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_PGVECTOR", "0")
    out = _cfg(
        state={"provider": "postgres", "url": "postgresql://localhost/kazma", "role": "mirror"}
    )
    _apply_pgvector_scale_defaults(out)
    assert out["vector"]["provider"] == "sqlite_vec"
    assert out["mode"] == "local"


def test_explicit_qdrant_not_overridden(monkeypatch) -> None:
    monkeypatch.delenv("KAZMA_PGVECTOR", raising=False)
    out = _cfg(
        vector={"provider": "qdrant", "url": "http://qdrant:6333"},
        state={"provider": "postgres", "url": "postgresql://localhost/kazma"},
    )
    _apply_pgvector_scale_defaults(out)
    assert out["vector"]["provider"] == "qdrant"
    assert out["vector"]["url"] == "http://qdrant:6333"


def test_get_backends_cfg_respects_kill_switch(monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_PGVECTOR", "0")
    cfg = get_backends_cfg()
    assert cfg["vector"]["provider"] in ("sqlite_vec", "local", "local_sqlite")


def test_hybrid_belief_search_does_not_fall_back_to_episodes() -> None:
    class _Remote:
        available = True

        def search(self, *a, **k):
            return []

    class _Local:
        available = True

        def search(self, *a, **k):
            return [("ep-should-not-leak", 0.99)]

    hybrid = HybridVectorBackend(_Remote(), _Local())
    assert hybrid.search([0.1], kind="belief") == []
    assert hybrid.search([0.1], kind="episode") == [("ep-should-not-leak", 0.99)]


def test_postgres_primary_fuses_pgvector_dense(monkeypatch) -> None:
    class _State:
        name = "postgres"
        available = True

        def search_episodes(self, query, *, tenant_id="default", limit=10):
            return [{"id": "e-sparse", "summary_text": "I live in Paris", "tier": "episodic"}]

        def search_beliefs(self, query, *, tenant_id="default", limit=10):
            return [
                {
                    "id": "b-sparse",
                    "subject": "user",
                    "predicate": "lives_in",
                    "object": "Paris",
                    "structural_importance": 3,
                    "confidence": 0.9,
                }
            ]

        def fetch_episodes(self, ids, *, tenant_id="default"):
            return [{"id": "e-dense", "summary_text": "moved to Lyon last year", "tier": "episodic"}]

        def fetch_beliefs(self, ids, *, tenant_id="default"):
            return [
                {
                    "id": "b-dense",
                    "subject": "user",
                    "predicate": "lives_in",
                    "object": "Lyon",
                }
            ]

        def mirror_episode(self, row):
            return False

        def mirror_belief(self, row):
            return False

    class _Vec:
        name = "pgvector"
        available = True
        write_ready = True

        def search(self, query_vec, *, tenant_id="default", tier=None, limit=10, kind=None):
            if kind == "belief":
                return [("b-dense", 0.91)]
            return [("e-dense", 0.88)]

        def upsert(self, *a, **k):
            return True

        def delete(self, *a, **k):
            return True

    class _Emb:
        def encode(self, text):
            return [0.1, 0.2, 0.3]

    monkeypatch.setattr(
        "kazma_core.memory.state_backend.is_state_primary", lambda cfg=None: True
    )
    monkeypatch.setattr(
        "kazma_core.memory.state_backend.get_state_backend", lambda: _State()
    )
    monkeypatch.setattr(
        "kazma_core.memory.backends.get_vector_backend", lambda conn=None: _Vec()
    )
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: _Emb())

    result = recall("where do I live")
    ep_ids = {h.id for h in result.episodes}
    bel_ids = {h.id for h in result.beliefs}
    assert "e-sparse" in ep_ids
    assert "e-dense" in ep_ids
    assert "b-sparse" in bel_ids
    assert "b-dense" in bel_ids
    assert any(h.source == "dense" for h in result.episodes)


def test_postgres_primary_still_fail_closed(monkeypatch) -> None:
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


def test_rrf_keeps_both_channels() -> None:
    sparse = [
        RecallHit(id="a", content="s", score=1.0, kind="episode", source="postgres_state"),
    ]
    dense = [
        RecallHit(id="b", content="d", score=0.9, kind="episode", source="dense"),
    ]
    fused = _rrf_fuse(sparse, dense, {}, 5)
    assert {h.id for h in fused} == {"a", "b"}


def test_pgvector_search_filters_kind(monkeypatch) -> None:
    from kazma_core.memory.backends import PgvectorBackend

    captured: dict[str, Any] = {}

    class _Cur:
        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params

        def fetchall(self):
            return [("b-1", 0.8)]

        def close(self):
            return None

    class _Conn:
        def cursor(self):
            return _Cur()

        def commit(self):
            return None

        def close(self):
            return None

    be = PgvectorBackend(dsn="postgresql://localhost/kazma", dimension=3)
    monkeypatch.setattr(be, "_connect", lambda: _Conn())
    monkeypatch.setattr(be, "_ensure_table", lambda conn: None)
    hits = be.search([0.1, 0.2, 0.3], tenant_id="default", tier=None, kind="belief", limit=5)
    assert hits == [("b-1", 0.8)]
    assert "meta->>'kind'" in captured["sql"]
    assert "belief" in list(captured["params"])
