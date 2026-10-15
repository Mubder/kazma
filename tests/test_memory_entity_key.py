"""Entities are keyed by ``(tenant_id, id)`` (Memory audit M21, 2026-10-15).

``entities.id`` was the primary key alone: a second tenant's ``acme_corp`` was
never created, the ``user`` hub went to whichever tenant made it first, and a
statement that named an entity by id reached every tenant's row of it. Here:

- the source gate: every statement on ``entities``, ``entity_merges`` or
  ``entity_merges_archive`` in product code names its tenant, and one that
  addresses an entity by id names it where it does (a statement that reads
  across tenants on purpose is declared with why); planted old statements as
  the negative control;
- two tenants each hold an entity of the same id, and every memory route
  (enumerated from the app) and every agent memory tool (from the registry)
  reads and changes only the caller's own -- the routes with the twin's id,
  the tools likewise; a route that resolves the wrong tenant as the negative
  control, and the old key, which cannot hold the twins at all;
- the migration of a store made under the old key, seeded through the
  product's writers with both tenants' rows, on a copy: keyed by the pair,
  every row and id kept, idempotent, and recall, the graph and a merge work
  after it; ``scripts/memory_entity_key.py`` sizes, copies first, applies.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import sqlite3
import subprocess
import textwrap
import uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import test_memory_routes_tenant_scope as scope
from tests.test_memory_routes_tenant_scope import (  # noqa: F401 - the shared fixture
    _as_tenant,
    _beta_rows,
    _memory_tools,
    memory_client,
)

REPO = Path(__file__).resolve().parents[1]

#: The table definitions a store made before 2026-10-15 holds.
LEGACY_ENTITY_DDL = """
CREATE TABLE IF NOT EXISTS entities (
  id              TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL DEFAULT 'default',
  type            TEXT NOT NULL,
  name            TEXT NOT NULL,
  aliases_json    TEXT DEFAULT '[]',
  is_high_stakes  INTEGER DEFAULT 0,
  is_protected    INTEGER DEFAULT 0,
  is_major        INTEGER DEFAULT 0,
  metadata_json   TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS entity_merges (
  id                TEXT PRIMARY KEY,
  tenant_id         TEXT NOT NULL DEFAULT 'default',
  source_entity_id  TEXT NOT NULL,
  target_entity_id  TEXT NOT NULL,
  status            TEXT NOT NULL DEFAULT 'pending',
  merge_tier        TEXT NOT NULL,
  confidence        REAL NOT NULL,
  requested_at      REAL NOT NULL,
  resolved_at       REAL,
  metadata_json     TEXT DEFAULT '{}',
  FOREIGN KEY (source_entity_id) REFERENCES entities(id),
  FOREIGN KEY (target_entity_id) REFERENCES entities(id)
);
"""


def _legacy_store(path: Path) -> sqlite3.Connection:
    """A memory database as a build before the change made it: the entity
    tables under the old key, everything else as today."""
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(LEGACY_ENTITY_DDL)
    ensure_primary_schema(conn)
    return conn


# ── The source gate ───────────────────────────────────────────────────────

_TABLE = re.compile(r"\b(?:FROM|JOIN|UPDATE|INTO)\s+(?:entities|entity_merges|entity_merges_archive)\b", re.I)
#: A WHERE that picks an entity, or a merge row, out by an id.
_BY_ID = re.compile(r"(?<![\w])(?:\w+\.)?(?:id|source_entity_id|target_entity_id)\s*(?:=|\bIN\b)|merged_into", re.I)
_SEGMENT_END = re.compile(r"\b(?:UNION|SELECT|ORDER\s+BY|LIMIT|GROUP\s+BY)\b", re.I)

#: Entity statements that read across tenants on purpose, by (file, function).
ACROSS_TENANTS: dict[tuple[str, str], str] = {
    ("kazma-core/kazma_core/memory/schema_v2.py", "_backfill_entity_counts"):
        "counts the rows still at the -1 sentinel in the whole store; each is recomputed under its own tenant",
    ("kazma-core/kazma_core/memory/schema_v2.py", "_ensure_fts5"):
        "counts every row to decide whether the search index needs a rebuild",
    ("kazma-core/kazma_core/memory/backfill_v2.py", "backfill_status"):
        "counts what the one-off V1 backfill wrote, install-wide",
    ("kazma-core/kazma_core/memory/entity_key.py", "size_entity_key"):
        "the operator's sizing of the whole store before the migration",
    ("kazma-core/kazma_core/memory/entity_repair.py", "size_collapsed"):
        "lists every tenant that holds the collapsed id 'entity', for the operator's sizing (M02)",
    ("kazma-ui/kazma_ui/memory_api.py", "<module>"):
        "_OWNER_SQL reads a merge row's tenant by the row's own random id, to check it before acting",
    ("scripts/verify_v2_coverage.py", "v2_counts"):
        "the operator's coverage report counts the whole store",
}


def _statement_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) else "{" + ast.unparse(v.value) + "}" for v in node.values
        )
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def unscoped(text: str) -> bool:
    """True when an entity statement does not name its tenant: nowhere at all,
    or not in a WHERE that picks an entity or a merge row by id."""
    if "tenant_id" not in text:
        return True
    for segment in re.split(r"\bWHERE\b", text, flags=re.I)[1:]:
        segment = _SEGMENT_END.split(segment)[0]
        if _BY_ID.search(segment) and "tenant_id" not in segment:
            return True
    return False


def entity_statements(sources: dict[str, str]) -> set[tuple[str, str, int, str]]:
    """(file, enclosing function, line, text) of every unscoped entity statement."""
    found: set[tuple[str, str, int, str]] = set()
    for rel, source in sources.items():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        def visit(node: ast.AST, function: str, rel: str = rel) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = node.name
            text = _statement_text(node)
            if text and _TABLE.search(text) and unscoped(text):
                found.add((rel, function, getattr(node, "lineno", 0), " ".join(text.split())[:120]))
            if not isinstance(node, ast.JoinedStr):  # its parts were read whole above
                for child in ast.iter_child_nodes(node):
                    visit(child, function)

        visit(tree, "<module>")
    return found


def _product_sources() -> dict[str, str]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    out = {}
    for rel in listed:
        if not (rel.startswith("kazma-") or rel.startswith("scripts/")) or "/tests/" in rel or "_tests/" in rel:
            continue
        path = REPO / rel
        if path.exists():
            out[rel] = path.read_text(encoding="utf-8", errors="replace")
    return out


def test_every_entity_statement_names_its_tenant():
    found = entity_statements(_product_sources())
    undeclared = sorted(f for f in found if (f[0], f[1]) not in ACROSS_TENANTS)
    assert not undeclared, (
        "Entity statements that do not name their tenant -- entities are keyed by (tenant_id, id): "
        "scope them, or declare the function in ACROSS_TENANTS with why:\n  "
        + "\n  ".join(f"{f}:{line} in {fn}(): {text}" for f, fn, line, text in undeclared)
    )
    stale = sorted(set(ACROSS_TENANTS) - {(f, fn) for f, fn, _line, _text in found})
    assert not stale, f"declared but no longer unscoped -- drop the entry: {stale}"


def test_the_gate_finds_the_old_statements():
    """Negative control: the statements of 2026-10-14 (a global key's), planted."""
    planted = {
        "x.py": textwrap.dedent(
            '''
            def rename(conn, eid, name):
                conn.execute("UPDATE entities SET name=? WHERE id=?", (name, eid))

            def protection(conn, eid):
                return conn.execute("SELECT tenant_id, is_protected FROM entities WHERE id = ?", (eid,))

            def delete(conn, eid):
                conn.execute("DELETE FROM entity_merges WHERE source_entity_id=? OR target_entity_id=?", (eid, eid))

            def graph(conn, marks, ids):
                return conn.execute(f"SELECT id, name FROM entities WHERE id IN ({marks})", ids)

            def fine(conn, eid, tid):
                conn.execute("UPDATE entities SET name='x' WHERE id=? AND tenant_id=?", (eid, tid))
                conn.execute("SELECT 1 FROM entities WHERE tenant_id = ? AND (id = ? OR name = ?)", (tid, eid, eid))
            '''
        )
    }
    assert {fn for _f, fn, _l, _t in entity_statements(planted)} == {"rename", "protection", "delete", "graph"}


# ── The key ───────────────────────────────────────────────────────────────


def test_two_tenants_hold_an_entity_of_the_same_id(tmp_path):
    from kazma_core.memory.entity_key import tenant_keyed
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    conn = sqlite3.connect(str(tmp_path / "m.db"))
    ensure_primary_schema(conn)
    assert tenant_keyed(conn)
    conn.executemany("INSERT INTO entities (id, tenant_id, type, name) VALUES (?, ?, 'concept', ?)",
                     [("kazma", "alpha", "Alpha's Kazma"), ("kazma", "beta", "Beta's Kazma")])
    assert sorted(conn.execute("SELECT tenant_id, name FROM entities WHERE id = 'kazma'")) == [
        ("alpha", "Alpha's Kazma"), ("beta", "Beta's Kazma")]
    with pytest.raises(sqlite3.IntegrityError):  # still one row per tenant and id
        conn.execute("INSERT INTO entities (id, tenant_id, type, name) VALUES ('kazma', 'beta', 'concept', 'x')")
    conn.close()


def test_the_old_key_cannot_hold_them(tmp_path):
    """Negative control: under the global key the second tenant's row collides."""
    from kazma_core.memory.entity_key import tenant_keyed

    conn = _legacy_store(tmp_path / "m.db")
    assert not tenant_keyed(conn)
    conn.execute("INSERT INTO entities (id, tenant_id, type, name) VALUES ('kazma', 'alpha', 'concept', 'a')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO entities (id, tenant_id, type, name) VALUES ('kazma', 'beta', 'concept', 'b')")
    conn.close()


def test_extraction_gives_each_tenant_its_own_entity(tmp_path, monkeypatch):
    """The R15 witness through the writer: the second tenant's "Acme Corp" is
    its own entity, with its own aliases, and the first tenant's is unchanged."""
    from kazma_core.memory.entity_resolution import resolve_entity
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    conn = sqlite3.connect(str(tmp_path / "m.db"))
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    resolve_entity(conn, "Acme Corp", tenant_id="a")
    before = conn.execute("SELECT aliases_json FROM entities WHERE id = 'acme_corp' AND tenant_id = 'a'").fetchone()[0]
    out = resolve_entity(conn, "ACME corp!", tenant_id="b")
    assert out == {"canonical_id": "acme_corp", "action": "create"}
    rows = {r["tenant_id"]: r["aliases_json"] for r in conn.execute("SELECT * FROM entities WHERE id = 'acme_corp'")}
    assert set(rows) == {"a", "b"} and rows["a"] == before
    conn.close()


# ── Every route and tool, with an id both tenants hold ────────────────────

TWIN = "shared_node"

#: Requests alpha makes naming the twin, by route: added to the gate's own.
TWIN_REQUESTS: dict[tuple[str, str], list[tuple[str, object]]] = {
    ("GET", "/api/memory/v2/entities"): [(f"/api/memory/v2/entities?q={TWIN}", None)],
    ("POST", "/api/memory/v2/entities/{entity_id}/rename"): [
        (f"/api/memory/v2/entities/{TWIN}/rename", {"name": "Alice renamed it"})],
    ("POST", "/api/memory/v2/entities/{entity_id}/protect"): [
        (f"/api/memory/v2/entities/{TWIN}/protect", {"protected": True})],
    ("POST", "/api/memory/v2/entities/{entity_id}/major"): [
        (f"/api/memory/v2/entities/{TWIN}/major", {"major": True})],
    ("DELETE", "/api/memory/v2/entities/{entity_id}"): [(f"/api/memory/v2/entities/{TWIN}", None)],
    ("POST", "/api/memory/v2/entities/merge"): [
        ("/api/memory/v2/entities/merge", {"source_id": TWIN, "target_id": "alice"}),
        ("/api/memory/v2/entities/merge", {"source_id": "alice_pet", "target_id": TWIN}),
    ],
    ("POST", "/api/memory/v2/entities/link"): [
        ("/api/memory/v2/entities/link", {"subject": TWIN, "predicate": "knows", "object": "alice"}),
        ("/api/memory/v2/entities/link", {"subject": "alice", "predicate": "knows", "object": TWIN}),
    ],
    ("POST", "/api/memory/v2/entities/unlink"): [
        ("/api/memory/v2/entities/unlink", {"subject": TWIN, "predicate": "lives_in", "object": "BETA9 Oslo"}),
    ],
    ("PATCH", "/api/memory/v2/beliefs/{belief_id}"): [
        ("/api/memory/v2/beliefs/alpha_b2", {"object": TWIN}),
    ],
    ("POST", "/api/memory/v2/beliefs/{belief_id}/repoint"): [
        ("/api/memory/v2/beliefs/alpha_b2/repoint", {"object": TWIN}),
    ],
    ("POST", "/api/memory/v2/graph/groups"): [
        ("/api/memory/v2/graph/groups", {"group_root": "alice", "member": TWIN}),
    ],
    ("POST", "/api/memory/v2/graph/groups/member/{member_id}/move"): [
        (f"/api/memory/v2/graph/groups/member/{TWIN}/move", {"new_root": "alice"}),
    ],
    ("POST", "/api/memory/v2/graph/groups/node/{node_id}/tier"): [
        (f"/api/memory/v2/graph/groups/node/{TWIN}/tier", {"tier": 2}),
    ],
}

#: Body fields and path parameters that name an entity.
_ENTITY_FIELDS = frozenset({"source_id", "target_id", "subject", "object", "group_root", "member", "new_root"})
_ENTITY_PARAMS = frozenset({"entity_id", "member_id", "node_id"})


def _seed_twins(root: Path) -> None:
    """The gate's two tenants, plus an entity of the same id in each: alpha's
    plain, beta's carrying the mark with facts, a merge and a grouping."""
    scope_seed(root)
    c = sqlite3.connect(root / "memory_state.db")
    now = scope._NOW
    c.executemany(
        "INSERT INTO entities (id, tenant_id, type, name, aliases_json) VALUES (?, ?, 'concept', ?, ?)",
        [(TWIN, "alpha", "Alice shared", '["Alice shared"]'),
         (TWIN, "beta", "BETA9 shared node", '["BETA9 shared node", "BETA9 alias"]')],
    )
    c.executemany(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, "
        "structural_importance, source_trust_weight, extraction_method, valid_from, ingested_at) "
        "VALUES (?, 'beta', ?, ?, 'set', ?, 0.9, 3, 1.0, 'user_explicit', ?, ?)",
        [("beta9_tw1", "beta9_bob", "knows", TWIN, now, now),
         ("beta9_tw2", TWIN, "lives_in", "BETA9 Oslo", now, now)],
    )
    c.execute(
        "INSERT INTO entity_merges (id, tenant_id, source_entity_id, target_entity_id, merge_tier, confidence, "
        "requested_at) VALUES ('beta9_tm', 'beta', ?, 'beta9_bob', 'tier2_vector', 0.7, ?)", (TWIN, now))
    c.execute(
        "INSERT INTO graph_associations (id, tenant_id, group_root, member, member_tier, label, created_at) "
        "VALUES ('beta9_tg', 'beta', 'beta9_bob', ?, 2, 'BETA9 twin', ?)", (TWIN, now))
    c.commit()
    c.close()


scope_seed = scope._seed


@pytest.fixture()
def twin_routes(memory_client, monkeypatch):  # noqa: F811 - the shared fixture, by name
    """The gate's own requests and the twin's, on a store holding the twins."""
    merged = {route: list(reqs) for route, reqs in scope.REQUESTS.items()}
    for route, reqs in TWIN_REQUESTS.items():
        merged.setdefault(route, []).extend(reqs)
    monkeypatch.setattr(scope, "REQUESTS", merged)
    monkeypatch.setattr(scope, "_seed", _seed_twins)
    monkeypatch.setattr(scope, "SHOWS_OWN", {**scope.SHOWS_OWN,
                                             f"/api/memory/v2/entities?q={TWIN}": '"Alice shared"'})
    return memory_client


def test_every_route_that_names_an_entity_has_a_twin_request(memory_client):  # noqa: F811
    """Enumerated from the app: a route taking an entity in its path or body."""
    routes = scope._memory_routes(memory_client.app)
    naming = set()
    for method, path in routes:
        params = set(re.findall(r"{(\w+)}", path))
        bodies = [body for _p, body in scope.REQUESTS.get((method, path), []) if isinstance(body, dict)]
        if params & _ENTITY_PARAMS or any(set(b) & _ENTITY_FIELDS for b in bodies):
            naming.add((method, path))
    assert naming - set(TWIN_REQUESTS) == set(), "add a request naming the twin to TWIN_REQUESTS"
    assert set(TWIN_REQUESTS) <= routes


@pytest.mark.parametrize("route", sorted(scope.REQUESTS), ids=lambda r: f"{r[0]} {r[1]}")
def test_a_route_with_the_twins_keeps_to_the_callers_entity(twin_routes, route):
    """Every memory route of the app, on a store where both tenants hold
    ``shared_node``: nothing of beta's shown, no row of beta's changed."""
    assert scope._problems(twin_routes, route) == []


def _alpha_twin(root: Path) -> dict | None:
    c = sqlite3.connect(root / "memory_state.db")
    c.row_factory = sqlite3.Row
    try:
        row = c.execute("SELECT * FROM entities WHERE id = ? AND tenant_id = 'alpha'", (TWIN,)).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


@pytest.mark.parametrize(("method", "path", "body", "expect"), [
    ("POST", f"/api/memory/v2/entities/{TWIN}/rename", {"name": "Alice renamed it"},
     lambda r: r and r["name"] == "Alice renamed it"),
    ("POST", f"/api/memory/v2/entities/{TWIN}/protect", {"protected": True}, lambda r: r and r["is_protected"] == 1),
    ("POST", f"/api/memory/v2/entities/{TWIN}/major", {"major": True}, lambda r: r and r["is_major"] == 1),
    ("DELETE", f"/api/memory/v2/entities/{TWIN}", None, lambda r: r is None),
    ("POST", "/api/memory/v2/entities/merge", {"source_id": TWIN, "target_id": "alice"},
     lambda r: r and json.loads(r["metadata_json"])["merged_into"] == "alice"),
], ids=["rename", "protect", "major", "delete", "merge"])
def test_a_route_changes_the_callers_own_twin(twin_routes, method, path, body, expect):
    root = scope._fresh(twin_routes, uuid.uuid4().hex[:10])
    before = _beta_rows(root)
    resp = twin_routes.client.request(method, path, json=body)
    assert resp.status_code == 200 and resp.json().get("ok"), resp.text[:300]
    assert expect(_alpha_twin(root)), _alpha_twin(root)
    assert _beta_rows(root) == before


def test_the_gate_sees_a_route_that_acts_on_the_other_tenants_twin(twin_routes, monkeypatch):
    """Negative control: an owner lookup that answers the other tenant (what a
    statement keyed by id alone reached) changes beta's twin, and the gate says so."""
    monkeypatch.setattr("kazma_ui.memory_api._entity_owner", lambda conn, eid: "beta")
    problems = scope._problems(twin_routes, ("POST", "/api/memory/v2/entities/{entity_id}/rename"))
    assert any("changed tenant beta's entities" in p for p in problems), problems


#: Agent memory tools that take an entity, called with the twin.
TWIN_TOOL_CALLS: dict[str, list[dict]] = {
    "memory_merge_entities": [{"source_id": TWIN, "target_id": "alice"},
                              {"source_id": "alice_pet", "target_id": TWIN}],
    "memory_delete_entity": [{"entity_id": TWIN}],
    "memory_link_entities": [{"subject": TWIN, "object": "alice"}, {"subject": "alice", "object": TWIN}],
    "memory_list_entities": [{"q": "shared"}],
    "memory_admin": [{"action": "merge", "id": TWIN, "target": "alice"},
                     {"action": "delete_entity", "id": TWIN},
                     {"action": "list_entities", "q": "shared"},
                     {"action": "link", "subject": TWIN, "object": "alice"}],
}
_ENTITY_TOOL_PARAMS = frozenset({"entity_id", "source_id", "target_id", "subject", "object", "target", "id"})


@pytest.fixture()
def twin_store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    _seed_twins(tmp_path)
    return tmp_path


def test_every_tool_that_takes_an_entity_has_a_twin_call():
    """Enumerated from the registry: a tool with an entity parameter (or a
    list of entities by query)."""
    tools = _memory_tools()
    taking = {name for name, fn in tools.items()
              if set(inspect.signature(fn).parameters) & _ENTITY_TOOL_PARAMS} | {"memory_list_entities"}
    assert taking - set(TWIN_TOOL_CALLS) == set(), "add a call with the twin to TWIN_TOOL_CALLS"


@pytest.mark.parametrize("name", sorted(TWIN_TOOL_CALLS))
def test_a_tool_with_the_twins_keeps_to_the_turns_tenant(twin_store, name):
    tool = _memory_tools()[name]
    for kwargs in TWIN_TOOL_CALLS[name]:
        before = _beta_rows(twin_store)
        answer = _as_tenant("alpha", lambda kw=kwargs: tool(**kw))
        assert isinstance(answer, str)
        assert _beta_rows(twin_store) == before, f"{name}({kwargs}) changed beta's rows"
        assert "beta9" not in answer.lower(), f"{name}({kwargs}) showed beta's data: {answer[:300]}"


def test_the_merge_tool_merges_the_callers_own_twin(twin_store):
    before = _beta_rows(twin_store)
    out = json.loads(_as_tenant("alpha", _memory_tools()["memory_merge_entities"], TWIN, "alice"))
    assert out["ok"], out
    assert json.loads(_alpha_twin(twin_store)["metadata_json"])["merged_into"] == "alice"
    assert _beta_rows(twin_store) == before


def test_the_delete_tool_deletes_the_callers_own_twin(twin_store):
    before = _beta_rows(twin_store)
    out = json.loads(_as_tenant("alpha", _memory_tools()["memory_delete_entity"], TWIN))
    assert out["ok"] and _alpha_twin(twin_store) is None, out
    assert _beta_rows(twin_store) == before


def test_the_tool_check_sees_a_tool_that_reaches_the_other_twin(twin_store, monkeypatch):
    """Negative control: an owner lookup answering the other tenant moves
    beta's twin, and the check above would fail."""
    monkeypatch.setattr("kazma_core.memory.entity_key.entity_owner", lambda conn, eid, caller: "beta")
    before = _beta_rows(twin_store)
    _as_tenant("alpha", _memory_tools()["memory_merge_entities"], TWIN, "beta9_bob")
    assert _beta_rows(twin_store) != before


def test_each_tenant_has_its_own_hub(twin_store, monkeypatch):
    """The S04 witness inverted under the key: renaming one's own "You" makes
    one's own ``user`` hub; beta's is untouched."""
    from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id
    from kazma_ui import memory_api

    monkeypatch.setenv("KAZMA_MEMORY_ENFORCE_TENANT", "1")
    monkeypatch.setenv("KAZMA_DATA_DIR", str(twin_store))
    c = sqlite3.connect(twin_store / "memory_state.db")
    c.executemany("INSERT INTO entities (id, tenant_id, type, name, aliases_json) VALUES (?, ?, 'person', ?, ?)",
                  [("user", "beta", "BETA9 Owner", '["BETA9 Owner"]'), ("alpha_me", "alpha", "You", '["You"]')])
    c.commit()
    c.close()
    before = _beta_rows(twin_store)
    token = set_current_tenant_id("alpha")
    try:
        out = memory_api._rename_entity_sync("alpha_me", {"name": "Alice"})
    finally:
        reset_current_tenant_id(token)
    assert out["ok"] and out["hub_synced"] is True
    c = sqlite3.connect(twin_store / "memory_state.db")
    hubs = dict(c.execute("SELECT tenant_id, name FROM entities WHERE id = 'user'").fetchall())
    c.close()
    assert hubs == {"beta": "BETA9 Owner", "alpha": "Alice"}
    assert _beta_rows(twin_store) == before


# ── The migration ─────────────────────────────────────────────────────────


@pytest.fixture()
def legacy(tmp_path, monkeypatch):
    """A store made under the old key, seeded through the product's writers:
    the install's own facts, entities and memories, another tenant's, a merge
    and a grouping each, and the facts the old key left without an entity."""
    from kazma_core.memory import dual_write
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.schema_v2 import ensure_ops_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    dual_write._reset_mirror()
    db = _legacy_store(tmp_path / "memory_state.db")
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)

    def fact(subject, pred, obj, tenant, at, ptype="set"):
        return [{"subject": subject, "predicate": pred, "predicate_type": ptype, "object": obj,
                 "confidence": 0.9, "importance": 4}], tenant, at

    for facts, tenant, at in (
        fact("user", "works_at", "Kazma Labs", "default", 1000.0, "functional"),
        fact("Kazma Labs", "builds", "Kazma", "default", 1100.0),
        fact("Kazma", "written_in", "Python", "default", 1200.0),
        fact("Kazma Labs", "sponsors", "Beta Project", "beta", 1300.0),  # the old key: no row of beta's
        fact("Beta Project", "uses", "Postgres", "beta", 1400.0),
    ):
        _apply_beliefs_to_v2(facts, db, ops, tenant_id=tenant, session_id=f"s_{tenant}", turn=1, now=at)
    mutate_belief(db, "user", "noted", "likes sailing on Fridays", ops_conn=ops, tenant_id="default",
                  extraction_method="user_explicit", private=True, now=1500.0)
    db.executemany(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, created_at) "
        "VALUES (?, ?, ?, 1, ?, ?, 'episodic', ?)",
        [("e_default", "default", "s_default", "where do I work? Kazma Labs builds Kazma", "At Kazma Labs.", 1600.0),
         ("e_beta", "beta", "s_beta", "what does Beta Project use?", "Postgres.", 1600.0)],
    )
    db.executemany(
        "INSERT INTO entities (id, tenant_id, type, name, aliases_json) VALUES (?, ?, 'concept', ?, ?)",
        [("beta_old_name", "beta", "Beta old name", '["Beta old name"]'),
         ("kazma_core", "default", "Kazma core", '["Kazma core"]')],
    )
    db.execute(
        "INSERT INTO entity_merges (id, tenant_id, source_entity_id, target_entity_id, merge_tier, confidence, "
        "requested_at) VALUES ('m_beta', 'beta', 'beta_old_name', 'beta_project', 'tier2_vector', 0.9, 1700)")
    db.execute(
        "INSERT INTO graph_associations (id, tenant_id, group_root, member, member_tier, created_at) "
        "VALUES ('g1', 'default', 'kazma', 'kazma_core', 2, 1700)")
    db.commit()
    yield SimpleNamespace(db=db, ops=ops, dir=tmp_path)
    dual_write._reset_mirror()
    ops.close()
    db.close()


