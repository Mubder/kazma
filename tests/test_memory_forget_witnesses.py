"""The memory audit's Forget witnesses, inverted (Memory audit M01, M22).

Each test is a witness from ``session-artifacts/audits-2026-10-13``
(``memory_surfaces_probes.py`` MEM-S01/S02/S05, ``memory_lifecycle_probes.py``
MEM-L03) with its assertion turned around: the witness passed while the
defect was there, this passes once it is gone. The witness's own setup is
kept, so the old code fails here exactly where the witness passed. Then the
tracker's searches: a fact invalidated, archived (Hygiene or the 180-day
sweep) or forgotten is not found by the Memory page's federated search.
"""

from __future__ import annotations

import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

BLOB = b"\x00\x00\x80?" * 8  # 8 float32 ones: a stand-in vector


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    for var in ("KAZMA_MEMORY_ENFORCE_TENANT", "KAZMA_PRODUCTION", "KAZMA_MULTI_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: BLOB)
    monkeypatch.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key] if key else [])
    monkeypatch.setattr("kazma_core.tenant_isolation.multi_user_or_production", lambda: False)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"), check_same_thread=False)
    ops.row_factory = sqlite3.Row
    ensure_ops_schema(ops)
    dual_write._reset_mirror()
    yield SimpleNamespace(db=db, ops=ops, mp=monkeypatch)
    dual_write._reset_mirror()
    db.close()
    ops.close()


def _fact_from_turn(mem, obj: str, *, session="chat-1", turn=3, predicate="has_condition",
                    ptype="set", private=False) -> str:
    """A fact the product writer stores from a chat turn, plus that turn's memory."""
    from kazma_core.memory.belief_mutation import mutate_belief

    r = mutate_belief(
        mem.db, "user", predicate, obj, ops_conn=mem.ops, predicate_type=ptype,
        confidence=0.9, importance=3, extraction_method="user_explicit", tenant_id="default",
        source_session=session, source_turn=turn, private=private,
    )
    mem.db.execute(
        "INSERT OR IGNORE INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
        "tier, created_at) VALUES (?, 'default', ?, ?, ?, ?, 'episodic', ?)",
        (f"ep-{session}-{turn}", session, turn, "what do I have?", f"You have {obj}.", time.time()),
    )
    mem.db.commit()
    return str(r["belief_id"])


def _anywhere(mem, value: str) -> list[str]:
    """Where *value* is still held, in either database."""
    found = []
    for name, conn in (("state", mem.db), ("ops", mem.ops)):
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
            cur = conn.execute(f'SELECT * FROM "{table}"')
            for row in cur.fetchall():
                for v in tuple(row):
                    if isinstance(v, str) and value.lower() in v.lower():
                        found.append(f"{name}:{table}")
    return sorted(set(found))


def _federated(value: str) -> list[dict]:
    from kazma_core.memory.federated_search import federated_search

    out = federated_search(value, tenant_id="default", include_knowledge=False)
    return [h for h in out["hits"] if value.lower() in json.dumps(h, default=str).lower()]


# ── MEM-S01 ────────────────────────────────────────────────────────────────


def test_s01_a_forgotten_fact_is_not_found_by_federated_search(mem):
    _fact_from_turn(mem, "zebrafinch diagnosis", session="chat-3", turn=2)
    assert _federated("zebrafinch")  # found before (negative control)
    from kazma_core.memory.forget import forget_episode

    assert forget_episode("ep-chat-3-2", tenant_id="default")["facts_forgotten"] == 1
    assert _federated("zebrafinch") == []
    assert _anywhere(mem, "zebrafinch") == []


# ── MEM-S02 ────────────────────────────────────────────────────────────────


