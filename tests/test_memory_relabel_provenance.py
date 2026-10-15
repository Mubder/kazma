"""The facts the memory tools stored as the user's are relabelled (Memory
audit M03, the rows written before the fix; 2026-10-15).

Until 2026-10-13 ``memory_store`` and ``memory_link_entities`` wrote
``user_explicit`` for whatever the model chose, and those rows still block
what only the user's words should block. ``memory/provenance_relabel.py``
finds them by what the store recorded; ``scripts/memory_relabel_provenance.py``
sizes and applies it.

Here, on a store seeded the way the old tools and the Memory page wrote it:
each case -- a note with and without its chat, a link, a turn that asked to
remember, a Memory page edit, an operator action (and a failed one), the
page's own link, the V1 backfill, another tenant -- classified as it should
be; ``size`` names ids, never values; ``apply`` relabels, journals and is
idempotent; the script copies first. Negative controls: before the relabel a
tool's fact blocks a later model reading (after it, it does not), and without
the operator's trail the kept facts would be relabelled.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
SECRET = "locker code 4471"  # a value: size and the audit rows never show it


#: The tool-written facts no user channel claims.
RELABELLED = ("store_old", "store_chat", "link_tool", "failed_action", "beta_tool", "tool_functional")


def _same_key(key: str) -> list[str]:
    return [key]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    dual_write._reset_mirror()
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ids: dict[str, str] = {}

    def episode(eid, tenant, session, turn, text, at, meta=None):
        db.execute(
            "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
            "created_at, metadata_json) VALUES (?, ?, ?, ?, ?, 'ok', 'episodic', ?, ?)",
            (eid, tenant, session, turn, text, at, json.dumps(meta or {})))

    def fact(name, subject, pred, obj, *, tenant="default", method="user_explicit", session=None, turn=None,
             at=0.0, ptype="set", importance=5):
        out = mutate_belief(db, subject, pred, obj, ops_conn=ops, predicate_type=ptype, confidence=0.9,
                            importance=importance, extraction_method=method, tenant_id=tenant,
                            source_session=session, source_turn=turn, now=at, private=True)
        ids[name] = out["belief_id"]
        db.execute("UPDATE beliefs SET ingested_at = ? WHERE id = ?", (at, out["belief_id"]))

    # 1. memory_store before notes named their chat (no session): the note, then the fact.
    episode("n_old", "default", "memory_store", 0, "the boat is called Seagull", 1000.0,
            {"source": "memory_store_tool"})
    fact("store_old", "user", "noted", "the boat is called Seagull", at=1000.4)
    # 2. memory_store naming its chat turn; the turn asked nothing to remember.
    episode("q_t1_2", "default", "t1", 2, "what is the weather like today", 2000.0)
    episode("n_t1_2", "default", "memory_store", 0, "the user likes rain", 2001.0,
            {"source": "memory_store_tool", "chat": "t1", "chat_turn": 2})
    fact("store_chat", "user", "noted", "the user likes rain", session="t1", turn=2, at=2001.2)
    # 3. memory_link_entities naming its chat turn.
    episode("q_t1_3", "default", "t1", 3, "tell me about my project", 3000.0)
    fact("link_tool", "shipx", "uses", "postgres", session="t1", turn=3, at=3001.0, importance=4)
    # 4. a turn that asked to remember those words: the user's channel.
    episode("q_t2_1", "default", "t2", 1, f"Remember that my {SECRET}", 4000.0)
    episode("n_t2_1", "default", "memory_store", 0, f"my {SECRET}", 4001.0,
            {"source": "memory_store_tool", "chat": "t2", "chat_turn": 1})
    fact("asked", "user", "noted", f"my {SECRET}", session="t2", turn=1, at=4001.3)
    # 5. a tool's fact the operator then edited on the Memory page.
    episode("q_t1_4", "default", "t1", 4, "ok", 5000.0)
    fact("edited", "user", "prefers", "tea", session="t1", turn=4, at=5001.0)
    db.execute("UPDATE beliefs SET metadata_json = json_set(COALESCE(NULLIF(metadata_json,''),'{}'), "
               "'$.operator_edit', json('{\"at\": 5100}')) WHERE id = ?", (ids["edited"],))
    # 6. a tool's fact an operator action named (kept), and one whose action failed (relabelled).
    fact("acted", "user", "owns", "a bicycle", session="t1", turn=5, at=6001.0)
    fact("failed_action", "user", "owns", "a kayak", session="t1", turn=6, at=6002.0)
    ops.executemany(
        "INSERT INTO memory_audit_log (id, tenant_id, timestamp, event_type, target_table, target_id, actor, reason) "
        "VALUES (?, 'default', 6100, 'operator_action', 'memory', ?, 'web:admin:abcd', ?)",
        [("op1", ids["acted"], "PATCH /api/memory/v2/beliefs/{belief_id} -> 200"),
         ("op2", ids["failed_action"], "PATCH /api/memory/v2/beliefs/{belief_id} -> 404")])
    # 7. the Memory page's own link, and 8. the V1 backfill: no chat, no note.
    fact("page_link", "user", "knows", "ana", at=7001.0, importance=3)
    fact("backfill", "user", "speaks", "portuguese", at=8001.0, importance=4)
    # 9. labels that are not the user's: never touched.
    fact("stated", "user", "lives_in", "porto", method="user_stated", session="t3", turn=1, at=9001.0,
         ptype="functional")
    fact("inferred", "user", "plays", "chess", method="llm_inferred", session="t3", turn=1, at=9002.0)
    # 10. another tenant's tool fact.
    episode("q_b_1", "beta", "tb", 1, "what time is it", 10000.0)
    fact("beta_tool", "user", "noted", "beta's note", tenant="beta", session="tb", turn=1, at=10001.0)
    # 11. a tool's functional fact, for the behaviour check.
    episode("q_t1_7", "default", "t1", 7, "hmm", 11000.0)
    fact("tool_functional", "user", "works_at", "acme", session="t1", turn=7, at=11001.0, ptype="functional")
    db.commit()
    ops.commit()
    yield SimpleNamespace(db=db, ops=ops, dir=tmp_path, ids=ids)
    dual_write._reset_mirror()
    ops.close()
    db.close()


def _method(db, bid: str) -> str:
    return db.execute("SELECT extraction_method FROM beliefs WHERE id = ?", (bid,)).fetchone()[0]


def test_each_case_is_classified(store):
    from kazma_core.memory.provenance_relabel import _classify_tool_facts

    groups = _classify_tool_facts(store.db, store.ops, keys_of=_same_key)
    by_id = {item["id"]: (kind, item["tool"], item["why"]) for kind, items in groups.items() for item in items}
    name = {v: k for k, v in store.ids.items()}
    got = {name[i]: (kind, tool) for i, (kind, tool, _why) in by_id.items()}
    assert got == {
        "store_old": ("relabel", "memory_store"),
        "store_chat": ("relabel", "memory_store"),
        "link_tool": ("relabel", "memory_link_entities"),
        "asked": ("kept", "memory_store"),
        "edited": ("kept", "memory_link_entities"),
        "acted": ("kept", "memory_link_entities"),
        "failed_action": ("relabel", "memory_link_entities"),
        "page_link": ("unattributed", None),
        "backfill": ("unattributed", None),
        "beta_tool": ("relabel", "memory_link_entities"),
        "tool_functional": ("relabel", "memory_link_entities"),
    }
    whys = {name[i]: why for i, (_k, _t, why) in by_id.items()}
    assert whys["asked"] == "the turn asked to remember these words"
    assert whys["edited"] == "edited on the Memory page"
    assert whys["acted"] == "an operator action on the Memory page named it"


def test_size_names_ids_never_values(store):
    from kazma_core.memory.provenance_relabel import size_relabel

    sized = size_relabel(store.db, store.ops, keys_of=_same_key)
    assert sized["user_explicit"] == 11 and sized["relabel"] == 6 and sized["kept"] == 3
    assert sized["unattributed"] == 2
    assert sized["relabel_by_tool"] == {"memory_link_entities": 4, "memory_store": 2}
    assert sized["relabel_by_tenant"] == {"beta": 1, "default": 5}
    assert set(sized["examples"]["relabel"]) <= set(store.ids.values())
    text = json.dumps(sized)
    for value in ("Seagull", "rain", "4471", "bicycle", "postgres", "tea"):
        assert value not in text


def test_apply_relabels_journals_and_is_idempotent(store):
    from kazma_core.memory.provenance_relabel import apply_relabel, size_relabel

    out = apply_relabel(store.db, store.ops, keys_of=_same_key, now=20000.0)
    assert out["relabelled"] == 6 and out["kept"] == 3 and out["unattributed"] == 2
    for name in RELABELLED:
        assert _method(store.db, store.ids[name]) == "agent_asserted", name
        weight = store.db.execute("SELECT source_trust_weight FROM beliefs WHERE id = ?",
                                  (store.ids[name],)).fetchone()[0]
        assert weight == pytest.approx(0.65)
    for name in ("asked", "edited", "acted", "page_link", "backfill"):
        assert _method(store.db, store.ids[name]) == "user_explicit", name
    assert _method(store.db, store.ids["stated"]) == "user_stated"
    assert _method(store.db, store.ids["inferred"]) == "llm_inferred"
    rows = store.ops.execute(
        "SELECT tenant_id, target_id, actor, reason, state_before_json, state_after_json FROM memory_audit_log "
        "WHERE event_type = 'provenance_relabel'").fetchall()
    assert len(rows) == 6 and {r[1] for r in rows} == {store.ids[n] for n in RELABELLED}
    assert all(r[2] == "operator:memory_relabel_provenance" and "M03" in r[3] for r in rows)
    assert all(json.loads(r[5]) == {"extraction_method": "agent_asserted"} for r in rows)
    assert not any(v in json.dumps([tuple(r) for r in rows]) for v in ("Seagull", "rain", "kayak", "acme"))
    # Once more: nothing left to do.
    assert apply_relabel(store.db, store.ops, keys_of=_same_key)["relabelled"] == 0
    assert size_relabel(store.db, store.ops, keys_of=_same_key)["relabel"] == 0


def test_a_relabelled_tool_fact_no_longer_blocks_a_later_reading(store):
    """Negative control first: with the old label a tool's ``works_at acme``
    blocks the model's later reading of another turn; relabelled, the newer
    reading supersedes it."""
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.provenance_relabel import apply_relabel

    def later_reading(at):
        return mutate_belief(store.db, "user", "works_at", "globex", ops_conn=store.ops,
                             predicate_type="functional", confidence=0.8, extraction_method="llm_inferred",
                             source_session="t9", source_turn=1, now=at, private=True)

    blocked = later_reading(12000.0)
    assert blocked.get("blocked") == "lower_trust_source", blocked
    apply_relabel(store.db, store.ops, keys_of=_same_key)
    assert later_reading(12001.0)["action"] == "supersede"


def test_without_the_operators_trail_the_kept_facts_would_go(store, monkeypatch):
    """Negative control: the user-channel checks are what keep the edited and
    acted-on facts the user's."""
    from kazma_core.memory import provenance_relabel

    monkeypatch.setattr(provenance_relabel, "_operator_named", lambda ops: set())
    store.db.execute("UPDATE beliefs SET metadata_json = '{}' WHERE id = ?", (store.ids["edited"],))
    groups = provenance_relabel._classify_tool_facts(store.db, store.ops, keys_of=_same_key)
    relabel = {i["id"] for i in groups["relabel"]}
    assert store.ids["edited"] in relabel and store.ids["acted"] in relabel


