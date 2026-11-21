"""The quick recall check (the Memory page's button; it was "golden eval").

Runs the cases of ``kazma-core/tests/fixtures/memory_golden.json`` against a
private memory database (a temp file of its own, or an injected connection
that is not the live one). No live LLM. It is a smoke test: recall finds the
answer to an obvious question. The measurement of recall's quality is the
retrieval benchmark (``memory/benchmark.py``).

**A case passes only when the answer is shown and nothing unrelated is**
(the memory benchmarks audit's F11 and R10, 2026-11-21). Every case's own
memories are written among the fixture's twenty unrelated ones
(``distractors``: turns and facts about other things), and no word of theirs
(``marker``), nor any word the case lists in ``must_not_show``, may come
back. Each case had only its own one to three memories, so a recall that
showed every memory whatever was asked passed every case; it now fails every
one (``kazma-core/tests/test_memory_eval_golden.py``).

Measured with the real embedder (bge-m3) on 2026-11-21: 2 of the 6 cases
pass. The other four find their answer and also show 3 to 5 of the twenty
unrelated turns, nearly all as "possibly related" (evidence 0.04 to 0.10;
one at 0.12, strong): where the answer is a fact, the turns' own lane keeps
its best unrelated turns. A finding about recall in a small store, left for
a decision, not tuned away here. Without an embedder (CI) the cases pass on
their words.

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

__all__ = ["GOLDEN_PATH", "load_golden_cases", "run_golden_eval"]

logger = logging.getLogger(__name__)

GOLDEN_PATH = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "memory_golden.json"
)


def _fixture() -> dict[str, Any]:
    if not GOLDEN_PATH.is_file():
        return {}
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def load_golden_cases() -> list[dict[str, Any]]:
    return list(_fixture().get("cases") or [])


def _distractors() -> list[dict[str, Any]]:
    """The unrelated memories every case is written among: a turn
    (``content``) or a fact (``subject``, ``predicate``, ``object``), each with
    the word of it that must never come back (``marker``)."""
    return list(_fixture().get("distractors") or [])


def _seed_distractors(conn: sqlite3.Connection, distractors: list[dict[str, Any]], *, now: float) -> None:
    for turn, item in enumerate(distractors, start=1):
        if item.get("content"):
            _seed_episode(conn, session_id="golden-unrelated", turn=turn, content=str(item["content"]),
                          now=now - 3600)
        else:
            _seed_belief(conn, item, now=now - 3600)


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
    from kazma_core.memory.dual_write import episode_row
    from kazma_core.memory.embedder import encode_text_to_blob

    row = episode_row(
        session_id=session_id,
        turn_number=turn,
        user_text=content,
        summary_text=content[:500],
        importance=3,
        source="golden_eval",
        created_at=now,
    )
    conn.execute(
        """INSERT OR IGNORE INTO episodes
           (id, tenant_id, session_id, turn_number, user_text, assistant_text,
            summary_text, tier, structural_importance, created_at, metadata_json,
            embedding, embedding_model_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            row["id"], row["tenant_id"], row["session_id"], row["turn_number"],
            row["user_text"], row["assistant_text"], row["summary_text"], row["tier"],
            row["importance"], row["created_at"], json.dumps(row["meta"]),
            encode_text_to_blob(row["embed_text"]), row["embedding_model_version"],
        ),
    )


def _seed_belief(conn: sqlite3.Connection, fact: dict[str, Any], *, now: float) -> None:
    """A case's fact, stored the way extraction stores it: ``mutate_belief``,
    so it has the vector recall judges it by -- a raw row had none, and was
    judged on its words alone -- kept private (nothing reaches a mirror or a
    shared index)."""
    from kazma_core.memory.belief_mutation import mutate_belief

    mutate_belief(
        conn,
        fact["subject"],
        fact["predicate"],
        fact["object"],
        predicate_type="functional",
        confidence=0.9,
        importance=4,
        extraction_method="user_explicit",
        tenant_id="default",
        now=now,
        private=True,
    )


def _needs_meaning_unavailable(case: dict[str, Any]) -> bool:
    """A case marked ``needs_meaning`` asks what only meaning can answer (the
    question shares no word with the fact); without an embedder it is skipped
    with that reason, never failed or passed on words."""
    if not case.get("needs_meaning"):
        return False
    try:
        from kazma_core.memory.embedder import get_embedder

        embedder = get_embedder()
        return embedder is None or not embedder.encode("probe")
    except Exception:  # noqa: BLE001 -- no embedder is the answer, whatever broke it
        return True


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

    # Imported at each run (a test's stand-in recall is the module's).
    from kazma_core.memory.recall import recall
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    distractors = _distractors()
    unrelated = [str(d.get("marker") or "").lower() for d in distractors if d.get("marker")]

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
            if _needs_meaning_unavailable(case):
                skipped += 1
                results.append(
                    {"id": case.get("id"), "status": "skipped", "reason": "needs an embedder"}
                )
                continue
            conn.execute("DELETE FROM episodes")
            conn.execute("DELETE FROM beliefs")
            now = time.time()
            _seed_distractors(conn, distractors, now=now)
            for b in case.get("setup_beliefs") or []:
                _seed_belief(conn, b, now=now)
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
                [(h.content or "").lower()
                 for h in (result.beliefs + result.episodes + list(getattr(result, "summaries", []) or []))]
            )
            if match_any:
                found = any(e in blob for e in expect) if expect else True
            else:
                found = all(e in blob for e in expect) if expect else True
            must_not = unrelated + [str(x).lower() for x in (case.get("must_not_show") or [])]
            shown_wrongly = sorted({w for w in must_not if w and w in blob})
            ok = found and not shown_wrongly

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
                    "found": found,
                    "shown_wrongly": shown_wrongly,
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
        "unrelated_per_case": len(distractors),
        "cases": results,
        "fixture": str(GOLDEN_PATH),
    }
