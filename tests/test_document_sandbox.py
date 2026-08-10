from __future__ import annotations

import os
import sys
from io import BytesIO
from unittest.mock import Mock

from kazma_core.documents.sandbox import SandboxRequest, run_isolated_subprocess


def test_sandbox_times_out_and_terminates_process(tmp_path) -> None:
    result = run_isolated_subprocess(
        SandboxRequest(
            command=(sys.executable, "-c", "import time; time.sleep(5)"),
            work_dir=tmp_path,
            timeout_seconds=0.1,
        )
    )
    assert result.timed_out
    assert result.returncode != 0
    assert result.duration_seconds < 3


def test_sandbox_caps_output_bytes(tmp_path) -> None:
    result = run_isolated_subprocess(
        SandboxRequest(
            command=(sys.executable, "-c", "print('x' * 100000)"),
            work_dir=tmp_path,
            timeout_seconds=2,
            stdout_limit_bytes=128,
            stderr_limit_bytes=128,
        )
    )
    assert result.output_limit_exceeded
    assert len(result.stdout) == 128


def test_sandbox_scrubs_inherited_secrets_and_allows_explicit_safe_env(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("KAZMA_DOCUMENT_SECRET", "must-not-leak")
    script = (
        "import os; "
        "print(os.getenv('KAZMA_DOCUMENT_SECRET', 'missing')); "
        "print(os.getenv('DOCUMENT_MODE', 'missing'))"
    )
    result = run_isolated_subprocess(
        SandboxRequest(
            command=(sys.executable, "-c", script),
            work_dir=tmp_path,
            timeout_seconds=2,
            env={"DOCUMENT_MODE": "sandboxed"},
        )
    )
    assert result.returncode == 0
    assert result.stdout.decode().splitlines() == ["missing", "sandboxed"]
    assert b"must-not-leak" not in result.stdout + result.stderr


def test_resource_limit_support_is_explicit(tmp_path) -> None:
    result = run_isolated_subprocess(
        SandboxRequest(
            command=(sys.executable, "-c", "print('ok')"),
            work_dir=tmp_path,
            timeout_seconds=2,
            memory_limit_bytes=256 * 1024 * 1024,
            cpu_limit_seconds=1,
        )
    )
    if os.name == "nt":
        assert not result.resource_limits_enforced
        assert "CPU-time quota" in (result.resource_limit_degraded_reason or "")
    else:
        assert result.resource_limits_enforced
        assert result.resource_limit_degraded_reason is None


def test_windows_limit_status_reports_partial_cpu_degradation(tmp_path) -> None:
    from kazma_core.documents import sandbox

    request = SandboxRequest(
        command=("python", "-c", "pass"),
        work_dir=tmp_path,
        memory_limit_bytes=123_456,
        cpu_limit_seconds=2,
    )
    enforced, reason = sandbox._windows_limit_status(request, True, None)

    assert not enforced
    assert "CPU-time quota" in (reason or "")
    assert "memory" not in (reason or "").lower()


def test_windows_job_handle_closes_exactly_once() -> None:
    from kazma_core.documents.sandbox import _WindowsJobHandle

    close = Mock()
    job = _WindowsJobHandle(42, close)
    job.close()
    job.close()

    close.assert_called_once_with(42)


def test_windows_memory_job_is_retained_until_process_completion(
    tmp_path, monkeypatch
) -> None:
    from kazma_core.documents import sandbox

    request = SandboxRequest(
        command=("python", "-c", "pass"),
        work_dir=tmp_path,
        memory_limit_bytes=987_654,
    )
    process = Mock()
    process.stdout = BytesIO(b"ok")
    process.stderr = BytesIO()
    process.poll.return_value = 0
    process.returncode = 0
    job = Mock()
    captured: dict[str, object] = {}

    def fake_popen(*args, **kwargs):
        captured["creationflags"] = kwargs["creationflags"]
        return process

    def fake_assign(candidate, memory_limit):
        assert candidate is process
        captured["memory_limit"] = memory_limit
        return job, None

    monkeypatch.setattr(sandbox, "_validate_request", lambda candidate: tmp_path)
    monkeypatch.setattr(sandbox.os, "name", "nt")
    monkeypatch.setattr(sandbox.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(sandbox, "_assign_windows_job", fake_assign)

    result = sandbox.run_isolated_subprocess(request)

    assert result.resource_limits_enforced
    assert captured["memory_limit"] == 987_654
    assert int(captured["creationflags"]) & 0x200
    job.close.assert_called_once_with()
