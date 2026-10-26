"""A turn the user keeps out of memory leaves no facts (audit 2026-10-26 AUD-010).

The forget ledger kept the episode out, but the heuristic fact pass that runs
on every finished turn never asked it: "Please forget that I live in Paris"
-- forgotten, its own turn kept out -- was read as a statement and stored
``lives_in Paris`` at user trust, where it blocks later corrections. The
ledger's sweep looked at episodes only, so nothing took it back.
"""

from __future__ import annotations

import sqlite3

import pytest

from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

_ASK = "Please forget that I live in Paris."
_CHAT = "chat-keepout"


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write

    for var in ("KAZMA_PRODUCTION", "KAZMA_MULTI_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    monkeypatch.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key])
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row  # as forget._open
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    dual_write._reset_mirror()
    yield db
    dual_write._reset_mirror()
    ops.close()
    db.close()


def _paris_facts(db: sqlite3.Connection) -> list[tuple]:
    return db.execute(
        "SELECT predicate, object FROM beliefs WHERE source_session = ? AND object LIKE '%Paris%'",
        (_CHAT,),
    ).fetchall()


def _extract() -> None:
    from kazma_core.memory.consolidator import _v2_extract_sync

    _v2_extract_sync(
        [{"role": "user", "content": _ASK}, {"role": "assistant", "content": "Done."}],
        session_id=_CHAT, turn=1, tenant_id="default",
    )


def test_a_kept_out_turn_leaves_no_fact(mem) -> None:
    from kazma_core.memory.forget import keep_turn_out

    assert keep_turn_out(_CHAT, 1, _ASK, tenant_id="default")
    _extract()
    assert _paris_facts(mem) == []


def test_negative_control_without_the_ledger_row_the_fact_is_written(mem) -> None:
    """The heuristic pass does read the sentence as a fact: what the ledger
    check stops."""
    _extract()
    assert _paris_facts(mem), "the heuristic pass no longer extracts this sentence"


def test_the_ledger_sweep_takes_back_a_fact_written_before_the_forget(mem) -> None:
    from kazma_core.memory.forget import enforce_ledger, keep_turn_out

    _extract()  # the fact pass ran first...
    assert _paris_facts(mem)
    assert keep_turn_out(_CHAT, 1, _ASK, tenant_id="default")  # ...then the forget
    out = enforce_ledger(mem)
    assert out["facts"] >= 1
    assert _paris_facts(mem) == []
