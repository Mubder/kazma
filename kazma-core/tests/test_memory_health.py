"""Tests for Dashboard memory health board builder."""

from __future__ import annotations

from kazma_core.memory.health import (
    build_memory_health,
    mark_recall_degraded,
    recall_degraded,
)


def test_build_memory_health_shape():
    data = build_memory_health()
    assert "status" in data
    assert "components" in data
    assert isinstance(data["components"], list)
    assert data["components"], "expected at least one component row"
    ids = {c["id"] for c in data["components"]}
    # Core rows always present
    for required in (
        "memory_enabled",
        "per_turn_retrieval",
        "auto_store",
        "consolidation",
        "embedder",
        "vector_memory",
        "layer_l1",
        "layer_l2",
        "layer_l3",
        "layer_l4",
        "pkg_chromadb",
        "pkg_st",
        "pkg_sqlite_vec",
    ):
        assert required in ids, f"missing component {required}"
    for c in data["components"]:
        assert "name" in c and "status" in c and "detail" in c
        assert c["status"] in ("ok", "warn", "error", "off")


def test_build_memory_health_surfaces_recall_failures(monkeypatch):
    monkeypatch.setattr("kazma_core.memory.health._recall_fail_count", 0)
    monkeypatch.setattr("kazma_core.memory.health._recall_last_failure", 0.0)
    monkeypatch.setattr("kazma_core.memory.health._recall_last_reason", "")

    assert int((recall_degraded().get("fail_count") or 0)) == 0
    mark_recall_degraded("unit-test")
    data = build_memory_health()
    by_id = {c["id"]: c for c in data["components"]}
    assert "recall_failures" in by_id
    assert by_id["recall_failures"]["status"] == "warn"
    assert int((by_id["recall_failures"].get("meta") or {}).get("fail_count") or 0) >= 1
