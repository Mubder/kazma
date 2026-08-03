"""Sprint 6: run golden memory cases against V2 (lightweight, no LLM)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "memory_golden.json"


@pytest.fixture()
def mem_db(tmp_path, monkeypatch):
    db = tmp_path / "memory_state.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(db))
    try:
        import kazma_core.paths as paths

        monkeypatch.setattr(paths, "primary_memory_db", lambda: str(db))
    except Exception:
        pass
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    conn = sqlite3.connect(str(db), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    yield conn
    conn.close()


def _load_cases():
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return data.get("cases") or []


def test_golden_set_pass_rate(mem_db):
    from kazma_core.memory.recall import recall

    cases = _load_cases()
    assert cases
    passed = 0
    failed = []
    for case in cases:
        # Fresh isolation per case: clear tables
        mem_db.execute("DELETE FROM episodes")
        mem_db.execute("DELETE FROM beliefs")
        mem_db.commit()
        # Seed beliefs if provided
        now = time.time()
        for i, b in enumerate(case.get("setup_beliefs") or []):
            mem_db.execute(
                """INSERT INTO beliefs
                   (id, tenant_id, subject, predicate, predicate_type, object,
                    confidence, structural_importance, source_trust_weight,
                    valid_from, ingested_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"{case['id']}-b{i}",
                    "default",
                    b["subject"],
                    b["predicate"],
                    "functional",
                    b["object"],
                    0.9,
                    4,
                    1.0,
                    now,
                    now,
                ),
            )
        mem_db.commit()
        # Seed episodes via dual-write mirror (singleton uses primary_memory_db)
        from kazma_core.memory.dual_write import get_mirror, reset_mirror

        reset_mirror()
        mirror = get_mirror()
        for turn, msg in enumerate(case.get("setup") or [], start=1):
            if msg.get("role") == "user":
                mirror.mirror_episode(
                    session_id=f"golden-{case['id']}",
                    turn_number=turn,
                    user_text=msg.get("content") or "",
                    assistant_text="OK",
                    tenant_id="default",
                )
        result = recall(
            case["query"],
            conn=mem_db,
            limit=8,
            session_id=f"golden-{case['id']}",
        )
        blob = " ".join(
            h.content for h in (result.beliefs + result.episodes)
        ).lower()
        expected = [e.lower() for e in case.get("expect_contains") or []]
        match_any = bool(case.get("match_any"))
        ok = any(e in blob for e in expected) if match_any else all(
            e in blob for e in expected
        )
        if ok:
            passed += 1
        else:
            if case.get("optional"):
                continue
            failed.append({"id": case["id"], "blob": blob[:200], "expected": expected})

    rate = passed / max(1, len([c for c in cases if not c.get("optional")]))
    assert not failed, f"golden failures ({passed} passed): {failed}"
    assert rate >= 0.5
