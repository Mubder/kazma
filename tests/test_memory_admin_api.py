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


# ── Phase 2.1: FTS5 search in the operator UI ────────────────────────────


@pytest.fixture
def mem_db_fts(tmp_path, monkeypatch):
    """Seed entities + beliefs with aliases and diacritic-laden text."""
    state = tmp_path / "memory_state.db"
    ops = tmp_path / "memory_ops.db"
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_core import paths as paths_mod

    monkeypatch.setattr(paths_mod, "primary_memory_db", lambda: str(state))
    monkeypatch.setattr(paths_mod, "memory_ops_db", lambda: str(ops))
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    c = sqlite3.connect(state)
    ensure_primary_schema(c)
    # Entity with an alias; diacritic-rich name.
    c.execute(
        "INSERT INTO entities (id, tenant_id, type, name, aliases_json) "
        "VALUES ('mubder','default','person','Mubder','[\"Mubdir, Mupder\"]')"
    )
    c.execute(
        "INSERT INTO entities (id, tenant_id, type, name, aliases_json) "
        "VALUES ('shipx','default','project','ShipX','[]')"
    )
    # A belief with a diacritic in the object text.
    c.execute(
        """INSERT INTO beliefs
           (id, tenant_id, subject, predicate, predicate_type, object,
            confidence, structural_importance, source_trust_weight, extraction_method,
            valid_from, ingested_at)
           VALUES ('bf1','default','mubder','speaks','set','Arabic Français',
                   1.0, 2, 1.0, 'user_explicit', 1.0, 1.0)"""
    )
    c.commit()
    c.close()
    o = sqlite3.connect(ops)
    ensure_ops_schema(o)
    o.close()
    return state


@pytest.mark.asyncio
async def test_entity_search_matches_alias(mem_db_fts):
    """Entity search via FTS matches a name stored only in aliases_json."""
    from kazma_ui.memory_api import list_entities

    # "mubdir" is in aliases_json, not the name. FTS unicode61 tokenizes the
    # JSON array string so the alias is searchable.
    out = await list_entities(q="mubdir", limit=20)
    assert out["ok"]
    ids = {e["id"] for e in out["entities"]}
    assert "mubder" in ids, f"alias search missed mubder; got {ids}"


@pytest.mark.asyncio
async def test_entity_search_no_match_returns_empty(mem_db_fts):
    """A query that matches nothing returns total=0 (not a LIKE false-positive)."""
    from kazma_ui.memory_api import list_entities

    out = await list_entities(q="zzzznomatch", limit=20)
    assert out["ok"]
    assert out["total"] == 0
    assert out["entities"] == []


