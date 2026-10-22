"""How far a running document job's OCR has got, for its status.

A durable job that OCRs a long scan runs for minutes to an hour (a 54-page
Arabic scan is ~30 minutes on one core, 2026-10-22), and its status said
only "parsing" all that time. ``DocumentService.ocr_in_batches`` reports
pages read as it goes; the job's stage records them here and
``DocumentIngestionService.job_status`` adds them to the job's status
(``progress``), which the agent's ``document_status`` and the API show.

The record starts when the stage starts (:func:`start`), before the first
page is read: the first worker run parses the document and reads its first
pages, minutes on a long scan, and a status with no progress in that time
read as stuck -- the model checked three times in 80 s and told the user
the job never progressed (2026-10-22). The rate and the time left are
measured from the stage's start.

In this process only: the stage runs in the process that claimed the job,
and progress is a view of that run, not a record -- a restarted job counts
again from its first page. Bounded: a finished job's entry is dropped, and
at most :data:`MAX_JOBS` are kept.
"""

from __future__ import annotations

import threading
import time

__all__ = ["MAX_JOBS", "clear", "get", "record", "start"]

#: At most this many jobs are tracked; the oldest entry goes first.
MAX_JOBS = 256

_lock = threading.Lock()
_progress: dict[str, dict[str, float | int]] = {}


def _keep(job_id: str, value: dict[str, float | int]) -> None:
    _progress.pop(job_id, None)
    _progress[job_id] = value
    while len(_progress) > MAX_JOBS:
        _progress.pop(next(iter(_progress)))


def start(job_id: str) -> None:
    """A job's parse stage began: its status says so before a page is read."""

    now = time.time()
    with _lock:
        _keep(str(job_id), {"started_epoch": now, "updated_epoch": now})


def record(job_id: str, done: int, total: int) -> None:
    """Pages read so far of a job's OCR."""

    now = time.time()
    with _lock:
        started = float(_progress.get(str(job_id), {}).get("started_epoch", now))
        _keep(str(job_id), {
            "ocr_pages_done": int(done),
            "ocr_pages_total": int(total),
            "started_epoch": started,
            "updated_epoch": now,
        })


def get(job_id: str) -> dict[str, float | int] | None:
    """A job's OCR progress, or None when this process has none for it.

    With pages read, ``seconds_per_page`` and ``seconds_left`` estimate the
    rest from the stage's own pace.
    """

    with _lock:
        value = _progress.get(str(job_id))
        if value is None:
            return None
        value = dict(value)
    done = int(value.get("ocr_pages_done", 0) or 0)
    total = int(value.get("ocr_pages_total", 0) or 0)
    elapsed = time.time() - float(value.get("started_epoch", time.time()))
    value["elapsed_seconds"] = round(elapsed)
    if done > 0 and total >= done:
        per_page = elapsed / done
        value["seconds_per_page"] = round(per_page, 1)
        value["seconds_left"] = round(per_page * (total - done))
    return value


def clear(job_id: str) -> None:
    """Forget a job's progress (its OCR stage ended)."""

    with _lock:
        _progress.pop(str(job_id), None)
