"""Dashboard session delete must use the checkpointer's own delete.

A SQLite ``?`` statement against the Postgres pool returned HTTP 500 and
left the thread in place.
"""

from __future__ import annotations

import json

import pytest
from starlette.requests import Request

from kazma_ui.dashboard import delete_session, set_dashboard_context


class _Manager:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted.append(thread_id)

    @property
    def conn(self):  # noqa: ANN201
        raise AssertionError("raw SQL must not run when adelete_thread exists")


def _request() -> Request:
    return Request({"type": "http", "method": "DELETE", "path": "/api/sessions/x", "headers": []})


@pytest.mark.asyncio
async def test_delete_session_calls_adelete_thread() -> None:
    manager = _Manager()
    set_dashboard_context(checkpoint_manager=manager)
    try:
        response = await delete_session(_request(), "battery-20260922-parta")
    finally:
        set_dashboard_context(checkpoint_manager=None)
    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["deleted"] is True
    assert manager.deleted == ["battery-20260922-parta"]


@pytest.mark.asyncio
async def test_delete_session_requires_admin_when_auth_is_on(monkeypatch) -> None:
    """Every tenant's threads: an operator must not delete one (audit 2026-09-22)."""
    import kazma_ui.auth as auth

    monkeypatch.setattr(auth, "get_kazma_secret", lambda: "configured")
    monkeypatch.setattr(auth, "is_authenticated", lambda request, secret: True)
    monkeypatch.setattr(
        auth, "get_request_principal", lambda request: {"role": "operator", "source": "session"}
    )
    manager = _Manager()
    set_dashboard_context(checkpoint_manager=manager)
    try:
        response = await delete_session(_request(), "someone-elses-thread")
    finally:
        set_dashboard_context(checkpoint_manager=None)
    assert response.status_code == 403
    assert manager.deleted == []
