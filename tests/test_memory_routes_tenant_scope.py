"""Every memory route keeps to the caller's tenant.

With tenants enforced (``KAZMA_MEMORY_ENFORCE_TENANT=1``, multi-user, or
``KAZMA_PRODUCTION=1``) a memory route reads and changes only the request
tenant's rows, or it is install-wide and only an admin may call it. Until
2026-09-27 a dozen did neither:

- invalidating a belief, deciding an entity merge, and deleting, moving or
  re-tiering a graph grouping took any tenant's id;
- the episode list, the quality checks, the health counts, the merge count and
  the hygiene preview covered every tenant, and the hygiene run changed every
  tenant's rows;
- ``invalidate-batch`` named the entities another tenant's beliefs touch;
- the task queue, reconsolidation and the golden eval answered any role.

The gate takes every route under ``/api/memory`` and ``/memory`` from the
registrars the app uses. It calls each one as a non-admin principal of tenant
"alpha", passing tenant "beta"'s ids wherever a route takes one, on a fresh
database per request. It then checks that no answer shows beta's text or ids
(apart from ids the request itself sent) and that no beta row changed. A route
with no request below fails the gate, so a new route is covered from its first
commit.

The install's own tenant ("default": single-user, or a principal bound to no
tenant) sees the whole install. That is ``_tenant_clause``'s rule, and
``test_the_installs_own_view_is_unchanged`` pins it.

The same walk -- and one call per agent memory tool -- checks that no SQLite
statement runs while an event loop runs on its thread (``_trace_sql_on_loop``):
that loop serves every chat stream (AGENTS §35). Until 2026-09-27, 17 memory
routes opened their connection in a thread and then ran every statement on
the loop, the graph search and the reconsolidate enqueue ran there outright,
and 9 of the 10 memory tools did (``memory_search`` embedded the query there).
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

#: Every beta id and every beta text carries this; alpha is never shown it.
MARK = "beta9"

_TENANT_TABLES = (
    "beliefs",
    "beliefs_archive",
    "entities",
    "entity_merges",
    "episodes",
    "graph_associations",
    "memory_forgotten",
    "memory_summaries",
    "memory_summary_periods",
    "procedural_dags",
)
#: Derived caches any write may mark stale (-1) for the read path to recompute.
_CACHE_COLUMNS = frozenset({"belief_count", "graph_degree"})

_NOW = time.time()

#: (METHOD, route) -> the requests alpha makes, with beta's ids.
REQUESTS: dict[tuple[str, str], list[tuple[str, Any]]] = {
    ("GET", "/api/memory/graph/search"): [("/api/memory/graph/search?q=Berlin", None)],
    ("POST", "/api/memory/graph/clear"): [("/api/memory/graph/clear?tenant=beta&confirm=true", None)],
    ("GET", "/api/memory/v2/health"): [("/api/memory/v2/health", None)],
    ("POST", "/api/memory/v2/federated-search"): [
        ("/api/memory/v2/federated-search", {"query": "Berlin"}),
    ],
    ("POST", "/api/memory/v2/probe"): [("/api/memory/v2/probe", {"query": "Berlin"})],
    ("GET", "/api/memory/v2/beliefs"): [
        ("/api/memory/v2/beliefs", None),
        ("/api/memory/v2/beliefs?q=Berlin", None),
        ("/api/memory/v2/beliefs?q=Paris", None),
        ("/api/memory/v2/beliefs?q=lives", None),
    ],
    ("GET", "/api/memory/v2/beliefs/{belief_id}"): [("/api/memory/v2/beliefs/beta9_lives", None)],
    ("GET", "/api/memory/v2/beliefs/{belief_id}/recall-trail"): [
        ("/api/memory/v2/beliefs/beta9_lives/recall-trail", None),
    ],
    ("POST", "/api/memory/v2/beliefs/{belief_id}/invalidate"): [
        ("/api/memory/v2/beliefs/beta9_lives/invalidate", None),
    ],
    ("GET", "/api/memory/v2/entity-merges"): [("/api/memory/v2/entity-merges", None)],
    ("POST", "/api/memory/v2/entity-merges/{merge_id}"): [
        ("/api/memory/v2/entity-merges/beta9_m1", {"action": "approve"}),
        ("/api/memory/v2/entity-merges/beta9_m1", {"action": "reject"}),
    ],
    ("GET", "/api/memory/v2/episodes"): [
        ("/api/memory/v2/episodes", None),
        ("/api/memory/v2/episodes?tier=episodic", None),
    ],
    ("GET", "/api/memory/v2/procedural"): [
        ("/api/memory/v2/procedural", None),
        ("/api/memory/v2/procedural?q=routine", None),
    ],
    ("GET", "/api/memory/v2/quality"): [("/api/memory/v2/quality", None)],
    ("GET", "/api/memory/v2/graph/export"): [
        ("/api/memory/v2/graph/export?format=json", None),
        ("/api/memory/v2/graph/export?format=graphml", None),
    ],
    ("GET", "/api/memory/v2/graph"): [
        ("/api/memory/v2/graph", None),
        (f"/api/memory/v2/graph?at={_NOW - 30}", None),
    ],
    ("GET", "/api/memory/v2/vocab"): [("/api/memory/v2/vocab", None)],
    ("GET", "/api/memory/v2/admin/summary"): [("/api/memory/v2/admin/summary", None)],
    ("POST", "/api/memory/v2/undo/{token}"): [("/api/memory/v2/undo/{beta_undo}", None)],
    ("GET", "/api/memory/v2/entities"): [
        ("/api/memory/v2/entities", None),
        ("/api/memory/v2/entities?q=Bob", None),
        ("/api/memory/v2/entities?q=Alice", None),
        ("/api/memory/v2/entities?empty_only=true", None),
        ("/api/memory/v2/entities?isolated_only=true", None),
    ],
    ("POST", "/api/memory/v2/entities/{entity_id}/rename"): [
        ("/api/memory/v2/entities/beta9_bob/rename", {"name": "Renamed"}),
    ],
    ("POST", "/api/memory/v2/entities/{entity_id}/protect"): [
        ("/api/memory/v2/entities/beta9_bob/protect", {"protected": True}),
    ],
    ("POST", "/api/memory/v2/entities/{entity_id}/major"): [
        ("/api/memory/v2/entities/beta9_bob/major", {"major": True}),
    ],
    ("DELETE", "/api/memory/v2/entities/{entity_id}"): [
        ("/api/memory/v2/entities/beta9_shell", None),
        ("/api/memory/v2/entities/beta9_bob", None),
    ],
    ("POST", "/api/memory/v2/entities/merge"): [
        ("/api/memory/v2/entities/merge", {"source_id": "beta9_shell", "target_id": "beta9_bob"}),
        ("/api/memory/v2/entities/merge", {"source_id": "beta9_bob", "target_id": "alice"}),
        ("/api/memory/v2/entities/merge", {"source_id": "alice", "target_id": "beta9_bob"}),
    ],
    ("POST", "/api/memory/v2/entities/link"): [
        ("/api/memory/v2/entities/link", {"subject": "alice", "predicate": "knows", "object": "Carol"}),
        # Entity ids are global (AGENTS §16): alpha may point at beta's node
        # id, and gets an edge of its own -- beta's rows stay as they were.
        ("/api/memory/v2/entities/link", {"subject": "alice", "predicate": "knows", "object": "beta9_bob"}),
    ],
    ("POST", "/api/memory/v2/entities/unlink"): [
        ("/api/memory/v2/entities/unlink", {"belief_id": "beta9_edge"}),
        (
            "/api/memory/v2/entities/unlink",
            {"subject": "beta9_bob", "predicate": "reports_to", "object": "beta9_ann"},
        ),
    ],
    ("PATCH", "/api/memory/v2/beliefs/{belief_id}"): [
        ("/api/memory/v2/beliefs/beta9_lives", {"object": "Rome"}),
    ],
    ("POST", "/api/memory/v2/beliefs/{belief_id}/repoint"): [
        ("/api/memory/v2/beliefs/beta9_lives/repoint", {"subject": "alice"}),
    ],
    ("POST", "/api/memory/v2/beliefs/invalidate-batch"): [
        ("/api/memory/v2/beliefs/invalidate-batch", {"ids": ["beta9_lives", "beta9_edge"]}),
    ],
    ("GET", "/api/memory/v2/graph/groups"): [("/api/memory/v2/graph/groups", None)],
    ("POST", "/api/memory/v2/graph/groups"): [
        ("/api/memory/v2/graph/groups", {"group_root": "alice", "member": "alice_pet"}),
        ("/api/memory/v2/graph/groups", {"group_root": "beta9_ann", "member": "alice"}),
    ],
    ("DELETE", "/api/memory/v2/graph/groups/{group_id}"): [
        ("/api/memory/v2/graph/groups/beta9_g1", None),
        ("/api/memory/v2/graph/groups/beta9_g2", None),
    ],
    ("POST", "/api/memory/v2/graph/groups/member/{member_id}/move"): [
        ("/api/memory/v2/graph/groups/member/alice/move", {"new_root": "alice_pet"}),
        ("/api/memory/v2/graph/groups/member/beta9_ann/move", {"new_root": "alice"}),
    ],
    ("POST", "/api/memory/v2/graph/groups/node/{node_id}/tier"): [
        ("/api/memory/v2/graph/groups/node/alice/tier", {"tier": 3}),
        ("/api/memory/v2/graph/groups/node/beta9_ann/tier", {"tier": 3}),
    ],
    ("GET", "/api/memory/v2/hygiene/preview"): [("/api/memory/v2/hygiene/preview", None)],
    ("POST", "/api/memory/v2/hygiene/run"): [
        (
            "/api/memory/v2/hygiene/run",
            {"purge_empty_entities": True, "invalidate_near_dup_noted": True, "archive_invalidated": True},
        ),
    ],
    # Plan U1: what the user decides to keep.
    ("POST", "/api/memory/v2/episodes/{episode_id}/forget"): [
        ("/api/memory/v2/episodes/beta9_e0/forget", None),
    ],
    ("GET", "/api/memory/v2/chats/{chat_id}/memory"): [
        ("/api/memory/v2/chats/beta9_s1/memory", None),
        ("/api/memory/v2/chats/alpha_s1/memory", None),
    ],
    ("PUT", "/api/memory/v2/chats/{chat_id}/memory"): [
        ("/api/memory/v2/chats/beta9_s1/memory", {"remember": False, "forget_past": True}),
        ("/api/memory/v2/chats/beta9_s1/memory", {"remember": True}),
    ],
    ("GET", "/api/memory/v2/export"): [("/api/memory/v2/export", None)],
    # Plan C2: weekly topic summaries.
    ("GET", "/api/memory/v2/summaries"): [("/api/memory/v2/summaries", None)],
    ("POST", "/api/memory/v2/summaries/{summary_id}/forget"): [
        ("/api/memory/v2/summaries/beta9_sum/forget", None),
    ],
}

#: What alpha must see of its own rows. A route that answers a tenant-bound
#: caller with an error or an empty page leaks nothing and would pass the
#: checks above; the beliefs list did exactly that until 2026-09-27 (its
#: tenant predicate had no value bound).
SHOWS_OWN: dict[str, str] = {
    "/api/memory/graph/search?q=Berlin": "alpha_e1",
    "/api/memory/v2/health": '"active":2',
    "/api/memory/v2/federated-search": "alpha_e1",
    "/api/memory/v2/probe": "alpha_e1",
    "/api/memory/v2/beliefs": "alpha_b1",
    "/api/memory/v2/beliefs?q=Paris": "alpha_b1",
    "/api/memory/v2/beliefs?q=lives": "alpha_b1",
    "/api/memory/v2/episodes": "alpha_e1",
    "/api/memory/v2/episodes?tier=episodic": "alpha_e1",
    "/api/memory/v2/graph": '"alice"',
    "/api/memory/v2/graph/export?format=json": "alpha_b1",
    "/api/memory/v2/graph/export?format=graphml": "alice",
    "/api/memory/v2/vocab": '"lives_in"',
    "/api/memory/v2/admin/summary": '"beliefs_live":2',
    "/api/memory/v2/entities": '"alice"',
    "/api/memory/v2/entities?q=Alice": '"alice"',
    "/api/memory/v2/chats/alpha_s1/memory": '"memories":1',
    "/api/memory/v2/export": '"alpha_e1"',
    "/api/memory/v2/summaries": '"alpha_sum1"',
}

#: Install-wide: the platform role check lets an admin through and no one else.
ADMIN_ONLY = {
    ("GET", "/api/memory/v2/queue"): "the durable maintenance queue belongs to the install",
    ("POST", "/api/memory/v2/queue/{task_id}/retry"): "re-runs an install maintenance task",
    ("POST", "/api/memory/v2/queue/clear-failed"): "deletes the install's dead-letter rows",
    ("POST", "/api/memory/v2/reconsolidate"): "reconsolidates every tenant's facts",
    ("POST", "/api/memory/v2/eval/golden"): "a CPU-heavy benchmark of the engine on a private database",
}

#: One request per install-wide route, made as an admin: they answer the
#: install, but they still may not run SQL on the event loop.
ADMIN_REQUESTS: dict[tuple[str, str], tuple[str, Any]] = {
    ("GET", "/api/memory/v2/queue"): ("/api/memory/v2/queue", None),
    ("POST", "/api/memory/v2/queue/{task_id}/retry"): ("/api/memory/v2/queue/beta9_t1/retry", None),
    ("POST", "/api/memory/v2/queue/clear-failed"): ("/api/memory/v2/queue/clear-failed", None),
    ("POST", "/api/memory/v2/reconsolidate"): ("/api/memory/v2/reconsolidate", None),
    ("POST", "/api/memory/v2/eval/golden"): ("/api/memory/v2/eval/golden", {"include_optional": False}),
}

#: No tenant data at all.
NO_DATA = {
    ("GET", "/api/memory/graph"): "retired: answers 410",
    ("GET", "/api/memory/graph/stats"): "retired: answers 410",
    ("GET", "/api/memory/graph/export"): "retired: answers 410",
    ("GET", "/memory"): "the page shell; its data comes from the routes above",
}


# ── Fixture ────────────────────────────────────────────────────────────────


def _seed(root: Path) -> None:
    """One database, two tenants. Everything of beta's carries MARK."""
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    now = _NOW
    c = sqlite3.connect(root / "memory_state.db")
    ensure_primary_schema(c)
    c.executemany(
        "INSERT INTO entities (id, tenant_id, type, name) VALUES (?, ?, ?, ?)",
        [
            ("alice", "alpha", "person", "Alice"),
            ("alice_pet", "alpha", "concept", "Rex"),
            ("beta9_bob", "beta", "person", "BETA9 Bob"),
            ("beta9_ann", "beta", "person", "BETA9 Ann"),
            ("beta9_shell", "beta", "concept", "BETA9 shell"),
        ],
    )
    beliefs = [
        # id, tenant, subject, predicate, type, object, valid_from, valid_until, invalidated_at
        ("alpha_b1", "alpha", "alice", "lives_in", "functional", "Paris", now, None, None),
        ("alpha_b2", "alpha", "alice", "owns", "set", "alice_pet", now, None, None),
        ("beta9_lives", "beta", "beta9_bob", "lives_in", "functional", "BETA9 Berlin", now, None, None),
        ("beta9_edge", "beta", "beta9_bob", "reports_to", "set", "beta9_ann", now, None, None),
        ("beta9_note1", "beta", "beta9_bob", "noted", "set", "BETA9 a note", now, None, None),
        ("beta9_note2", "beta", "beta9_bob", "noted", "set", "BETA9 a note", now - 60, None, None),
        ("beta9_old", "beta", "beta9_bob", "likes", "set", "BETA9 tea", now - 600, now - 60, now - 60),
    ]
    c.executemany(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, "
        "confidence, structural_importance, source_trust_weight, extraction_method, "
        "valid_from, valid_until, invalidated_at, ingested_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0.9, 3, 1.0, 'user_explicit', ?, ?, ?, ?)",
        [(*b, now) for b in beliefs],
    )
    c.executemany(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, "
        "assistant_text, tier, created_at) VALUES (?, ?, ?, ?, ?, ?, 'episodic', ?)",
        [
            ("alpha_e1", "alpha", "alpha_s1", 1, "where does Alice live in Berlin terms", "Paris", now),
            *[
                (f"beta9_e{i}", "beta", "beta9_s1", i, f"BETA9 question {i} Berlin", f"BETA9 answer {i}", now)
                for i in range(3)
            ],
        ],
    )
    c.execute(
        "INSERT INTO entity_merges (id, tenant_id, source_entity_id, target_entity_id, "
        "merge_tier, confidence, requested_at) "
        "VALUES ('beta9_m1', 'beta', 'beta9_shell', 'beta9_bob', 'fuzzy', 0.8, ?)",
        (now,),
    )
    c.executemany(
        "INSERT INTO graph_associations (id, tenant_id, group_root, member, member_tier, "
        "label, created_at) VALUES (?, 'beta', ?, ?, ?, ?, ?)",
        [
            ("beta9_g1", "beta9_bob", "beta9_ann", 2, "BETA9 team", now),
            # beta grouped alpha's entity: ids are global, rows are not
            ("beta9_g2", "beta9_bob", "alice", 2, "BETA9 contacts", now),
        ],
    )
    c.execute(
        "INSERT INTO procedural_dags (id, tenant_id, name, description, precond_signature_hash, "
        "preconditions_json, dag_steps_json, postconditions_json, created_at) "
        "VALUES ('beta9_p1', 'beta', 'BETA9 routine', 'BETA9 how Bob ships', 'h', '[]', '[]', '[]', ?)",
        (now,),
    )
    # beta took one turn back (plan U1): its ledger row is beta's too
    c.execute(
        "INSERT INTO memory_forgotten (tenant_id, session_key, turn_number, question_sha, "
        "episode_id, forgotten_at) VALUES ('beta', 'beta9_s1', 9, 'beta9sha', NULL, ?)",
        (now,),
    )
    # a weekly summary each (plan C2), written from their own turns
    c.executemany(
        "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, title, "
        "summary_text, turn_count, chat_count, created_at, updated_at) "
        "VALUES (?, ?, '2026-W30', ?, ?, ?, ?, 3, 1, ?, ?)",
        [
            ("alpha_sum1", "alpha", now - 9 * 86400, now - 2 * 86400, "Alice's week",
             "Alice asked where she lives.", now, now),
            ("beta9_sum", "beta", now - 9 * 86400, now - 2 * 86400, "BETA9 week",
             "BETA9 Bob moved to Berlin.", now, now),
        ],
    )
    c.executemany(
        "INSERT INTO memory_summary_sources (summary_id, episode_id) VALUES (?, ?)",
        [("alpha_sum1", "alpha_e1"), ("beta9_sum", "beta9_e0"), ("beta9_sum", "beta9_e1")],
    )
    c.executemany(
        "INSERT INTO memory_summary_periods (tenant_id, period_key, period_start, period_end, "
        "status, turns, summaries, detail_json) VALUES (?, '2026-W30', ?, ?, 'done', 3, 1, ?)",
        [("alpha", now - 9 * 86400, now - 2 * 86400, "{}"),
         ("beta", now - 9 * 86400, now - 2 * 86400, '{"note": "BETA9"}')],
    )
    c.commit()
    c.close()
    o = sqlite3.connect(root / "memory_ops.db")
    ensure_ops_schema(o)
    o.execute(
        "INSERT INTO memory_task_queue (id, task_type, payload_json, status, created_at, "
        "updated_at, error_log) VALUES ('beta9_t1', 'micro_consolidation', ?, 'failed', ?, ?, ?)",
        (json.dumps({"tenant_id": "beta"}), now, now, "BETA9 failure"),
    )
    o.commit()
    o.close()


