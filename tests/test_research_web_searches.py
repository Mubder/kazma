"""The Research page lists research runs and the chat's web searches apart.

Live 2026-10-31: every web search the chat makes is recorded as a research
session (depth "chat"), and the page asked for the newest 30 sessions: 26 of
them were chat searches, listed first as "[Chat] <query> · session · done ·
1 source", above every report. The owner read them as Kazma's research and
reported "this is not a real professional research paper". The list now asks
for each kind on its own (``kind=research`` / ``kind=search``), and a chat
search keeps how many results it returned.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest


@pytest.fixture()
def rs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import kazma_core.tools.research_session as rs

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(rs, "_db_path", lambda: data / "research_sessions.db")
    rs._SUBS.clear()
    rs._RUNNING.clear()
    return rs


def _ids(rows):
    return {row.id for row in rows}


def test_each_kind_is_listed_apart(rs):
    deep = rs.create_session("History of Kuwait", depth="deep")
    brief = rs.create_session("Kuwait pearling", depth="brief")
    chat = rs.record_chat_research("Kuwait Failaka Dilmun archaeology", result_text="## results")
    # A row from before depth was recorded: its id says it is a chat search.
    with sqlite3.connect(rs._db_path()) as connection:
        connection.execute(
            "INSERT INTO research_sessions (id, topic, depth, status, stage, created_at, updated_at)"
            " VALUES ('rs_chat_0123456789ab', 'old search', '', 'done', 'complete', 1, 1)"
        )
    connection.close()

    research = _ids(rs.list_sessions(limit=50, kind="research"))
    searches = _ids(rs.list_sessions(limit=50, kind="search"))
    assert research == {deep.id, brief.id}
    assert searches == {chat.id, "rs_chat_0123456789ab"}
    assert _ids(rs.list_sessions(limit=50)) == research | searches
    with pytest.raises(ValueError):
        rs.list_sessions(kind="papers")


def test_chat_searches_no_longer_crowd_out_the_research(rs):
    deep = rs.create_session("History of Kuwait", depth="deep")
    for n in range(30):
        rs.record_chat_research(f"Kuwait search {n}", result_text="## results")
    # Negative control: the page's old read, the newest 30 of every kind.
    assert deep.id not in _ids(rs.list_sessions(limit=30))
    assert deep.id in _ids(rs.list_sessions(limit=30, kind="research"))
    assert len(rs.list_sessions(limit=30, kind="search")) == 30


def test_a_chat_search_keeps_its_result_count_with_its_snapshot(rs):
    first = rs.record_chat_research("Kuwait 1961", result_text="eight results", result_count=8)
    assert first.meta["results"] == 8
    assert first.meta["source"] == "chat"
    # A fuller snapshot replaces the stored one, and its count with it.
    rs.record_chat_research("Kuwait 1961", result_text="five longer results here", result_count=5)
    assert rs.get_session(first.id).meta["results"] == 5
    # A shorter one is not kept, so neither is its count.
    rs.record_chat_research("Kuwait 1961", result_text="two", result_count=2)
    stored = rs.get_session(first.id)
    assert stored.meta["results"] == 5 and stored.summary == "five longer results here"


@pytest.mark.asyncio
async def test_the_route_takes_a_kind_and_refuses_an_unknown_one(rs):
    from fastapi import FastAPI

    from kazma_ui.research_panel.routes import create_research_router

    rs.create_session("History of Kuwait", depth="deep")
    rs.record_chat_research("Kuwait search", result_text="## results", result_count=3)
    app = FastAPI()
    app.include_router(create_research_router())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
        research = (await client.get("/api/research/sessions?kind=research")).json()["sessions"]
        searches = (await client.get("/api/research/sessions?kind=search")).json()["sessions"]
        refused = await client.get("/api/research/sessions?kind=papers")
    assert [s["depth"] for s in research] == ["deep"]
    assert [s["meta"]["results"] for s in searches] == [3]
    assert refused.status_code == 400 and refused.json()["error"] == "unknown_kind"


@pytest.mark.asyncio
async def test_web_search_records_how_many_results_it_returned(rs, monkeypatch):
    import kazma_core.tools.web_search as web_search
    import kazma_core.web_acquire.search_runtime as search_runtime

    async def fake_run_search(_run, query, max_results):
        results = [
            {"title": f"Kuwait page {n}", "href": f"https://example.org/{n}", "body": "Kuwait"}
            for n in range(4)
        ]
        return results, ["fake:ok"], "fake"

    monkeypatch.setattr(search_runtime, "run_search", fake_run_search)
    await web_search.web_search("Kuwait history", max_results=8)
    row, = rs.list_sessions(kind="search")
    assert row.topic == "Kuwait history" and row.meta["results"] == 4
