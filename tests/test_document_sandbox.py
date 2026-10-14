from __future__ import annotations

import os
import sys
import time
from io import BytesIO
from unittest.mock import Mock

import pytest
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
        process_limit=16,
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
        cpu_cores=1,
        process_limit=16,
    )
    process = Mock()
    process.stdout = BytesIO(b"ok")
    process.stderr = BytesIO()
    process.poll.return_value = 0
    process.returncode = 0
    job = Mock()
    captured: dict[str, object] = {}
    sequence = []
    process.stdin = Mock()
    process.stdin.write.side_effect = lambda _value: sequence.append("gate")

    def fake_popen(*args, **kwargs):
        sequence.append("spawn")
        captured["creationflags"] = kwargs["creationflags"]
        return process

    def fake_assign(candidate, memory_limit, *, cpu_cores, process_limit):
        sequence.append("assign")
        assert candidate is process
        captured["memory_limit"] = memory_limit
        captured["cpu_cores"] = cpu_cores
        captured["process_limit"] = process_limit
        return job, None

    monkeypatch.setattr(sandbox, "_validate_request", lambda candidate: tmp_path)
    monkeypatch.setattr(sandbox.os, "name", "nt")
    monkeypatch.setattr(sandbox.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(sandbox, "_assign_windows_job", fake_assign)
    monkeypatch.setattr(sandbox, "_resume_process", lambda candidate: sequence.append("resume") or True)

    result = sandbox.run_isolated_subprocess(request)

    assert result.resource_limits_enforced
    assert captured["memory_limit"] == 987_654
    assert captured["cpu_cores"] == 1
    assert captured["process_limit"] == 16
    assert int(captured["creationflags"]) & 0x200
    assert int(captured["creationflags"]) & 0x4  # CREATE_SUSPENDED until the job holds it
    assert sequence == ["spawn", "assign", "resume", "gate"]
    job.close.assert_called_once_with()


def test_windows_cpu_rate_and_process_quotas_are_set_on_the_job_object(monkeypatch):
    import ctypes
    import struct

    from kazma_core.documents import sandbox

    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("Windows API shape is verified by the Windows worker environment")

    calls = []

    class Api:
        def __init__(self, name):
            self.name = name

        def __call__(self, *args):
            if self.name == "CreateJobObjectW":
                return 7
            if self.name == "SetInformationJobObject":
                info_class, address, size = args[1], args[2], args[3]
                calls.append((info_class, ctypes.string_at(address, size)))
                return 1
            if self.name in {"AssignProcessToJobObject", "CloseHandle"}:
                return 1
            return 0

    class Kernel:
        def __getattr__(self, name):
            return Api(name)

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: Kernel())
    process = type("Child", (), {"_handle": 9})()
    job, reason = sandbox._assign_windows_job(
        process, 512 * 1024 * 1024, cpu_cores=2, process_limit=8
    )
    assert job is not None and reason is None
    cpu = next(raw for info_class, raw in calls if info_class == 15)
    flags, rate = struct.unpack("=II", cpu)
    assert flags == 0x1 | 0x4  # ENABLE | HARD_CAP
    # A share of the WHOLE machine: two cores of os.cpu_count().
    assert rate == max(1, 20_000 // (os.cpu_count() or 1))
    job.close()


def test_sandbox_rejects_invalid_cpu_and_process_limits(tmp_path):
    for kwargs in ({"cpu_cores": 0}, {"cpu_cores": -1},
                   {"cpu_cores": True}, {"process_limit": 0}, {"process_limit": True}):
        with pytest.raises(ValueError):
            run_isolated_subprocess(SandboxRequest(
                command=(sys.executable, "-c", "pass"), work_dir=tmp_path, **kwargs
            ))


def test_windows_hard_cpu_rate_and_process_limits_are_reported_as_enforced(tmp_path):
    from kazma_core.documents import sandbox

    request = SandboxRequest(
        command=("python", "-c", "pass"), work_dir=tmp_path,
        memory_limit_bytes=123_456, cpu_limit_seconds=2,
        cpu_cores=1, process_limit=16,
    )
    enforced, reason = sandbox._windows_limit_status(request, True, None)
    assert enforced and reason is None
    enforced, reason = sandbox._windows_limit_status(request, False, "Job setup failed")
    assert not enforced
    assert "Job setup failed" in (reason or "")
    assert "CPU-rate" in (reason or "")


def test_windows_job_setup_failure_kills_worker_before_parser_release(tmp_path, monkeypatch):
    from kazma_core.documents import sandbox

    request = SandboxRequest(
        command=("python", "-c", "print('must not run')"),
        work_dir=tmp_path,
        memory_limit_bytes=123_456,
        cpu_cores=1,
        process_limit=16,
    )
    process = Mock()
    process.stdin = Mock()
    process.stdout = BytesIO()
    process.stderr = BytesIO()
    stopped = []

    monkeypatch.setattr(sandbox, "_validate_request", lambda _request: tmp_path)
    monkeypatch.setattr(sandbox.os, "name", "nt")
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        sandbox, "_assign_windows_job", lambda *_args, **_kwargs: (None, "Job setup failed")
    )
    monkeypatch.setattr(sandbox, "_terminate_process_tree", lambda _process: stopped.append(True))

    result = sandbox.run_isolated_subprocess(request)

    assert result.returncode == 125
    assert not result.resource_limits_enforced
    assert "Job setup failed" in (result.resource_limit_degraded_reason or "")
    assert stopped == [True]
    process.stdin.write.assert_not_called()


