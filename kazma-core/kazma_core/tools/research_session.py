"""Durable research sessions + in-process progress broadcast (industry R3).

Sessions survive process lifetime for list/detail; progress SSE uses in-memory
queues (live only while this process runs).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

__all__ = [
    "ResearchSession",
    "create_session",
    "get_session",
    "list_sessions",
    "update_session",
    "start_deep_research",
    "cancel_session",
    "subscribe_progress",
    "unsubscribe_progress",
]

logger = logging.getLogger(__name__)

_ProgressCb = Callable[[str, str], Awaitable[None] | None]

# session_id → list of asyncio.Queue for SSE subscribers
_SUBS: dict[str, list[asyncio.Queue]] = {}
_RUNNING: dict[str, asyncio.Task] = {}


@dataclass
class ResearchSession:
    id: str
    topic: str
    depth: str = "deep"
    status: str = "pending"  # pending|running|done|error|cancelled
    stage: str = "queued"
    message: str = ""
    log: list[str] = field(default_factory=list)
    report_path: str = ""
    summary: str = ""
    error: str = ""
    sources: int = 0
    max_sources: int = 8
    rubric_score: float | None = None
    rubric_ok: bool | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _db_path() -> Path:
    try:
        from kazma_core.paths import data_dir

        root = Path(data_dir())
    except Exception:
        root = Path.cwd() / "kazma-data"
    root.mkdir(parents=True, exist_ok=True)
    return root / "research_sessions.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_db_path()), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS research_sessions (
            id TEXT PRIMARY KEY,
            topic TEXT NOT NULL,
            depth TEXT DEFAULT 'deep',
            status TEXT DEFAULT 'pending',
            stage TEXT DEFAULT 'queued',
            message TEXT DEFAULT '',
            log_json TEXT DEFAULT '[]',
            report_path TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            error TEXT DEFAULT '',
            sources INTEGER DEFAULT 0,
            max_sources INTEGER DEFAULT 8,
            rubric_score REAL,
            rubric_ok INTEGER,
            created_at REAL,
            updated_at REAL,
            meta_json TEXT DEFAULT '{}'
        )
        """
    )
    # Idempotent migrations for pre-R4 DBs
    cols = {row[1] for row in c.execute("PRAGMA table_info(research_sessions)").fetchall()}
    if "rubric_score" not in cols:
        try:
            c.execute("ALTER TABLE research_sessions ADD COLUMN rubric_score REAL")
        except Exception:
            pass
    if "rubric_ok" not in cols:
        try:
            c.execute("ALTER TABLE research_sessions ADD COLUMN rubric_ok INTEGER")
        except Exception:
            pass
    c.commit()
    return c


def _row_to_session(r: sqlite3.Row) -> ResearchSession:
    try:
        log = json.loads(r["log_json"] or "[]")
    except Exception:
        log = []
    try:
        meta = json.loads(r["meta_json"] or "{}")
    except Exception:
        meta = {}
    keys = r.keys()
    rscore = None
    rok = None
    if "rubric_score" in keys and r["rubric_score"] is not None:
        try:
            rscore = float(r["rubric_score"])
        except (TypeError, ValueError):
            rscore = None
    if "rubric_ok" in keys and r["rubric_ok"] is not None:
        rok = bool(int(r["rubric_ok"]))
    return ResearchSession(
        id=r["id"],
        topic=r["topic"] or "",
        depth=r["depth"] or "deep",
        status=r["status"] or "pending",
        stage=r["stage"] or "",
        message=r["message"] or "",
        log=list(log) if isinstance(log, list) else [],
        report_path=r["report_path"] or "",
        summary=r["summary"] or "",
        error=r["error"] or "",
        sources=int(r["sources"] or 0),
        max_sources=int(r["max_sources"] or 8),
        rubric_score=rscore,
        rubric_ok=rok,
        created_at=float(r["created_at"] or 0),
        updated_at=float(r["updated_at"] or 0),
        meta=meta if isinstance(meta, dict) else {},
    )


