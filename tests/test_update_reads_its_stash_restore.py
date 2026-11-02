"""``kazma update`` says when the owner's local edits could not be put back (FA-101).

The 2026-11 functional audit (offline hunt): a checkout with an uncommitted
edit to a line the update also changes. The update stashed the edit, reset
to ``origin/main``, popped the stash -- which conflicted -- and reported
success, with ``<<<<<<< Updated upstream`` in ``kazma.yaml`` for the next
boot to read and the edits in a stash nobody was told about. The pop's
answer was dropped, and the postflight checked HEAD and the CLI import only.

Real git, a real origin; the server hold and the package reinstall stubbed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kazma_cli import update


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A clone one commit behind its origin, with a conflicting local edit."""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "init", "-b", "main", str(seed))
    for repo in (seed,):
        _git(repo, "config", "user.email", "t@example.test")
        _git(repo, "config", "user.name", "t")
    (seed / "kazma.yaml").write_text("agent:\n  language: en\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "one")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "origin", "main")
    _git(tmp_path, "clone", str(origin), str(clone))
    _git(clone, "config", "user.email", "t@example.test")
    _git(clone, "config", "user.name", "t")
    (seed / "kazma.yaml").write_text("agent:\n  language: fr\n", encoding="utf-8")
    _git(seed, "commit", "-am", "two")
    _git(seed, "push", "origin", "main")
    (clone / "kazma.yaml").write_text("agent:\n  language: ar\n", encoding="utf-8")  # the owner's edit

    monkeypatch.setattr(update, "_find_git_root", lambda: clone)
    monkeypatch.setattr(update, "_origin_is_trusted", lambda cwd: (True, "github.com/mubder/kazma"))
    monkeypatch.setattr(update, "_preflight_git", lambda cwd, **kw: (True, "main"))
    for name in ("_write_update_state", "_clear_update_state"):
        monkeypatch.setattr(update, name, lambda *a, **k: None)
    monkeypatch.setattr(update, "_read_update_state", lambda cwd: {})

    class _Hold:
        held = False

        def __init__(self, command):
            pass

        def preview(self):
            return True, ""

        def stop_for_update(self):
            return True

        def resume_after_update(self, ok):
            return True

    monkeypatch.setattr(update, "_ServerHold", _Hold)
    monkeypatch.setattr(update, "_reinstall_after_reset", lambda cwd, from_commit, stash_msg: True)
    return clone


def test_a_conflicting_stash_pop_is_not_reported_as_success(checkout):
    outcome = update.do_git_update()
    assert outcome == update.EDITS_NOT_RESTORED
    text = (checkout / "kazma.yaml").read_text(encoding="utf-8")
    assert "<<<<<<<" not in text and "language: fr" in text  # the updated code, clean
    assert update._STASH_MSG_PREFIX in _git(checkout, "stash", "list")  # the edit is kept


def test_the_cli_ends_with_its_own_status(checkout, monkeypatch, capsys):
    """Not "Update complete!": the edits are in the stash, exit status 3."""
    monkeypatch.setattr(update, "_fresh_version_report", lambda cwd: ("0.0", "abc"))
    monkeypatch.setattr(update, "check_git_behind", lambda: (1, []))
    monkeypatch.setattr(update, "_refuse_or_note", lambda command: "")
    with pytest.raises(SystemExit) as stop:
        update._run_git_check_and_update("0.0", False, False, True)
    assert stop.value.code == 3
    out = capsys.readouterr().out
    assert "NOT reapplied" in out and "Update complete" not in out


def test_the_postflight_refuses_a_tree_in_a_conflict(checkout):
    """The old pop left the tree in a conflict; the postflight now says so."""
    _git(checkout, "stash", "push", "-m", "edit")
    _git(checkout, "fetch", "origin")
    _git(checkout, "reset", "--hard", "origin/main")
    subprocess.run(["git", "stash", "pop"], cwd=checkout, capture_output=True)
    assert "<<<<<<<" in (checkout / "kazma.yaml").read_text(encoding="utf-8")
    assert update._unmerged_paths(str(checkout)) == ["kazma.yaml"]
    assert update._postflight_ok(str(checkout)) is False


def test_the_old_flow_reported_success(checkout, monkeypatch):
    """Negative control: with the pop's answer dropped, as before, the
    update said it worked over the conflict."""
    real = update._restore_stash

    def old_restore(cwd, stash_message=None):
        cmd = ["git", "stash", "pop"]
        subprocess.run(cmd, cwd=cwd, capture_output=True)
        return True  # the answer was never read

    monkeypatch.setattr(update, "_restore_stash", old_restore)
    assert update.do_git_update() is True
    assert "<<<<<<<" in (checkout / "kazma.yaml").read_text(encoding="utf-8")
    assert real is not old_restore
