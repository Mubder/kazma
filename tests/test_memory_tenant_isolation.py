"""Phase 4 — multi-tenant correctness for the memory operator UI.

With ``KAZMA_MEMORY_ENFORCE_TENANT=1``, memory reads/writes scope by the
request-scoped tenant (set via ``set_current_tenant_id`` by the auth
middleware). Tenant A must not see or mutate tenant B's beliefs/entities.
With the flag off (default), every call returns ``"default"`` so existing
single-tenant deployments behave exactly as before.
"""

from __future__ import annotations

import sqlite3
import time

import pytest


@pytest.fixture
def two_tenant_db(tmp_path, monkeypatch):
    """Seed one DB with two tenants (alpha + beta) and distinct entities."""
    state = tmp_path / "memory_state.db"
    ops = tmp_path / "memory_ops.db"
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    # Enforcement ON for these tests.
    monkeypatch.setenv("KAZMA_MEMORY_ENFORCE_TENANT", "1")
    from kazma_core import paths as paths_mod

    monkeypatch.setattr(paths_mod, "primary_memory_db", lambda: str(state))
    monkeypatch.setattr(paths_mod, "memory_ops_db", lambda: str(ops))
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    c = sqlite3.connect(state)
    c.row_factory = sqlite3.Row
    ensure_primary_schema(c)
    now = time.time()
    # alpha tenant: alice + a belief
    c.execute("INSERT INTO entities (id, tenant_id, type, name) VALUES ('alice','alpha','person','Alice')")
    c.execute(
        "INSERT INTO beliefs (id,tenant_id,subject,predicate,predicate_type,object,confidence,"
        "structural_importance,valid_from,ingested_at) "
        "VALUES ('b_alpha','alpha','alice','lives_in','functional','Paris',0.9,3,?,?)",
        (now, now),
    )
    # beta tenant: bob + a belief. (entity id is globally unique — PK — so
    # each tenant uses distinct ids; the tenant_id column scopes ownership.)
    c.execute("INSERT INTO entities (id, tenant_id, type, name) VALUES ('bob','beta','person','Bob')")
    c.execute(
        "INSERT INTO beliefs (id,tenant_id,subject,predicate,predicate_type,object,confidence,"
        "structural_importance,valid_from,ingested_at) "
        "VALUES ('b_beta','beta','bob','lives_in','functional','Berlin',0.9,3,?,?)",
        (now, now),
    )
    c.commit()
    c.close()
    o = sqlite3.connect(ops)
    ensure_ops_schema(o)
    o.close()
    return state


def _set_tenant(tid: str) -> None:
    from kazma_core.tenant_context import set_current_tenant_id

    set_current_tenant_id(tid)


def _clear_tenant() -> None:
    from kazma_core.tenant_context import set_current_tenant_id

    set_current_tenant_id("default")


# ── Read isolation ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_entities_scoped_to_tenant(two_tenant_db):
    """Tenant alpha sees only alpha entities; beta sees only beta's."""
    from kazma_ui.memory_api import list_entities

    _set_tenant("alpha")
    out_a = await list_entities(limit=50)
    ids_a = {e["id"] for e in out_a["entities"]}
    assert "alice" in ids_a, "alpha should see its alice"
    assert "bob" not in ids_a, "alpha must NOT see beta's bob"

    _set_tenant("beta")
    out_b = await list_entities(limit=50)
    ids_b = {e["id"] for e in out_b["entities"]}
    assert "bob" in ids_b, "beta should see bob"
    assert "alice" not in ids_b, "beta must NOT see alpha's alice"
    _clear_tenant()


@pytest.mark.asyncio
async def test_summary_scoped_to_tenant(two_tenant_db):
    """admin/summary counts are per-tenant when the flag is on."""
    from kazma_ui.memory_api import memory_admin_summary

    _set_tenant("alpha")
    sa = await memory_admin_summary()
    assert sa["ok"]
    # alpha has 1 live belief + 2 entities (alice + Paris-virtual isn't an entity row;
    # only 'alice' was seeded as an entity). At minimum, alpha's live count is small.
    assert sa["beliefs_live"] == 1, f"alpha expected 1 live belief, got {sa['beliefs_live']}"

    _set_tenant("beta")
    sb = await memory_admin_summary()
    assert sb["beliefs_live"] == 1, f"beta expected 1 live belief, got {sb['beliefs_live']}"
    _clear_tenant()


# ── Write isolation ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_link_writes_to_active_tenant(two_tenant_db):
    """A link created under tenant alpha lands in alpha, not beta."""
    import sqlite3
    from kazma_core import paths as paths_mod
    from kazma_ui.memory_api import link_entities

    class Req:
        async def json(self):
            return {"subject": "alice", "predicate": "knows", "object": "carol"}

    _set_tenant("alpha")
    out = await link_entities(Req())
    assert out["ok"], f"link failed: {out}"
    _clear_tenant()

    # Verify the belief row is tenant=alpha, not beta or default.
    conn = sqlite3.connect(paths_mod.primary_memory_db())
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT tenant_id FROM beliefs WHERE subject='alice' AND predicate='knows'"
    ).fetchone()
    conn.close()
    assert row is not None, "link belief not found"
    assert row["tenant_id"] == "alpha", f"link landed in tenant {row['tenant_id']}, expected alpha"


# ── Flag-off default behavior (regression guard) ─────────────────────────


@pytest.mark.asyncio
async def test_flag_off_returns_default_everything(tmp_path, monkeypatch):
    """With the flag unset, behavior is identical to pre-Phase-4 (default tenant)."""
    state = tmp_path / "memory_state.db"
    ops = tmp_path / "memory_ops.db"
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    # Flag NOT set.
    monkeypatch.delenv("KAZMA_MEMORY_ENFORCE_TENANT", raising=False)
    from kazma_core import paths as paths_mod

    monkeypatch.setattr(paths_mod, "primary_memory_db", lambda: str(state))
    monkeypatch.setattr(paths_mod, "memory_ops_db", lambda: str(ops))
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    c = sqlite3.connect(state)
    ensure_primary_schema(c)
    c.execute("INSERT INTO entities (id, tenant_id, type, name) VALUES ('x','default','concept','X')")
    c.execute(
        "INSERT INTO beliefs (id,tenant_id,subject,predicate,predicate_type,object,confidence,"
        "valid_from,ingested_at) VALUES ('bx','default','x','is','set','real',0.9,1.0,1.0)"
    )
    c.commit()
    c.close()
    o = sqlite3.connect(ops)
    ensure_ops_schema(o)
    o.close()

    # Even if a stray tenant context is set, the flag is off → default.
    from kazma_core.tenant_context import set_current_tenant_id
    set_current_tenant_id("stray-tenant")

    from kazma_ui.memory_api import list_entities, memory_admin_summary

    out = await list_entities(limit=50)
    assert out["ok"]
    ids = {e["id"] for e in out["entities"]}
    assert "x" in ids, "flag-off should see default-tenant rows regardless of context"

    summ = await memory_admin_summary()
    assert summ["ok"]
    assert summ["beliefs_live"] >= 1, "flag-off summary should count default rows"
    set_current_tenant_id("default")
