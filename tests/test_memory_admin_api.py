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
