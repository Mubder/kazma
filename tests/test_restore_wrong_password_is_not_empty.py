"""A repository restic cannot read is not a repository with nothing in it.

Audit 2026-10-26 AUD-044: ``backup/restore._snapshots`` ignored restic's
return code and parsed whatever stdout held: a wrong passphrase (restic
exits 1, prints nothing on stdout) read as ``[]``, so ``--list`` said "No
restore points found" and ``restore_files`` said "no restorable generations
found in the repository" -- at the one moment someone needs a restore.

Now a nonzero exit, a restic that cannot run, or an unreadable listing
raises ``_SnapshotListError`` naming restic's own message, and both the
restore and the CLI say that. Negative control: the old reader, replayed.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from kazma_core.backup import restore as restore_mod

_WRONG = "Fatal: wrong password or no key found\n"


def _restic(monkeypatch, *, returncode: int, stdout: str = "", stderr: str = "") -> None:
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(restore_mod.subprocess, "run", run)


def test_a_wrong_passphrase_raises_with_restics_message(monkeypatch):
    _restic(monkeypatch, returncode=1, stderr=_WRONG)
    with pytest.raises(restore_mod._SnapshotListError, match="wrong password"):
        restore_mod.list_restore_points("repo", "not-the-passphrase")


def test_the_restore_says_it_could_not_read_the_repository(monkeypatch, tmp_path):
    _restic(monkeypatch, returncode=1, stderr=_WRONG)
    res = restore_mod.restore_files("repo", "pw", tmp_path / "target")
    assert res.ok is False
    assert "wrong password" in res.error
    assert "no restorable generations" not in res.error


def test_the_cli_list_exits_nonzero_and_names_the_cause(monkeypatch, capsys):
    import kazma_core.backup.restic_repo as rr

    _restic(monkeypatch, returncode=1, stderr=_WRONG)
    monkeypatch.setattr(rr, "ensure_password", lambda: ("pw", "env"))
    assert restore_mod.main(["--list", "--repo", "repo"]) == 2
    out = capsys.readouterr().out
    assert "wrong password" in out
    assert "No restore points found" not in out


def test_an_unreadable_listing_is_an_error_too(monkeypatch):
    _restic(monkeypatch, returncode=0, stdout="not json")
    with pytest.raises(restore_mod._SnapshotListError, match="unreadable"):
        restore_mod.list_restore_points("repo", "pw")


def test_an_empty_repository_is_still_empty(monkeypatch):
    _restic(monkeypatch, returncode=0, stdout="[]")
    assert restore_mod.list_restore_points("repo", "pw") == []


def test_negative_control_the_old_reader_calls_a_bad_passphrase_empty(monkeypatch):
    _restic(monkeypatch, returncode=1, stderr=_WRONG)
    out = subprocess.run(["restic", "snapshots", "--json"], capture_output=True, text=True, check=False).stdout
    assert json.loads(out or "[]") == []
