"""Recall reaches every fact near the question (R5) and every vector it holds (R8).

R5: the graph walk loaded the tenant's most important facts -- 800 at the
defaults -- and walked those. A fact below that cut was out of reach however
close it was to the question: with many important facts, "user works at
Acme" and "Acme is in Lisbon" (both said once, importance 1) could never be
found by walking from Acme. It now loads the seeds' neighbourhood, hop by hop.

R8: hybrid vector search returned the remote index's hits alone whenever it
had any, so a memory not yet in the remote index was invisible to meaning
search. It merges both, the local store's score winning.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from kazma_core.memory.schema_v2 import ensure_primary_schema

# ── R5 ────────────────────────────────────────────────────────────────────


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    c = sqlite3.connect(str(tmp_path / "memory_state.db"))
    c.row_factory = sqlite3.Row
    ensure_primary_schema(c)
    now = time.time()
    rows = [(f"b{i}", f"topic_{i}", "has_value", f"value_{i}", 0.95, 5) for i in range(1100)]
    rows += [("chain1", "user", "works_at", "acme", 0.5, 1),
             ("chain2", "acme", "located_in", "lisbon", 0.5, 1)]
    c.executemany(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, "
        "structural_importance, source_trust_weight, valid_from, ingested_at) "
        "VALUES (?, 'default', ?, ?, 'set', ?, ?, ?, 1.0, ?, ?)",
        [(*r, now, now) for r in rows],
    )
    c.commit()
    yield c
    c.close()


def test_the_walk_reaches_a_fact_below_every_important_one(conn):
    from kazma_core.memory import recall

    scores = recall._belief_graph_ppr(conn, "tell me about acme", "default")
    assert {"chain1", "chain2"} <= set(scores)


def test_the_old_global_cut_could_not(conn, monkeypatch):
    """Negative control: walking the 800 most important facts instead."""
    from kazma_core.memory import recall

    def top_800(conn, tenant_id, seeds, *, rounds, cap, norm):
        return conn.execute(
            "SELECT id, subject, predicate, object, confidence, structural_importance FROM beliefs "
            "WHERE tenant_id = ? ORDER BY structural_importance DESC, confidence DESC LIMIT 800",
            (tenant_id,),
        ).fetchall()

    monkeypatch.setattr(recall, "_belief_neighbourhood", top_800)
    assert "chain2" not in recall._belief_graph_ppr(conn, "tell me about acme", "default")


def test_the_neighbourhood_is_read_hop_by_hop(conn):
    from kazma_core.memory import recall

    one = recall._belief_neighbourhood(conn, "default", {"user"}, rounds=1, cap=100,
                                       norm=lambda s: (s or "").lower())
    two = recall._belief_neighbourhood(conn, "default", {"user"}, rounds=2, cap=100,
                                       norm=lambda s: (s or "").lower())
    assert [r["id"] for r in one] == ["chain1"]
    assert sorted(r["id"] for r in two) == ["chain1", "chain2"]


# ── R8 ────────────────────────────────────────────────────────────────────


class _Store:
    def __init__(self, hits, available=True):
        self.hits, self.available = hits, available

    def search(self, query_vec, *, tenant_id, tier, limit, kind):
        return list(self.hits)[:limit]


def test_a_memory_not_yet_in_the_remote_index_is_found():
    from kazma_core.memory.backends import HybridVectorBackend

    remote = _Store([("a", 0.9)])
    local = _Store([("b", 0.85), ("a", 0.8)])  # "b" was written while the remote was down
    hits = HybridVectorBackend(remote, local).search([1.0], limit=5)
    assert hits == [("b", 0.85), ("a", 0.8)]  # both; the local score wins for "a"


def test_without_a_remote_index_it_is_the_local_store():
    from kazma_core.memory.backends import HybridVectorBackend

    local = _Store([("b", 0.85)])
    assert HybridVectorBackend(_Store([("a", 0.9)], available=False), local).search(
        [1.0], limit=5) == [("b", 0.85)]