def _beta_rows(root: Path) -> dict[str, list[dict]]:
    """Every beta row, minus derived caches."""
    out: dict[str, list[dict]] = {}
    c = sqlite3.connect(root / "memory_state.db")
    c.row_factory = sqlite3.Row
    try:
        for table in _TENANT_TABLES:
            rows = c.execute(
                f"SELECT * FROM {table} WHERE tenant_id = 'beta' ORDER BY rowid"  # table: a fixed name above
            ).fetchall()
            out[table] = [{k: r[k] for k in r.keys() if k not in _CACHE_COLUMNS} for r in rows]
    finally:
        c.close()
    return out


def _register_beta_undo(root: Path) -> str:
    """An undo token beta's own session minted; redeeming it renames beta's Bob."""
    from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id
    from kazma_ui import memory_api

    async def _restore() -> dict:
        c = sqlite3.connect(root / "memory_state.db")
        try:
            c.execute("UPDATE entities SET name = 'undone' WHERE id = 'beta9_bob'")
            c.commit()
        finally:
            c.close()
        return {"restored": 1}

    token = set_current_tenant_id("beta")
    try:
        return memory_api.register_undo(_restore, label="beta edit", kind="edit")
    finally:
        reset_current_tenant_id(token)


def _sent(path: str, body: Any) -> set[str]:
    """Strings the request itself carried; an answer may echo those."""
    from urllib.parse import parse_qsl, urlsplit

    parts = urlsplit(path)
    out = {seg for seg in parts.path.split("/") if seg}
    out |= {v for _k, v in parse_qsl(parts.query)}

    def walk(value: Any) -> None:
        if isinstance(value, str):
            out.add(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(body)
    return {s for s in out if MARK in s.lower()}


@pytest.fixture()
def memory_client(tmp_path, monkeypatch):
    """The memory routes as the app registers them, called as tenant alpha (not an admin)."""
    monkeypatch.setenv("KAZMA_MEMORY_ENFORCE_TENANT", "1")
    monkeypatch.delenv("KAZMA_MEMORY_STATE_DB", raising=False)
    monkeypatch.delenv("KAZMA_MEMORY_OPS_DB", raising=False)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_ui.auth.admin_decision", lambda request: "forbidden")
    who = {"tenant": "alpha"}

    on_loop = _trace_sql_on_loop(monkeypatch)

    app = FastAPI()

    @app.middleware("http")
    async def _as_tenant(request, call_next):
        from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id

        token = set_current_tenant_id(who["tenant"])
        try:
            return await call_next(request)
        finally:
            reset_current_tenant_id(token)

    from kazma_ui.memory_api import mount_memory_api, register_memory_page
    from kazma_ui.routes_direct import register_direct_routes

    register_direct_routes(SimpleNamespace(app=app))
    mount_memory_api(app)
    register_memory_page(app, SimpleNamespace(), None)
    client = TestClient(app, raise_server_exceptions=False)
    return SimpleNamespace(
        client=client, app=app, who=who, tmp=tmp_path, monkeypatch=monkeypatch, on_loop=on_loop
    )


def _on_loop() -> bool:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _trace_sql_on_loop(monkeypatch) -> list[str]:
    """Every SQLite statement run while an event loop runs on its thread.

    That loop serves every chat stream (AGENTS §35). A trace callback sees the
    statement where it executes, so a connection opened in a thread and then
    used on the loop -- the memory admin API's shape until 2026-09-27 -- is
    caught too.
    """
    on_loop: list[str] = []
    real_connect = sqlite3.connect

    def _traced_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(lambda sql: on_loop.append(" ".join(sql.split())[:80]) if _on_loop() else None)
        return conn

    monkeypatch.setattr(sqlite3, "connect", _traced_connect)
    return on_loop


def _fresh(ctx, name: str) -> Path:
    root = ctx.tmp / name
    root.mkdir()
    ctx.monkeypatch.setenv("KAZMA_DATA_DIR", str(root))
    _seed(root)
    return root


def _problems(ctx, route: tuple[str, str]) -> list[str]:
    method = route[0]
    problems: list[str] = []
    for i, (path, body) in enumerate(REQUESTS[route]):
        root = _fresh(ctx, uuid.uuid4().hex[:10])
        if "{beta_undo}" in path:
            path = path.replace("{beta_undo}", _register_beta_undo(root))
        before = _beta_rows(root)
        ctx.on_loop.clear()
        resp = ctx.client.request(method, path, json=body)
        after = _beta_rows(root)
        label = f"{method} {path}"
        if ctx.on_loop:
            problems.append(f"{label}: ran SQL on the event loop: {ctx.on_loop[:3]}")
        if resp.status_code in (404, 405, 422):
            # The request never reached the handler: the gate would pass for
            # a route it did not exercise.
            problems.append(f"{label}: HTTP {resp.status_code} -- never reached the route")
        if resp.status_code >= 500:
            problems.append(f"{label}: HTTP {resp.status_code} {resp.text[:200]}")
        own = SHOWS_OWN.get(path)
        if own is not None and own not in resp.text:
            problems.append(f"{label}: did not show the caller's own rows ({own}): {resp.text[:200]}")
        changed = sorted(t for t in before if before[t] != after[t])
        if changed:
            problems.append(f"{label}: changed tenant beta's {', '.join(changed)}")
        shown = resp.text
        for echo in _sent(path, body):
            shown = shown.replace(echo, "")
        at = shown.lower().find(MARK)
        if at >= 0:
            problems.append(f"{label}: showed tenant beta's data: ...{shown[max(0, at - 80):at + 80]}...")
    return problems


def _all_routes(routes) -> list:
    """Every route, including an included router's (FastAPI 0.138 wraps those)."""
    out = []
    for route in routes:
        inner = getattr(route, "original_router", None)
        out.extend(_all_routes(inner.routes) if inner is not None else [route])
    return out


def _memory_routes(app) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for route in _all_routes(app.routes):
        path = getattr(route, "path", "")
        if not (path.startswith("/api/memory") or path == "/memory"):
            continue
        for method in getattr(route, "methods", None) or ():
            if method not in ("HEAD", "OPTIONS"):
                out.add((method, path))
    return out


# ── The gate ───────────────────────────────────────────────────────────────


def test_every_memory_route_is_declared(memory_client):
    declared = set(REQUESTS) | set(ADMIN_ONLY) | set(NO_DATA)
    routes = _memory_routes(memory_client.app)
    assert routes - declared == set(), "a memory route with no tenant rule: add requests to REQUESTS"
    assert declared - routes == set(), "a declared route no longer exists"
    assert not (set(REQUESTS) & set(ADMIN_ONLY)) and not (set(REQUESTS) & set(NO_DATA))
    assert set(ADMIN_REQUESTS) == set(ADMIN_ONLY)


@pytest.mark.parametrize("route", sorted(REQUESTS), ids=lambda r: f"{r[0]} {r[1]}")
def test_a_memory_route_keeps_to_the_callers_tenant(memory_client, route):
    assert _problems(memory_client, route) == []


@pytest.mark.parametrize("route", sorted(ADMIN_ONLY), ids=lambda r: f"{r[0]} {r[1]}")
def test_an_install_wide_memory_route_is_for_admins(route):
    from kazma_core.security.platform_rbac import role_allows

    method, template = route
    path = template.replace("{task_id}", "t1")
    assert role_allows("admin", path, method)
    assert not role_allows("operator", path, method)
    assert not role_allows("viewer", path, method)


@pytest.mark.parametrize("route", sorted(ADMIN_REQUESTS), ids=lambda r: f"{r[0]} {r[1]}")
def test_an_install_wide_memory_route_runs_off_the_loop(memory_client, route):
    _fresh(memory_client, uuid.uuid4().hex[:10])
    path, body = ADMIN_REQUESTS[route]
    memory_client.on_loop.clear()
    resp = memory_client.client.request(route[0], path, json=body)
    assert resp.status_code < 500, resp.text[:300]
    assert memory_client.on_loop == [], f"{route[0]} {path} ran SQL on the event loop"


def test_the_gate_sees_sql_on_the_loop(memory_client):
    """Negative control: a route that opens its connection in a thread and
    then runs its statement on the loop (memory_api's shape until 2026-09-27)."""
    import asyncio

    from kazma_ui.memory_api import _conn

    @memory_client.app.get("/api/memory/zz-negative-control")
    async def _on_the_loop():
        conn = await asyncio.to_thread(_conn)
        try:
            return {"n": conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0]}
        finally:
            conn.close()

    _fresh(memory_client, "control")
    memory_client.on_loop.clear()
    assert memory_client.client.get("/api/memory/zz-negative-control").status_code == 200
    assert any("SELECT COUNT(*) FROM beliefs" in s for s in memory_client.on_loop)


