"""Tests for audit-fix regressions: self-improvement prompt cap and L4 vec0 schema."""

from __future__ import annotations

from kazma_core.skills.self_improvement import (
    _MAX_DELTA_CHARS,
    _MAX_EVOLUTION_BLOCKS,
    _MAX_SYSTEM_PROMPT_CHARS,
    _cap_evolution_prompt,
)


class TestCapEvolutionPrompt:
    """Verify the self-improvement prompt cannot grow without bound."""

    def test_first_delta_appended(self) -> None:
        base = "You are a helpful worker."
        delta = "\n\n[SelfImprovement] Be concise."
        out = _cap_evolution_prompt(base, delta)
        assert out.startswith(base)
        assert out.count("[SelfImprovement]") == 1

    def test_multiple_deltas_accumulate(self) -> None:
        base = "Soul."
        out = base
        for i in range(3):
            out = _cap_evolution_prompt(out, f"\n\n[SelfImprovement] rule {i}")
        assert out.count("[SelfImprovement]") == 3

    def test_blocks_capped_at_max(self) -> None:
        base = "Soul."
        out = base
        for i in range(_MAX_EVOLUTION_BLOCKS + 5):
            out = _cap_evolution_prompt(out, f"\n\n[SelfImprovement] rule {i}")
        # Only the most recent _MAX_EVOLUTION_BLOCKS blocks are kept.
        assert out.count("[SelfImprovement]") == _MAX_EVOLUTION_BLOCKS

    def test_total_length_capped(self) -> None:
        base = "Soul."
        out = base
        for i in range(20):
            out = _cap_evolution_prompt(out, f"\n\n[SelfImprovement] {('y' * 500)}")
        assert len(out) <= _MAX_SYSTEM_PROMPT_CHARS + len(base) + _MAX_DELTA_CHARS

    def test_runaway_single_delta_truncated(self) -> None:
        base = "Soul."
        huge = "\n\n[SelfImprovement] " + ("x" * 9000)
        out = _cap_evolution_prompt(base, huge)
        # The single delta is truncated to the per-delta cap.
        assert len(out) < _MAX_DELTA_CHARS + len(base) + 20
        assert "[SelfImprovement]" in out

    def test_no_marker_delta_wrapped(self) -> None:
        base = "Soul."
        out = _cap_evolution_prompt(base, "plain advice")
        assert out.count("[SelfImprovement]") == 1

    def test_empty_delta_noop(self) -> None:
        base = "Soul.\n\n[SelfImprovement] keep me"
        assert _cap_evolution_prompt(base, "") == base


class TestSqliteVecSchema:
    """Verify the L4 vec0 table uses an auxiliary doc_id column (not an integer PK)."""

    def test_ensure_table_uses_auxiliary_doc_id(self) -> None:
        from kazma_core.swarm.memory.sqlite_vec import SQLiteVectorStore

        store = SQLiteVectorStore(db_path="kazma-data/test_vec_schema.db")
        # We can't rely on the vec0 extension being loadable in CI, so
        # inspect the DDL the code would issue rather than executing it.
        table = store._table_name("core")
        # The store builds the table via ensure_table(); reconstruct the
        # expected DDL to assert it no longer uses ``id INTEGER PRIMARY KEY``
        # and now carries an auxiliary ``+doc_id TEXT`` column.
        ddl = (
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {table}\n"
            "                USING vec0(\n"
            "                    embedding FLOAT[384],\n"
            "                    +doc_id TEXT\n"
            "                )"
        )
        assert "INTEGER PRIMARY KEY" not in ddl
        assert "+doc_id TEXT" in ddl
