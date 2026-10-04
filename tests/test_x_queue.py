"""The planner renders authoritative states and actions require the viewed version."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    from kazma_core.x_api.account_binding import credential_revision
    from kazma_core.x_api.config import XCredentials, get_x_config
    from kazma_core.x_api.publication_store import _PublicationStore
    from kazma_ui.x_api import protected_router, router

    cfg = replace(get_x_config(), enabled=True, account_id="123", credentials=XCredentials("k", "s", "t", "ts"),
                  kill_switch=False, max_posts_per_day=50, max_posts_per_month=500)
    state = {"cfg": cfg}
    store = _PublicationStore(tmp_path / "publications.db")
    monkeypatch.setattr("kazma_core.x_api.config.get_x_config", lambda: state["cfg"])
    monkeypatch.setattr("kazma_core.x_api.publication_service.get_publication_store", lambda: store)
    monkeypatch.setattr("kazma_core.x_api.publication_service._project_pending", lambda: {})
    app = FastAPI()
    app.include_router(router)
    app.include_router(protected_router)

    def book(key, *, tenant="default", origin="schedule", due=None):
        return store.reserve(tenant_id=tenant, account_id="123", credential_revision=credential_revision(cfg.credentials),
                             idempotency_key=key, text=key, reply_to_id="", due_at=time.time() + 3600 if due is None else due,
                             max_day=50, max_month=500, duplicate_days=30, origin=origin)

    with TestClient(app) as client:
        yield store, state, book, client


def test_queue_keyset_counts_and_tenant_scope(env):
    store, _, book, client = env
    own = {book(str(i))["id"] for i in range(3)}
    book("another tenant", tenant="other")
    book("immediate write", origin="immediate")
    page = client.get("/api/x/queue?limit=2").json()
    assert page["ok"] and page["count"] == 3 and page["counts"] == {"scheduled": 3}
    assert len(page["items"]) == 2 and page["next_cursor"]
    next_page = client.get("/api/x/queue", params={"limit": 2, "cursor": page["next_cursor"]}).json()
    assert len(next_page["items"]) == 1 and not next_page["next_cursor"]
    assert {item["id"] for item in page["items"] + next_page["items"]} == own
    assert all("credential_revision" not in item and "owner" not in item for item in page["items"])
    assert client.get("/api/x/queue?cursor=invalid").status_code == 400


def test_uncertain_work_stays_visible_without_send_actions(env):
    store, _, book, client = env
    row = book("Uncertain booking", due=time.time() - 1)
    store.claim(row["id"], tenant_id="default", account_id="123", credential_revision=row["credential_revision"], owner="test")
    store.fail(row["id"], owner="test", outcome="unknown", reason="Lost remote result")
    item = client.get("/api/x/queue").json()["items"][0]
    assert item["state"] == "outcome_unknown" and item["needs_reconciliation"]
    assert not item["can_cancel"] and not item["can_reschedule"]
    refused = client.post(f'/api/x/queue/{row["id"]}/cancel', headers={"X-Requested-With": "XMLHttpRequest"},
                          json={"expected_version": item["version"]})
    assert refused.status_code == 409
    assert store.get(row["id"], tenant_id="default")["state"] == "outcome_unknown"


def test_stale_queue_action_cannot_overwrite_new_time(env):
    store, _, book, client = env
    from datetime import UTC, datetime

    row = book("Reviewed schedule")
    new_time = time.time() + 7200
    assert store.reschedule(row["id"], tenant_id="default", expected_version=1, due_at=new_time, max_day=50, max_month=500)
    url = f'/api/x/queue/{row["id"]}/reschedule'
    headers = {"X-Requested-With": "XMLHttpRequest"}
    body = {"expected_version": 1, "when": datetime.fromtimestamp(time.time() + 10800, UTC).isoformat()}
    assert client.post(url, headers=headers, json=body).status_code == 409
    assert store.get(row["id"], tenant_id="default")["due_at"] == new_time
    assert client.post(url, json=body).status_code == 403
    body["expected_version"] = 2
    assert client.post(url, headers=headers, json=body).status_code == 200


def test_account_change_holds_reschedule_but_allows_unsent_cancel(env):
    _, state, book, client = env
    row = book("Original account")
    state["cfg"] = replace(state["cfg"], account_id="999")
    item = client.get("/api/x/queue").json()["items"][0]
    assert not item["can_reschedule"] and item["can_cancel"]
    result = client.post(f'/api/x/queue/{row["id"]}/cancel', headers={"X-Requested-With": "XMLHttpRequest"},
                         json={"expected_version": 1})
    assert result.status_code == 200
    assert client.get("/api/x/queue").json()["count"] == 0
    assert client.get("/api/x/queue?include_finished=true").json()["items"][0]["state"] == "cancelled"


async def test_missed_schedule_holds_and_explicit_reschedule_releases(env, monkeypatch):
    from kazma_core.x_api.publication_service import _dispatch_operation

    store, state, book, client = env
    row = book("Missed schedule", due=time.time() - 900)
    monkeypatch.setattr("kazma_core.x_api.publication_service._client", lambda cfg: pytest.fail("Missed work must never dispatch"))
    ok, result = await _dispatch_operation(row["id"], cfg=state["cfg"])
    assert not ok
    held = store.get(row["id"], tenant_id="default")
    assert held["state"] == "awaiting_approval" and "missed" in held["reason"]
    assert held["attempts"] == 0
    assert client.get("/api/x/queue").json()["items"][0]["can_reschedule"]
    assert store.reschedule(row["id"], tenant_id="default", expected_version=held["version"],
                            due_at=time.time() + 7200, max_day=50, max_month=500)
    assert store.get(row["id"], tenant_id="default")["state"] == "scheduled"
