"""The Memory page's "Search both stores" says when a store's search failed.

Audit 2026-10-26 AUD-057: ``federated_search`` logged a memory search
failure at DEBUG, dropped ``RecallResult.degraded``, and still answered
``ok: True`` with no hits -- the Memory page then said "No hits in either
store." over a search that never ran. Now the response carries ``degraded``
(recall's own reason, ``recall_failed:<code>`` for a failure) and
``knowledge_error``, and the page shows a banner for each (memory_console.js
``_memoryDegradedBanner`` / ``_knowledgeFailedBanner``).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from tests._no_models import finish_turn_memory, keep_models_out

ROOT = Path(__file__).resolve().parents[1]
CONSOLE_JS = ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "memory_console.js"


@pytest.fixture(autouse=True)
def _no_models(monkeypatch):
    keep_models_out(monkeypatch, llm=False)
    yield
    finish_turn_memory()


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    db = tmp_path / "memory_state.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(db))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    conn = sqlite3.connect(str(db))
    ensure_primary_schema(conn)
    conn.commit()
    conn.close()
    return db


def _locked(*_a, **_k):
    raise sqlite3.OperationalError("database is locked")


def test_a_failed_recall_reaches_the_response(mem, monkeypatch):
    from kazma_core.memory.federated_search import federated_search

    monkeypatch.setattr("kazma_core.memory.recall._recall_episodes", _locked)
    out = federated_search("where do I live", include_knowledge=False)
    assert out["hits"] == []
    assert out["degraded"] == "recall_failed:search_failed"


def test_a_raising_memory_search_is_reported_and_logged(mem, monkeypatch, caplog):
    from kazma_core.memory.federated_search import federated_search

    def boom(*_a, **_k):
        raise RuntimeError("recall exploded")

    monkeypatch.setattr("kazma_core.memory.recall.recall", boom)
    with caplog.at_level(logging.WARNING, logger="kazma_core.memory.federated_search"):
        out = federated_search("where do I live", include_knowledge=False)
    assert out["degraded"] == "recall_failed:federated_error"
    assert any(r.levelno >= logging.WARNING and "memory search failed" in r.getMessage()
               for r in caplog.records)


def test_a_failed_knowledge_search_is_reported(mem, monkeypatch):
    from kazma_core.memory.federated_search import federated_search

    monkeypatch.setattr("kazma_core.memory.federated_search._search_knowledge", _locked)
    out = federated_search("where do I live", include_memory=False)
    assert out["knowledge_error"] == "knowledge_search_failed"


def test_a_search_that_ran_reports_nothing(mem):
    from kazma_core.memory.federated_search import federated_search

    out = federated_search("where do I live", include_knowledge=False)
    assert out["degraded"] is None and out["knowledge_error"] is None


def test_the_page_shows_a_banner_instead_of_no_hits():
    """The panel reads both fields and only says "no hits" when neither is
    set (the empty state comes after the banners' branch)."""
    js = CONSOLE_JS.read_text(encoding="utf-8")
    body = js[js.index("async function runFederatedSearch"):js.index("async function runGoldenEval")]
    assert "_memoryDegradedBanner(data.degraded)" in body
    assert "_knowledgeFailedBanner()" in body
    assert body.index("else if (banners.length)") < body.index("memory.console.no_hits_either")
    assert "recall_failed:" in js[js.index("function _memoryDegradedBanner"):]


def test_negative_control_without_the_signal_a_failure_is_no_hits(mem, monkeypatch):
    """The old recall returned a plain empty result on a failure and the old
    response had no failure field: the same locked database then produced a
    response indistinguishable from "nothing matched"."""
    from kazma_core.memory.federated_search import federated_search
    from kazma_core.memory.recall import RecallResult

    monkeypatch.setattr("kazma_core.memory.recall._recall_episodes", _locked)
    monkeypatch.setattr(
        "kazma_core.memory.recall._failed_recall", lambda _code: RecallResult([], []),
    )
    out = federated_search("where do I live", include_knowledge=False)
    assert out["ok"] is True and out["hits"] == [] and not out["degraded"]
