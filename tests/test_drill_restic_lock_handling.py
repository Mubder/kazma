"""A locked restic repository is two different events, not one.

``restic check`` refuses with "unable to create lock in backend: repository is
already locked by PID N on <host>" in both of these cases, and the drill used
to call both a failure:

* **A live backup holds it.** A backup in progress is health. Alerting "a
  backup cannot be restored" because one was being written is the false alarm
  this drill exists not to be. Observed 2026-09-21: a deep run collided with
  the scheduled pg snapshot, reported 3/4, and the repository was provably
  fine seconds later -- zero locks, and a fresh snapshot already written.

* **A dead owner left it.** This one is dangerous and the opposite of a false
  alarm. ``restic_repo.unlock_stale`` spells it out: a killed restic leaves an
  EXCLUSIVE lock and every later backup fails, "while the schedule keeps
  reporting that it ran". Kazma is restarted mid-backup regularly, and a lock
  was cleared by hand once already (2026-08-29).

So the drill now clears the dead locks -- which repairs the backups, not just
the check -- and only then, if a lock survives, treats it as a live backup and
declines to call it a restore failure.

The third case must keep working: a genuine corruption failure is still a
failure. A fix that swallowed every ``check`` error would pass the first two
tests here and destroy the point of the drill.
"""

from __future__ import annotations

import pytest

from kazma_core.backup import restore_drill as rd


class _Res:
    def __init__(self, ok: bool, error: str = "") -> None:
        self.ok = ok
        self.error = error


LOCKED = (
    "unable to create lock in backend: repository is already locked by "
    "PID 60684 on bAlfaris by bAlfaris\\balfa (UID 0, GID 0)"
)


@pytest.fixture
def restic(monkeypatch):
    """Stub the restic layer; record what the drill asked it to do."""
    calls: list[str] = []
    state = {"checks": [], "unlocked": 0}

    class FakeRepo:
        @staticmethod
        def restic_available() -> bool:
            return True

        @staticmethod
        def ensure_password():
            return ("pw", False)

        @staticmethod
        def repo_paths():
            return {"remote": "s3:https://example/bucket"}

        @staticmethod
        def check(repo, password, *, read_data=False, read_data_subset=""):
            calls.append("check")
            return state["checks"].pop(0)

        @staticmethod
        def unlock_stale(repo, password):
            calls.append("unlock")
            state["unlocked"] += 1
            return _Res(True)

    # `_check_restic_data` does `from kazma_core.backup import restic_repo`,
    # which reads the ATTRIBUTE on the package, not sys.modules. Once any
    # earlier test has imported the real module the attribute exists, and
    # patching sys.modules alone is silently ignored -- these tests passed
    # alone and failed inside the backup suite until both were patched. That
    # is the same import-caching trap that makes a monkeypatch look like it
    # held when it did not, so patch both and let neither be the load-bearing
    # one.
    import sys

    import kazma_core.backup as pkg

    monkeypatch.setattr(pkg, "restic_repo", FakeRepo, raising=False)
    monkeypatch.setitem(sys.modules, "kazma_core.backup.restic_repo", FakeRepo)
    return state, calls


def _detail(res, name):
    hits = [c for c in res.checks if c.get("check") == name]
    assert hits, f"no {name} check recorded: {res.checks}"
    return hits[-1]


def test_a_stale_lock_is_cleared_and_the_check_then_runs(restic):
    """The dangerous case: clearing it repairs the BACKUPS, not just the drill."""
    state, calls = restic
    state["checks"] = [_Res(False, LOCKED), _Res(True)]

    res = rd.DrillResult(backup_dir="(deep)")
    rd._check_restic_data(res)

    assert state["unlocked"] == 1, "a stale lock was never cleared"
    assert calls == ["check", "unlock", "check"], calls
    got = _detail(res, "restic:remote")
    assert got["ok"] is True
    assert "re-read" in got["detail"], got


def test_a_live_backup_is_not_reported_as_a_restore_failure(restic):
    """The false alarm: still locked after clearing the dead ones."""
    state, calls = restic
    state["checks"] = [_Res(False, LOCKED), _Res(False, LOCKED)]

    res = rd.DrillResult(backup_dir="(deep)")
    rd._check_restic_data(res)

    assert state["unlocked"] == 1
    got = _detail(res, "restic:remote")
    assert got["ok"] is True, (
        "a backup being written was reported as 'a backup cannot be restored'"
    )
    assert "NOT re-read" in got["detail"], (
        "the skip must say the packs were not verified rather than claim a "
        "re-read that never happened"
    )


def test_a_real_corruption_failure_still_fails(restic):
    """The counterweight — without this, 'swallow everything' passes."""
    state, calls = restic
    state["checks"] = [_Res(False, "pack 1a2b3c: invalid data returned")]

    res = rd.DrillResult(backup_dir="(deep)")
    rd._check_restic_data(res)

    assert state["unlocked"] == 0, "a corruption error must not trigger unlock"
    got = _detail(res, "restic:remote")
    assert got["ok"] is False, "genuine repository corruption was swallowed"
    assert "invalid data" in got["detail"]


@pytest.mark.parametrize(
    "text,expected",
    [
        (LOCKED, True),
        ("unable to create lock in backend: ...", True),
        ("Repository Is Already Locked by PID 9", True),
        ("pack 1a2b3c: invalid data returned", False),
        ("", False),
        (None, False),
    ],
)
def test_lock_error_detection(text, expected):
    assert rd._is_locked_error(text) is expected
