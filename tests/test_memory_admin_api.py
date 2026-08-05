"""Memory admin API — entities merge/link/hygiene (UI backend)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def mem_db(tmp_path, monkeypatch):
    state = tmp_path / "memory_state.db"
    ops = tmp_path / "memory_ops.db"
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    # paths re-read from env
    from kazma_core import paths as paths_mod

    monkeypatch.setattr(paths_mod, "primary_memory_db", lambda: str(state))
    monkeypatch.setattr(paths_mod, "memory_ops_db", lambda: str(ops))

    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    c = sqlite3.connect(state)
    ensure_primary_schema(c)
    c.execute(
        "INSERT INTO entities (id, tenant_id, type, name) VALUES ('shipx','default','concept','ShipX')"
    )
    c.execute(
        "INSERT INTO entities (id, tenant_id, type, name) VALUES ('shipx_old','default','concept','ShipX Old')"
    )
    c.execute(
        "INSERT INTO entities (id, tenant_id, type, name) VALUES ('lonely','default','concept','Lonely')"
    )
    c.execute(
        "INSERT INTO entities (id, tenant_id, type, name) VALUES ('empty_shell','default','concept','Empty')"
    )
    # lonely has a belief but only to a literal, not another entity
    c.execute(
        """INSERT INTO beliefs
           (id, tenant_id, subject, predicate, predicate_type, object,
            confidence, structural_importance, source_trust_weight, extraction_method,
            valid_from, ingested_at)
           VALUES ('b1','default','lonely','description','set','orphan node',
                   1.0, 3, 1.0, 'user_explicit', 1.0, 1.0)"""
    )
    c.commit()
    c.close()
    o = sqlite3.connect(ops)
    ensure_ops_schema(o)
    o.close()
    return state


@pytest.mark.asyncio
async def test_list_entities_flags(mem_db):
    from kazma_ui.memory_api import list_entities

    out = await list_entities(limit=50)
    assert out["ok"]
    by_id = {e["id"]: e for e in out["entities"]}
    assert by_id["empty_shell"]["empty"] is True
    assert by_id["lonely"]["isolated"] is True or by_id["lonely"]["belief_count"] >= 1


@pytest.mark.asyncio
async def test_merge_entities(mem_db):
    from kazma_ui.memory_api import merge_entities

    class Req:
        async def json(self):
            return {"source_id": "shipx_old", "target_id": "shipx"}

    out = await merge_entities(Req())
    assert out["ok"] is True
    assert out["target_id"] == "shipx"


@pytest.mark.asyncio
async def test_link_entities(mem_db):
    from kazma_ui.memory_api import link_entities

    class Req:
        async def json(self):
            return {
                "subject": "lonely",
                "predicate": "part_of",
                "object": "shipx",
            }

    out = await link_entities(Req())
    assert out["ok"] is True
    assert out.get("belief_id") or (out.get("link") or {}).get("belief_id")


@pytest.mark.asyncio
async def test_unlink_by_triple(mem_db):
    """Graph unlink must work via subject–predicate–object when id is known."""
    from kazma_ui.memory_api import link_entities, unlink_entities

    class LinkReq:
        async def json(self):
            return {
                "subject": "lonely",
                "predicate": "part_of",
                "object": "shipx",
            }

    linked = await link_entities(LinkReq())
    assert linked["ok"] is True
    bid = linked.get("belief_id") or (linked.get("link") or {}).get("belief_id")
    assert bid

    class UnlinkReq:
        async def json(self):
            return {
                "subject": "lonely",
                "predicate": "part_of",
                "object": "shipx",
            }

    out = await unlink_entities(UnlinkReq())
    assert out["ok"] is True
    assert out.get("belief_id") == bid

    # Idempotent second unlink
    out2 = await unlink_entities(UnlinkReq())
    # No active match after first unlink → ok False OR already via id
    assert out2.get("ok") is False or out2.get("already") is True


@pytest.mark.asyncio
async def test_unlink_by_belief_id(mem_db):
    from kazma_ui.memory_api import link_entities, unlink_entities

    class LinkReq:
        async def json(self):
            return {
                "subject": "lonely",
                "predicate": "near",
                "object": "shipx",
            }

    linked = await link_entities(LinkReq())
    bid = linked.get("belief_id") or (linked.get("link") or {}).get("belief_id")
    assert bid

    class UnlinkReq:
        async def json(self):
            return {"belief_id": bid}

    out = await unlink_entities(UnlinkReq())
    assert out["ok"] is True
    assert out.get("via") == "id"


@pytest.mark.asyncio
async def test_hygiene_purge_empty(mem_db):
    from kazma_ui.memory_api import hygiene_run

    class Req:
        async def json(self):
            return {"purge_empty_entities": True}

    out = await hygiene_run(Req())
    assert out["ok"]
    deleted = out["actions"]["purge_empty_entities"]["deleted"]
    assert "empty_shell" in deleted


@pytest.mark.asyncio
async def test_rename_entity_keeps_id_and_aliases(mem_db):
    """Display rename must not change canonical id; old name becomes alias."""
    from kazma_ui.memory_api import rename_entity

    class Req:
        async def json(self):
            return {"name": "ShipX Brand"}

    out = await rename_entity("shipx", Req())
    assert out["ok"] is True
    assert out["id"] == "shipx"
    assert out["name"] == "ShipX Brand"
    assert "ShipX" in (out.get("aliases") or [])

    # Persisted
    conn = sqlite3.connect(mem_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT name, aliases_json FROM entities WHERE id=?", ("shipx",)
    ).fetchone()
    conn.close()
    assert row["name"] == "ShipX Brand"
    aliases = json.loads(row["aliases_json"] or "[]")
    assert "ShipX" in aliases


@pytest.mark.asyncio
async def test_rename_user_hub_upsert(mem_db):
    """You/user hub can be labeled (Mubder) even when no entities row yet."""
    from kazma_ui.memory_api import rename_entity

    class Req:
        async def json(self):
            return {"name": "Mubder"}

    out = await rename_entity("user", Req())
    assert out["ok"] is True
    assert out["id"] == "user"
    assert out["name"] == "Mubder"
    assert out.get("created") is True
    aliases = out.get("aliases") or []
    assert "You" in aliases or "user" in aliases

    conn = sqlite3.connect(mem_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT type, name FROM entities WHERE id=?", ("user",)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["name"] == "Mubder"
    assert row["type"] == "person"


@pytest.mark.asyncio
async def test_edit_belief_object(mem_db):
    """Operator can fix an active belief triple in place."""
    import time as _time

    from kazma_ui.memory_api import edit_belief

    conn = sqlite3.connect(mem_db)
    now = _time.time()
    conn.execute(
        """INSERT INTO beliefs
           (id, tenant_id, subject, predicate, predicate_type, object,
            confidence, structural_importance, source_trust_weight, extraction_method,
            valid_from, ingested_at)
           VALUES ('edit_b1','default','lonely','description','set','wrong text',
                   1.0, 3, 1.0, 'llm_inferred', ?, ?)""",
        (now, now),
    )
    conn.commit()
    conn.close()

    class Req:
        async def json(self):
            return {
                "object": "corrected description",
                "predicate": "description",
                "subject": "lonely",
            }

    out = await edit_belief("edit_b1", Req())
    assert out["ok"] is True
    assert out["belief"]["object"] == "corrected description"
    assert out["object_changed"] is True

    conn = sqlite3.connect(mem_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT object, extraction_method FROM beliefs WHERE id=?", ("edit_b1",)
    ).fetchone()
    conn.close()
    assert row["object"] == "corrected description"
    assert row["extraction_method"] == "user_explicit"


@pytest.mark.asyncio
async def test_rename_person_user_shell_syncs_hub(mem_db):
    """ent_* person named User → Mubder must drive graph hub label + graph_id."""
    from kazma_ui.memory_api import list_entities, rename_entity

    conn = sqlite3.connect(mem_db)
    conn.execute(
        "INSERT INTO entities (id, tenant_id, type, name, aliases_json) "
        "VALUES ('ent_9ed7ffa178bef5770403c39a','default','person','User','[\"User\"]')"
    )
    conn.commit()
    conn.close()

    class Req:
        async def json(self):
            return {"name": "Mubder"}

    out = await rename_entity("ent_9ed7ffa178bef5770403c39a", Req())
    assert out["ok"] is True
    assert out["name"] == "Mubder"
    assert out.get("hub_synced") is True
    assert out.get("graph_id") == "user"

    conn = sqlite3.connect(mem_db)
    conn.row_factory = sqlite3.Row
    hub = conn.execute(
        "SELECT name, aliases_json FROM entities WHERE id='user'"
    ).fetchone()
    shell = conn.execute(
        "SELECT name, aliases_json FROM entities WHERE id=?",
        ("ent_9ed7ffa178bef5770403c39a",),
    ).fetchone()
    conn.close()
    assert hub is not None
    assert hub["name"] == "Mubder"
    assert shell["name"] == "Mubder"
    aliases = json.loads(shell["aliases_json"] or "[]")
    assert "User" in aliases  # keep self surface for is_self

    listed = await list_entities(limit=50)
    by_id = {e["id"]: e for e in listed["entities"]}
    ent = by_id["ent_9ed7ffa178bef5770403c39a"]
    assert ent["is_self"] is True
    assert ent["graph_id"] == "user"
    assert ent["name"] == "Mubder"


# ── Phase 1.1: pagination + total counts ─────────────────────────────────


@pytest.fixture
def mem_db_many(tmp_path, monkeypatch):
    """Seed 25 entities so pagination windows are non-trivial."""
    state = tmp_path / "memory_state.db"
    ops = tmp_path / "memory_ops.db"
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_core import paths as paths_mod

    monkeypatch.setattr(paths_mod, "primary_memory_db", lambda: str(state))
    monkeypatch.setattr(paths_mod, "memory_ops_db", lambda: str(ops))
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    c = sqlite3.connect(state)
    ensure_primary_schema(c)
    for i in range(25):
        c.execute(
            "INSERT INTO entities (id, tenant_id, type, name) VALUES (?,?,?,?)",
            (f"ent_{i:02d}", "default", "concept", f"Entity {i}"),
        )
    # Give a couple of entities active beliefs so belief_count ordering varies.
    for i, bid in enumerate(["b_a", "b_b", "b_c"]):
        c.execute(
            """INSERT INTO beliefs
               (id, tenant_id, subject, predicate, predicate_type, object,
                confidence, structural_importance, source_trust_weight,
                extraction_method, valid_from, ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (bid, "default", f"ent_{i:02d}", "related_to", "set", "ent_24",
             1.0, 1, 1.0, "user_explicit", 1.0, 1.0),
        )
    c.commit()
    c.close()
    o = sqlite3.connect(ops)
    ensure_ops_schema(o)
    o.close()
    return state