def create_session(
    topic: str,
    *,
    depth: str = "deep",
    max_sources: int = 8,
    meta: dict[str, Any] | None = None,
) -> ResearchSession:
    now = time.time()
    sid = f"rs_{uuid.uuid4().hex[:16]}"
    sess = ResearchSession(
        id=sid,
        topic=(topic or "").strip(),
        depth=(depth or "deep").strip().lower(),
        status="pending",
        stage="queued",
        message="Queued",
        max_sources=max(2, min(15, int(max_sources or 8))),
        created_at=now,
        updated_at=now,
        meta=meta or {},
    )
    c = _conn()
    try:
        c.execute(
            """
            INSERT INTO research_sessions
            (id, topic, depth, status, stage, message, log_json, report_path,
             summary, error, sources, max_sources, rubric_score, rubric_ok,
             created_at, updated_at, meta_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                sess.id,
                sess.topic,
                sess.depth,
                sess.status,
                sess.stage,
                sess.message,
                "[]",
                "",
                "",
                "",
                0,
                sess.max_sources,
                None,
                None,
                sess.created_at,
                sess.updated_at,
                json.dumps(sess.meta),
            ),
        )
        c.commit()
    finally:
        c.close()
    return sess


def get_session(session_id: str) -> ResearchSession | None:
    c = _conn()
    try:
        r = c.execute(
            "SELECT * FROM research_sessions WHERE id=?", (session_id,)
        ).fetchone()
        return _row_to_session(r) if r else None
    finally:
        c.close()


def list_sessions(*, limit: int = 50) -> list[ResearchSession]:
    c = _conn()
    try:
        rows = c.execute(
            """
            SELECT * FROM research_sessions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(200, int(limit or 50))),),
        ).fetchall()
        return [_row_to_session(r) for r in rows]
    finally:
        c.close()


def update_session(session_id: str, **fields: Any) -> ResearchSession | None:
    sess = get_session(session_id)
    if not sess:
        return None
    for k, v in fields.items():
        if hasattr(sess, k) and k not in ("id", "created_at"):
            setattr(sess, k, v)
    sess.updated_at = time.time()
    c = _conn()
    try:
        c.execute(
            """
            UPDATE research_sessions SET
              topic=?, depth=?, status=?, stage=?, message=?,
              log_json=?, report_path=?, summary=?, error=?,
              sources=?, max_sources=?, rubric_score=?, rubric_ok=?,
              updated_at=?, meta_json=?
            WHERE id=?
            """,
            (
                sess.topic,
                sess.depth,
                sess.status,
                sess.stage,
                sess.message,
                json.dumps(sess.log[-200:]),
                sess.report_path,
                sess.summary,
                sess.error,
                sess.sources,
                sess.max_sources,
                sess.rubric_score,
                (None if sess.rubric_ok is None else (1 if sess.rubric_ok else 0)),
                sess.updated_at,
                json.dumps(sess.meta or {}),
                sess.id,
            ),
        )
        c.commit()
    finally:
        c.close()
    # Broadcast
    event = {
        "type": "progress",
        "session_id": sess.id,
        "status": sess.status,
        "stage": sess.stage,
        "message": sess.message,
        "sources": sess.sources,
        "report_path": sess.report_path,
        "rubric_score": sess.rubric_score,
        "rubric_ok": sess.rubric_ok,
        "updated_at": sess.updated_at,
    }
    for q in list(_SUBS.get(sess.id, [])):
        try:
            q.put_nowait(event)
        except Exception:
            pass
    return sess


