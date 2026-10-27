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
    # No gateway session store: one another test's app build left there was
    # opened by this delete, and its aiosqlite thread kept the process from
    # exiting (2026-09-27).
    set_dashboard_context(checkpoint_manager=manager, session_store=None)
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
    set_dashboard_context(checkpoint_manager=manager, session_store=None)
    try:
        response = await delete_session(_request(), "someone-elses-thread")
    finally:
        set_dashboard_context(checkpoint_manager=None)
    assert response.status_code == 403
    assert manager.deleted == []


@pytest.mark.asyncio
async def test_delete_session_forgets_its_time_travel_steps(tmp_path, monkeypatch) -> None:
    """(audit 2026-10-26 AUD-055) the Dashboard's delete reaches the steps.

    The chat page's delete forgot a chat's saved steps under every key it had
    (§12 G, TT-05); this route deleted the checkpoints and the chat and left
    every step of it in snapshots.db.
    """
    from kazma_core import time_travel
    from kazma_core.time_travel import SnapshotRecorder
    from kazma_ui import dashboard

    rec = SnapshotRecorder(db_path=str(tmp_path / "snapshots.db"))
    time_travel.set_process_recorder(rec)
    thread_id, session_id = "thread-aud055", "sess-aud055"
    for key in (thread_id, session_id, "someone-else"):
        state = {"thread_id": key, "iteration": 1,
                 "messages": [{"role": "user", "content": "hello"}]}
        assert rec.capture(state) is not None
    # The chat store's answer: the thread's chat was ``session_id``.
    monkeypatch.setattr(dashboard, "_delete_chats_of_thread",
                        lambda tid: sorted({tid, session_id}))

    manager = _Manager()
    set_dashboard_context(checkpoint_manager=manager, session_store=None)
    try:
        response = await delete_session(_request(), thread_id)
    finally:
        set_dashboard_context(checkpoint_manager=None)
        time_travel.set_process_recorder(None)
    assert response.status_code == 200
    assert rec.list_snapshots(thread_id) == []
    assert rec.list_snapshots(session_id) == []
    assert len(rec.list_snapshots("someone-else")) == 1