def test_the_gate_sees_a_route_that_ignores_the_tenant(memory_client, monkeypatch):
    """Negative control: with the tenant lookup answering "the install's view",
    the same requests show beta's rows and change them."""
    monkeypatch.setattr("kazma_ui.memory_api._memory_tenant_id", lambda: "default")
    shown = _problems(memory_client, ("GET", "/api/memory/v2/beliefs"))
    changed = _problems(memory_client, ("POST", "/api/memory/v2/beliefs/{belief_id}/invalidate"))
    assert any("showed tenant beta's data" in p for p in shown), shown
    assert any("changed tenant beta's beliefs" in p for p in changed), changed


def test_the_gate_sees_a_route_that_shows_the_caller_nothing(memory_client, monkeypatch):
    """Negative control for SHOWS_OWN: a lookup that resolves to a tenant with
    no rows leaks nothing, and the gate still fails it."""
    monkeypatch.setattr("kazma_ui.memory_api._memory_tenant_id", lambda: "nobody")
    problems = _problems(memory_client, ("GET", "/api/memory/v2/beliefs"))
    assert any("did not show the caller's own rows" in p for p in problems), problems


# ── The agent's own memory tools ───────────────────────────────────────────


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


def _as_tenant(tenant: str, coro_fn, *args):
    import asyncio

    from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id

    token = set_current_tenant_id(tenant)
    try:
        return asyncio.run(coro_fn(*args))
    finally:
        reset_current_tenant_id(token)


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    """The two-tenant database, where memory code looks for it (the conftest
    pins these two variables per test)."""
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    _seed(tmp_path)
    return tmp_path


