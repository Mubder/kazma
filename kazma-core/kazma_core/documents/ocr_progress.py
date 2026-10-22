"""How far a running document job's OCR has got, for its status.

A durable job that OCRs a long scan runs for minutes to an hour (a 54-page
Arabic scan is ~30 minutes on one core, 2026-10-22), and its status said
only "ocr_running" all that time. ``DocumentService.ocr_in_batches`` reports
pages read as it goes; the job's stage records them here and
``DocumentIngestionService.job_status`` adds them to the job's status
(``progress``), which the agent's ``document_status`` and the API show.

In this process only: the stage runs in the process that claimed the job,
and progress is a view of that run, not a record -- a restarted job counts
again from its first page. Bounded: a finished job's entry is dropped, and
at most :data:`MAX_JOBS` are kept.
"""

from __future__ import annotations

import threading
import time

__all__ = ["MAX_JOBS", "clear", "get", "record"]

#: At most this many jobs are tracked; the oldest entry goes first.
MAX_JOBS = 256

_lock = threading.Lock()
_progress: dict[str, dict[str, float | int]] = {}


def record(job_id: str, done: int, total: int) -> None:
    """Pages read so far of a job's OCR."""

    with _lock:
        _progress.pop(str(job_id), None)
        _progress[str(job_id)] = {
            "ocr_pages_done": int(done),
            "ocr_pages_total": int(total),
            "updated_epoch": time.time(),
        }
        while len(_progress) > MAX_JOBS:
            _progress.pop(next(iter(_progress)))


def get(job_id: str) -> dict[str, float | int] | None:
    """A job's OCR progress, or None when this process has none for it."""

    with _lock:
        value = _progress.get(str(job_id))
        return dict(value) if value is not None else None


def clear(job_id: str) -> None:
    """Forget a job's progress (its OCR stage ended)."""

    with _lock:
        _progress.pop(str(job_id), None)
