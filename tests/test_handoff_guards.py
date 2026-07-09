"""Unit tests for handoff_guards cycle detection."""

from __future__ import annotations

from time import perf_counter

from kazma_core.swarm.handoff_guards import (
    MAX_HANDOFF_DEPTH,
    MAX_VISITS,
    handoff_guard_error,
    register_visit,
)


def test_depth_limit():
    visited: dict[str, int] = {}
    register_visit(visited, "a")
    err = handoff_guard_error(
        source_worker="a",
        target_worker="b",
        visited=visited,
        depth=MAX_HANDOFF_DEPTH,
        started=perf_counter(),
    )
    assert err is not None
    assert "max depth" in err.error.lower()


def test_allows_return_handoff_once():
    visited: dict[str, int] = {}
    register_visit(visited, "a")  # a=1
    # A -> B first time: B visits=0 < 2
    assert (
        handoff_guard_error(
            source_worker="a",
            target_worker="b",
            visited=visited,
            depth=1,
            started=perf_counter(),
        )
        is None
    )
    register_visit(visited, "b")  # b=1
    # B -> A: A has visits=1 < 2 — allowed (return)
    assert (
        handoff_guard_error(
            source_worker="b",
            target_worker="a",
            visited=visited,
            depth=2,
            started=perf_counter(),
        )
        is None
    )


def test_blocks_ping_pong():
    visited = {"a": MAX_VISITS, "b": 1}
    err = handoff_guard_error(
        source_worker="b",
        target_worker="a",
        visited=visited,
        depth=2,
        started=perf_counter(),
    )
    assert err is not None
    assert "cycle" in err.error.lower()
