"""A stopping document worker ends its stage's isolated process at once.

Roadmap theme 2 (2026-11), found by the graceful reload drill
(``tests/test_drill_graceful_reload.py``): on a reload the document worker
gave its OCR job back ("on the same attempt", FA-264) while the job's OCR
process ran on to its last page, and the server's exit waited for the thread
holding it -- past the guard's grace, so the reload was killed and paged. The
stage's own stop signal (``StageContext``) was never the sandbox's cancel
signal (``documents.cancellation``): only ``run_cancellable`` bound that.

The worker now runs each stage with its event bound as the cancel signal
(``call_with_cancel``). A stage running a real sandboxed process is stopped;
the process is gone within seconds. The stage called the old way -- no
signal bound -- is the negative control: its process outlives the stop.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time

import pytest

from kazma_core.documents import cancellation
from kazma_core.documents.models import DocumentJobState
from kazma_core.documents.sandbox import SandboxRequest, run_isolated_subprocess
from kazma_core.documents.worker import DocumentWorker, StageResult
from tests.test_document_worker import _ready_job

pytestmark = pytest.mark.timeout(120)

#: The stand-in for a long OCR run: thirty seconds of a real process.
_LONG = "import time; time.sleep(30)"


def _alive(pid: int) -> bool:
    import psutil

    try:
        return psutil.Process(pid).is_running() and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


async def _stop_during_a_long_run(tmp_path, monkeypatch, *, bind: bool) -> tuple[float, list[int], float]:
    """Run a stage that starts a 30 s sandboxed process, stop the worker,
    and return how long the process outlived the stop (None: still alive
    at the 10 s check), the pids seen, and the stop's duration."""
    metadata, jobs, job = _ready_job(tmp_path, key=f"sandbox-stop-{bind}")
    started = threading.Event()
    pids: list[int] = []
    finished = threading.Event()

    def long_ocr(_job, _context):
        import psutil

        def watch() -> None:
            # The sandbox's process, as soon as it exists: a child of this one.
            end = time.monotonic() + 10
            me = psutil.Process()
            while time.monotonic() < end and not pids:
                for child in me.children(recursive=True):
                    if "sleep(30)" in " ".join(child.cmdline()):
                        pids.append(child.pid)
                if not pids:
                    time.sleep(0.05)
            started.set()

        threading.Thread(target=watch, daemon=True).start()
        try:
            run_isolated_subprocess(SandboxRequest(
                command=(sys.executable, "-c", _LONG), work_dir=tmp_path, timeout_seconds=60,
            ))
        except cancellation.WorkerCancelled:
            pass
        finally:
            finished.set()
        return StageResult(DocumentJobState.NORMALIZING)

    worker = DocumentWorker(jobs, {DocumentJobState.PARSING: long_ocr}, concurrency=1,
                            poll_interval=0.01, lease_seconds=60, heartbeat_interval=0.05,
                            shutdown_grace_seconds=30)
    if not bind:
        # The old stage call: the handler ran with no cancel signal bound.
        from kazma_core.documents import worker as worker_mod

        def unbound(event, fn, *args, **kwargs):
            return fn(*args, **kwargs)

        monkeypatch.setattr(worker_mod, "call_with_cancel", unbound)
    try:
        await worker.start()
        await asyncio.to_thread(started.wait, 15)
        assert pids, "the sandboxed process never started"
        loop = asyncio.get_running_loop()
        began = loop.time()
        await worker.stop()
        took = loop.time() - began
        stopped_at = time.monotonic()
        outlived = None
        while time.monotonic() - stopped_at < 10:
            if not any(_alive(pid) for pid in pids):
                outlived = time.monotonic() - stopped_at
                break
            await asyncio.sleep(0.1)
        return outlived, pids, took
    finally:
        for pid in pids:
            try:
                import psutil

                psutil.Process(pid).kill()
            except Exception:  # noqa: BLE001 -- already gone
                pass
        await asyncio.to_thread(finished.wait, 15)
        metadata.close()


async def test_a_stop_ends_the_stages_sandboxed_process(tmp_path, monkeypatch):
    outlived, pids, took = await _stop_during_a_long_run(tmp_path, monkeypatch, bind=True)
    assert outlived is not None and outlived < 3.0, (outlived, pids)
    assert took < 6, took


async def test_negative_control_without_the_signal_the_process_runs_on(tmp_path, monkeypatch):
    outlived, pids, _took = await _stop_during_a_long_run(tmp_path, monkeypatch, bind=False)
    assert outlived is None, "the old stage call ended its process: the control proves nothing"
