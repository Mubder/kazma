"""Every memory and weekly summary can be reached from the Memory page
(Memory audit M23).

The page listed the newest 30 memories and 40 summaries, and Forget was only
on those rows: an older memory could be forgotten only by the API or by the
chat it came from. The two routes now page (``offset``, ``total``), the
memories route searches by every word typed (``q``), and search results carry
a Forget button. The browser half is ``tests/e2e/test_memory_forget_older.py``.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONSOLE_JS = ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "memory_console.js"


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
    monkeypatch.setattr("kazma_core.tenant_isolation.multi_user_or_production", lambda: False)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    dual_write._reset_mirror()
    yield SimpleNamespace(db=db, mp=monkeypatch)
    dual_write._reset_mirror()
    db.close()


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import memory_api
    from kazma_ui.routes_direct import register_direct_routes

    app = FastAPI()
    register_direct_routes(SimpleNamespace(app=app))
    memory_api.mount_memory_api(app)
    return TestClient(app)


def _seed_episodes(db, n: int) -> None:
    now = time.time()
    for i in range(n):
        question = f"note number {i} about the garden"
        if i == 0:
            question = "my locker code is 4417"
        elif i % 3 == 0:
            question = f"the code review for module {i}"
        db.execute(
            "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
            "created_at) VALUES (?, 'default', ?, ?, ?, 'Noted.', 'episodic', ?)",
            (f"ep-{i:03d}", f"chat-{i}", 1, question, now - (n - i) * 60),
        )
    db.commit()


def _seed_summaries(db, n: int) -> None:
    week = 7 * 86400
    base = time.time() - (n + 2) * week
    for i in range(n):
        start = base + i * week
        db.execute(
            "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, title, "
            "summary_text, status, turn_count, created_at, updated_at) VALUES (?, 'default', ?, ?, ?, ?, ?, "
            "'active', 4, ?, ?)",
            (f"sum-{i:03d}", f"w{i}", start, start + week, f"Topic {i}", f"Week {i} in short.", start, start),
        )
    db.commit()


def _walk(client, path: str, page: int, key: str, **params) -> tuple[list[str], int]:
    seen: list[str] = []
    offset, total = 0, None
    while total is None or offset < total:
        body = client.get(path, params={"limit": page, "offset": offset, **params}).json()
        total = body["total"]
        rows = body[key]
        if not rows:
            break
        seen.extend(r["id"] for r in rows)
        offset += len(rows)
    return seen, int(total or 0)


def test_every_memory_is_reached_page_by_page(mem):
    _seed_episodes(mem.db, 75)
    seen, total = _walk(_client(), "/api/memory/v2/episodes", 30, "episodes")
    assert total == 75
    assert sorted(seen) == [f"ep-{i:03d}" for i in range(75)]
    assert seen[0] == "ep-074" and seen[-1] == "ep-000"  # newest first


def test_the_first_page_alone_misses_the_oldest(mem):
    """Negative control: the page's old request (newest 30) never held the
    oldest memory, so its Forget button never existed."""
    _seed_episodes(mem.db, 75)
    body = _client().get("/api/memory/v2/episodes", params={"limit": 30}).json()
    assert "ep-000" not in {e["id"] for e in body["episodes"]}


def test_every_summary_is_reached_page_by_page(mem):
    _seed_summaries(mem.db, 45)
    seen, total = _walk(_client(), "/api/memory/v2/summaries", 40, "summaries")
    assert total == 45
    assert sorted(seen) == [f"sum-{i:03d}" for i in range(45)]
    first = _client().get("/api/memory/v2/summaries", params={"limit": 40}).json()
    assert "sum-000" not in {s["id"] for s in first["summaries"]}  # the old page's reach


def test_the_search_finds_a_memory_by_every_word(mem):
    _seed_episodes(mem.db, 75)
    body = _client().get("/api/memory/v2/episodes", params={"limit": 30, "q": "locker code"}).json()
    assert [e["id"] for e in body["episodes"]] == ["ep-000"] and body["total"] == 1
    # Negative control: recall's expression ORs the words, and newest first
    # the 25 memories saying "code" fill the page before the one asked for.
    from kazma_core.memory.recall import _fts_match_query

    rows = mem.db.execute(
        "SELECT e.id FROM episodes e WHERE e.rowid IN (SELECT rowid FROM episodes_fts WHERE episodes_fts MATCH ?) "
        "ORDER BY e.created_at DESC LIMIT 10", (_fts_match_query("locker code"),)).fetchall()
    assert "ep-000" not in {r[0] for r in rows}


def test_a_search_with_no_content_word_lists_nothing(mem):
    _seed_episodes(mem.db, 5)
    body = _client().get("/api/memory/v2/episodes", params={"q": "the and of"}).json()
    assert body["episodes"] == [] and body["total"] == 0


def test_arabic_search_finds_its_memory(mem):
    now = time.time()
    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, created_at) "
        "VALUES ('ep-ar', 'default', 'chat-ar', 1, 'رقم خزانتي في النادي 4417', 'تم.', 'episodic', ?)", (now,))
    mem.db.commit()
    body = _client().get("/api/memory/v2/episodes", params={"q": "خزانتي النادي"}).json()
    assert [e["id"] for e in body["episodes"]] == ["ep-ar"]


# ── the page: every list with a Forget button pages ──────────────────────


def _functions_rendering(js: str, marker: str) -> list[str]:
    """The bodies of the top-level ``async function``s whose markup holds *marker*."""
    out = []
    for m in re.finditer(r"\n  async function (\w+)\([^)]*\) \{\n(.*?)\n  \}(?=\n)", js, re.S):
        if marker in m.group(2):
            out.append(m.group(2))
    return out


def _lists_page(js: str) -> list[str]:
    problems = []
    for marker, route in (("v2-memory-forget", "/api/memory/v2/episodes"),
                          ("v2-summary-forget", "/api/memory/v2/summaries")):
        bodies = _functions_rendering(js, f"class=\"btn btn-sm {marker}") or _functions_rendering(js, marker)
        if not bodies:
            problems.append(f"no list renders {marker}")
        for body in bodies:
            fetches = re.findall(r"fetch\(([^;]+)\)", body)
            if not any(route in f and "offset=" in f for f in fetches) and not (
                    route in body and "offset=" in body):
                problems.append(f"{marker}: its list loads {route} without an offset")
    return problems


def test_every_forget_list_on_the_page_pages():
    assert _lists_page(CONSOLE_JS.read_text(encoding="utf-8")) == []


def test_the_page_gate_sees_a_list_that_does_not_page():
    js = CONSOLE_JS.read_text(encoding="utf-8")
    old = re.sub(r"'/api/memory/v2/episodes\?limit=' \+ _MEM_PAGE \+ '&offset=' \+ _memOffset \+",
                 "'/api/memory/v2/episodes?limit=30' +", js)
    assert old != js
    assert any("v2-memory-forget" in p for p in _lists_page(old))


def test_search_results_offer_forget():
    """A memory or summary a search found can be forgotten where it shows."""
    js = CONSOLE_JS.read_text(encoding="utf-8")
    assert "_hitForget('episode', h.id)" in js and "_hitForget('summary', h.id)" in js
    assert js.count("_wireHitForgets(out)") >= 2