@pytest.mark.asyncio
async def test_list_entities_pagination_total(mem_db_many):
    """list_entities returns total + offset + limit and respects the window."""
    from kazma_ui.memory_api import list_entities

    page1 = await list_entities(limit=10, offset=0)
    assert page1["ok"]
    assert page1["count"] == 10
    assert page1["total"] >= 25, f"expected total>=25, got {page1['total']}"
    assert page1["offset"] == 0
    assert page1["limit"] == 10

    page2 = await list_entities(limit=10, offset=10)
    assert page2["ok"]
    assert page2["count"] == 10
    assert page2["offset"] == 10
    # No overlap between windows.
    ids1 = {e["id"] for e in page1["entities"]}
    ids2 = {e["id"] for e in page2["entities"]}
    assert not (ids1 & ids2), "pages overlap"

    # Final partial window.
    page3 = await list_entities(limit=10, offset=20)
    assert page3["count"] >= 5
    assert page3["total"] == page1["total"]  # stable across pages


@pytest.mark.asyncio
async def test_list_entities_offset_clamped(mem_db_many):
    """Negative offset is clamped to 0 (doesn't error or duplicate)."""
    from kazma_ui.memory_api import list_entities

    out = await list_entities(limit=5, offset=-3)
    assert out["ok"]
    assert out["offset"] == 0
    assert out["count"] == 5


