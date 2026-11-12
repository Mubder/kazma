"""A restic run Kazma's own stop ended is not a repository that failed.

Live 2026-11-12: a deploy's reload stopped the server while its six-hourly
remote ``restic check`` ran. The guard stops the server with CTRL_BREAK to its
whole process group; restic was in that group and quit mid-check, and Kazma
logged "restic check FAILED for remote", paged "The remote backup repository
failed verification", and the deploy script read the ERROR and reverted a good
commit. restic now starts in its own process group, and a run that fails while
Kazma is stopping is marked interrupted: said at INFO, never alerted.
"""

from __future__ import annotations

import asyncio
import os
import subprocess

import pytest

from kazma_core.backup import restic_repo


@pytest.fixture
def restic(monkeypatch):
    calls = []

    def run(argv, **kw):
        calls.append(kw)
        return subprocess.CompletedProcess(argv, 1, "check snapshots, trees and blobs\n[0:00] 0.00%  0 / 10 snapshots",
                                           "")

    monkeypatch.setattr(restic_repo, "restic_available", lambda: True)
    monkeypatch.setattr(restic_repo, "remote_writable", lambda repo: (True, ""))
    monkeypatch.setattr(restic_repo.subprocess, "run", run)
    return calls


def test_restic_runs_outside_the_servers_process_group(restic):
    restic_repo.check("rclone:remote:kazma", "pw")
    kw = restic[-1]
    if os.name == "nt":
        assert kw["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert kw["start_new_session"] is True


@pytest.fixture
def stopping():
    """Kazma's own stop flag, set as ``signal_shutdown`` sets it, without its
    stop listeners (one arms the process's exit deadline)."""
    from kazma_core import shutdown

    shutdown._get_event().set()
    try:
        yield
    finally:
        shutdown.reset_shutdown()


def test_a_run_that_fails_while_kazma_stops_is_interrupted(restic, stopping):
    res = restic_repo.check("rclone:remote:kazma", "pw")
    assert not res.ok and res.interrupted and "Kazma's own stop" in res.error
    assert res.as_dict()["interrupted"] is True


def test_control_the_same_failure_while_running_is_a_failure(restic):
    res = restic_repo.check("rclone:remote:kazma", "pw")
    assert not res.ok and not res.interrupted


@pytest.mark.parametrize("interrupted, alerted", [(True, False), (False, True)])
def test_the_maintenance_pages_only_a_real_failure(monkeypatch, interrupted, alerted):
    from kazma_core.memory import worker_bootstrap as wb

    monkeypatch.setattr(restic_repo, "restic_available", lambda: True)
    monkeypatch.setattr(restic_repo, "ensure_password", lambda: ("pw", "file"))
    monkeypatch.setattr(restic_repo, "repo_paths", lambda: {"remote": "rclone:remote:kazma"})
    monkeypatch.setattr(restic_repo, "unlock_stale", lambda repo, password: None)
    monkeypatch.setattr(restic_repo, "forget_prune",
                        lambda repo, password: restic_repo.ResticResult(ok=True, action="forget"))
    monkeypatch.setattr(restic_repo, "check", lambda repo, password: restic_repo.ResticResult(
        ok=False, action="check", error="check stopped", interrupted=interrupted))
    pages = []
    monkeypatch.setattr(wb, "_alert_repo_unhealthy", lambda name, error: pages.append(name))
    assert asyncio.run(wb._handle_restic_maintenance({})) is True
    assert (pages == ["remote"]) is alerted
