"""Cursors and budgets for the memory sweeps (the memory framework audit's
F15, part H).

Measured on 2026-11-21 (a private store at one and four times the live size,
SQLite VM steps of a pass with nothing to do): every memory sweep's idle pass
grew four times with the store. Now:

* Every memory sweep of the maintenance runner is declared with its budget
  and its cursor (``sweep_cursors.SWEEPS``), both ways; a sweep declared
  ``changes`` goes through ``idle_skip`` under its own label.
* **An idle pass does not grow with memory**: each ``changes`` sweep's second
  pass reads the same at one and four times the store. Negative control: the
  same passes without the cursor (``idle_skip`` never skipping) grow with it.
* A change after an idle pass runs the next one; a vector repair's cursor
  names the embedding model (a model switch is work).
* The runner keeps each pass's time and says a pass over its budget.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import sqlite3
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from kazma_core.memory import sweep_cursors  # noqa: E402
from kazma_core.memory import worker_bootstrap as wb  # noqa: E402

#: The memory sweeps the runner names otherwise (its order is AGENTS.md §15B).
_ALSO_MEMORY = {"forget convergence", "retired knowledge promotions", "entity count repair"}


def _memory_sweeps() -> dict[str, object]:
    """The runner's memory sweeps: named "memory ...", or reading the memory
    database (``primary_memory_db``), or one of the three named otherwise."""
    return {label: fn for label, fn in wb._MAINTENANCE_SWEEPS
            if label.startswith("memory ") or label in _ALSO_MEMORY
            or "primary_memory_db" in inspect.getsource(fn)}


def test_every_memory_sweep_is_declared_both_ways():
    runner = _memory_sweeps()
    assert set(runner) == set(sweep_cursors.SWEEPS), (
        sorted(set(runner) ^ set(sweep_cursors.SWEEPS)))
    for label, sweep in sweep_cursors.SWEEPS.items():
        assert sweep.seconds > 0 and sweep.how
        source = inspect.getsource(runner[label])
        if sweep.cursor == "changes":
            assert f'idle_skip("{label}"' in source, label
        else:
            assert sweep.cursor == "own", label


def test_a_planted_memory_sweep_without_a_budget_is_caught(monkeypatch):
    def _planted() -> None:
        from kazma_core.memory.recall import recall  # noqa: F401

    monkeypatch.setattr(wb, "_MAINTENANCE_SWEEPS", (*wb._MAINTENANCE_SWEEPS, ("memory planted", _planted)))
    assert set(_memory_sweeps()) != set(sweep_cursors.SWEEPS)


# ── an idle pass does not grow with memory ─────────────────────────────────


class _Emb:
    dim = 1024

    def encode(self, text):
        return [0.0] * 1024


def _store(tmp_path: Path, factor: int, monkeypatch) -> None:
    from tests.test_memory_scale_bench import _seed

    root = tmp_path / f"x{factor}"
    root.mkdir()
    monkeypatch.setenv("KAZMA_DATA_DIR", str(root))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(root / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(root / "memory_ops.db"))
    # A quarter of the live size, and four times that: the shape, quickly.
    import tests.test_memory_scale_bench as bench

    monkeypatch.setattr(bench, "LIVE_MEMORIES", 400)
    monkeypatch.setattr(bench, "LIVE_FACTS", 400)
    _seed(root / "memory_state.db", factor)


def _idle_steps(tmp_path, factor, monkeypatch) -> dict[str, int]:
    _store(tmp_path, factor, monkeypatch)
    steps = [0]
    real = sqlite3.connect

    def connect(*a, **k):
        conn = real(*a, **k)

        def tick():
            steps[0] += 1
            return 0

        conn.set_progress_handler(tick, 1000)
        return conn

    changes = {label: fn for label, fn in _memory_sweeps().items()
               if sweep_cursors.SWEEPS[label].cursor == "changes"}
    out: dict[str, int] = {}
    with monkeypatch.context() as mp:
        mp.setattr("kazma_core.memory.embedder.get_embedder", lambda: _Emb())
        mp.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "")
        mp.setattr("kazma_core.memory.task_queue.enqueue_task", lambda *a, **k: "t")
        mp.setattr(sqlite3, "connect", connect)
        for _round in range(2):  # the first passes do the store's work
            for fn in changes.values():
                fn()
        for label, fn in changes.items():
            steps[0] = 0
            fn()
            out[label] = steps[0]
    return out


def test_an_idle_pass_reads_the_same_at_four_times_the_store(tmp_path, monkeypatch):
    small = _idle_steps(tmp_path, 1, monkeypatch)
    large = _idle_steps(tmp_path, 4, monkeypatch)
    for label in small:
        assert large[label] <= small[label] + 2, (label, small[label], large[label])


def test_control_without_the_cursor_an_idle_pass_grows_with_the_store(tmp_path, monkeypatch):
    @contextlib.contextmanager
    def never_skip(name, *, extra="", db=None):
        yield sweep_cursors._Cursor(skip=False, seq=None)

    monkeypatch.setattr(sweep_cursors, "idle_skip", never_skip)
    small = _idle_steps(tmp_path, 1, monkeypatch)
    large = _idle_steps(tmp_path, 4, monkeypatch)
    grew = [label for label in small if large[label] >= 3 * max(1, small[label])]
    assert {"memory vector repair", "memory vector codes", "memory text fill",
            "retired knowledge promotions"} <= set(grew), (small, large)


# ── the cursor ──────────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    path = tmp_path / "memory_state.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(path))
    conn = sqlite3.connect(str(path))
    ensure_primary_schema(conn)
    conn.close()
    return path


def _pass(name, *, idle=True, extra=""):
    with sweep_cursors.idle_skip(name, extra=extra) as cursor:
        if cursor.skip:
            return "skipped"
        cursor.idle = idle
        return "ran"


def test_a_change_after_an_idle_pass_runs_the_next(db):
    assert _pass("memory text fill") == "ran"
    assert _pass("memory text fill") == "skipped"
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
                 "created_at, last_accessed) VALUES ('e1', 'default', 's', 1, 'q', 'a', 'episodic', 1, 1)")
    conn.commit()
    conn.close()
    assert _pass("memory text fill") == "ran"
    assert _pass("memory text fill") == "skipped"


def test_a_pass_with_work_left_runs_again(db):
    assert _pass("memory vector codes", idle=False) == "ran"
    assert _pass("memory vector codes", idle=False) == "ran"


def test_an_access_bump_is_no_change(db):
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
                 "created_at, last_accessed) VALUES ('e1', 'default', 's', 1, 'q', 'a', 'episodic', 1, 1)")
    conn.commit()
    assert _pass("entity count repair") == "ran"
    conn.execute("UPDATE episodes SET access_count = access_count + 1, last_accessed = 9 WHERE id = 'e1'")
    conn.commit()
    conn.close()
    assert _pass("entity count repair") == "skipped"


def test_a_model_switch_is_work_for_the_vector_repair(db):
    assert _pass("memory vector repair", extra="bge-m3") == "ran"
    assert _pass("memory vector repair", extra="bge-m3") == "skipped"
    assert _pass("memory vector repair", extra="another-model") == "ran"


# ── the budget ─────────────────────────────────────────────────────────────


def test_the_runner_keeps_each_pass_time_and_says_one_over_budget(monkeypatch, caplog):
    import logging
    import time

    monkeypatch.setitem(sweep_cursors.SWEEPS, "memory text fill",
                        sweep_cursors.Sweep(0.01, "changes", "test"))
    monkeypatch.setattr(wb, "_SWEEP_STATE", {})
    with caplog.at_level(logging.WARNING, logger="kazma_core.memory.worker_bootstrap"):
        asyncio.run(wb._run_sweeps((("memory text fill", lambda: time.sleep(0.1)),)))
    state = wb.sweep_health()["memory text fill"]
    assert state["last_seconds"] >= 0.05 and state["over_budget"] == 1
    assert "its budget is" in caplog.text


def test_a_fresh_database_keeps_its_foreign_keys(tmp_path):
    """The counter's first row is written while the schema is made; the
    foreign-key pragma after it must not land inside that transaction (where
    SQLite ignores it). Found by tests/test_memory_graph_clean.py's control."""
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    conn = sqlite3.connect(str(tmp_path / "fresh.db"))
    try:
        ensure_primary_schema(conn)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert not conn.in_transaction
    finally:
        conn.close()