def test_s02_forget_leaves_no_value_in_the_audit_log_and_no_vector(mem):
    from kazma_core.memory.forget import forget_episode

    bid = _fact_from_turn(mem, "SECRET-DIAGNOSIS-7")
    assert mem.ops.execute("SELECT COUNT(*) FROM memory_audit_log WHERE target_id = ?", (bid,)).fetchone()[0]
    res = forget_episode("ep-chat-1-3", tenant_id="default")
    assert res["ok"] and res["facts_forgotten"] == 1
    row = mem.db.execute("SELECT object, embedding FROM beliefs WHERE id=?", (bid,)).fetchone()
    assert row["object"] == "" and row["embedding"] is None
    audit = mem.ops.execute(
        "SELECT reason, state_before_json, state_after_json FROM memory_audit_log WHERE target_id=?", (bid,)
    ).fetchall()
    assert audit  # the event stays ...
    assert not any("SECRET-DIAGNOSIS-7" in json.dumps(tuple(r)) for r in audit)  # ... its value does not


def test_s02_forget_reaches_an_archived_copy_of_the_fact(mem):
    from kazma_core.memory.forget import forget_episode
    from kazma_core.memory.hygiene import invalidate_belief
    from kazma_ui import memory_api

    bid = _fact_from_turn(mem, "SECRET-ADDRESS-12")
    invalidate_belief(bid, tenant_id="default")
    assert memory_api._archive_invalidated_sync()["archived"] >= 1
    assert forget_episode("ep-chat-1-3", tenant_id="default")["ok"]
    blob = mem.db.execute("SELECT original_belief_json FROM beliefs_archive WHERE id=?", (bid,)).fetchone()[0]
    assert "SECRET-ADDRESS-12" not in blob
    archived = json.loads(blob)
    assert archived["object"] == "" and "embedding" not in archived
    assert archived["source_session"] == "chat-1"  # where it came from stays


def test_s02_forget_leaves_no_operator_edit_history_value(mem):
    from kazma_core.memory.forget import forget_episode
    from kazma_ui import memory_api

    bid = _fact_from_turn(mem, "SECRET-EMPLOYER-3")
    assert memory_api._edit_belief_sync(bid, {"object": "corrected"})["ok"]
    assert forget_episode("ep-chat-1-3", tenant_id="default")["ok"]
    row = mem.db.execute("SELECT object, metadata_json FROM beliefs WHERE id=?", (bid,)).fetchone()
    assert row["object"] == "" and "SECRET-EMPLOYER-3" not in row["metadata_json"]
    assert "operator_edit" not in json.loads(row["metadata_json"])


# ── MEM-L03 ────────────────────────────────────────────────────────────────


def test_l03_a_forgotten_turns_fact_archived_by_the_180_day_sweep_keeps_no_value(mem):
    from kazma_core.memory.forget import forget_episode
    from kazma_core.memory.macro_sleep import run_macro_sleep

    _fact_from_turn(mem, "acme", session="th1", turn=1, predicate="works_at", ptype="functional")
    _fact_from_turn(mem, "globex", session="th1", turn=2, predicate="works_at", ptype="functional")
    assert run_macro_sleep(mem.db, cfg={}, now=time.time() + 200 * 86400)["archived_beliefs"] == 1
    forget_episode("ep-th1-1", tenant_id="default")
    archived = [json.loads(r[0]) for r in mem.db.execute("SELECT original_belief_json FROM beliefs_archive")]
    assert archived and not any(b.get("object") == "acme" for b in archived)
    assert [b["source_turn"] for b in archived] == [1]


