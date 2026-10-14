"""Entity resolution queries stored name vectors; an extraction embeds only
the names it resolves, however many entities the tenant has (Memory audit
M18, MEM-R08).

Found 2026-10-13: every extraction that wrote a fact embedded every entity
name of the tenant to compare against (93 names, seconds of CPU a turn on the
live install), and past 4,096 names the embedder's short-text cache thrashed,
so every name was a model call every time.
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
import struct
from types import SimpleNamespace

import pytest


class _Embedder:
    """Deterministic unit vectors; "acme corp" and "acme corporation" share one."""

    dim = 8

    def __init__(self):
        self.calls: list[str] = []

    def encode(self, text: str) -> list[float]:
        self.calls.append(text)
        key = "acme" if text.lower().startswith("acme") else text.lower()
        raw = hashlib.sha256(key.encode()).digest()[:8]
        vec = [b - 127.5 for b in raw]
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec]


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    emb = _Embedder()
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: emb)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "fake-8")
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"), check_same_thread=False)
    ensure_ops_schema(ops)
    yield SimpleNamespace(db=db, ops=ops, emb=emb, mp=monkeypatch)
    ops.close()
    db.close()


def _entities(mem, n: int, *, vectors: bool = True) -> None:
    rows = []
    for i in range(n):
        name = f"thing {i}"
        blob = struct.pack("8f", *mem.emb.encode(name)) if vectors else None
        rows.append((f"thing_{i}", "t", "concept", name, "[]", blob, "fake-8" if vectors else None))
    mem.db.executemany(
        "INSERT INTO entities (id, tenant_id, type, name, aliases_json, embedding, embedding_model_version) "
        "VALUES (?,?,?,?,?,?,?)", rows)
    mem.db.commit()
    mem.emb.calls.clear()


def _extract(mem) -> list[str]:
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2

    _apply_beliefs_to_v2(
        [{"subject": "acme corporation", "predicate": "located_in", "predicate_type": "functional",
          "object": "lisbon", "confidence": 0.9, "importance": 3}],
        mem.db, mem.ops, tenant_id="t", now=1000.0)
    return list(mem.emb.calls)


@pytest.mark.parametrize("n", [60, 6000])
def test_an_extraction_embeds_the_same_at_60_and_6000_entities(mem, n):
    _entities(mem, n)
    calls = _extract(mem)
    assert not [c for c in calls if c.startswith("thing ")], "an unneeded name was embedded"
    # The subject, the object, the fact and its hub link -- whatever the entity count.
    assert sorted(calls) == sorted(["acme corporation", "lisbon", "acme_corporation located_in lisbon",
                                    "user related_to acme_corporation"])


def test_the_old_candidate_scan_grew_with_the_entities(mem):
    """Negative control: the old pre-pass embedded every name."""
    from kazma_core.memory.embedder import encode_text_to_blob

    def old_gather(tenant: str) -> dict:
        return {r["id"]: encode_text_to_blob(r["name"]) for r in
                mem.db.execute("SELECT id, name FROM entities WHERE tenant_id = ?", (tenant,))}

    _entities(mem, 60, vectors=False)
    old_gather("t")
    assert len(mem.emb.calls) == 60


def test_a_name_resolves_onto_an_entity_by_its_stored_vector(mem):
    from kazma_core.memory.entity_resolution import resolve_entity

    def vec(text: str) -> bytes:
        return struct.pack("8f", *mem.emb.encode(text))

    first = resolve_entity(mem.db, "Acme Corp", tenant_id="t", query_vector=vec("acme corp"))
    assert first["action"] == "create"
    assert mem.db.execute("SELECT embedding IS NOT NULL FROM entities WHERE id = ?",
                          (first["canonical_id"],)).fetchone()[0]
    second = resolve_entity(mem.db, "Acme Corporation", tenant_id="t", query_vector=vec("acme corporation"))
    assert second == {"canonical_id": first["canonical_id"], "action": "auto_merge"}
    other = resolve_entity(mem.db, "Lisbon", tenant_id="t", query_vector=vec("lisbon"))
    assert other["action"] == "create"  # far from Acme: a new entity


def test_vector_repair_stores_the_names_that_have_none(mem):
    from kazma_core.memory.entity_resolution import repair_entity_name_vectors

    _entities(mem, 5, vectors=False)
    assert repair_entity_name_vectors(mem.db) == 5
    assert mem.db.execute("SELECT COUNT(*) FROM entities WHERE embedding IS NULL").fetchone()[0] == 0
    assert repair_entity_name_vectors(mem.db) == 0
    # With no embedder it stops at once and leaves them for the next pass.
    mem.db.execute("UPDATE entities SET embedding = NULL")
    mem.db.commit()
    mem.mp.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    assert repair_entity_name_vectors(mem.db) == 0
