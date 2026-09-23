"""Background belief re-extraction: only the LLM call is on the event loop.

``_handle_micro_consolidation`` opened both memory databases, ran the schema
check, read the episode, ran the heuristic pre-pass and applied the beliefs
(entity resolution + mutations) inline, between awaits, on the loop that
serves every chat stream. It was the one entry in the blocking-driver gate's
allowlist ("cannot be offloaded wholesale"). The SQLite halves now run in
threads around the awaited extraction.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@pytest.fixture
def episode():
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.paths import memory_ops_db, primary_memory_db

    p = sqlite3.connect(primary_memory_db())
    ensure_primary_schema(p)
    p.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
        "tier, structural_importance, created_at) VALUES ('e1','default','s1',1,"
        "'My name is Alice and I live in Paris','Nice to meet you','episodic',1,?)",
        (time.time(),),
    )
    p.commit()
    p.close()
    o = sqlite3.connect(memory_ops_db())
    ensure_ops_schema(o)
    o.close()
    return "e1"


def test_only_the_llm_call_runs_on_the_loop(episode, monkeypatch):
    import kazma_core.memory.belief_extractor as be
    import kazma_core.memory.schema_v2 as schema
    from kazma_core.memory import worker_bootstrap as wb

    where: dict[str, bool] = {}
    real_schema, real_apply = schema.ensure_primary_schema, be._apply_beliefs_to_v2
    real_extract = be.extract_beliefs_for_turn

    def schema_spy(conn):
        where["prepare"] = _on_loop()
        return real_schema(conn)

    async def extract(user_text, assistant_text="", *, use_llm=True, ignore_filler=False):
        where["extract"] = _on_loop()
        # The real extraction with the LLM off (heuristic path, no network).
        return await real_extract(user_text, assistant_text, use_llm=False, ignore_filler=ignore_filler)

    def apply_spy(*a, **k):
        where["apply"] = _on_loop()
        return real_apply(*a, **k)

    monkeypatch.setattr(schema, "ensure_primary_schema", schema_spy)
    monkeypatch.setattr(be, "extract_beliefs_for_turn", extract)
    monkeypatch.setattr(be, "_apply_beliefs_to_v2", apply_spy)

    assert asyncio.run(wb._handle_micro_consolidation({"episode_id": episode})) is True
    assert where == {"prepare": False, "extract": True, "apply": False}


def test_extract_and_apply_beliefs_is_unchanged_for_its_callers():
    """The public async function still extracts and applies in one call."""
    from kazma_core.memory.belief_extractor import extract_and_apply_beliefs
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.paths import memory_ops_db, primary_memory_db

    p = sqlite3.connect(primary_memory_db(), isolation_level=None)
    ensure_primary_schema(p)
    o = sqlite3.connect(memory_ops_db(), isolation_level=None)
    ensure_ops_schema(o)
    stats = asyncio.run(
        extract_and_apply_beliefs(p, o, "My name is Alice and I live in Paris", use_llm=False)
    )
    assert stats["source"] == "heuristic" and stats["applied"] >= 1
    filler = asyncio.run(extract_and_apply_beliefs(p, o, "okay thanks", use_llm=False))
    assert filler["skipped_filler"] is True
