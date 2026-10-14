"""Memory input bounds and defense in depth (Memory audit M36, MEM-S12).

* one set of length limits, enforced by ``mutate_belief`` (the edit route
  capped 200/120/4000, every other writer nothing; group fields unbounded);
* a request-body size limit on every memory route;
* the entity search applies the tenant inside its full-text subquery (its
  2,000 rows were taken across every tenant first);
* ``/memory off`` in a chat app asks before it forgets.
The DOM half (attributes escaped) is ``tests/e2e/test_memory_hostile_type.py``.
"""

from __future__ import annotations

import sqlite3
import time
from types import SimpleNamespace

import pytest


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
    monkeypatch.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key] if key else [])
    monkeypatch.setattr("kazma_core.tenant_isolation.multi_user_or_production", lambda: False)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    dual_write._reset_mirror()
    yield SimpleNamespace(db=db, mp=monkeypatch)
    dual_write._reset_mirror()
    db.close()


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import memory_api
    from kazma_ui.routes_direct import register_direct_routes

    app = FastAPI()
    register_direct_routes(SimpleNamespace(app=app))
    memory_api.mount_memory_api(app)
    return TestClient(app)


# ── one set of limits ─────────────────────────────────────────────────────


@pytest.mark.parametrize(("field", "kw"), [
    ("subject", {"subject": "s" * 201, "predicate": "likes", "obj": "tea"}),
    ("predicate", {"subject": "user", "predicate": "p" * 121, "obj": "tea"}),
    ("object", {"subject": "user", "predicate": "likes", "obj": "o" * 4001}),
])
def test_the_one_writer_refuses_a_value_past_its_limit(mem, field, kw):
    from kazma_core.memory.belief_mutation import mutate_belief

    out = mutate_belief(mem.db, kw["subject"], kw["predicate"], kw["obj"], predicate_type="set", tenant_id="default")
    assert out["action"] == "noop" and out["rejected"] == f"{field}_too_long"
    assert mem.db.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0] == 0


def test_a_value_at_its_limit_is_written(mem):
    """Negative control: the limit itself is allowed, never cut."""
    from kazma_core.memory.belief_mutation import mutate_belief

    out = mutate_belief(mem.db, "user", "likes", "o" * 4000, predicate_type="set", tenant_id="default")
    assert out["action"] == "append"
    assert len(mem.db.execute("SELECT object FROM beliefs").fetchone()[0]) == 4000


def test_the_link_and_group_routes_keep_to_the_limits(mem):
    c = _client()
    r = c.post("/api/memory/v2/entities/link", json={"subject": "user", "object": "x" * 4001})
    assert r.status_code == 422 and r.json()["error"] == "field too long"
    r = c.post("/api/memory/v2/graph/groups", json={"group_root": "r", "member": "m", "label": "l" * 121})
    assert r.status_code == 422 and r.json()["error"] == "field too long"


# ── the body limit ────────────────────────────────────────────────────────


def test_a_body_past_the_limit_is_refused_unread(mem):
    from kazma_ui import memory_api

    c = _client()
    big = {"about": "x" * (memory_api._BODY_MAX_BYTES + 10)}
    assert c.put("/api/memory/v2/profile", json=big).status_code == 413
    # Undeclared length (a streamed body) is cut off as it passes the limit.

    def chunks():
        for _ in range(64):
            yield b"x" * 8192

    r = c.put("/api/memory/v2/profile", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413
    # Negative control: a body under the limit is read.
    assert c.put("/api/memory/v2/profile", json={"about": "I am Sara."}).status_code == 200


# ── the tenant inside the full-text subquery ──────────────────────────────


def test_a_tenant_finds_its_own_entity_past_other_tenants_matches(mem):
    from kazma_ui import memory_api

    mem.db.executemany("INSERT INTO entities (id, tenant_id, type, name) VALUES (?, 'beta', 'concept', ?)",
                       [(f"acme_{i}", f"acme {i}") for i in range(2100)])
    mem.db.execute("INSERT INTO entities (id, tenant_id, type, name) VALUES ('acme_alpha', 'alpha', 'concept', "
                   "'acme alpha')")
    mem.db.commit()
    mem.mp.setattr(memory_api, "_memory_tenant_id", lambda: "alpha")
    out = memory_api._list_entities_sync(q="acme", limit=50, offset=0, empty_only=False, isolated_only=False)
    assert [e["id"] for e in out["entities"]] == ["acme_alpha"]
    # Negative control: the subquery without the tenant took beta's 2,000 first.
    rows = mem.db.execute("SELECT e.rowid FROM entities_fts JOIN entities e ON e.rowid = entities_fts.rowid "
                          "WHERE entities_fts MATCH ? LIMIT 2000", ('"acme"*',)).fetchall()
    alpha = mem.db.execute("SELECT rowid FROM entities WHERE id = 'acme_alpha'").fetchone()[0]
    assert alpha not in {r[0] for r in rows}


# ── /memory off asks first ────────────────────────────────────────────────


def test_memory_off_in_a_chat_app_asks_before_it_forgets(mem):
    from kazma_core.memory import forget
    from kazma_core.memory.dual_write import mirror_episode
    from kazma_gateway.slash_commands import _cmd_memory

    mirror_episode(session_id="tg-chat-1", turn_number=1, user_text="remember my locker code is 4417",
                   assistant_text="Noted.")
    ctx = {"thread_id": "tg-chat-1", "memory_tenant": "default"}
    reply = _cmd_memory("/memory off", ctx)
    assert "/memory off confirm" in reply
    assert forget.chat_remembered("tg-chat-1", tenant_id="default")  # nothing changed
    assert mem.db.execute("SELECT tier FROM episodes").fetchone()[0] != "forgotten"
    reply = _cmd_memory("/memory off confirm", ctx)
    assert "won't remember" in reply
    assert not forget.chat_remembered("tg-chat-1", tenant_id="default")
    assert mem.db.execute("SELECT tier FROM episodes").fetchone()[0] == "forgotten"