def test_the_agent_invalidate_tool_keeps_to_the_turns_tenant(seeded):
    """memory_invalidate takes a belief id from the model: the turn's tenant decides."""
    before = _beta_rows(seeded)
    answer = _as_tenant("alpha", _memory_tools()["memory_invalidate"], "beta9_lives")
    assert "not found" in answer
    assert _beta_rows(seeded) == before


def test_the_agent_invalidate_tool_reaches_the_row_it_may_change(seeded):
    """Negative control: the install's own tenant may, and the row changes."""
    before = _beta_rows(seeded)
    answer = _as_tenant("default", _memory_tools()["memory_invalidate"], "beta9_lives")
    assert '"ok": true' in answer
    assert _beta_rows(seeded)["beliefs"] != before["beliefs"]


#: Calls per memory tool. The registry runs an async tool ON the loop and
#: threads only sync ones, so every one of these must hand its work to a
#: thread: memory_search embedded the query and searched SQLite on the loop
#: until 2026-09-27.
TOOL_CALLS: dict[str, list[dict]] = {
    "memory_search": [{"query": "where does Alice live"}],
    "memory_admin": [
        {"action": "list_beliefs", "q": "Paris"},
        {"action": "list_entities", "q": "Alice"},
        {"action": "invalidate", "id": "alpha_b1"},
        {"action": "delete_entity", "id": "alice_pet"},
        {"action": "purge_empty_entities"},
        {"action": "merge", "id": "alice_pet", "target": "alice"},
        {"action": "link", "subject": "alice", "object": "Carol"},
    ],
    "memory_merge_entities": [{"source_id": "alice_pet", "target_id": "alice"}],
    "memory_link_entities": [{"subject": "alice", "object": "Carol"}],
    "memory_list_beliefs": [{"q": "Paris"}],
    "memory_invalidate": [{"belief_id": "alpha_b1"}],
    "memory_list_entities": [{"q": "Alice"}],
    "memory_delete_entity": [{"entity_id": "alice_pet"}],
    "memory_purge_empty_entities": [{"confirm": False}],
    "memory_store": [{"text": "Alice likes tea"}],
}