# ── Phase 1.2: action receipts + undo ─────────────────────────────────────


@pytest.mark.asyncio
async def test_invalidate_batch_undo_restores(mem_db_many):
    """Invalidating beliefs then undo re-activates them."""
    import sqlite3

    from kazma_core import paths as paths_mod
    from kazma_ui.memory_api import invalidate_batch, undo_action

    class Req:
        async def json(self):
            return {"ids": ["b_a", "b_b"]}

    out = await invalidate_batch(Req())
    assert out["ok"]
    assert out["invalidated"] == 2
    assert out["undo_token"], "undo_token must be returned"

    # Confirm they are invalidated.
    conn = sqlite3.connect(paths_mod.primary_memory_db())
    active = conn.execute(
        "SELECT COUNT(*) FROM beliefs WHERE id IN ('b_a','b_b') "
        "AND valid_until IS NULL AND invalidated_at IS NULL"
    ).fetchone()[0]
    conn.close()
    assert active == 0

    # Undo.
    restored = await undo_action(out["undo_token"])
    assert restored["ok"], f"undo failed: {restored}"

    conn = sqlite3.connect(paths_mod.primary_memory_db())
    active = conn.execute(
        "SELECT COUNT(*) FROM beliefs WHERE id IN ('b_a','b_b') "
        "AND valid_until IS NULL AND invalidated_at IS NULL"
    ).fetchone()[0]
    conn.close()
    assert active == 2, f"expected 2 active after undo, got {active}"


