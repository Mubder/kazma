"""Tenant binding tests for V2 memory HTTP search routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

_JWT_SECRET = "memory-route-test-secret-with-adequate-length"


def _tenant_client(monkeypatch) -> TestClient:
    """Build only the target routes with production tenant authentication."""
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.setenv("KAZMA_JWT_SECRET", _JWT_SECRET)

    from kazma_ui.auth import create_tenant_middleware
    from kazma_ui.routes_direct import register_direct_routes

    app = FastAPI()
    app.middleware("http")(create_tenant_middleware())
    register_direct_routes(SimpleNamespace(app=app))
    return TestClient(app)


def _tenant_headers(tenant_id: str) -> dict[str, str]:
    token = jwt.encode(
        {
            "tenant_id": tenant_id,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        _JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-ID": "other-tenant"}


def test_federated_search_uses_authenticated_tenant_not_json(monkeypatch) -> None:
    captured: list[str] = []

    def fake_federated_search(query: str, **kwargs):
        captured.append(kwargs["tenant_id"])
        return {"ok": True, "query": query, "hits": [], "summary": {}}

    monkeypatch.setattr(
        "kazma_core.memory.federated_search.federated_search",
        fake_federated_search,
    )
    response = _tenant_client(monkeypatch).post(
        "/api/memory/v2/federated-search",
        headers=_tenant_headers("authenticated-tenant"),
        json={"query": "private fact", "tenant_id": "attacker-tenant"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert captured == ["authenticated-tenant"]


def test_probe_uses_authenticated_tenant_not_json(monkeypatch) -> None:
    captured: list[str] = []

    def fake_recall(query: str, **kwargs):
        captured.append(kwargs["tenant_id"])
        return SimpleNamespace(empty=False, beliefs=[], episodes=[])

    monkeypatch.setattr("kazma_core.memory.recall.recall", fake_recall)
    response = _tenant_client(monkeypatch).post(
        "/api/memory/v2/probe",
        headers=_tenant_headers("authenticated-tenant"),
        json={"query": "private fact", "tenant_id": "attacker-tenant"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert captured == ["authenticated-tenant"]