def test_l03_a_forget_from_before_the_fix_is_finished_by_asking_again(mem, monkeypatch):
    """A forget done by the old code (value column only) left the vector, the
    audit values and the archive; asking again finishes it."""
    from kazma_core.memory import forget

    bid = _fact_from_turn(mem, "SECRET-OLD-FORGET-1")
    mp = pytest.MonkeyPatch()
    mp.setattr(forget, "_forget_archived_facts", lambda *a, **k: [])
    mp.setattr(forget, "_after_facts", lambda *a, **k: [])
    mp.setattr(forget, "_provenance_only", lambda meta, *, now: {**meta, "forgotten": {"at": now}})
    mp.setattr(forget, "purge_search_index", lambda conn: None)
    mem.db.execute("UPDATE beliefs SET embedding = ? WHERE id = ?", (BLOB, bid))
    mem.db.commit()
    try:
        forget.forget_episode("ep-chat-1-3", tenant_id="default")
    finally:
        mp.undo()
    assert _anywhere(mem, "SECRET-OLD-FORGET-1")  # the old forget left it (negative control)
    out = forget.forget_episode("ep-chat-1-3", tenant_id="default")
    assert out["already"] and out["facts_forgotten"] == 1
    assert _anywhere(mem, "SECRET-OLD-FORGET-1") == []
    assert mem.db.execute("SELECT embedding FROM beliefs WHERE id = ?", (bid,)).fetchone()[0] is None


# ── Invalidate and archive: not served by federated search ─────────────────


def test_an_invalidated_fact_is_not_found_by_federated_search(mem):
    from kazma_core.memory.hygiene import invalidate_belief

    bid = _fact_from_turn(mem, "quokkastripe")
    assert _federated("quokkastripe")
    invalidate_belief(bid, tenant_id="default")
    assert _federated("quokkastripe") == []


def test_an_archived_fact_is_not_found_by_federated_search(mem):
    from kazma_core.memory.hygiene import invalidate_belief
    from kazma_ui import memory_api

    bid = _fact_from_turn(mem, "pangolinwhorl")
    invalidate_belief(bid, tenant_id="default")
    assert memory_api._archive_invalidated_sync()["archived"] >= 1
    assert _federated("pangolinwhorl") == []


# ── Knowledge-library delete (MEM-L11, KB-E08) ─────────────────────────────


def test_a_deleted_librarys_text_is_not_found_and_was_never_copied_into_memory(mem, tmp_path):
    """The unified index copied every Knowledge chunk into memory_ops.db and
    served it after the library was deleted. Now nothing of a chunk reaches
    the memory databases, and a deleted library's text is not found."""
    import kazma_core.stores.knowledge as kb_module
    from kazma_core.stores.knowledge import KnowledgeStore, reset_knowledge_store
    from kazma_core.stores.knowledge_chunker import chunk_markdown_doc, chunk_to_dict
    from kazma_core.stores.knowledge_index import KnowledgeIndex, reset_knowledge_index

    mem.mp.setenv("KAZMA_PROJECT_ROOT", str(tmp_path))
    reset_knowledge_index()
    reset_knowledge_store()
    store = KnowledgeStore(db_path=str(tmp_path / "kb_settings.db"))
    mem.mp.setattr(kb_module, "_knowledge_store", store)
    store.create_library("lib_q", "Q")
    index = KnowledgeIndex(store=store)
    mem.mp.setattr("kazma_core.stores.knowledge_index.get_knowledge_index", lambda: index)
    # A page that still names the library (a stale selection) asks for it.
    mem.mp.setattr("kazma_core.memory.federated_search.resolve_kb_library_ids",
                   lambda *a, **k: ["lib_q"])
    doc = "# Field notes\n\n## Quillvortex\n\nThe quillvortex valve opens at dawn and closes at dusk.\n"
    index.index("lib_q", [chunk_to_dict(c) for c in chunk_markdown_doc(doc, source_url="https://x/q",
                                                                       library_id="lib_q")])

    from kazma_core.memory.federated_search import federated_search

    found = federated_search("quillvortex valve", tenant_id="default")
    assert any("quillvortex" in (h.get("content") or "").lower() for h in found["hits"])  # control
    assert _anywhere(mem, "quillvortex") == []  # no copy in either memory database
    assert index.delete_library("lib_q") is True
    after = federated_search("quillvortex valve", tenant_id="default")
    assert not any("quillvortex" in json.dumps(h, default=str).lower() for h in after["hits"])
    reset_knowledge_index()
    reset_knowledge_store()