def subscribe_progress(session_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _SUBS.setdefault(session_id, []).append(q)
    # Seed current state
    s = get_session(session_id)
    if s:
        try:
            q.put_nowait(
                {
                    "type": "snapshot",
                    "session": s.to_dict(),
                }
            )
        except Exception:
            pass
    return q


def unsubscribe_progress(session_id: str, q: asyncio.Queue) -> None:
    lst = _SUBS.get(session_id) or []
    if q in lst:
        lst.remove(q)
    if not lst and session_id in _SUBS:
        del _SUBS[session_id]


def cancel_session(session_id: str) -> ResearchSession | None:
    """Cancel a running session (best-effort). Returns updated session or None."""
    sess = get_session(session_id)
    if not sess:
        return None
    if sess.status in ("done", "error", "cancelled"):
        return sess
    task = _RUNNING.get(session_id)
    if task is not None and not task.done():
        task.cancel()
    update_session(
        session_id,
        status="cancelled",
        stage="cancelled",
        message="Cancelled by user",
        error="",
    )
    for q in list(_SUBS.get(session_id, [])):
        try:
            q.put_nowait(
                {
                    "type": "done",
                    "session_id": session_id,
                    "status": "cancelled",
                    "session": (get_session(session_id) or sess).to_dict(),
                }
            )
        except Exception:
            pass
    return get_session(session_id)


async def start_deep_research(
    topic: str,
    *,
    depth: str = "deep",
    max_sources: int = 8,
    export_docx: bool = False,
) -> ResearchSession:
    """Create session and run pipeline in background task."""
    sess = create_session(topic, depth=depth, max_sources=max_sources)
    if not sess.topic:
        update_session(sess.id, status="error", error="topic required", stage="error")
        return get_session(sess.id) or sess

    async def _run() -> None:
        update_session(
            sess.id, status="running", stage="start", message="Starting pipeline…"
        )

        import re

        async def progress_cb(stage: str, message: str) -> None:
            s = get_session(sess.id)
            log = list(s.log) if s else []
            log.append(f"[{stage}] {message}")
            fields: dict[str, Any] = {
                "status": "running",
                "stage": stage,
                "message": message,
                "log": log,
            }
            # Pipeline emits "Report ready: <path>" on done stage
            ready = re.search(r"Report ready:\s*(.+)$", message or "")
            if ready:
                fields["report_path"] = ready.group(1).strip()
            # "Fetching up to N pages…" / "Digesting N sources…"
            src_m = re.search(r"(\d+)\s+sources", message or "", re.I)
            if src_m:
                fields["sources"] = int(src_m.group(1))
            update_session(sess.id, **fields)

        try:
            from kazma_core.tools.research_pipeline import run_research_pipeline

            result = await run_research_pipeline(
                sess.topic,
                depth=sess.depth,
                max_sources=sess.max_sources,
                progress_cb=progress_cb,
                export_docx=export_docx,
            )
            report_path = ""
            sources = 0
            m = re.search(r"\*\*Report:\*\*\s*`([^`]+)`", result or "")
            if m:
                report_path = m.group(1)
            m2 = re.search(r"\*\*Sources acquired:\*\*\s*(\d+)", result or "")
            if m2:
                sources = int(m2.group(1))
            cur = get_session(sess.id)
            if not report_path and cur and cur.report_path:
                report_path = cur.report_path
            if not sources and cur and cur.sources:
                sources = cur.sources
            # Parse rubric from summary line: **Rubric:** 85/100 (pass)
            rubric_score: float | None = None
            rubric_ok: bool | None = None
            m3 = re.search(
                r"\*\*Rubric:\*\*\s*([\d.]+)/100\s*\(([^)]+)\)", result or ""
            )
            if m3:
                try:
                    rubric_score = float(m3.group(1))
                except ValueError:
                    rubric_score = None
                rubric_ok = "pass" in (m3.group(2) or "").lower()
            # Prefer scoring the report file when available
            if report_path:
                try:
                    from kazma_core.tools.research_eval import score_report_file
                    from kazma_core.tools.research_pipeline import (
                        _candidate_report_roots,
                    )

                    scored = None
                    rp = Path(report_path)
                    if rp.is_file():
                        scored = score_report_file(rp)
                    else:
                        for root in _candidate_report_roots():
                            cand = (root / report_path).resolve()
                            if cand.is_file():
                                scored = score_report_file(cand)
                                break
                    if scored is not None:
                        rubric_score = float(scored.score)
                        rubric_ok = bool(scored.ok)
                except Exception:
                    logger.debug(
                        "[research_session] rubric file score skipped", exc_info=True
                    )
            if (result or "").startswith("Error:"):
                log = list(cur.log) if cur else []
                log.append(result[:500])
                update_session(
                    sess.id,
                    status="error",
                    stage="error",
                    message=(result or "")[:200],
                    error=(result or "")[:2000],
                    summary=(result or "")[:4000],
                    log=log,
                    rubric_score=rubric_score,
                    rubric_ok=rubric_ok,
                )
            else:
                update_session(
                    sess.id,
                    status="done",
                    stage="done",
                    message="Complete",
                    summary=(result or "")[:8000],
                    report_path=report_path,
                    sources=sources,
                    rubric_score=rubric_score,
                    rubric_ok=rubric_ok,
                )
            final = get_session(sess.id)
            for q in list(_SUBS.get(sess.id, [])):
                try:
                    q.put_nowait(
                        {
                            "type": "done",
                            "session_id": sess.id,
                            "status": final.status if final else "done",
                            "session": final.to_dict() if final else None,
                        }
                    )
                except Exception:
                    pass
        except asyncio.CancelledError:
            cur = get_session(sess.id)
            if cur and cur.status != "cancelled":
                update_session(
                    sess.id,
                    status="cancelled",
                    stage="cancelled",
                    message="Cancelled",
                )
            raise
        except Exception as exc:
            logger.exception("[research_session] pipeline failed")
            # Friendly short message for UI; full detail in error
            brief = str(exc)[:200]
            if "Error:" in brief:
                brief = brief.split("Error:", 1)[-1].strip()[:200]
            update_session(
                sess.id,
                status="error",
                stage="error",
                error=str(exc)[:2000],
                message=f"Failed: {brief}" if brief else "Pipeline failed",
            )
            err_sess = get_session(sess.id)
            for q in list(_SUBS.get(sess.id, [])):
                try:
                    q.put_nowait(
                        {
                            "type": "error",
                            "session_id": sess.id,
                            "error": str(exc)[:500],
                            "session": err_sess.to_dict() if err_sess else None,
                        }
                    )
                except Exception:
                    pass
        finally:
            _RUNNING.pop(sess.id, None)

    task = asyncio.create_task(_run())
    _RUNNING[sess.id] = task
    return sess
