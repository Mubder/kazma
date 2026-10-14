"""Every operator memory change leaves a trail and answers with a real status
(Memory audit M35, MEM-S11).

Found 2026-10-13: no memory mutation but undo logged or audited who did it
(a forget logged nothing on success); most routes answered 200 for every
failure, ``not_found`` included; ``invalidate_belief`` returned the
exception's own text to API clients through unlink.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import UTC, datetime, timedelta
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
    db.execute("INSERT INTO entities (id, tenant_id, type, name) VALUES ('user', 'default', 'person', 'You')")
    db.commit()
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    dual_write._reset_mirror()
    yield SimpleNamespace(db=db, mp=monkeypatch, dir=tmp_path)
    dual_write._reset_mirror()
    db.close()


def _app(*, recorder: bool = True):
    from fastapi import FastAPI
    from kazma_ui import memory_api
    from kazma_ui.routes_direct import register_direct_routes

    app = FastAPI()
    register_direct_routes(SimpleNamespace(app=app))
    if recorder:
        memory_api.mount_memory_api(app)
    else:
        app.include_router(memory_api.router)
    return app


def _all_routes(routes) -> list:
    """Every route, an included router's too (FastAPI 0.138 wraps those)."""
    out = []
    for route in routes:
        inner = getattr(route, "original_router", None)
        out.extend(_all_routes(inner.routes) if inner is not None else [route])
    return out


def _mutating_routes(app) -> list[tuple[str, str]]:
    out = []
    for route in _all_routes(app.routes):
        path = getattr(route, "path", "")
        if path.startswith("/api/memory"):
            out += [(m, path) for m in sorted(getattr(route, "methods", ()) or ())
                    if m in ("POST", "PUT", "PATCH", "DELETE")]
    return sorted(set(out))


def unrecorded(app, mp) -> list[str]:
    """Mutating memory routes whose request left no trail."""
    from fastapi.testclient import TestClient
    from kazma_ui import memory_actions

    seen: list[str] = []
    mp.setattr(memory_actions, "_record", lambda action, ids, status, headers: seen.append(action))
    client = TestClient(app, raise_server_exceptions=False)
    missing = []
    for method, template in _mutating_routes(app):
        path = template
        for name in ("{entity_id}", "{belief_id}", "{episode_id}", "{summary_id}", "{chat_id}", "{task_id}",
                     "{token}", "{merge_id}", "{group_id}", "{node_id}"):
            path = path.replace(name, "nope")
        seen.clear()
        client.request(method, path, json={})
        if f"{method} {template}" not in seen:
            missing.append(f"{method} {template}")
    return missing


def test_every_memory_mutation_is_recorded(mem):
    app = _app()
    assert len(_mutating_routes(app)) >= 25
    assert unrecorded(app, mem.mp) == []


def test_a_route_without_the_recorder_is_seen(mem):
    """Negative control: the same routes without the recorder mounted."""
    app = _app(recorder=False)
    assert unrecorded(app, mem.mp)


def _client():
    from fastapi.testclient import TestClient

    return TestClient(_app())


def test_failures_answer_real_status_codes(mem):
    c = _client()
    assert c.post("/api/memory/v2/episodes/nope/forget").status_code == 404
    assert c.delete("/api/memory/v2/entities/user").status_code == 409  # protected
    assert c.put("/api/memory/v2/profile", json={"about": 3}).status_code == 422
    r = c.put("/api/memory/v2/profile", json={"about": "I am Sara."})
    assert r.status_code == 200 and r.json()["ok"]


def test_a_change_leaves_an_audit_row_and_a_log_line_with_ids_never_values(mem, caplog):
    from kazma_core.memory.dual_write import mirror_episode

    eid = mirror_episode(session_id="chat-9", turn_number=1, user_text="my secret is zanzibar",
                         assistant_text="Noted.")
    caplog.set_level(logging.INFO, logger="kazma_ui.memory_actions")
    r = _client().post(f"/api/memory/v2/episodes/{eid}/forget")
    assert r.status_code == 200
    line = next(rec.getMessage() for rec in caplog.records if "operator action=" in rec.getMessage())
    assert "/forget" in line and eid in line and "status=200" in line and "zanzibar" not in line
    ops = sqlite3.connect(str(mem.dir / "memory_ops.db"))
    rows = ops.execute("SELECT target_id, actor, reason, state_before_json, state_after_json FROM memory_audit_log "
                       "WHERE event_type = 'operator_action'").fetchall()
    ops.close()
    assert rows and rows[0][0] == eid and rows[0][1] == "web:anon" and rows[0][3] is None and rows[0][4] is None
    assert "zanzibar" not in json.dumps(rows)


def test_the_actions_reach_the_metrics_page(mem):
    """The recorder's counter is exposed on /metrics (it was counted and
    read by nothing)."""
    from fastapi.testclient import TestClient
    from kazma_ui import memory_actions
    from kazma_ui.metrics import create_metrics_router

    mem.mp.setattr(memory_actions, "_COUNTS", memory_actions.Counter())
    app = _app()
    gateway = SimpleNamespace(metrics=SimpleNamespace(inbound_total=0, outbound_total=0, errors_total=0),
                              adapters={}, queue=SimpleNamespace(qsize=lambda: 0))
    app.include_router(create_metrics_router(gateway))
    c = TestClient(app)
    before = c.get("/metrics").text
    assert "kazma_memory_operator_actions_total" not in before  # nothing done yet
    c.post("/api/memory/v2/episodes/nope/forget")
    text = c.get("/metrics").text
    assert ('kazma_memory_operator_actions_total{action="POST /api/memory/v2/episodes/{episode_id}/forget"} 1'
            in text)


def test_an_invalidate_failure_carries_no_exception_text(mem):
    from kazma_core.memory.hygiene import invalidate_belief

    class _Broken:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("disk I/O error at /secret/path/memory_state.db")

        def close(self):
            pass

    out = invalidate_belief("b1", conn=_Broken())
    assert out["ok"] is False and "(ref: " in out["error"] and "/secret/path" not in out["error"]


def test_the_daily_digest_counts_the_forgets(tmp_path, monkeypatch):
    from kazma_core.observability import daily_digest

    ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    lines = [
        "[memory] operator action=POST_/api/memory/v2/episodes/{episode_id}/forget actor=web:admin:abcd1234 "
        "tenant=default ids=ep1 status=200",
        "[memory] operator action=POST_/api/memory/v2/summaries/{summary_id}/forget actor=web:admin:abcd1234 "
        "tenant=default ids=s1 status=200",
        "[memory] operator action=POST_/api/memory/v2/episodes/{episode_id}/forget actor=web:admin:abcd1234 "
        "tenant=default ids=nope status=404",
        "[memory] operator action=DELETE_/api/memory/v2/entities/{entity_id} actor=web:admin:abcd1234 "
        "tenant=default ids=e1 status=200",
    ]
    log = tmp_path / "kazma.log"
    log.write_text("\n".join(json.dumps({"timestamp": ts, "level": "INFO", "message": m}) for m in lines),
                   encoding="utf-8")
    guard = tmp_path / "guard.log"
    guard.write_text("", encoding="utf-8")
    monkeypatch.setenv("KAZMA_LOG_FILE", str(log))
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(guard))
    text = daily_digest.build_digest()
    assert "memories forgotten from the Memory page: 2" in text, text
