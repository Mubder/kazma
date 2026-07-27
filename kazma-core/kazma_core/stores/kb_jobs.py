"""Durable Knowledge Library crawl job registry.

In-process dicts (``kb_api._kb_api_jobs``, gateway ``_kb_jobs``) lose state
on restart — the UI shows "Unknown job_id" mid-crawl even when partial
chunks already landed in SQLite.  This module dual-writes job snapshots to
ConfigStore so:

* job status survives process restart
* incomplete jobs are marked ``interrupted`` on boot
* both Web and gateway can share the same key space
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "CONFIG_KEY",
    "get_job",
    "list_jobs",
    "mark_stale_jobs_interrupted",
    "save_job",
    "upsert_job",
]

logger = logging.getLogger(__name__)

CONFIG_KEY = "kb.ingest_jobs"
_lock = threading.Lock()
_MAX_JOBS = 100  # LRU-ish cap: drop oldest finished jobs


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _load_all() -> dict[str, dict[str, Any]]:
    try:
        from kazma_core.config_store import get_config_store

        raw = get_config_store().get(CONFIG_KEY, {})
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for k, v in raw.items():
            if isinstance(v, dict):
                out[str(k)] = dict(v)
        return out
    except Exception as exc:
        logger.debug("[kb_jobs] load failed: %s", exc)
        return {}


def _save_all(jobs: dict[str, dict[str, Any]]) -> None:
    # Cap finished jobs to avoid unbounded growth
    if len(jobs) > _MAX_JOBS:
        finished = [
            (jid, j)
            for jid, j in jobs.items()
            if j.get("finished_at") or j.get("phase") in ("done", "error", "interrupted")
        ]
        finished.sort(key=lambda x: x[1].get("finished_at") or x[1].get("started_at") or "")
        drop = len(jobs) - _MAX_JOBS
        for jid, _ in finished[:drop]:
            jobs.pop(jid, None)

    try:
        from kazma_core.config_store import get_config_store

        get_config_store().set(
            CONFIG_KEY,
            json.dumps(jobs, ensure_ascii=False, default=str),
            category="kb",
        )
    except Exception as exc:
        logger.warning("[kb_jobs] save failed: %s", exc)


def get_job(job_id: str) -> dict[str, Any] | None:
    """Return a job snapshot or ``None``."""
    with _lock:
        jobs = _load_all()
        j = jobs.get(job_id)
        return dict(j) if j else None


def list_jobs(*, library_id: str | None = None) -> list[dict[str, Any]]:
    """List jobs, optionally filtered by library."""
    with _lock:
        jobs = _load_all()
        items = []
        for jid, j in jobs.items():
            if library_id and j.get("library_id") != library_id:
                continue
            row = dict(j)
            row["job_id"] = jid
            items.append(row)
        items.sort(key=lambda r: r.get("started_at") or "", reverse=True)
        return items


def save_job(job_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Replace the full job record."""
    with _lock:
        jobs = _load_all()
        row = dict(data)
        row.setdefault("updated_at", _now())
        jobs[job_id] = row
        _save_all(jobs)
        return dict(row)


def upsert_job(job_id: str, **fields: Any) -> dict[str, Any]:
    """Merge *fields* into an existing job (or create)."""
    with _lock:
        jobs = _load_all()
        row = dict(jobs.get(job_id) or {})
        row.update(fields)
        row["updated_at"] = _now()
        jobs[job_id] = row
        _save_all(jobs)
        return dict(row)


def mark_stale_jobs_interrupted() -> int:
    """On boot: any job without finished_at → phase=interrupted.

    Returns the number of jobs marked.  Safe to call multiple times.
    """
    with _lock:
        jobs = _load_all()
        n = 0
        for jid, j in list(jobs.items()):
            if j.get("finished_at"):
                continue
            phase = (j.get("phase") or "").lower()
            if phase in ("done", "error", "interrupted", "complete", "completed"):
                continue
            j["phase"] = "interrupted"
            j["message"] = (
                j.get("message")
                or "Process restarted mid-crawl — re-run crawl/refresh to continue."
            )
            j["finished_at"] = _now()
            j["updated_at"] = _now()
            jobs[jid] = j
            n += 1
        if n:
            _save_all(jobs)
            logger.info("[kb_jobs] marked %d in-flight crawl job(s) interrupted", n)
        return n