def test_belief_search_fts_diacritic_insensitive(tmp_path, monkeypatch):
    """Belief search finds diacritic-laden object text via beliefs_fts.

    Uses the TestClient route so the FTS path in routes_direct is exercised.
    """
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.paths import memory_ops_db, primary_memory_db

    primary = sqlite3.connect(primary_memory_db())
    primary.row_factory = sqlite3.Row
    ensure_primary_schema(primary)
    ops = sqlite3.connect(memory_ops_db())
    ensure_ops_schema(ops)
    primary.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, "
        "confidence, structural_importance, valid_from, ingested_at) "
        "VALUES ('b1','default','user','speaks','set','Français',1.0,2,1.0,1.0)"
    )
    primary.commit()
    primary.close()
    ops.close()

    from fastapi.testclient import TestClient
    from kazma_ui.app import create_app

    client = TestClient(create_app())
    # 'francais' (no cedilla) should match 'Français' under unicode61
    # remove_diacritics.
    resp = client.get("/api/memory/v2/beliefs", params={"q": "francais"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["matched_via"] == "fts"
    ids = [b["id"] for b in payload["beliefs"]]
    assert "b1" in ids, f"diacritic-insensitive FTS missed b1; got {ids}"


def test_belief_search_falls_back_to_like_on_fts_error(tmp_path, monkeypatch):
    """If FTS raises, the beliefs endpoint falls back to LIKE and still returns."""
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.paths import memory_ops_db, primary_memory_db

    primary = sqlite3.connect(primary_memory_db())
    primary.row_factory = sqlite3.Row
    ensure_primary_schema(primary)
    ops = sqlite3.connect(memory_ops_db())
    ensure_ops_schema(ops)
    primary.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, "
        "confidence, structural_importance, valid_from, ingested_at) "
        "VALUES ('b9','default','shipx','has','set','phase1',1.0,2,1.0,1.0)"
    )
    primary.commit()
    primary.close()
    ops.close()

    from fastapi.testclient import TestClient
    from kazma_ui.app import create_app

    client = TestClient(create_app())
    # A single-char token ('p') has no usable FTS token (len<2) → LIKE path.
    resp = client.get("/api/memory/v2/beliefs", params={"q": "p"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["matched_via"] == "like"
    assert any(b["id"] == "b9" for b in payload["beliefs"])


# ── F3: per-entity protection flag + orphan warnings ─────────────────────


@pytest.mark.asyncio
async def test_protect_flag_blocks_delete(mem_db):
    """An entity marked is_protected=1 cannot be deleted."""
    from kazma_ui.memory_api import delete_entity, protect_entity

    class Req:
        async def json(self):
            return {"protected": True}

    out = await protect_entity("shipx", Req())
    assert out["ok"] is True
    assert out["protected"] is True

    out2 = await delete_entity("shipx")
    assert out2["ok"] is False
    assert "protected" in str(out2.get("error", "")).lower()


@pytest.mark.asyncio
async def test_protect_flag_blocks_merge_source(mem_db):
    """A protected entity cannot be used as a merge source."""
    from kazma_ui.memory_api import merge_entities, protect_entity

    class ProtReq:
        async def json(self):
            return {"protected": True}

    await protect_entity("shipx_old", ProtReq())

    class MergeReq:
        async def json(self):
            return {"source_id": "shipx_old", "target_id": "shipx"}

    out = await merge_entities(MergeReq())
    assert out["ok"] is False
    assert "protected" in str(out.get("error", "")).lower()


@pytest.mark.asyncio
async def test_protect_toggle_unprotects_non_core(mem_db):
    """A non-core entity can be protected then unprotected; core entities cannot."""
    from kazma_ui.memory_api import protect_entity

    class On:
        async def json(self):
            return {"protected": True}

    class Off:
        async def json(self):
            return {"protected": False}

    # Non-core toggles both ways.
    assert (await protect_entity("shipx", On()))["ok"] is True
    assert (await protect_entity("shipx", Off()))["ok"] is True
    # Core entity (kazma) cannot be unprotected via this route.
    res = await protect_entity("kazma", Off())
    assert res["ok"] is False
    assert "core" in str(res.get("error", "")).lower()


@pytest.mark.asyncio
async def test_invalidate_batch_warns_on_orphan(mem_db):
    """Invalidating the only belief of an entity surfaces warn_orphaned.

    The `lonely` entity has exactly one belief (b1). Invalidating b1 leaves
    `lonely` with zero live edges → warn_orphaned must include it.
    """
    from kazma_ui.memory_api import invalidate_batch

    class Req:
        async def json(self):
            return {"ids": ["b1"]}

    out = await invalidate_batch(Req())
    assert out["ok"] is True
    assert out["invalidated"] == 1
    assert "warn_orphaned" in out
    # `lonely` is the subject of b1 and has no other beliefs → orphaned.
    assert "lonely" in out["warn_orphaned"]


@pytest.mark.asyncio
async def test_invalidate_batch_no_warn_when_other_edges_remain(mem_db):
    """If an entity keeps other live edges, it is NOT in warn_orphaned."""
    from kazma_ui.memory_api import invalidate_batch, link_entities

    # Give `lonely` a second belief so invalidating b1 doesn't strand it.
    class LinkReq:
        async def json(self):
            return {"subject": "lonely", "predicate": "related_to", "object": "shipx"}

    await link_entities(LinkReq())

    class Req:
        async def json(self):
            return {"ids": ["b1"]}

    out = await invalidate_batch(Req())
    assert out["ok"] is True
    # `lonely` still has the related_to belief → not orphaned.
    assert "lonely" not in out.get("warn_orphaned", [])


# ── F1: repoint (move) a belief endpoint ──────────────────────────────────


@pytest.mark.asyncio
async def test_repoint_moves_subject_in_place(mem_db):
    """POST /beliefs/{id}/repoint moves the subject to a new entity, keeps
    the predicate, recomputes counts, and is undoable.
    """
    from kazma_ui.memory_api import repoint_belief

    # The fixture's belief b1 is: lonely --description--> 'orphan node'.
    # Move its subject to shipx_old.
    class Req:
        async def json(self):
            return {"subject": "shipx_old"}

    out = await repoint_belief("b1", Req())
    assert out["ok"] is True
    assert out.get("op") == "repoint"
    # An undo token should be present (delegated from edit_belief).
    assert out.get("undo_token"), "repoint should be undoable"

    # Verify the belief row actually moved in the DB.
    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db())
    c.row_factory = sqlite3.Row
    row = c.execute(
        "SELECT subject, predicate, object FROM beliefs WHERE id='b1'"
    ).fetchone()
    c.close()
    assert row["subject"] == "shipx_old", f"subject not moved: {row['subject']}"
    assert row["predicate"] == "description", "predicate must be preserved"
    assert row["object"] == "orphan node", "object must be unchanged"


@pytest.mark.asyncio
async def test_repoint_rejects_predicate_change(mem_db):
    """repoint is endpoint-only; passing predicate is rejected."""
    from kazma_ui.memory_api import repoint_belief

    class Req:
        async def json(self):
            return {"subject": "shipx_old", "predicate": "renamed"}

    out = await repoint_belief("b1", Req())
    assert out["ok"] is False
    assert "endpoint-only" in str(out.get("error", "")).lower()


@pytest.mark.asyncio
async def test_repoint_requires_an_endpoint(mem_db):
    """repoint with neither subject nor object is rejected."""
    from kazma_ui.memory_api import repoint_belief

    class Req:
        async def json(self):
            return {}

    out = await repoint_belief("b1", Req())
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_repoint_undo_restores(mem_db):
    """Undoing a repoint restores the original subject."""
    from kazma_ui.memory_api import repoint_belief, undo_action

    class Req:
        async def json(self):
            return {"subject": "shipx_old"}

    out = await repoint_belief("b1", Req())
    token = out.get("undo_token")
    assert token

    restored = await undo_action(token)
    assert restored.get("ok") is True

    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db())
    c.row_factory = sqlite3.Row
    row = c.execute("SELECT subject FROM beliefs WHERE id='b1'").fetchone()
    c.close()
    assert row["subject"] == "lonely", "undo did not restore original subject"


# ── Link from a virtual-fact node: edge attaches to the clicked node ──────


@pytest.mark.asyncio
async def test_link_from_virtual_fact_node_attaches_verbatim(mem_db):
    """Linking FROM a virtual-fact node id (raw text that's a belief object,
    not an entity slug) must attach the edge to THAT node verbatim — not mint
    a slug-divergent entity. Reproduces the operator's "ShipX — Deployment
    Modes" bug: the link succeeded but connected to a different (slug) entity.
    """
    from kazma_ui.memory_api import link_entities
    from kazma_core.paths import primary_memory_db

    # 'lonely' has belief b1 with object='orphan node' (a literal/virtual
    # fact). 'orphan node' is therefore a virtual-fact node id on the canvas.
    virtual_id = "orphan node"

    class Req:
        async def json(self):
            return {"subject": virtual_id, "predicate": "related_to", "object": "shipx"}

    out = await link_entities(Req())
    assert out["ok"] is True, out

    # The new belief's subject MUST be the virtual id verbatim.
    c = sqlite3.connect(primary_memory_db())
    c.row_factory = sqlite3.Row
    row = c.execute(
        "SELECT subject FROM beliefs WHERE subject=? AND object='shipx' "
        "AND invalidated_at IS NULL AND valid_until IS NULL",
        (virtual_id,),
    ).fetchone()
    c.close()
    assert row is not None, (
        "edge did not attach to the virtual-fact node verbatim — the bug"
    )
    assert row["subject"] == virtual_id


@pytest.mark.asyncio
async def test_link_from_new_free_text_still_slugifies(mem_db):
    """Genuinely new free-text subjects (not an existing node) still slugify,
    so we don't create entities with arbitrary long raw-text ids. Back-compat.
    """
    from kazma_ui.memory_api import link_entities
    from kazma_core.paths import primary_memory_db

    free_text = "Brand New Concept Never Seen Before"

    class Req:
        async def json(self):
            return {"subject": free_text, "predicate": "related_to", "object": "shipx"}

    out = await link_entities(Req())
    assert out["ok"] is True

    c = sqlite3.connect(primary_memory_db())
    c.row_factory = sqlite3.Row
    # No belief should store the raw free text as subject.
    raw = c.execute(
        "SELECT subject FROM beliefs WHERE subject=?", (free_text,)
    ).fetchone()
    # The slug form should exist.
    slug = c.execute(
        "SELECT subject FROM beliefs WHERE subject='brand_new_concept_never_seen_before'"
    ).fetchone()
    c.close()
    assert raw is None, "raw free-text stored as subject (should have been slugified)"
    assert slug is not None, "slug form not created"


# ── F4: vocab route (chip source) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_vocab_returns_predicates_by_frequency(mem_db):
    """/vocab returns predicates frequency-sorted + entities count-sorted.

    The mem_db fixture seeds belief b1 (lonely → description). After creating
    two more beliefs with a repeated predicate, that predicate must rank first.
    """
    from kazma_ui.memory_api import link_entities, memory_vocab

    # Create two `related_to` links so `related_to` (count 2) outranks
    # `description` (count 1) in the frequency-sorted predicate list.
    for obj in ("shipx", "kazma"):
        class Req:
            async def json(self):
                return {"subject": "lonely", "predicate": "related_to", "object": obj}

        await link_entities(Req())

    out = await memory_vocab()
    assert out["ok"] is True
    preds = out["predicates"]
    assert isinstance(preds, list) and preds
    # Frequency-sorted: related_to (2) before description (1).
    names = [p["name"] for p in preds]
    assert "related_to" in names and "description" in names
    assert names.index("related_to") < names.index("description")
    # Each predicate carries its type + count.
    rt = next(p for p in preds if p["name"] == "related_to")
    assert rt["cnt"] >= 2 and rt["type"] in ("set", "state", "functional")
    # Entities are count-sorted and include the seeded ones.
    ents = out["entities"]
    ent_ids = [e["id"] for e in ents]
    assert "shipx" in ent_ids and "lonely" in ent_ids


@pytest.mark.asyncio
async def test_vocab_excludes_invalidated_beliefs(mem_db):
    """Soft-deleted beliefs must not count toward predicate frequency."""
    from kazma_ui.memory_api import invalidate_batch, link_entities, memory_vocab

    class LinkReq:
        async def json(self):
            return {"subject": "lonely", "predicate": "stale_pred", "object": "shipx"}

    await link_entities(LinkReq())
    # Invalidate it — the predicate should NOT appear in vocab.
    class InvReq:
        async def json(self):
            return {"ids": ["b1"]}  # b1 is `description`, not stale_pred

    # Create a stale_pred belief then invalidate it via its own id would need
    # a lookup; simpler: invalidate b1 (description) and confirm `description`
    # disappears from vocab while `related_to`/`stale_pred` remain.
    await invalidate_batch(InvReq())
    out = await memory_vocab()
    names = [p["name"] for p in out["predicates"]]
    assert "description" not in names, "invalidated belief's predicate leaked into vocab"
