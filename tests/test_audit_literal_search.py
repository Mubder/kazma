"""The audited search fallbacks treat a percent sign as text, within tenant scope."""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest


def test_symbol_search_preserves_literal_percent_and_underscore():
    from kazma_core.code_index.store import search_symbols
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE symbols (path, name, kind, line, signature)")
        db.executemany("INSERT INTO symbols VALUES ('p', ?, 'function', 1, '')",
                       [("foo_bar",), ("fooXbar",), ("ratio%value",), ("ordinary",)])
        assert [r["name"] for r in search_symbols(db, "_")] == ["foo_bar"]
        assert [r["name"] for r in search_symbols(db, "%")] == ["ratio%value"]


@pytest.mark.asyncio
async def test_hub_query_and_tags_are_literal(tmp_path):
    from kazma_core.hub.manifest_schema import SkillManifest
    from kazma_core.hub.registry import KazmaHub
    hub = KazmaHub(registry_path=str(tmp_path / "hub.db"))
    try:
        for name, desc, tags in [("one", "uses 50% confidence", ["value%"]), ("two", "plain text", ["valueX"])]:
            await hub.register(SkillManifest.from_dict({
                "name": name, "description": desc, "author": "operator", "version": "1.0",
                "license": "MIT", "tags": tags,
            }))
        assert [m.data["name"] for m in await hub.search(query="%")] == ["one"]
        assert [m.data["name"] for m in await hub.search(tags=["value%"])] == ["one"]
    finally:
        await hub.close()


@pytest.mark.asyncio
async def test_memory_route_and_tools_percent_query_stay_literal_and_tenant_scoped(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from kazma_core.agent.tool_builtins.memory import register_memory_tools
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_ui.routes_direct import memory
    path = tmp_path / "memory_state.db"
    monkeypatch.setattr("kazma_core.paths.primary_memory_db", lambda: str(path))
    monkeypatch.setattr("kazma_core.safety.hitl.get_current_tenant_id", lambda: "alpha")
    monkeypatch.setattr(memory, "_mem_tid", lambda: "alpha")
    with sqlite3.connect(path) as db:
        ensure_primary_schema(db)
        for row_id, tenant, text in [("a", "alpha", "50% confidence"), ("b", "alpha", "plain"), ("c", "beta", "100% confidence")]:
            db.execute("""INSERT INTO beliefs
                (id,tenant_id,subject,predicate,predicate_type,object,confidence,structural_importance,source_trust_weight,valid_from,ingested_at,extraction_method)
                VALUES (?,?,'user','prefers','set',?,1,2,1,1,1,'user_explicit')""", (row_id, tenant, text))
            db.execute("INSERT INTO entities(id,tenant_id,type,name) VALUES (?,?,'concept',?)", (row_id, tenant, text))
    # Percent has no FTS token, exercising the real LIKE fallback.
    app = FastAPI()
    memory.register_memory_routes(SimpleNamespace(app=app))
    route = next(r for r in app.routes if r.path == "/api/memory/v2/beliefs")
    result = route.endpoint(q="%", limit=50, offset=0)
    assert result["total"] == 1
    assert [b["id"] for b in result["beliefs"]] == ["a"]
    class Registry:
        funcs = {}
        def register(self, **kwargs):
            def capture(fn):
                self.funcs[fn.__name__] = fn
                return fn
            return capture
    registry = Registry()
    register_memory_tools(registry)
    beliefs = json.loads(await registry.funcs["memory_list_beliefs"](q="%"))
    assert [b["id"] for b in beliefs["beliefs"]] == ["a"]
    entities = json.loads(await registry.funcs["memory_admin"](action="list_entities", q="%"))
    assert [e["id"] for e in entities["entities"]] == ["a"]