def test_an_unreadable_trail_refuses(store):
    """No relabel on a guess: an operational database that cannot answer stops it."""
    from kazma_core.memory.provenance_relabel import apply_relabel

    store.ops.execute("DROP TABLE memory_audit_log")
    with pytest.raises(sqlite3.Error):
        apply_relabel(store.db, store.ops, keys_of=_same_key)
    assert _method(store.db, store.ids["store_chat"]) == "user_explicit"


def test_the_script_sizes_copies_first_and_applies(store, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location("memory_relabel_provenance",
                                                  REPO / "scripts" / "memory_relabel_provenance.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    path = str(store.dir / "memory_state.db")
    store.db.commit()
    store.ops.commit()
    assert script.main(["size", "--db", path], keys_of=_same_key) == 0
    sized = json.loads(capsys.readouterr().out)
    assert sized["relabel"] == 6 and SECRET not in json.dumps(sized)
    assert script.main(["apply", "--db", path], keys_of=_same_key) == 2  # needs --yes
    capsys.readouterr()
    assert script.main(["apply", "--db", path, "--yes"], keys_of=_same_key) == 0
    assert Path(path + ".pre_relabel").exists()
    with closing(sqlite3.connect(path + ".pre_relabel")) as before:
        assert before.execute("SELECT COUNT(*) FROM beliefs WHERE extraction_method = 'user_explicit'"
                              ).fetchone()[0] == 11
    assert _method(store.db, store.ids["store_chat"]) == "agent_asserted"