def _rows(conn) -> list[tuple]:
    return sorted(tuple(r) for r in conn.execute(
        "SELECT rowid, tenant_id, id, type, name, aliases_json, metadata_json FROM entities"))


def test_the_migration_keys_a_copy_by_tenant_and_keeps_every_row(legacy):
    from kazma_core.db.sqlite_snapshot import snapshot_copy
    from kazma_core.memory.entity_key import migrate_entity_key, size_entity_key, tenant_keyed

    sized = size_entity_key(legacy.db)
    assert sized["keyed_by"] == "id" and sized["other_tenants"] >= 2
    assert sized["facts_naming_another_tenants_entity"] >= 1  # beta's kazma_labs sponsors ...
    copy = legacy.dir / "copy.db"
    snapshot_copy(legacy.dir / "memory_state.db", copy)
    conn = sqlite3.connect(str(copy), isolation_level=None)
    before = _rows(conn)
    merges = sorted(conn.execute("SELECT * FROM entity_merges").fetchall())
    out = migrate_entity_key(conn)
    assert out["migrated"] and tenant_keyed(conn)
    assert _rows(conn) == before  # every row, id, tenant and rowid as it was
    assert sorted(conn.execute("SELECT * FROM entity_merges").fetchall()) == merges
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    # The search index answers over the rebuilt table.
    hits = {r[0] for r in conn.execute(
        "SELECT e.id FROM entities_fts JOIN entities e ON e.rowid = entities_fts.rowid "
        "WHERE entities_fts MATCH 'beta'")}
    assert "beta_project" in hits
    # Idempotent; and now beta may hold its own kazma_labs.
    assert migrate_entity_key(conn).get("already") is True
    conn.execute("INSERT INTO entities (id, tenant_id, type, name) VALUES ('kazma_labs', 'beta', 'concept', 'x')")
    assert size_entity_key(conn)["facts_naming_another_tenants_entity"] == 0
    conn.close()
    assert not tenant_keyed(legacy.db)  # the original untouched


