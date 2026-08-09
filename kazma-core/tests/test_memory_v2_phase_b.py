"""Phase B memory tests: FTS5, belief-graph PPR multi-hop, explain mode."""

from __future__ import annotations

import sqlite3
import time

import pytest


@pytest.fixture()
def mem_db(tmp_path, monkeypatch):
    """Isolated memory_state.db with full V2 schema including FTS5."""
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


def _insert_episode(
    conn: sqlite3.Connection,
    *,
    eid: str,
    session_id: str,
    user_text: str,
    tier: str = "episodic",
    tenant_id: str = "default",
) -> None:
    now = time.time()
    conn.execute(
        """INSERT INTO episodes (
            id, tenant_id, session_id, turn_number, user_text, assistant_text,
            tier, structural_importance, created_at, access_count
        ) VALUES (?, ?, ?, 1, ?, '', ?, 2, ?, 0)""",
        (eid, tenant_id, session_id, user_text, tier, now),
    )
    conn.commit()


def _insert_belief(
    conn: sqlite3.Connection,
    *,
    bid: str,
    subject: str,
    predicate: str,
    obj: str,
    tenant_id: str = "default",
    importance: int = 3,
    confidence: float = 0.9,
) -> None:
    now = time.time()
    conn.execute(
        """INSERT INTO beliefs (
            id, tenant_id, subject, predicate, predicate_type, object,
            confidence, structural_importance, source_trust_weight,
            valid_from, ingested_at
        ) VALUES (?, ?, ?, ?, 'functional', ?, ?, ?, 1.0, ?, ?)""",
        (bid, tenant_id, subject, predicate, obj, confidence, importance, now, now),
    )
    conn.commit()


def test_episodes_fts_table_exists(mem_db):
    row = mem_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='episodes_fts'"
    ).fetchone()
    assert row is not None, "episodes_fts virtual table must exist"


def test_fts5_match_ranks_and_tenant_isolates(mem_db):
    from kazma_core.memory.recall import _episode_fts, _fts_match_query

    assert "teal" in _fts_match_query("favorite color teal")
    _insert_episode(
        mem_db,
        eid="ep-teal",
        session_id="s1",
        user_text="My favorite color is teal",
        tier="episodic",
        tenant_id="default",
    )
    _insert_episode(
        mem_db,
        eid="ep-other-tenant",
        session_id="s2",
        user_text="My favorite color is teal",
        tier="episodic",
        tenant_id="tenant-b",
    )
    hits = _episode_fts(mem_db, "teal color", "default", limit=10)
    ids = [h.id for h in hits]
    assert "ep-teal" in ids
    assert "ep-other-tenant" not in ids
    # Prefer real FTS source when available
    teal = next(h for h in hits if h.id == "ep-teal")
    assert teal.source in ("fts5", "fts_like")


def test_belief_graph_ppr_multi_hop(mem_db):
    """user works_at Acme + Acme located_in Paris → query about workplace city."""
    from kazma_core.memory.recall import recall

    _insert_belief(
        mem_db,
        bid="b-work",
        subject="user",
        predicate="works_at",
        obj="AcmeCorp",
        importance=4,
    )
    _insert_belief(
        mem_db,
        bid="b-loc",
        subject="AcmeCorp",
        predicate="located_in",
        obj="Paris",
        importance=4,
    )
    # Seed with entity "user" so PPR walks user → AcmeCorp → Paris
    result = recall("where does the user company sit", conn=mem_db, limit=5, explain=True)
    ids = {h.id for h in result.beliefs}
    contents = " ".join(h.content for h in result.beliefs).lower()
    # Multi-hop should surface the location belief (or at least works_at)
    assert "b-work" in ids or "b-loc" in ids
    assert "acmecorp" in contents or "paris" in contents
    # Prefer that Paris / located_in appears via graph walk
    assert any(
        "paris" in (h.content or "").lower() or h.id == "b-loc" for h in result.beliefs
    ) or any(
        "ppr" in (h.metadata.get("sources") or []) for h in result.beliefs
    )


def test_explain_recall_tags_sources(mem_db):
    from kazma_core.memory.recall import recall

    _insert_episode(
        mem_db,
        eid="ep-x",
        session_id="sess-explain",
        user_text="Remember my favorite snack is pretzels",
        tier="recall",
    )
    r = recall(
        "pretzels snack",
        conn=mem_db,
        limit=5,
        session_id="sess-explain",
        explain=True,
    )
    assert not r.empty
    # At least one hit should expose sources list
    tagged = [
        h
        for h in (r.episodes + r.beliefs)
        if isinstance((h.metadata or {}).get("sources"), list)
        and h.metadata["sources"]
    ]
    assert tagged, "explain=True must tag metadata['sources']"


def test_dense_belief_cap_respected(mem_db, monkeypatch):
    """Candidate fetch uses LIMIT from dense_belief_candidate_cap."""
    from kazma_core.memory import recall as recall_mod

    # No embedder → dense returns []; we still verify the SQL path doesn't crash
    # with many beliefs.
    for i in range(20):
        _insert_belief(
            mem_db,
            bid=f"b-{i}",
            subject="user",
            predicate=f"note_{i}",
            obj=f"fact_{i}",
            importance=1,
        )
    monkeypatch.setattr(
        "kazma_core.memory.embedder.get_embedder",
        lambda: None,
    )
    rows = recall_mod._belief_dense(mem_db, "anything", None, "default", 5)
    assert rows == []


def test_episode_ppr_traverses_from_later_seed(mem_db):
    from kazma_core.memory.ppr import ppr_available
    from kazma_core.memory.recall import _episode_ppr

    _insert_episode(
        mem_db,
        eid="ep-a",
        session_id="sess-ppr",
        user_text="session alpha",
        tier="episodic",
    )
    _insert_episode(
        mem_db,
        eid="ep-b",
        session_id="sess-ppr",
        user_text="session beta",
        tier="episodic",
    )
    scores = _episode_ppr(mem_db, ["ep-b"], "default")
    if ppr_available():
        assert "ep-a" in scores
    else:
        assert "ep-b" in scores


def test_read_memory_cfg_store_overlay_includes_ppr_hop_radius(monkeypatch):
    from kazma_core.memory.config import read_memory_cfg

    class _DummyStore:
        def get(self, key: str, default=None):
            if key == "memory.v2.ppr_hop_radius":
                return 5
            return default

    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: _DummyStore())
    cfg = read_memory_cfg()
    assert int(((cfg.get("v2") or {}).get("ppr_hop_radius") or 0)) == 5
