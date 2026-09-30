"""One restic run per repository at a time (2026-09-30).

The universal backup's snapshot and the maintenance task's ``forget --prune``
/ ``check`` are separate durable-queue tasks, and they ran side by side.
``forget`` and ``check`` lock a repository exclusively, so whichever came
second failed with "repository is already locked": a skipped offsite
snapshot on 2026-09-28 04:07, a skipped remote forget on 2026-09-29 22:21.
``restic_repo._run`` now holds a lock per repository, and passes restic
``--retry-lock`` for a run started outside Kazma.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from kazma_core.backup import restic_repo as rr


@pytest.fixture
def fake_restic(monkeypatch):
    """restic that takes a moment, and counts how many run at once per repo."""
    state = {"now": {}, "most": {}, "argv": [], "total": 0, "most_total": 0}
    guard = threading.Lock()

    def run(argv, env=None, **_kw):
        repo = env["RESTIC_REPOSITORY"]
        with guard:
            state["argv"].append(list(argv))
            state["now"][repo] = state["now"].get(repo, 0) + 1
            state["most"][repo] = max(state["most"].get(repo, 0), state["now"][repo])
            state["total"] += 1
            state["most_total"] = max(state["most_total"], state["total"])
        time.sleep(0.15)
        with guard:
            state["now"][repo] -= 1
            state["total"] -= 1
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(rr, "restic_available", lambda: True)
    monkeypatch.setattr(rr, "remote_writable", lambda repo, **_: (True, ""))
    monkeypatch.setattr(rr.subprocess, "run", run)
    return state


def _concurrently(*calls) -> None:
    threads = [threading.Thread(target=c) for c in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)


def test_a_snapshot_and_a_prune_of_one_repo_never_overlap(fake_restic):
    _concurrently(
        lambda: rr._run(["backup", "x"], "rclone:r2:kazma", "pw", action="backup"),
        lambda: rr._run(["forget", "--prune"], "rclone:r2:kazma", "pw", action="forget"),
        lambda: rr._run(["check"], "rclone:r2:kazma", "pw", action="check"),
    )
    assert fake_restic["most"]["rclone:r2:kazma"] == 1


def test_two_repositories_still_run_side_by_side(fake_restic):
    _concurrently(
        lambda: rr._run(["backup", "x"], "C:/local-repo", "pw", action="backup"),
        lambda: rr._run(["backup", "x"], "rclone:r2:kazma", "pw", action="backup"),
    )
    assert fake_restic["most"] == {"C:/local-repo": 1, "rclone:r2:kazma": 1}
    # ...and ran at the same time: the lock is per repository, not global.
    assert fake_restic["most_total"] == 2


def test_restic_waits_for_a_lock_taken_outside_kazma(fake_restic):
    rr._run(["snapshots", "--json"], "C:/local-repo", "pw", action="snapshots")
    argv = fake_restic["argv"][0]
    assert argv[:3] == ["restic", "--retry-lock", rr._RETRY_LOCK]
    assert argv[3:] == ["snapshots", "--json"]


def test_without_the_lock_the_runs_collide(fake_restic, monkeypatch):
    """Negative control: a fresh lock per call is no lock -- they overlap."""
    monkeypatch.setattr(rr, "_repo_lock", lambda repo: threading.Lock())
    _concurrently(
        lambda: rr._run(["backup", "x"], "rclone:r2:kazma", "pw", action="backup"),
        lambda: rr._run(["forget", "--prune"], "rclone:r2:kazma", "pw", action="forget"),
    )
    assert fake_restic["most"]["rclone:r2:kazma"] == 2
