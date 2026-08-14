"""R3: durable research sessions + progress broadcast."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture()
def session_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point research_sessions.db at a temp data dir."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(
        "kazma_core.tools.research_session.data_dir",
        lambda: str(data),
        raising=False,
    )

    # paths.data_dir may not exist on module — patch _db_path instead
    def _db_path():
        data.mkdir(parents=True, exist_ok=True)
        return data / "research_sessions.db"

    import kazma_core.tools.research_session as rs

    monkeypatch.setattr(rs, "_db_path", _db_path)
    # clear in-memory subs/running between tests
    rs._SUBS.clear()
    rs._RUNNING.clear()
    return rs


def test_create_get_list_session(session_db):
    rs = session_db
    s = rs.create_session("Python GIL", depth="deep", max_sources=6)
    assert s.id.startswith("rs_")
    assert s.topic == "Python GIL"
    assert s.status == "pending"
    assert s.max_sources == 6

    got = rs.get_session(s.id)
    assert got is not None
    assert got.topic == "Python GIL"

    listed = rs.list_sessions(limit=10)
    assert any(x.id == s.id for x in listed)


def test_update_session_broadcasts(session_db):
    rs = session_db
    s = rs.create_session("topic")
    q = rs.subscribe_progress(s.id)
    # snapshot already in queue
    snap = q.get_nowait()
    assert snap["type"] == "snapshot"
    assert snap["session"]["id"] == s.id

    rs.update_session(s.id, status="running", stage="plan", message="Planning…")
    ev = q.get_nowait()
    assert ev["type"] == "progress"
    assert ev["stage"] == "plan"
    assert ev["status"] == "running"
    rs.unsubscribe_progress(s.id, q)


@pytest.mark.asyncio
async def test_start_deep_research_success(session_db):
    rs = session_db

    async def fake_pipeline(topic, depth="deep", max_sources=8, progress_cb=None, export_docx=False, **kw):
        if progress_cb:
            await progress_cb("plan", "Planning research on: " + topic)
            await progress_cb("discover", "Found 3 candidate URLs")
            await progress_cb("done", "Report ready: research/reports/x/report.md")
        return (
            "# Deep research complete\n\n"
            f"**Topic:** {topic}\n"
            "**Sources acquired:** 4\n"
            "**Report:** `research/reports/x/report.md`\n"
            "**Rubric:** 82/100 (pass)\n"
        )

    with patch(
        "kazma_core.tools.research_pipeline.run_research_pipeline",
        new=fake_pipeline,
    ):
        sess = await rs.start_deep_research("asyncio patterns", depth="brief", max_sources=4)
        assert sess.id
        # wait for background task
        task = rs._RUNNING.get(sess.id)
        if task:
            await asyncio.wait_for(task, timeout=5)

    final = rs.get_session(sess.id)
    assert final is not None
    assert final.status == "done"
    assert final.report_path == "research/reports/x/report.md"
    assert final.sources == 4
    assert final.rubric_score == 82.0
    assert final.rubric_ok is True
    assert any("plan" in line for line in final.log)


@pytest.mark.asyncio
async def test_start_deep_research_error_string(session_db):
    rs = session_db

    async def fake_pipeline(*a, progress_cb=None, **kw):
        if progress_cb:
            await progress_cb("acquire", "Fetching…")
        return "Error: no search backends available"

    with patch(
        "kazma_core.tools.research_pipeline.run_research_pipeline",
        new=fake_pipeline,
    ):
        sess = await rs.start_deep_research("fail me")
        task = rs._RUNNING.get(sess.id)
        if task:
            await asyncio.wait_for(task, timeout=5)

    final = rs.get_session(sess.id)
    assert final is not None
    assert final.status == "error"
    assert "no search" in (final.error or "").lower() or "Error" in (final.summary or "")


@pytest.mark.asyncio
async def test_start_empty_topic(session_db):
    rs = session_db
    sess = await rs.start_deep_research("   ")
    final = rs.get_session(sess.id)
    assert final is not None
    assert final.status == "error"


def test_session_router_import():
    from kazma_ui.research_panel.routes import create_research_router

    router = create_research_router()
    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/api/research/sessions" in paths
    assert "/api/research/sessions/{session_id}" in paths
    assert "/api/research/sessions/{session_id}/stream" in paths
    assert "/api/research/sessions/{session_id}/cancel" in paths
    # Sessions export through their OWN endpoint — the swarm-task export
    # path 404'd for them (different store).
    assert "/api/research/sessions/{session_id}/export" in paths
    assert "/api/research/eval" in paths


def test_record_chat_research_persists_full_output(session_db):
    """The old [:500] write-time cap discarded the full chat-tool output —
    the detail view could never show more than a teaser."""
    rs = session_db
    long_result = "R" * 5000 + " (end)"
    s = rs.record_chat_research("full output test", result_text=long_result)
    assert len(s.summary) == len(long_result)
    fetched = rs.get_session(s.id)
    assert fetched is not None
    assert fetched.summary == long_result


def test_record_chat_research_refreshes_on_longer_result(session_db):
    """Re-querying the same topic with a LONGER result refreshes the stored
    snapshot (richest wins); a shorter one does not clobber it."""
    rs = session_db
    s1 = rs.record_chat_research("refresh topic", result_text="short result")
    assert s1.summary == "short result"

    longer = "L" * 800 + " fuller answer"
    s2 = rs.record_chat_research("refresh topic", result_text=longer)
    assert s2.summary == longer

    s3 = rs.record_chat_research("refresh topic", result_text="tiny")
    assert s3.summary == longer  # shorter result must NOT overwrite


def test_record_chat_research_caps_at_bound(session_db):
    """The snapshot is generous but bounded (200K chars)."""
    rs = session_db
    s = rs.record_chat_research("cap topic", result_text="x" * (rs._CHAT_RESULT_MAX + 5000))
    assert len(s.summary) == rs._CHAT_RESULT_MAX


def test_cancel_session(session_db):
    rs = session_db
    s = rs.create_session("cancel me")
    rs.update_session(s.id, status="running", stage="plan", message="Planning")
    out = rs.cancel_session(s.id)
    assert out is not None
    assert out.status == "cancelled"
    assert out.stage == "cancelled"


def test_evaluate_report_path(tmp_path: Path):
    from kazma_core.tools.research_eval import evaluate_report_path

    p = tmp_path / "report.md"
    p.write_text(
        "# Research report: X\n\n## Background\n\n"
        + ("body " * 400)
        + "\n\n## Key findings\n\n"
        + ("more " * 200)
        + "\n\n## Sources\n\n"
        "- https://docs.python.org/3/\n"
        "- https://peps.python.org/pep-0703/\n"
        "- https://sqlite.org/wal.html\n",
        encoding="utf-8",
    )
    d = evaluate_report_path(p, min_sources=2, min_chars=500)
    assert d["ok"] is True
    assert d["score"] >= 70
    assert d["grade"] == "pass"