def test_every_memory_tool_has_calls():
    assert set(TOOL_CALLS) == set(_memory_tools())


def test_the_tool_check_sees_sql_on_the_loop(seeded, monkeypatch):
    """Negative control: the pre-fix tool shape, a sync store call made
    straight from the async tool."""
    on_loop = _trace_sql_on_loop(monkeypatch)

    async def inline_tool() -> str:
        conn = sqlite3.connect(seeded / "memory_state.db")
        try:
            return str(conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0])
        finally:
            conn.close()

    _as_tenant("alpha", inline_tool)
    assert any("SELECT COUNT(*) FROM beliefs" in s for s in on_loop)


@pytest.mark.parametrize("name", sorted(TOOL_CALLS))
def test_an_agent_memory_tool_runs_off_the_loop(seeded, monkeypatch, name):
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    on_loop = _trace_sql_on_loop(monkeypatch)
    tool = _memory_tools()[name]
    for kwargs in TOOL_CALLS[name]:
        on_loop.clear()
        answer = _as_tenant("alpha", lambda kw=kwargs: tool(**kw))
        assert isinstance(answer, str)
        assert on_loop == [], f"{name}({kwargs}) ran SQL on the event loop: {on_loop[:3]}"


# ── Counts ─────────────────────────────────────────────────────────────────


