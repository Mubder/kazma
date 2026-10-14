"""No tenant writes to or probes another tenant's entities (Memory audit M21,
MEM-S04 and the entity-key item of MEM-R15).

``entities.id`` is a global key. A second tenant's entity with the same name
was silently not created and its alias hash appended to the first tenant's
row; renaming one's own "You" rewrote the global ``user`` hub whoever owned it;
the agent's merge tool read both rows without a tenant check, so alpha's agent
could redirect beta's entity into alpha's; and rename answered ``not_found``
for another tenant's id but "created" for an unused one -- an existence oracle.

Here: the four witnesses inverted, and the agent memory tools that take an
entity or belief id run as tenant alpha against beta's ids: refused, and
beta's rows unchanged (the install's own tenant may, the negative control).

The entity KEY is still global: keying entities by ``(tenant_id, id)`` rewrites
every entity statement (about ninety) and needs a migration of non-default
tenants' rows; until then these rules hold the boundary.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from tests.test_memory_routes_tenant_scope import _as_tenant, _beta_rows, _memory_tools, _seed


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    for var in ("KAZMA_PRODUCTION", "KAZMA_MULTI_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    yield SimpleNamespace(db=db, mp=monkeypatch, dir=tmp_path)
    db.close()


def _two_tenants(mem) -> None:
    mem.db.executemany(
        "INSERT INTO entities (id, tenant_id, type, name, aliases_json, metadata_json) VALUES (?, ?, ?, ?, ?, '{}')",
        [
            ("user", "beta", "person", "Beta Owner", '["Beta Owner"]'),
            ("alpha_me", "alpha", "person", "You", '["You"]'),
            ("beta_proj", "beta", "concept", "Beta secret project", "[]"),
            ("alpha_proj", "alpha", "concept", "Alpha project", "[]"),
        ],
    )
    mem.db.commit()


class _Tenant:
    def __init__(self, tid: str):
        self.tid = tid

    def __enter__(self):
        from kazma_core.tenant_context import set_current_tenant_id

        self.token = set_current_tenant_id(self.tid)

    def __exit__(self, *exc):
        from kazma_core.tenant_context import reset_current_tenant_id

        reset_current_tenant_id(self.token)


def test_r15_a_second_tenant_never_writes_into_the_firsts_row(mem):
    from kazma_core.memory.entity_resolution import alias_hash, resolve_entity

    resolve_entity(mem.db, "Acme Corp", tenant_id="a")
    before = mem.db.execute("SELECT aliases_json FROM entities WHERE id = 'acme_corp'").fetchone()[0]
    resolve_entity(mem.db, "ACME corp!", tenant_id="b")
    row = mem.db.execute("SELECT tenant_id, aliases_json FROM entities WHERE id = 'acme_corp'").fetchone()
    assert row["tenant_id"] == "a" and row["aliases_json"] == before
    assert alias_hash("ACME corp!") not in json.loads(row["aliases_json"]) or \
        alias_hash("ACME corp!") == alias_hash("Acme Corp")  # the same name folds alike


def test_s04_renaming_your_own_self_entity_leaves_another_tenants_hub(mem):
    from kazma_ui import memory_api

    mem.mp.setenv("KAZMA_MEMORY_ENFORCE_TENANT", "1")
    _two_tenants(mem)
    with _Tenant("alpha"):
        out = memory_api._rename_entity_sync("alpha_me", {"name": "ALPHA-PRIVATE-NAME"})
    assert out["ok"] and out["hub_synced"] is False
    hub = mem.db.execute("SELECT tenant_id, name, aliases_json FROM entities WHERE id='user'").fetchone()
    assert (hub["tenant_id"], hub["name"]) == ("beta", "Beta Owner") and "alpha_me" not in hub["aliases_json"]


def test_s04_the_agent_merge_tool_keeps_to_its_tenant(mem):
    _two_tenants(mem)
    tools = _memory_tools()
    out = json.loads(_as_tenant("alpha", tools["memory_merge_entities"], "beta_proj", "alpha_proj"))
    assert out == {"ok": False, "error": "source or target not found"}
    src = mem.db.execute("SELECT metadata_json FROM entities WHERE id='beta_proj'").fetchone()
    tgt = mem.db.execute("SELECT aliases_json FROM entities WHERE id='alpha_proj'").fetchone()
    assert "merged_into" not in src[0] and "Beta secret project" not in tgt[0]


def test_s04_rename_answers_alike_for_another_tenants_id_and_an_unused_one(mem):
    from kazma_ui import memory_api

    mem.mp.setenv("KAZMA_MEMORY_ENFORCE_TENANT", "1")
    _two_tenants(mem)
    with _Tenant("alpha"):
        taken = memory_api._rename_entity_sync("beta_proj", {"name": "x"})
        free = memory_api._rename_entity_sync("nobody_has_this", {"name": "x"})
    assert taken == free == {"ok": False, "error": "not_found"}


def test_rename_still_promotes_a_node_of_the_callers_own_graph(mem):
    """A fact's subject with no entity row yet becomes one for its own tenant."""
    from kazma_ui import memory_api

    mem.mp.setenv("KAZMA_MEMORY_ENFORCE_TENANT", "1")
    mem.db.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, "
        "valid_from, ingested_at) VALUES ('b1', 'alpha', 'alpha_garden', 'has', 'set', 'roses', 0.9, 1, 1)")
    mem.db.commit()
    with _Tenant("alpha"):
        out = memory_api._rename_entity_sync("alpha_garden", {"name": "Garden"})
    assert out["ok"] and out["created"]


# ── The agent tools, as another tenant ────────────────────────────────────

#: Every agent memory tool that takes an id, with beta's ids.
ID_TOOL_CALLS = [
    ("memory_merge_entities", ("beta9_shell", "beta9_bob")),
    ("memory_delete_entity", ("beta9_shell",)),
    ("memory_invalidate", ("beta9_lives",)),
    ("memory_admin", ("merge", "beta9_shell", "", "", "", "beta9_bob")),
    ("memory_admin", ("delete_entity", "beta9_shell")),
    ("memory_admin", ("invalidate", "beta9_lives")),
]


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    _seed(tmp_path)
    return tmp_path


def _call(tools, name, args, tenant):
    import inspect

    fn = tools[name]
    params = list(inspect.signature(fn).parameters)
    return _as_tenant(tenant, lambda: fn(**dict(zip(params, args, strict=False))))


@pytest.mark.parametrize(("name", "args"), ID_TOOL_CALLS, ids=[f"{n}:{a[0]}" for n, a in ID_TOOL_CALLS])
def test_no_agent_tool_changes_another_tenants_rows(seeded, name, args):
    before = _beta_rows(seeded)
    _call(_memory_tools(), name, args, "alpha")
    assert _beta_rows(seeded) == before, name


def test_the_install_reaches_them(seeded):
    """Negative control: the install's own tenant may, and beta's rows change."""
    before = _beta_rows(seeded)
    _call(_memory_tools(), "memory_merge_entities", ("beta9_shell", "beta9_bob"), "default")
    assert _beta_rows(seeded) != before
