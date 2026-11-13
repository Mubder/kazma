"""A Tesseract call keeps to the worker's thread budget.

The document sandbox passes a child only the variables it names, so
Tesseract never saw the worker's ``OMP_NUM_THREADS=1`` and started a thread
per CPU it could see, whatever the worker's CPU limit. Measured in the
document worker image, eight reads four at once at one CPU: 4.8-6.0 s against
2.5-3.2 s with the limit (found from CI run 43353265350, 2026-11-13, where an
Arabic table page's call ran past its 60 s limit; that stall itself was not
reproduced). Every call now names ``OMP_THREAD_LIMIT``
(``ocr.tesseract._thread_limit``), which Tesseract reads. The old request,
without it, is the negative control.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kazma_core.documents import sandbox
from kazma_core.documents.ocr import tesseract

_PRINT_LIMIT = ("-c", "import os; print(os.environ.get('OMP_THREAD_LIMIT'))")


@pytest.mark.parametrize("budget, expected", [(None, "1"), ("1", "1"), ("3", "3"), ("abc", "1"), ("0", "1")])
def test_the_limit_is_the_callers_budget(monkeypatch, budget, expected):
    if budget is None:
        monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    else:
        monkeypatch.setenv("OMP_NUM_THREADS", budget)
    assert tesseract._thread_limit() == expected


def test_every_tesseract_call_names_the_limit(monkeypatch, tmp_path):
    seen: list[sandbox.SandboxRequest] = []

    def run(request):
        seen.append(request)
        return sandbox.SandboxResult(command=request.command, returncode=0, stdout=b"", stderr=b"",
                                     duration_seconds=0.0, timed_out=False, output_limit_exceeded=False,
                                     resource_limits_enforced=True, resource_limit_degraded_reason=None)

    monkeypatch.setattr(tesseract, "run_isolated_subprocess", run)
    monkeypatch.setenv("OMP_NUM_THREADS", "1")
    tesseract.TesseractProvider(executable="tesseract")._run(
        ("page.png", "stdout", "tsv"), work_dir=tmp_path, timeout=5, stdout_limit=1024)
    assert seen and dict(seen[0].env or {}) == {"OMP_THREAD_LIMIT": "1"}


def test_the_child_sees_the_limit_through_the_real_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "1")
    provider = tesseract.TesseractProvider(executable=sys.executable)
    result = provider._run(_PRINT_LIMIT, work_dir=tmp_path, timeout=30, stdout_limit=4096)
    assert result.stdout.decode().strip() == "1"


def test_negative_control_the_old_request_passed_the_child_no_limit(tmp_path, monkeypatch):
    """The request as it was: the worker's own OMP_NUM_THREADS does not
    reach the child, and neither does a limit."""
    monkeypatch.setenv("OMP_NUM_THREADS", "1")
    monkeypatch.setenv("OMP_THREAD_LIMIT", "1")
    old = sandbox.SandboxRequest(command=(sys.executable, *_PRINT_LIMIT), work_dir=Path(tmp_path),
                                 timeout_seconds=30, stdout_limit_bytes=4096, stderr_limit_bytes=65_536)
    assert sandbox.run_isolated_subprocess(old).stdout.decode().strip() == "None"
