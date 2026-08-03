"""Golden-set recall evaluator — industry regression for V2 hybrid retrieval.

Runs fixture cases from ``kazma-core/tests/fixtures/memory_golden.json``
against a temporary memory DB (or an injected connection). No live LLM.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

__all__ = ["run_golden_eval", "load_golden_cases", "GOLDEN_PATH"]

logger = logging.getLogger(__name__)

GOLDEN_PATH = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "memory_golden.json"
)


def load_golden_cases() -> list[dict[str, Any]]:
    if not GOLDEN_PATH.is_file():
        return []
    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return list(data.get("cases") or [])


def run_golden_eval(
    *,
    include_optional: bool = False,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Execute golden cases; return pass-rate report for API / CI."""
    cases = load_golden_cases()
    if not cases:
        return {
            "ok": False,
            "error": "golden fixture missing",
            "passed": 0,
            "failed": 0,
            "total": 0,
            "cases": [],
        }

    owns = conn is None
    tmp_path: str | None = None
    _orig_primary = None
    if owns:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        tmp_path = tmp.name
        conn = sqlite3.connect(tmp_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        from kazma_core.memory.schema_v2 import ensure_primary_schema

        ensure_primary_schema(conn)
        # Point recall() dual-write/paths at the temp DB
        try:
            import kazma_core.paths as paths

            _orig_primary = getattr(paths, "primary_memory_db", None)
            paths.primary_memory_db = lambda: tmp_path  # type: ignore[assignment]
        except Exception:
            _orig_primary = None
        try:
            from kazma_core.memory.dual_write import reset_mirror

            reset_mirror()
        except Exception:
            pass

    from kazma_core.memory.recall import recall

    results: list[dict[str, Any]] = []
    passed = 0
    failed = 0
    skipped = 0

    try:
        for case in cases:
            if case.get("optional") and not include_optional:
                skipped += 1
                results.append(
                    {
                        "id": case.get("id"),
                        "status": "skipped",
                        "optional": True,
                    }
                )
                continue
            try:
                conn.execute("DELETE FROM episodes")
                conn.execute("DELETE FROM beliefs")
                conn.commit()
            except Exception:
                pass

            now = time.time()
            for i, b in enumerate(case.get("setup_beliefs") or []):
                try:
                    conn.execute(
                        """INSERT INTO beliefs
                           (id, tenant_id, subject, predicate, predicate_type, object,
                            confidence, structural_importance, source_trust_weight,
                            valid_from, ingested_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            f"{case.get('id', 'c')}-b{i}",
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
                except Exception as exc:
                    logger.debug("[eval_golden] seed belief failed: %s", exc)
            conn.commit()

            # Episodes via dual-write when available
            for turn, msg in enumerate(case.get("setup") or [], start=1):
                content = ""
                if isinstance(msg, dict):
                    content = str(msg.get("content") or "")
                else:
                    content = str(msg)
                if not content:
                    continue
                try:
                    from kazma_core.memory.dual_write import get_mirror, reset_mirror

                    reset_mirror()
                    get_mirror().mirror_episode(
                        session_id=f"golden-{case.get('id')}",
                        turn_number=turn,
                        user_text=content,
                        assistant_text="",
                        summary_text=content[:500],
                        tenant_id="default",
                        tier="episodic",
                        importance=3,
                        source="golden_eval",
                    )
                except Exception:
                    # Fallback: direct episode insert if dual-write needs full stack
                    try:
                        eid = f"{case.get('id')}-ep{turn}"
                        conn.execute(
                            """INSERT INTO episodes
                               (id, tenant_id, session_id, turn_number, role, content,
                                tier, importance, created_at)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            (
                                eid,
                                "default",
                                f"golden-{case.get('id')}",
                                turn,
                                "user",
                                content,
                                "episodic",
                                3,
                                now,
                            ),
                        )
                        conn.commit()
                    except Exception as exc:
                        logger.debug("[eval_golden] episode seed failed: %s", exc)

            query = str(case.get("query") or "")
            expect = [str(x).lower() for x in (case.get("expect_contains") or [])]
            match_any = bool(case.get("match_any"))
            try:
                # Point recall at this connection via env is hard; use in-process
                # if primary_memory_db was monkeypatched by tests. For API, seed
                # into live DB is dangerous — eval uses temp when owns=True.
                result = recall(
                    query,
                    limit=8,
                    tenant_id="default",
                    session_id=f"golden-{case.get('id')}",
                    explain=True,
                )
                blob = " ".join(
                    [(h.content or "").lower() for h in (result.beliefs + result.episodes)]
                )
                if match_any:
                    ok = any(e in blob for e in expect) if expect else True
                else:
                    ok = all(e in blob for e in expect) if expect else True
            except Exception as exc:
                ok = False
                blob = f"error:{exc}"

            if ok:
                passed += 1
                status = "pass"
            else:
                failed += 1
                status = "fail"
            results.append(
                {
                    "id": case.get("id"),
                    "status": status,
                    "query": query,
                    "expect": expect,
                    "match_any": match_any,
                    "preview": (blob or "")[:240],
                }
            )
    finally:
        if owns:
            try:
                if _orig_primary is not None:
                    import kazma_core.paths as paths

                    paths.primary_memory_db = _orig_primary  # type: ignore[assignment]
            except Exception:
                pass
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    total = passed + failed
    rate = (passed / total) if total else 0.0
    return {
        "ok": failed == 0 and total > 0,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": total,
        "pass_rate": round(rate, 3),
        "cases": results,
        "fixture": str(GOLDEN_PATH),
    }
