"""Which entities a delete, merge or purge may take: one rule, read from the hub.

``memory/entity_protection.py`` answers for every path (the Memory page's
Delete, Merge and Hygiene, the agent's memory tools, the automatic retire),
and every purge removes exactly what ``entity_retire.plain_empty_shells``
lists. Until 2026-10-03 there were five protected lists, four of them naming
the author of Kazma, and both purges deleted every entity with no live fact,
merge redirects included.

The install here belongs to Layla: her old person id ``Layla`` was merged
into the hub, and ``mubder`` is an empty word like any other.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

_ENTITIES = [
    # id, type, name, aliases, metadata, is_protected, is_major
    ("user", "person", "Layla", ["Layla", "user", "You", "layla"], {}, 0, 0),
    ("Layla", "concept", "Layla", ["Layla"], {"merged_into": "user"}, 0, 0),
    ("kazma", "concept", "kazma", ["kazma"], {}, 0, 0),
    ("acme", "concept", "acme", ["acme"], {}, 0, 0),
    ("acme_app", "concept", "acme app", ["acme app"], {"merged_into": "acme"}, 0, 0),
    ("mubder", "concept", "mubder", ["mubder"], {}, 0, 0),
    ("plain_one", "concept", "plain one", ["plain one"], {}, 0, 0),
    ("guarded", "concept", "guarded", ["guarded"], {}, 1, 0),
    ("big", "concept", "big", ["big"], {}, 0, 1),
    ("someone", "person", "someone", ["someone"], {}, 0, 0),
]


def _insert_entity(conn, eid, etype, name, aliases, meta, protected=0, major=0):
    conn.execute(
        "INSERT INTO entities (id, tenant_id, type, name, aliases_json, metadata_json, "
        "is_protected, is_major) VALUES (?, 'default', ?, ?, ?, ?, ?, ?)",
        (eid, etype, name, json.dumps(aliases), json.dumps(meta), protected, major),
    )


def _insert_fact(conn, bid, subject, predicate, obj):
    conn.execute(
        """INSERT INTO beliefs
           (id, tenant_id, subject, predicate, predicate_type, object, confidence,
            structural_importance, source_trust_weight, extraction_method, valid_from, ingested_at)
           VALUES (?, 'default', ?, ?, 'set', ?, 1.0, 3, 1.0, 'user_explicit', 1.0, 1.0)""",
        (bid, subject, predicate, obj),
    )


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    state, ops = tmp_path / "memory_state.db", tmp_path / "memory_ops.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(state))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(ops))
    conn = sqlite3.connect(state)
    ensure_primary_schema(conn)
    for row in _ENTITIES:
        _insert_entity(conn, *row)
    _insert_fact(conn, "b1", "user", "has_project", "acme")
    conn.commit()
    conn.close()
    o = sqlite3.connect(ops)
    ensure_ops_schema(o)
    o.close()
    return state


def _ids(db) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        return {r[0] for r in conn.execute("SELECT id FROM entities")}
    finally:
        conn.close()


def _memory_tools() -> dict:
    from kazma_core.agent.tool_builtins.memory import register_memory_tools

    tools: dict = {}

    class _Registry:
        def register(self, **_kw):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn

            return deco

    register_memory_tools(_Registry())
    return tools


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


# ── The rule ─────────────────────────────────────────────────────────────


def test_protection_reads_the_owner_from_the_hub(db):
    from kazma_core.memory.entity_protection import protection

    conn = sqlite3.connect(db)
    try:
        assert protection(conn, "user") == "core"
        assert protection(conn, "KAZMA") == "core"
        assert protection(conn, "Layla") == "self"
        assert protection(conn, "guarded") == "flag"
        assert protection(conn, "mubder") is None
        assert protection(conn, "plain_one") is None
    finally:
        conn.close()


def test_merge_refusal_lets_the_owners_shell_go_home(db):
    from kazma_core.memory.entity_protection import merge_refusal

    conn = sqlite3.connect(db)
    try:
        assert merge_refusal(conn, "Layla", "user") is None
        assert merge_refusal(conn, "Layla", "acme") == "self"
        assert merge_refusal(conn, "user", "acme") == "core"
        assert merge_refusal(conn, "guarded", "user") == "flag"
        assert merge_refusal(conn, "plain_one", "acme") is None
    finally:
        conn.close()


def test_plain_empty_shells_against_the_old_purge_rule(db):
    """Negative control: the purges' old rule -- no live fact, not one of four
    hardcoded ids -- takes the owner's redirect and the operator's choices."""
    from kazma_core.memory.entity_counts import belief_count_sql
    from kazma_core.memory.entity_retire import plain_empty_shells

    conn = sqlite3.connect(db)
    try:
        assert plain_empty_shells(conn) == [("default", "mubder"), ("default", "plain_one")]
        old = {
            r[0]
            for r in conn.execute(
                f"SELECT e.id FROM entities e WHERE {belief_count_sql()} = 0 "
                "AND LOWER(e.id) NOT IN ('user','assistant','kazma','mubder')"
            )
        }
    finally:
        conn.close()
    assert {"Layla", "acme_app", "big", "someone"} <= old


# ── Every purge takes what the rule lists ───────────────────────────────


def test_the_pages_purge_takes_only_plain_shells(db):
    from kazma_ui.memory_api import hygiene_preview, hygiene_run

    assert [e["id"] for e in hygiene_preview()["empty_entities"]] == ["mubder", "plain_one"]
    out = asyncio.run(hygiene_run(_Req({"purge_empty_entities": True})))
    assert out["actions"]["purge_empty_entities"] == {"deleted": ["mubder", "plain_one"], "count": 2}
    assert _ids(db) == {"user", "Layla", "kazma", "acme", "acme_app", "guarded", "big", "someone"}
    assert hygiene_preview()["empty_entities"] == []


def test_the_agents_purge_takes_only_plain_shells(db):
    tool = _memory_tools()["memory_purge_empty_entities"]
    dry = json.loads(asyncio.run(tool(confirm=False)))
    assert [e["id"] for e in dry["entities"]] == ["mubder", "plain_one"]
    done = json.loads(asyncio.run(tool(confirm=True)))
    assert done["deleted"] == ["mubder", "plain_one"]
    assert "Layla" in _ids(db) and "acme_app" in _ids(db)


def test_memory_health_counts_what_the_purge_takes(db):
    from kazma_core.memory.graph_hygiene import graph_hygiene_report

    conn = sqlite3.connect(db)
    try:
        assert graph_hygiene_report(conn)["empty"] == {"count": 2, "examples": ["mubder", "plain_one"]}
    finally:
        conn.close()


# ── Delete and merge ask the same rule ───────────────────────────────────


def test_delete_refuses_the_owners_own_names(db):
    from kazma_ui.memory_api import _delete_entity_sync

    assert _delete_entity_sync("Layla")["protected"] == "self"
    assert _delete_entity_sync("guarded")["protected"] == "flag"
    assert _delete_entity_sync("kazma")["protected"] == "core"
    assert _delete_entity_sync("mubder")["ok"] is True

    tool = _memory_tools()["memory_delete_entity"]
    assert "refusing" in asyncio.run(tool(entity_id="Layla"))
    assert json.loads(asyncio.run(tool(entity_id="someone")))["ok"] is True


def test_merge_refuses_the_owners_names_anywhere_but_the_hub(db):
    from kazma_ui.memory_api import _merge_entities_sync

    refused = _merge_entities_sync({"source_id": "Layla", "target_id": "acme"})
    assert refused["ok"] is False and refused["protected"] == "self"
    assert _merge_entities_sync({"source_id": "Layla", "target_id": "user"})["ok"] is True

    tool = _memory_tools()["memory_merge_entities"]
    assert "cannot merge protected source" in asyncio.run(tool(source_id="guarded", target_id="acme"))
    assert "cannot merge protected source" in asyncio.run(tool(source_id="kazma", target_id="acme"))


def test_the_entity_list_marks_the_owners_names_core(db):
    from kazma_ui.memory_api import _list_entities_sync

    by_id = {e["id"]: e for e in _list_entities_sync("", 100, 0, False, False)["entities"]}
    assert by_id["Layla"]["core"] is True and by_id["user"]["core"] is True
    assert by_id["mubder"]["core"] is False
    assert by_id["guarded"]["core"] is False and by_id["guarded"]["protected"] is True


def test_the_protect_route_keeps_the_owners_names_protected(db):
    from kazma_ui.memory_api import _protect_entity_sync

    assert "cannot unprotect" in _protect_entity_sync("Layla", {"protected": False})["error"]
    assert "cannot unprotect" in _protect_entity_sync("user", {"protected": False})["error"]
    assert _protect_entity_sync("guarded", {"protected": False})["ok"] is True


# ── The entity search finds an entity by its id ─────────────────────────


@pytest.fixture()
def search_db(db):
    conn = sqlite3.connect(db)
    _insert_entity(conn, "kazma_ai_admin", "concept", "kazma ai admin", ["kazma ai admin"], {})
    _insert_entity(conn, "grok_admin", "concept", "grok admin", ["grok admin"], {})
    _insert_entity(conn, "zzq_hidden", "concept", "Something else", ["Something else"], {})
    for i in range(3):
        _insert_fact(conn, f"g{i}", "grok_admin", "resets", f"day {i}")
    conn.commit()
    conn.close()
    return db


def test_the_entity_search_puts_the_named_id_first(search_db):
    from kazma_ui.memory_api import _list_entities_sync

    ids = [e["id"] for e in _list_entities_sync("kazma_ai_admin", 100, 0, False, False)["entities"]]
    assert ids[0] == "kazma_ai_admin"
    assert "grok_admin" in ids  # more facts: it led the list without the exact-first order


def test_the_entity_search_matches_an_id_the_index_has_no_word_of(search_db):
    from kazma_ui.memory_api import _list_entities_sync

    ids = [e["id"] for e in _list_entities_sync("zzq_hidden", 100, 0, False, False)["entities"]]
    assert ids == ["zzq_hidden"]


def test_the_old_search_token_matched_nothing(search_db):
    """Negative control: the id as one token, as the old builder made it."""
    from kazma_ui.memory_api import _fts_match_expr

    assert _fts_match_expr("kazma_ai_admin") == "kazma OR ai OR admin"
    conn = sqlite3.connect(search_db)
    try:
        hits = conn.execute("SELECT COUNT(*) FROM entities_fts WHERE entities_fts MATCH 'kazmaaiadmin'").fetchone()[0]
        assert hits == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM entities_fts WHERE entities_fts MATCH ?", (_fts_match_expr("kazma_ai_admin"),)
        ).fetchone()[0] >= 1
    finally:
        conn.close()


# ── The page's "empty" is what the purge takes ──────────────────────────


def test_the_stats_bar_counts_what_the_purge_takes(db):
    from kazma_ui.memory_api import _memory_admin_summary_sync

    assert _memory_admin_summary_sync()["entities_empty"] == 2


def test_a_redirect_is_listed_as_merged_not_empty(db):
    from kazma_ui.memory_api import _list_entities_sync

    by_id = {e["id"]: e for e in _list_entities_sync("", 100, 0, False, False)["entities"]}
    assert (by_id["acme_app"]["merged_into"], by_id["acme_app"]["empty"]) == ("acme", False)
    assert (by_id["Layla"]["merged_into"], by_id["Layla"]["empty"]) == ("user", False)
    assert (by_id["plain_one"]["merged_into"], by_id["plain_one"]["empty"]) == ("", True)

    empty_only = {e["id"] for e in _list_entities_sync("", 100, 0, True, False)["entities"]}
    assert "acme_app" not in empty_only and "Layla" not in empty_only
    assert {"mubder", "plain_one"} <= empty_only