def test_counts_are_the_callers_tenants(memory_client):
    _fresh(memory_client, "counts")
    get = lambda path: memory_client.client.get(path).json()  # noqa: E731

    health = get("/api/memory/v2/health")
    assert health["beliefs"]["active"] == 2
    assert health["beliefs"]["superseded"] == 0
    assert health["episodes"]["episodic"] == 1
    assert health["entities"] == 2
    assert health["procedural_dags"]["active"] == 0
    assert "queue" not in health and "findability" not in health  # the install's, not alpha's

    quality = {c["name"]: c["detail"] for c in get("/api/memory/v2/quality")["checks"]}
    assert quality["has_beliefs_or_episodes"] == "beliefs=2 episodes=1"
    assert quality["db_exists"] == ""

    assert get("/api/memory/v2/episodes")["total"] == 1
    assert get("/api/memory/v2/entity-merges")["total"] == 0
    preview = get("/api/memory/v2/hygiene/preview")
    assert preview["invalidated_belief_count"] == 0
    assert preview["near_dup_noted"] == []


def test_an_admin_bound_to_a_tenant_also_sees_the_engine(memory_client, monkeypatch):
    _fresh(memory_client, "admin")
    monkeypatch.setattr("kazma_ui.auth.admin_decision", lambda request: "ok")
    health = memory_client.client.get("/api/memory/v2/health").json()
    assert health["beliefs"]["active"] == 2  # still alpha's rows
    assert health["queue"]["failed"] == 1  # and the install's engine


def test_the_installs_own_view_is_unchanged(memory_client):
    """Single-user, or a principal bound to no tenant: the whole install."""
    _fresh(memory_client, "install")
    memory_client.who["tenant"] = "default"
    get = lambda path: memory_client.client.get(path).json()  # noqa: E731

    health = get("/api/memory/v2/health")
    assert health["beliefs"]["active"] == 6
    assert health["episodes"]["episodic"] == 4
    assert health["queue"]["failed"] == 1
    assert get("/api/memory/v2/episodes")["total"] == 4
    assert get("/api/memory/v2/entity-merges")["total"] == 1
    assert get("/api/memory/v2/hygiene/preview")["invalidated_belief_count"] == 1
