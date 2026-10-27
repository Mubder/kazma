"""The embedding rebuild and the vector repair write each vector the §15 D2
way: embedded once with no write lock held, written by one guarded UPDATE,
never onto a tombstone.

Audit 2026-10-26 AUD-028: both passes embedded rows and wrote
``UPDATE ... SET embedding`` inside an open batch transaction, unguarded. A
forget landing between the read and the write got the forgotten text's
vector written back onto its tombstone and re-sent to the remote index, and
the batch held the memory write lock while the model ran. They now go
through ``vector_write`` (``encode_vector``, ``write_local_vector(...,
replace=True)``, ``index_remotely``) via ``reembed._reencode_row``.
"""

from __future__ import annotations

import sqlite3
import struct
from types import SimpleNamespace

import pytest

DIM = 4
OLD = b"\x00\x00\x80?" * DIM  # a vector of the old model


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    state = tmp_path / "memory_state.db"
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(state))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key] if key else [])
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "new-model")
    monkeypatch.setattr("kazma_core.memory.reembed._MODEL_DIMS", {})
    db = sqlite3.connect(str(state), check_same_thread=False, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    yield SimpleNamespace(db=db, state=state, mp=monkeypatch)
    db.close()


class _Remote:
    available = True

    def __init__(self):
        self.ops: list[tuple[str, str]] = []

    def upsert(self, rid, *_a, **_k):
        self.ops.append(("upsert", rid))
        return True

    def delete(self, rid, *_a, **_k):
        self.ops.append(("delete", rid))
        return True


class _Embedder:
    """Counts its calls; on the secret, forgets that memory (another
    connection, as a real Forget does) and records whether the database's
    write lock was free while it embedded."""

    dim = DIM

    def __init__(self, mem, *, forget_on: str = ""):
        self.mem = mem
        self.forget_on = forget_on
        self.texts: list[str] = []
        self.lock_free: list[bool] = []

    def encode(self, text):
        self.texts.append(text)
        other = sqlite3.connect(str(self.mem.state), timeout=0)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.rollback()
            self.lock_free.append(True)
        except sqlite3.OperationalError:
            self.lock_free.append(False)
        finally:
            other.close()
        if self.forget_on and self.forget_on in text:
            self._forget()
        return [0.5] * DIM

    def _forget(self):
        from kazma_core.memory import forget

        other = sqlite3.connect(str(self.mem.state), check_same_thread=False)
        other.row_factory = sqlite3.Row
        try:
            assert forget.forget_episode("e-secret", tenant_id="t", conn=other)["ok"]
        finally:
            other.close()


def _seed(mem) -> None:
    for eid, text in (("e-plain", "the pump housings come from Nordvalve"),
                      ("e-secret", "my locker code is 4471")):
        mem.db.execute(
            "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
            "created_at, metadata_json, embedding, embedding_model_version) "
            "VALUES (?, 't', 's', 1, ?, 'Noted.', 'episodic', 1.0, '{}', ?, 'old-model')",
            (eid, text, OLD))


def _use(mem, emb, remote) -> None:
    mem.mp.setattr("kazma_core.memory.embedder.get_embedder", lambda: emb)
    mem.mp.setattr("kazma_core.memory.backends.get_remote_vector_backend", lambda: remote)


def _secret_row(mem):
    return mem.db.execute("SELECT tier, embedding FROM episodes WHERE id = 'e-secret'").fetchone()


def test_a_forget_during_the_rebuild_keeps_the_tombstone_vectorless(mem):
    from kazma_core.memory import reembed

    _seed(mem)
    emb, remote = _Embedder(mem, forget_on="4471"), _Remote()
    _use(mem, emb, remote)
    reembed.rebuild_embeddings(model_name="new-model")
    row = _secret_row(mem)
    assert row["tier"] == "forgotten" and row["embedding"] is None
    assert ("upsert", "e-secret") not in remote.ops
    plain = mem.db.execute("SELECT embedding_model_version FROM episodes WHERE id = 'e-plain'").fetchone()[0]
    assert plain == "new-model"


def test_the_rebuild_embeds_each_row_once_with_the_lock_free(mem):
    from kazma_core.memory import reembed

    _seed(mem)
    emb, remote = _Embedder(mem), _Remote()
    _use(mem, emb, remote)
    reembed.rebuild_embeddings(model_name="new-model")
    # One embedding per row (the remote copy is the same vector) ...
    assert sorted(emb.texts) == sorted(set(emb.texts)) and len(emb.texts) == 2
    assert sorted(r for _op, r in remote.ops) == ["e-plain", "e-secret"]
    # ... and no write lock held while the model runs.
    assert emb.lock_free and all(emb.lock_free)


def test_a_forget_during_the_repair_keeps_the_tombstone_vectorless(mem):
    from kazma_core.memory import reembed

    _seed(mem)
    emb, remote = _Embedder(mem, forget_on="4471"), _Remote()
    _use(mem, emb, remote)
    out = reembed.repair_unsearchable_vectors(mem.db, embedder=emb, model="new-model")
    row = _secret_row(mem)
    assert row["tier"] == "forgotten" and row["embedding"] is None
    assert ("upsert", "e-secret") not in remote.ops
    assert out["episodes"] == 1  # the plain one
    assert all(emb.lock_free)


def test_a_vector_written_meanwhile_is_not_overwritten(mem):
    """``replace`` is a compare-and-set on the vector read with the row."""
    from kazma_core.memory.vector_write import write_local_vector

    _seed(mem)
    newer = b"\x00\x00\x00@" * DIM
    mem.db.execute("UPDATE episodes SET embedding = ? WHERE id = 'e-plain'", (newer,))
    assert not write_local_vector(mem.db, "episodes", "e-plain", [0.5] * DIM, still_sql="1",
                                  replace=True, replacing=OLD, model="new-model")
    assert mem.db.execute("SELECT embedding FROM episodes WHERE id = 'e-plain'").fetchone()[0] == newer


def test_negative_control_the_old_unguarded_write_revives_the_tombstone(mem):
    """The old passes' write: read the row, embed, then a plain
    ``UPDATE ... WHERE id = ?``. With the forget landing in between, the
    tombstone gets the forgotten text's vector back."""
    _seed(mem)
    emb = _Embedder(mem, forget_on="4471")
    row = mem.db.execute("SELECT id, user_text FROM episodes WHERE id = 'e-secret'").fetchone()
    vec = emb.encode(row["user_text"])  # the forget lands here
    mem.db.execute("UPDATE episodes SET embedding = ?, embedding_model_version = ? WHERE id = ?",
                   (struct.pack(f"<{DIM}f", *vec), "new-model", row["id"]))
    revived = _secret_row(mem)
    assert revived["tier"] == "forgotten" and revived["embedding"] is not None