# DOC-A12 (2026-10-13): the cap is measured on a real busy worker. Windows
# reads CpuRate as a share of the whole machine; the old 100 * 100 ("one
# core") was 100 % of it, no cap at all.
_BUSY = (
    "import hashlib, threading, time\n"
    "buf = b'x' * (1 << 20)\n"
    "def spin():\n"
    "    end = time.monotonic() + 2.0\n"
    "    while time.monotonic() < end:\n"
    "        hashlib.sha256(buf).digest()\n"
    "started = time.monotonic()\n"
    "threads = [threading.Thread(target=spin) for _ in range(4)]\n"
    "[t.start() for t in threads]\n"
    "[t.join() for t in threads]\n"
    "print(time.process_time() / (time.monotonic() - started))\n"
)


def _cores_used(tmp_path, monkeypatch) -> float:
    from kazma_core.documents import sandbox

    real_assign = sandbox._assign_windows_job

    def slow_assign(*args, **kwargs):
        # A late assignment is what let the first run of a process escape:
        # made deterministic, the worker must still be inside the job.
        time.sleep(0.5)
        return real_assign(*args, **kwargs)

    monkeypatch.setattr(sandbox, "_assign_windows_job", slow_assign)
    result = run_isolated_subprocess(SandboxRequest(
        command=(sys.executable, "-I", "-c", _BUSY), work_dir=tmp_path, timeout_seconds=30,
        memory_limit_bytes=512 * 1024 * 1024, cpu_cores=1, process_limit=8,
    ))
    assert result.returncode == 0, result.stderr
    assert result.resource_limits_enforced
    return float(result.stdout.decode().strip())


def _real_windows_with_cores() -> None:
    if os.name != "nt":
        pytest.skip("Windows Job Object CPU cap")
    if (os.cpu_count() or 1) < 4:
        pytest.skip("needs four logical CPUs to tell one core from four")


def test_a_busy_worker_is_held_to_one_core(tmp_path, monkeypatch):
    _real_windows_with_cores()
    assert _cores_used(tmp_path, monkeypatch) < 1.5


def test_the_old_rate_does_not_cap_a_busy_worker(tmp_path, monkeypatch):
    # Negative control: CpuRate 10,000 (the old "one core") lets all four run.
    from kazma_core.security import process_budget

    _real_windows_with_cores()
    monkeypatch.setattr(process_budget, "job_cpu_rate", lambda cores, **_kwargs: 10_000)
    assert _cores_used(tmp_path, monkeypatch) > 2.5


def test_a_worker_not_started_suspended_escapes_a_late_job(tmp_path, monkeypatch):
    # Negative control: the old launch (running at once, gate byte only).
    # The venv launcher starts the interpreter before the late job exists.
    from kazma_core.documents import sandbox

    _real_windows_with_cores()
    monkeypatch.setattr(sandbox, "_CREATE_SUSPENDED", 0)
    assert _cores_used(tmp_path, monkeypatch) > 2.5


def test_job_cpu_rate_is_a_share_of_the_machine():
    from kazma_core.security.process_budget import job_cpu_rate

    assert job_cpu_rate(1, cpu_count=32) == 312
    assert job_cpu_rate(2, cpu_count=32) == 625
    assert job_cpu_rate(1, cpu_count=1) == 10_000
    assert job_cpu_rate(8, cpu_count=4) == 10_000
    assert job_cpu_rate(1, cpu_count=20_000) == 1
    for bad in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            job_cpu_rate(bad)


_HOG = "b = bytearray(768 * 1024 * 1024)\nprint(len(b))\n"


def _hog_exit(tmp_path, monkeypatch) -> int:
    from kazma_core.documents import sandbox

    real_assign = sandbox._assign_windows_job

    def slow_assign(*args, **kwargs):
        time.sleep(0.5)
        return real_assign(*args, **kwargs)

    monkeypatch.setattr(sandbox, "_assign_windows_job", slow_assign)
    result = run_isolated_subprocess(SandboxRequest(
        command=(sys.executable, "-I", "-c", _HOG), work_dir=tmp_path, timeout_seconds=30,
        memory_limit_bytes=256 * 1024 * 1024, cpu_cores=1, process_limit=8,
    ))
    assert result.resource_limits_enforced
    return result.returncode


def test_a_late_job_still_holds_the_memory_limit(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows Job Object memory limit")
    assert _hog_exit(tmp_path, monkeypatch) != 0


def test_a_worker_not_started_suspended_escapes_the_memory_limit(tmp_path, monkeypatch):
    # Negative control: the old launch allocates 768 MiB under a 256 MiB job.
    from kazma_core.documents import sandbox

    if os.name != "nt":
        pytest.skip("Windows Job Object memory limit")
    monkeypatch.setattr(sandbox, "_CREATE_SUSPENDED", 0)
    assert _hog_exit(tmp_path, monkeypatch) == 0
