"""Failures that were swallowed where the swallow hid a bug (AUD-027).

The debt ratchet counts ``except Exception: pass`` handlers; these tests hold
the ones whose silence was not just untidy but wrong -- each turned a real
failure into a false success or a quiet loss:

* the belief FTS rebuild committed inside ``try/except: pass`` and reported
  "rebuilt" when the commit failed;
* the long-task "continue" directive is cleared on every consume so a stale
  one cannot leak into a later turn (AGENTS.md §25 A) -- a failed clear said
  nothing;
* the memory routes read optional JSON bodies with ``except Exception: pass``
  and then called ``.get`` on whatever came back, so a JSON list crashed.
"""

from __future__ import annotations

import json
import logging
import sqlite3

import pytest

# ── the FTS rebuild does not report a failed commit as success ────────────


class _CommitFails:
    """A connection whose statements run and whose commit fails."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql: str, *args):
        self.executed.append(sql)

    def executescript(self, sql: str) -> None:
        self.executed.append(sql)

    def commit(self) -> None:
        raise sqlite3.OperationalError("database is locked")


def test_a_failed_commit_is_a_failed_rebuild(caplog) -> None:
    from kazma_core.memory.hygiene import rebuild_beliefs_fts

    with caplog.at_level(logging.WARNING, logger="kazma_core.memory.hygiene"):
        assert rebuild_beliefs_fts(_CommitFails()) is False
    assert any("could not recreate beliefs_fts" in r.getMessage() for r in caplog.records)


def test_a_rebuild_that_commits_is_a_rebuild(tmp_path) -> None:
    """Control: the same call on a real database reports success."""
    from kazma_core.memory.hygiene import rebuild_beliefs_fts
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    conn = sqlite3.connect(tmp_path / "m.db")
    ensure_primary_schema(conn)
    assert rebuild_beliefs_fts(conn) is True
    conn.close()


# ── a failed clear of the continue directive is said ──────────────────────


class _StoreDeleteFails:
    def __init__(self, value: dict) -> None:
        self.value = value

    def get(self, key, default=None):
        return self.value

    def delete(self, key):
        raise sqlite3.OperationalError("database is locked")


def test_a_failed_clear_of_the_continue_directive_is_logged(monkeypatch, caplog) -> None:
    from kazma_core.agent import long_task

    store = _StoreDeleteFails({"summary": "salvaged work"})
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: store)
    with caplog.at_level(logging.WARNING, logger="kazma_core.agent.long_task"):
        out = long_task.consume_continue_context("thread-1", user_text="a brand new task")
    assert out is None  # a new task is still not given the stale directive
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("thread-1" in r.getMessage() for r in warnings), caplog.text


# ── optional JSON bodies ──────────────────────────────────────────────────


def _request(body: bytes):
    from starlette.requests import Request

    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}
    return Request(scope, receive)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"", {}),
        (b"{not json", {}),
        (b"[1, 2]", {}),
        (b'"text"', {}),
        (json.dumps({"query": "hi"}).encode(), {"query": "hi"}),
    ],
)
@pytest.mark.asyncio
async def test_an_optional_body_is_an_object_or_empty(body, expected) -> None:
    from kazma_ui.routes_direct.memory import _json_object

    assert await _json_object(_request(body)) == expected
