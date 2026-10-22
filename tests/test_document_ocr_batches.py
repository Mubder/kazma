"""A long scan is OCR'd a few pages per worker run, and a stopped call stops its worker.

Live 2026-10-22: a 54-page Arabic scan sent to the chat. OCR is ~35 s a page
on one core, so ~30 minutes for the file, and every path tried it in one go:
the attachment's excerpt OCR'd the whole file before the model saw the
message (the worker's full five minutes, nothing read); ``read_document``
and ``ocr_document`` on six pages hit the 120 s tool limit; each timed-out
call left its OCR container running, so the next, smaller call shared the
CPU with it and timed out too; and a durable job had one worker run of five
minutes for every page.

Now (``documents/service.py``, ``documents/cancellation.py``):
- the excerpt reads the text layer only and says the file is scanned;
- ``ocr_in_batches`` OCRs a few pages per worker run (the worker's
  ``max_pages`` cap), sizing each run from the seconds a page took; a durable
  job reads every page this way and reports progress; a tool call reads what
  fits in its time and names the rest, with the next call;
- a cancelled call stops its worker: the broker exchange closes its
  connection, the local sandbox kills the process tree.

Each with its negative control: the old one-run parse, a read with no cancel
check, the old excerpt.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from kazma_core.documents import cancellation, ocr_progress
from kazma_core.documents import service as service_module
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.errors import DocumentSandboxError
from kazma_core.documents.models import (
    BlockType,
    DocumentBlock,
    DocumentIR,
    DocumentPage,
    Provenance,
    new_document_id,
    new_version_id,
)
from kazma_core.documents.service import DocumentService, ocr_continuation_note

PAGES = 54
PAGE_SECONDS = 35.0
RUN_SECONDS = 8.0


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def _scan() -> DocumentIR:
    return DocumentIR(
        document_id=new_document_id(),
        version_id=new_version_id(),
        pages=tuple(DocumentPage(n, (), metadata={"image_count": 1}) for n in range(1, PAGES + 1)),
        provenance=Provenance(source="fixture", parser="fixture"),
        metadata={"source_mime": "application/pdf", "warnings": []},
    )


class _FakeWorker:
    """The parser worker as the service sees it: one run reads the document
    and OCRs the pages asked for, PAGE_SECONDS each, and is killed past its
    time limit (worker_timeout_seconds)."""

    def __init__(self, clock: _Clock, limit: float) -> None:
        self.clock = clock
        self.limit = limit
        self.runs: list[dict] = []

    def __call__(self, source, *, force_ocr=False, ocr_language=None, ocr_pages=None,
                 allow_ocr=True, timeout_seconds=None, max_ocr_pages=None):
        base = _scan()
        limit = timeout_seconds if timeout_seconds is not None else self.limit
        if not allow_ocr and not force_ocr:
            selected: list[int] = []
        else:
            selected = list(ocr_pages) if ocr_pages else [p.page_number for p in base.pages]
        deferred: list[int] = []
        if max_ocr_pages is not None and len(selected) > max_ocr_pages:
            selected, deferred = selected[:max_ocr_pages], selected[max_ocr_pages:]
        seconds = RUN_SECONDS + PAGE_SECONDS * len(selected)
        self.runs.append({"pages": tuple(selected), "seconds": seconds, "limit": limit})
        if seconds > limit:
            self.clock.now += limit
            raise DocumentSandboxError("Document parser exceeded its time limit", code="parser_timeout")
        self.clock.now += seconds
        pages = tuple(
            replace(page, blocks=(DocumentBlock(f"p{page.page_number}-ocr", BlockType.TEXT,
                                                text=f"OCR page {page.page_number}"),))
            if page.page_number in selected else page
            for page in base.pages
        )
        metadata = dict(base.metadata)
        if selected:
            metadata["ocr"] = {"status": "completed", "selected_pages": selected,
                               "completed_pages": selected, "failed_pages": [],
                               **({"deferred_pages": deferred} if deferred else {})}
        return replace(base, pages=pages, metadata=metadata)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(service_module.time, "monotonic", clock.monotonic)
    service = DocumentService(config=DocumentConfig(storage_root=tmp_path / "documents"))
    worker = _FakeWorker(clock, float(service.config.worker_timeout_seconds))
    monkeypatch.setattr(service, "_parse_isolated", worker)
    return service, worker, clock


def test_a_long_scan_is_read_page_by_page_across_runs(svc, tmp_path) -> None:
    service, worker, _clock = svc
    progress: list[tuple[int, int]] = []
    result = service.ocr_in_batches(tmp_path / "scan.pdf", force=True,
                                    on_progress=lambda d, t: progress.append((d, t)))
    assert result.read == tuple(range(1, PAGES + 1)) and result.remaining == ()
    assert all(page.blocks for page in result.document.pages)
    assert result.document.metadata["ocr"]["status"] == "completed"
    assert "deferred_pages" not in result.document.metadata["ocr"]
    # Every run fits its time limit, and there were several.
    assert all(run["seconds"] <= run["limit"] for run in worker.runs) and len(worker.runs) > 5
    assert progress[-1] == (PAGES, PAGES)
    assert [d for d, _ in progress] == sorted(d for d, _ in progress)


def test_negative_control_one_run_for_every_page_times_out(svc, tmp_path) -> None:
    """The old parse: one worker run OCRs the whole scan within one limit."""
    service, worker, _clock = svc
    with pytest.raises(DocumentSandboxError) as caught:
        worker(tmp_path / "scan.pdf", force_ocr=True)
    assert caught.value.code == "parser_timeout"


def test_the_durable_parse_reads_every_page(svc, tmp_path) -> None:
    service, _worker, _clock = svc
    document = service.parse_ingested_blob(tmp_path / "scan.pdf", force_ocr=True)
    assert all(page.blocks for page in document.pages)


def test_a_document_that_needs_no_ocr_is_one_run(svc, tmp_path) -> None:
    service, worker, _clock = svc
    result = service.ocr_in_batches(tmp_path / "scan.pdf", force=True, pages=(1, 2))
    assert result.read == (1, 2) and len(worker.runs) <= 2


def test_a_tool_call_reads_what_fits_and_names_the_rest(svc, tmp_path) -> None:
    service, worker, clock = svc
    start = clock.now
    result = service.ocr_in_batches(tmp_path / "scan.pdf", force=True, budget_seconds=95.0)
    assert clock.now - start <= 95.0
    assert result.read and result.remaining
    assert set(result.read) | set(result.remaining) == set(range(1, PAGES + 1))
    assert result.document.metadata["ocr"]["deferred_pages"] == list(result.remaining)
    note = ocr_continuation_note(read=result.read, remaining=result.remaining, total_pages=PAGES)
    assert f"pages={list(result.remaining[:3])}" in note
    assert "document_import" in note and "document_status" in note


def test_the_note_names_page_ranges() -> None:
    note = ocr_continuation_note(read=(1, 2, 3), remaining=tuple(range(4, 55)), total_pages=54)
    assert "OCR read pages 1-3 of 54" in note and "Pages 4-54 are not read yet" in note
    assert ocr_continuation_note(read=(1,), remaining=(), total_pages=1) == ""


def test_the_job_status_carries_ocr_progress(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(ocr_progress.time, "time", lambda: now[0])
    ocr_progress.start("job-1")
    now[0] += 90
    started = ocr_progress.get("job-1")
    assert started["elapsed_seconds"] == 90 and "ocr_pages_done" not in started
    now[0] += 30
    ocr_progress.record("job-1", 3, 54)
    value = ocr_progress.get("job-1")
    assert value["ocr_pages_done"] == 3 and value["seconds_per_page"] == 40.0
    assert value["seconds_left"] == 40 * 51
    ocr_progress.clear("job-1")
    assert ocr_progress.get("job-1") is None


def test_document_status_says_the_job_is_working_and_not_to_wait() -> None:
    """Live 2026-10-22: the status said only "parsing" for the first minutes,
    the model checked three times in 80 s and told the user the job never
    progressed. It now says the job is working, its pace and the time left."""
    from kazma_skills.native.document_platform.tools import _format_status

    base = {"job_id": "j", "document_id": "d", "state": "parsing", "stage": "parsing",
            "attempt": 1, "max_attempts": 3}
    starting = _format_status({**base, "progress": {"elapsed_seconds": 80, "started_epoch": 1}})
    assert "the first pages are being read" in starting
    assert "Do not wait for it in this turn" in starting
    running = _format_status({**base, "progress": {
        "ocr_pages_done": 3, "ocr_pages_total": 54, "elapsed_seconds": 180,
        "seconds_per_page": 60.0, "seconds_left": 3060}})
    assert "3 of 54 pages read in about 3 minutes" in running
    assert "about 51 minutes left at this pace" in running
    # The control: the status before 2026-10-22 had no line for a job with no
    # pages read yet.
    assert "OCR" not in _format_status(base)


# ── A stopped call stops its worker ─────────────────────────────────────


def test_a_cancelled_broker_read_gives_up_within_a_second() -> None:
    from kazma_core.documents import worker_container

    ours, theirs = socket.socketpair()
    try:
        event = threading.Event()
        outcome: dict = {}

        def read() -> None:
            token = cancellation._CANCEL.set(event)
            try:
                worker_container._recv_exact(ours, 5, time.monotonic() + 30)
            except BaseException as exc:  # noqa: BLE001
                outcome["error"] = exc
            finally:
                cancellation._CANCEL.reset(token)

        thread = threading.Thread(target=read)
        thread.start()
        # Whether the cancel lands before the read waits or during it, the
        # read ends with WorkerCancelled: no timing to wait out.
        event.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert isinstance(outcome.get("error"), cancellation.WorkerCancelled)
    finally:
        ours.close()
        theirs.close()


def test_negative_control_without_a_cancel_the_read_waits_on() -> None:
    from kazma_core.documents import worker_container

    ours, theirs = socket.socketpair()
    try:
        with pytest.raises(TimeoutError):
            worker_container._recv_exact(ours, 5, time.monotonic() + 1.5)
    finally:
        ours.close()
        theirs.close()


def test_a_cancelled_call_kills_its_local_worker(tmp_path) -> None:
    from kazma_core.documents.sandbox import SandboxRequest, run_isolated_subprocess

    marker = tmp_path / "still-running"
    script = (
        "import pathlib, sys, time\n"
        "for _ in range(200):\n"
        "    time.sleep(0.05)\n"
        f"pathlib.Path({str(marker)!r}).write_text('ran on')\n"
    )
    request = SandboxRequest(command=(sys.executable, "-c", script), work_dir=tmp_path,
                             timeout_seconds=30)

    async def call_and_give_up() -> None:
        task = asyncio.ensure_future(cancellation.run_cancellable(run_isolated_subprocess, request))
        await asyncio.sleep(1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    started = time.monotonic()
    asyncio.run(call_and_give_up())
    assert time.monotonic() - started < 15
    time.sleep(11)
    assert not marker.exists(), "the worker ran on after its caller gave up"


# ── The chat attachment reads the text layer only ───────────────────────


def test_a_scanned_attachment_is_named_not_ocrd(tmp_path, monkeypatch) -> None:
    from kazma_gateway.agent_handler import attachments

    calls: list[dict] = []

    def text_layer(self, path, *, approved_path):
        calls.append({"path": path})
        return _scan()

    monkeypatch.setattr(DocumentService, "text_layer_sync", text_layer)
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF-1.4 fixture")
    excerpt = attachments._try_parse_attachment(str(source))
    assert calls, "the excerpt did not read the text layer"
    assert "scanned document" in excerpt and f"all {PAGES} pages" in excerpt
    assert "ocr_document" in excerpt and "document_import" in excerpt
    assert not excerpt.startswith("Error")


def test_negative_control_the_old_excerpt_ran_ocr(tmp_path) -> None:
    """The old excerpt called read_transient_sync with OCR on: no text-layer
    switch reached the worker."""
    import inspect

    from kazma_gateway.agent_handler import attachments

    source = inspect.getsource(attachments._try_parse_attachment)
    assert "read_transient_sync(" not in source
    assert "text_layer_sync(" in source
