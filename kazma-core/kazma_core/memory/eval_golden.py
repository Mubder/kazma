"""Golden-set recall evaluator — industry regression for V2 hybrid retrieval.

Runs fixture cases from ``kazma-core/tests/fixtures/memory_golden.json``
against a private memory database (a temp file of its own, or an injected
connection that is not the live one). No live LLM.

It never touches the live memory database or the process-wide episode
writer. Until 2026-09-26 it rebound ``paths.primary_memory_db`` to its temp
file and reset the shared ``dual_write`` mirror inside the live server (the
Dashboard runs it through ``POST /api/memory/v2/eval/golden``), so a chat
turn written meanwhile -- and, through the mirror left on the temp file,
every turn after it until a restart -- was stored in a file nobody read.
tests/test_memory_every_turn.py holds that no product code rebinds a
``kazma_core.paths`` function or resets the mirror.
"""

from __future__ import annotations

import json
import logging
import os
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


def _is_live_memory_db(conn: sqlite3.Connection) -> bool:
    """True when *conn* is open on the install's own memory database."""
    from kazma_core.paths import primary_memory_db

    live = primary_memory_db()
    for _seq, name, path in conn.execute("PRAGMA database_list").fetchall():
        if name == "main" and path and live and os.path.exists(path) and os.path.exists(live):
            return os.path.samefile(path, live)
    return False


def _seed_episode(
    conn: sqlite3.Connection, *, session_id: str, turn: int, content: str, now: float
) -> None:
    """A case's episode, written straight into the eval's own database -- the
    columns ``dual_write.mirror_episode`` writes and the vector it would
    compute -- never through the process-wide writer (which would also have
    copied it into a state mirror or remote vector index, when configured)."""
    from kazma_core.memory.dual_write import _episode_id
    from kazma_core.memory.embedder import encode_text_to_blob, get_embedding_model_name

    conn.execute(
        """INSERT OR IGNORE INTO episodes
           (id, tenant_id, session_id, turn_number, user_text, assistant_text,
            summary_text, tier, structural_importance, created_at, metadata_json,
            embedding, embedding_model_version)
           VALUES (?,?,?,?,?,'',?,'episodic',3,?,?,?,?)""",
        (
            _episode_id(session_id, turn, content.strip()),
            "default",
            session_id,
            turn,
            content[:4000],
            content[:500],
            now,
            json.dumps({"source": "golden_eval"}),
            encode_text_to_blob(content[:500]),
            get_embedding_model_name() or "",
        ),
    )


def run_golden_eval(
    *,
    include_optional: bool = False,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Execute golden cases on a private database; return the pass-rate report.

    Every case clears the database, seeds it, and recalls from it
    (``recall(conn=...)``). Without *conn* the database is a temp file of its
    own, removed afterwards. An injected *conn* must not be the live memory
    database: every case clears it.
    """
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

    from kazma_core.memory.recall import recall
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    owns = conn is None
    tmp_path = ""
    if conn is None:
        fd, tmp_path = tempfile.mkstemp(prefix="kazma-golden-", suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(tmp_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        ensure_primary_schema(conn)
    elif _is_live_memory_db(conn):
        raise ValueError("the golden eval clears its database; it never runs on live memory")

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
            conn.execute("DELETE FROM episodes")
            conn.execute("DELETE FROM beliefs")
            now = time.time()
            for i, b in enumerate(case.get("setup_beliefs") or []):
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
            session = f"golden-{case.get('id')}"
            for turn, msg in enumerate(case.get("setup") or [], start=1):
                content = str(msg.get("content") or "") if isinstance(msg, dict) else str(msg)
                if content:
                    _seed_episode(conn, session_id=session, turn=turn, content=content, now=now)
            conn.commit()

            query = str(case.get("query") or "")
            expect = [str(x).lower() for x in (case.get("expect_contains") or [])]
            match_any = bool(case.get("match_any"))
            # recall never raises: a failure comes back as an empty result.
            result = recall(
                query,
                conn=conn,
                limit=8,
                tenant_id="default",
                session_id=session,
                explain=True,
            )
            blob = " ".join(
                [(h.content or "").lower() for h in (result.beliefs + result.episodes)]
            )
            if match_any:
                ok = any(e in blob for e in expect) if expect else True
            else:
                ok = all(e in blob for e in expect) if expect else True

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
            conn.close()
            for leftover in (tmp_path, f"{tmp_path}-wal", f"{tmp_path}-shm"):
                if os.path.exists(leftover):
                    try:
                        os.remove(leftover)  # the eval's own temp file
                    except OSError:
                        logger.debug("[eval_golden] temp file %s left", leftover, exc_info=True)

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