@pytest.mark.asyncio
async def test_link_undo_invalidates(mem_db_many):
    """Linking two entities then undo removes the created belief."""
    import sqlite3

    from kazma_core import paths as paths_mod
    from kazma_ui.memory_api import link_entities, undo_action

    class Req:
        async def json(self):
            return {"subject": "ent_05", "predicate": "related_to", "object": "ent_06"}

    out = await link_entities(Req())
    assert out["ok"]
    bid = out.get("belief_id")
    assert bid, "link must return a belief_id"
    assert out["undo_token"], "link must return an undo_token"

    # Belief is active.
    conn = sqlite3.connect(paths_mod.primary_memory_db())
    active = conn.execute(
        "SELECT COUNT(*) FROM beliefs WHERE id=? AND valid_until IS NULL", (bid,)
    ).fetchone()[0]
    conn.close()
    assert active == 1

    restored = await undo_action(out["undo_token"])
    assert restored["ok"], f"undo failed: {restored}"

    # Undo invalidated the created belief.
    conn = sqlite3.connect(paths_mod.primary_memory_db())
    active = conn.execute(
        "SELECT COUNT(*) FROM beliefs WHERE id=? AND valid_until IS NULL", (bid,)
    ).fetchone()[0]
    conn.close()
    assert active == 0, "belief should be invalidated after undo"


@pytest.mark.asyncio
async def test_undo_token_single_use_and_expiry(mem_db_many):
    """An undo token is single-use and rejects a second redemption."""
    from kazma_ui.memory_api import invalidate_batch, undo_action

    class Req:
        async def json(self):
            return {"ids": ["b_c"]}

    out = await invalidate_batch(Req())
    token = out["undo_token"]
    assert token

    first = await undo_action(token)
    assert first["ok"]

    # Second use → not found (consumed).
    second = await undo_action(token)
    assert not second["ok"]
    assert "expired" in second.get("error", "").lower() or "not found" in second.get("error", "").lower()


@pytest.mark.asyncio
async def test_undo_expired_token_rejected(mem_db_many):
    """An expired token (past TTL) is rejected."""
    import time as _time

    from kazma_ui import memory_api
    from kazma_ui.memory_api import invalidate_batch, undo_action

    class Req:
        async def json(self):
            return {"ids": ["b_a"]}

    out = await invalidate_batch(Req())
    token = out["undo_token"]
    # Fast-forward the stored expiry into the past.
    memory_api._undo_store[token]["expires_at"] = _time.time() - 1
    res = await undo_action(token)
    assert not res["ok"]
