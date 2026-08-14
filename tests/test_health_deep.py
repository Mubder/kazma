"""Tests for the deep health canary (GET /health/deep).

The canary exists to catch SILENT failures (the recall NameError that was
swallowed for a day, dangling-import breakage, read-only settings) — each
check exercises one real roundtrip. These tests run the canary against a
minimal FastAPI app under the suite's isolated config/memory fixtures.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_ui import health as health_mod
from kazma_ui.health import router as health_router


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(health_router)
    # Reset the TTL cache so each test computes fresh results.
    health_mod._deep_cache.update(ts=0.0, payload=None)
    return TestClient(app)


def test_deep_canary_healthy(client):
    resp = client.get("/health/deep")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "healthy"
    assert not data.get("cached")  # fresh (not TTL-cached) response
    for name in (
        "config_roundtrip",
        "memory_recall",
        "workspace_binding",
        "research_stack",
        "brain_imports",
        "database",
    ):
        assert name in data["checks"], f"missing check: {name}"
        assert data["checks"][name]["status"] in ("ok", "degraded"), (
            f"{name}: {data['checks'][name]}"
        )
    # The canary's own checks report latency (check_database is reused from
    # the structural health module and does not).
    for name in (
        "config_roundtrip",
        "memory_recall",
        "workspace_binding",
        "research_stack",
        "brain_imports",
    ):
        assert "latency_ms" in data["checks"][name]


def test_deep_canary_ttl_cache(client):
    first = client.get("/health/deep").json()
    second = client.get("/health/deep")
    assert second.status_code == 200
    data = second.json()
    assert data["cached"] is True
    assert 0 <= data["age_seconds"] < _TTL_WINDOW
    # Cached payload carries the same checks as a fresh one.
    assert set(data["checks"]) == set(first["checks"])


_TTL_WINDOW = 35  # slightly above the 30s cache window


def test_deep_canary_unhealthy_on_failed_check(client, monkeypatch):
    """One failed check → 503 + status unhealthy (alerting-friendly)."""
    monkeypatch.setattr(
        health_mod,
        "_check_workspace_binding",
        lambda: {"status": "failed", "component": "workspace_binding", "error": "boom"},
    )
    resp = client.get("/health/deep")
    assert resp.status_code == 503
    data = resp.json()
    assert data["ok"] is False
    assert data["status"] == "unhealthy"
    assert data["failed"] == ["workspace_binding"]


def test_config_roundtrip_cleans_up_after_itself(client):
    """The canary must not leave its probe key behind in the store."""
    client.get("/health/deep")
    from kazma_core.config_store import get_config_store

    assert get_config_store().get("system.canary.config_roundtrip") is None
