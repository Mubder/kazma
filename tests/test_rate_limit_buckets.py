"""Rate-limit buckets on paid endpoints (audit — M14 follow-up).

Each new bucket gets one representative endpoint exercised through a REAL
ASGI app (FastAPI TestClient): a low per-minute limit must produce HTTP 429
with the bucket named in the detail. Documents/IDE routers are mounted
standalone; the backup/admin_ops routes are registered onto a fresh app via
``register_direct_routes`` with a dummy builder (routes only reference
``self.app`` at registration time). Lazy-imported handler dependencies are
stubbed via ``sys.modules`` entries so heavy handlers execute harmlessly on
their budgeted first call.
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kazma_ui.rate_limit as rl
from kazma_ui.documents_api import create_documents_router
from kazma_ui.ide_api import create_ide_router


@pytest.fixture(autouse=True)
def _force_limiter_on(monkeypatch: pytest.MonkeyPatch):
    """The limiter self-disables without KAZMA_SECRET / in demo mode."""
    monkeypatch.setattr(rl, "_enabled", lambda: True)
    rl._windows.clear()
    yield
    rl._windows.clear()


def _documents_app() -> FastAPI:
    app = FastAPI()
    app.include_router(create_documents_router())
    return app


def _ide_app() -> FastAPI:
    app = FastAPI()
    app.include_router(create_ide_router())
    return app


def _direct_routes_app() -> FastAPI:
    from kazma_ui.routes_direct import register_direct_routes

    class _Builder:
        pass

    builder = _Builder()
    builder.app = FastAPI()
    register_direct_routes(builder)
    return builder.app


def test_documents_bucket_429_at_low_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rl, "_per_minute", lambda bucket, default: 1)
    client = TestClient(_documents_app())
    headers = {"X-Document-Filename": "probe.txt"}
    first = client.post("/api/documents", headers=headers, content=b"x")
    assert first.status_code != 429  # allowed once
    second = client.post("/api/documents", headers=headers, content=b"x")
    assert second.status_code == 429
    assert "documents" in second.json()["detail"]
    assert "Retry-After" in second.headers


def test_ide_exec_bucket_429_at_low_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rl, "_per_minute", lambda bucket, default: 1)
    client = TestClient(_ide_app())
    payload = {}  # handler short-circuits on missing 'command'
    first = client.post("/api/ide/run", json=payload)
    assert first.status_code != 429
    second = client.post("/api/ide/run", json=payload)
    assert second.status_code == 429
    assert "ide_exec" in second.json()["detail"]


def test_backup_bucket_429_at_low_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rl, "_per_minute", lambda bucket, default: 1)

    # Stub the lazily-imported universal-backup module so the budgeted first
    # POST /api/backup/now performs no real work.
    stub = ModuleType("kazma_core.backup.universal")
    stub._backup_progress = {"phase": "idle"}  # type: ignore[attr-defined]
    stub.get_backup_progress = lambda: {"phase": "done"}  # type: ignore[attr-defined]
    stub.backup_progress_is_stale = lambda: False  # type: ignore[attr-defined]
    stub.perform_universal_backup = lambda **kw: {}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kazma_core.backup.universal", stub)

    client = TestClient(_direct_routes_app())
    first = client.post("/api/backup/now")
    assert first.status_code == 200
    assert first.json().get("ok") is True
    second = client.post("/api/backup/now")
    assert second.status_code == 429
    assert "backup" in second.json()["detail"]
    third = client.post("/api/backup/1750000000/archive")  # same bucket window
    assert third.status_code == 429


def test_admin_ops_bucket_429_at_low_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rl, "_per_minute", lambda bucket, default: 1)

    # Stub the lazily-imported durable queue so reconsolidation enqueues nothing.
    stub = ModuleType("kazma_core.memory.task_queue")
    stub.enqueue_task = lambda *a, **kw: "stub-task-id"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kazma_core.memory.task_queue", stub)

    client = TestClient(_direct_routes_app())
    first = client.post("/api/memory/v2/reconsolidate")
    assert first.status_code == 200
    second = client.post("/api/memory/v2/reconsolidate")
    assert second.status_code == 429
    assert "admin_ops" in second.json()["detail"]
