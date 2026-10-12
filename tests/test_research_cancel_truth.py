"""Cancellation reports cleanup truth against real SQLite and owned workers."""
import asyncio
import threading

import httpx
import pytest
from fastapi import FastAPI

from kazma_core.tools import research_session as sessions, research_pipeline as pipeline
from kazma_core.web_acquire import resources, crawl_jobs


@pytest.fixture
def controlled(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, '_db_path', lambda: tmp_path / 'sessions.db')
    monkeypatch.setattr(resources, '_RESEARCH_SESSIONS', resources._CapacityPool('background research sessions', 1, 0))
    monkeypatch.setattr('kazma_core.workspace.binding.resolve_active_root', lambda: tmp_path)
    monkeypatch.setattr(sessions, '_RUNNING', {})
    monkeypatch.setattr(sessions, '_SUBS', {})


async def wait_thread(event):
    async with asyncio.timeout(10):
        while not event.is_set():
            await asyncio.sleep(.01)


@pytest.mark.asyncio
@pytest.mark.parametrize('fixed', [True, False], ids=['joined-status', 'original-premature-status'])
async def test_held_worker_remains_visible_and_owned_until_cleanup(controlled, monkeypatch, fixed):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    def worker():
        started.set()
        assert release.wait(15)
        finished.set()
    async def run(*args, **kwargs):
        await crawl_jobs.call(worker)
        return 'fixture report'
    monkeypatch.setattr(pipeline, 'run_research_pipeline', run)
    if not fixed:
        def original_cancel(sid):
            task = sessions._RUNNING.get(sid)
            if task is not None and not task.done():
                sessions._cancel_on_its_loop(task)
            return sessions.update_session(sid, status='cancelled', stage='cancelled')
        def original_delete(sid):
            sessions._RUNNING.pop(sid, None)
            connection = sessions._conn()
            try:
                connection.execute('DELETE FROM research_sessions WHERE id=?', (sid,))
                connection.commit()
                return True
            finally:
                connection.close()
        monkeypatch.setattr(sessions, 'cancel_session', original_cancel)
        monkeypatch.setattr(sessions, 'delete_session', original_delete)
    session = await sessions.start_deep_research('held cleanup')
    task = sessions._RUNNING[session.id]
    queue = sessions.subscribe_progress(session.id)
    await queue.get()
    try:
        await wait_thread(started)
        from kazma_ui.research_panel.routes import create_research_router
        app = FastAPI()
        app.include_router(create_research_router())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://fixture') as client:
            cancelled = await client.post(f'/api/research/sessions/{session.id}/cancel')
            assert cancelled.status_code == 200
            assert cancelled.json()['session']['status'] == ('cancelling' if fixed else 'cancelled')
            # A duplicate request must not create a second cleanup owner.
            again = await client.post(f'/api/research/sessions/{session.id}/cancel') if fixed else cancelled
            assert again.json()['session']['status'] == ('cancelling' if fixed else 'cancelled')
            deleted = await client.delete(f'/api/research/sessions/{session.id}')
            assert deleted.status_code == (409 if fixed else 200)
            if fixed:
                assert deleted.json()['error_code'] == 'research_session_busy'
                late = await asyncio.to_thread(sessions.update_session, session.id,
                    status='running', stage='fetch', message='late progress')
                assert late.status == late.stage == 'cancelling'
                assert sessions.get_session(session.id) is not None
            else:
                assert sessions.get_session(session.id) is None
            assert not task.done() and not finished.is_set()
            assert resources.research_session_capacity().snapshot()['active'] == 1
            # No terminal event is permitted while the worker is held.
            if fixed:
                while not queue.empty():
                    event = queue.get_nowait()
                    assert event['type'] != 'done' and event.get('status') != 'cancelled'
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert finished.is_set()
    assert resources.research_session_capacity().snapshot()['active'] == 0
    if fixed:
        assert sessions.get_session(session.id).status == 'cancelled'
        assert sessions.delete_session(session.id)


@pytest.mark.asyncio
async def test_pipeline_that_suppresses_cancel_cannot_report_success(controlled, monkeypatch):
    entered = asyncio.Event()
    async def suppress(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return 'a success after cancellation'
    monkeypatch.setattr(pipeline, 'run_research_pipeline', suppress)
    session = await sessions.start_deep_research('suppressed')
    task = sessions._RUNNING[session.id]
    await asyncio.wait_for(entered.wait(), 10)
    cancelled = await asyncio.to_thread(sessions.cancel_session, session.id)
    assert cancelled.status == 'cancelling'
    await task
    assert sessions.get_session(session.id).status == 'cancelled'
    assert resources.research_session_capacity().snapshot()['active'] == 0


def test_terminal_commit_wins_and_archiving_remains_available(controlled):
    session = sessions.create_session('committed')
    sessions.update_session(session.id, status='done', message='Complete', report_path='saved/report.md')
    cancelled = sessions.cancel_session(session.id)
    assert cancelled.status == 'done'
    late = sessions.update_session(session.id, status='running', message='stale', report_path='')
    assert late.status == 'done' and late.report_path == 'saved/report.md'
    assert sessions.archive_session(session.id).archived


@pytest.mark.asyncio
async def test_pre_start_cancellation_retains_lease_through_terminal_commit(controlled, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = sessions._complete_cancellation
    def held(sid):
        started.set()
        assert release.wait(15)
        return original(sid)
    monkeypatch.setattr(sessions, '_complete_cancellation', held)
    session = await sessions.start_deep_research('cancel before execution')
    task = sessions._RUNNING[session.id]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    try:
        await wait_thread(started)
        owner = sessions._RUNNING[session.id]
        assert owner is not task and not owner.done()
        assert resources.research_session_capacity().snapshot()['active'] == 1
        with pytest.raises(sessions.ResearchSessionBusyError):
            await asyncio.to_thread(sessions.delete_session, session.id)
        assert sessions.get_session(session.id).status == 'cancelling'
    finally:
        release.set()
        await sessions._RUNNING[session.id]
    assert sessions.get_session(session.id).status == 'cancelled'
    assert resources.research_session_capacity().snapshot()['active'] == 0
