"""A recall that fails says so; it is never an empty memory.

Audit 2026-10-26 AUD-026: every failure branch of ``recall()`` (a locked or
corrupt memory database, a Postgres-primary outage) returned a plain
``RecallResult([], [])``. ``memory_search`` then answered "No relevant
memories found." and the model told the user it remembered nothing (§26 G,
Memory audit M11). Now each branch returns ``degraded="recall_failed:<code>"``
and the tool says recall is unavailable. The failure is made INSIDE recall
(``_recall_episodes``, the Postgres backend), where it really happens.
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from tests._no_models import finish_turn_memory, keep_models_out


@pytest.fixture(autouse=True)
def _no_models(monkeypatch):
    keep_models_out(monkeypatch, llm=False)
    yield
    finish_turn_memory()


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    ensure_primary_schema(db)
    db.commit()
    db.close()
    return SimpleNamespace(dir=tmp_path)


def _locked(*_a, **_k):
    raise sqlite3.OperationalError("database is locked")


def _memory_search(query: str) -> str:
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    return asyncio.run(registry.get_tool("memory_search").func(query=query))


def test_a_locked_database_is_a_failed_recall(mem, monkeypatch):
    from kazma_core.memory.recall import RECALL_FAILED, recall

    monkeypatch.setattr("kazma_core.memory.recall._recall_episodes", _locked)
    result = recall("where do I live", tenant_id="default")
    assert result.empty
    assert result.failed
    assert result.degraded == f"{RECALL_FAILED}search_failed"


def test_an_unavailable_postgres_primary_is_a_failed_recall(mem, monkeypatch):
    from kazma_core.memory.recall import recall

    monkeypatch.setattr("kazma_core.memory.state_backend.is_state_primary", lambda: True)
    monkeypatch.setattr(
        "kazma_core.memory.state_backend.get_state_backend",
        lambda: SimpleNamespace(name="postgres", available=False),
    )
    result = recall("where do I live", tenant_id="default")
    assert result.failed
    assert result.degraded.endswith("primary_unavailable")


def test_a_failing_postgres_primary_search_is_a_failed_recall(mem, monkeypatch):
    from kazma_core.memory.recall import recall

    monkeypatch.setattr("kazma_core.memory.state_backend.is_state_primary", lambda: True)
    monkeypatch.setattr(
        "kazma_core.memory.state_backend.get_state_backend",
        lambda: SimpleNamespace(name="postgres", available=True),
    )
    monkeypatch.setattr("kazma_core.memory.recall._pg_primary_episodes", _locked)
    result = recall("where do I live", tenant_id="default")
    assert result.failed
    assert result.degraded.endswith("primary_search_failed")


def test_no_memory_yet_is_not_a_failure(tmp_path, monkeypatch):
    """A fresh install's first question: nothing remembered, nothing failed."""
    from kazma_core.memory.recall import recall

    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "absent.db"))
    result = recall("where do I live", tenant_id="default")
    assert result.empty and not result.failed and result.degraded is None


def test_memory_search_says_recall_failed(mem, monkeypatch):
    monkeypatch.setattr("kazma_core.memory.recall._recall_episodes", _locked)
    out = _memory_search("where do I live")
    assert "NOT an empty result" in out and "recall_failed:search_failed" in out
    assert "No relevant memories found." not in out
    assert "keyword matches only" not in out


def test_negative_control_the_old_failure_read_as_nothing_remembered(mem, monkeypatch):
    """The old failure branches returned a plain empty result: the same
    locked database then reads as "No relevant memories found."."""
    from kazma_core.memory.recall import RecallResult

    monkeypatch.setattr("kazma_core.memory.recall._recall_episodes", _locked)
    monkeypatch.setattr(
        "kazma_core.memory.recall._failed_recall", lambda _code: RecallResult([], []),
    )
    assert _memory_search("where do I live") == _fenced_nothing_found()


def test_the_deep_canary_fails_on_a_failed_recall(mem, monkeypatch):
    """/health/deep's recall check exists to catch silent no-ops (§24 E):
    a failed recall is a failed check, not "ok, 0 hits"."""
    from kazma_ui.health import _check_memory_recall

    monkeypatch.setattr("kazma_core.memory.recall._recall_episodes", _locked)
    check = asyncio.run(_check_memory_recall())
    assert check["status"] == "failed"
    assert "recall_failed:" in check["error"]


def _fenced_nothing_found() -> str:
    from kazma_core.agent.tool_builtins.memory import _fence_result

    return _fence_result("No relevant memories found.", own=True)