def test_recall_the_graph_and_a_merge_work_after_the_migration(legacy, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.memory.entity_key import migrate_entity_key
    from kazma_core.memory.entity_resolution import decide_entity_merge, resolve_entity
    from kazma_core.memory.recall import recall

    legacy.db.close()
    conn = sqlite3.connect(str(legacy.dir / "memory_state.db"), isolation_level=None)
    assert migrate_entity_key(conn)["migrated"]
    conn.close()
    db = sqlite3.connect(str(legacy.dir / "memory_state.db"))
    db.row_factory = sqlite3.Row
    legacy.db = db

    found = recall("Kazma Labs", conn=db, tenant_id="default", local_only=True)
    shown = [h.content.lower() for h in [*found.beliefs, *found.episodes]]
    assert any("kazma" in text for text in shown), shown

    # The merge the old key recorded resolves under the new one.
    assert decide_entity_merge(db, "m_beta", approve=True)["ok"]
    redirect = db.execute(
        "SELECT metadata_json FROM entities WHERE id = 'beta_old_name' AND tenant_id = 'beta'").fetchone()[0]
    assert json.loads(redirect)["merged_into"] == "beta_project"
    # Beta now gets an entity of its own for a name the install holds.
    assert resolve_entity(db, "Kazma Labs", tenant_id="beta")["canonical_id"] == "kazma_labs"
    assert {r[0] for r in db.execute("SELECT tenant_id FROM entities WHERE id = 'kazma_labs'")} == {"default", "beta"}

    from kazma_ui.memory_api import mount_memory_api
    from kazma_ui.routes_direct import register_direct_routes

    monkeypatch.setattr("kazma_ui.auth.admin_decision", lambda request: "ok")
    app = FastAPI()
    register_direct_routes(SimpleNamespace(app=app))
    mount_memory_api(app)
    client = TestClient(app)
    graph = client.get("/api/memory/v2/graph").json()
    ids = {n["id"] for n in graph["nodes"]}
    assert {"kazma_labs", "kazma"} <= ids, ids
    merged = client.post("/api/memory/v2/entities/merge", json={"source_id": "kazma_core", "target_id": "kazma"})
    assert merged.json()["ok"], merged.text
    assert client.get("/api/memory/v2/entities?q=kazma").json()["ok"]


def test_the_script_sizes_copies_first_and_migrates(legacy, capsys):
    import importlib.util

    from kazma_core.memory.entity_key import tenant_keyed

    spec = importlib.util.spec_from_file_location("memory_entity_key", REPO / "scripts" / "memory_entity_key.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    path = str(legacy.dir / "memory_state.db")
    legacy.db.close()
    assert script.main(["size", "--db", path]) == 0
    assert json.loads(capsys.readouterr().out)["keyed_by"] == "id"
    assert script.main(["apply", "--db", path]) == 2  # needs --yes
    capsys.readouterr()
    assert script.main(["apply", "--db", path, "--yes"]) == 0
    backup = Path(path + ".pre_entity_key")
    assert backup.exists()
    with closing(sqlite3.connect(path)) as after, closing(sqlite3.connect(str(backup))) as before_copy:
        assert tenant_keyed(after) and not tenant_keyed(before_copy)
        assert _rows(after) == _rows(before_copy)
    capsys.readouterr()
    assert script.main(["apply", "--db", path, "--yes"]) == 0  # idempotent
    assert '"migrated": false' in capsys.readouterr().out
    legacy.db = sqlite3.connect(path)
