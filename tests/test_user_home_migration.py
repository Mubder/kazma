"""The migration that ate a passphrase.

`migrate_legacy_user_home` moves ~/.kazma into `<project root>/.kazma` and
deletes the original. `get_project_root()` finds the project by walking up
for a pyproject.toml -- so running Kazma from ANY copy (a test clone, a CI
checkout, an agent scratchpad) makes that copy the project root, and the
user's global state is moved into something disposable.

Live, 2026-08-29: a session running from
%TEMP%\\claude\\...\\scratchpad\\base took ~/.kazma with it, including the
restic passphrase that decrypts every backup, local and offsite. Temp was
cleaned. The marker file the migration politely left behind is what
identified the cause hours later.

A migration is a one-way move of the only copy. These tests keep it aimed
somewhere that still exists tomorrow.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from kazma_core.paths import _is_throwaway, migrate_legacy_user_home

# ── what counts as throwaway ──────────────────────────────────────────


def test_a_temp_scratchpad_is_throwaway():
    """The exact shape that caused the loss."""
    p = (Path(tempfile.gettempdir()) / "claude" / "sess" / "scratchpad"
         / "base" / ".kazma")
    assert _is_throwaway(p) is True


def test_a_real_install_is_not_throwaway(tmp_path, monkeypatch):
    """A genuine install must still migrate -- the feature is wanted."""
    monkeypatch.setenv("TEMP", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("TMP", str(tmp_path / "elsewhere"))
    assert _is_throwaway(Path.home() / "kazma" / ".kazma") is False


def test_detection_survives_a_missing_path():
    """The target does not exist yet at decision time -- that is the point
    of deciding before the move."""
    p = Path(tempfile.gettempdir()) / "does-not-exist-yet" / ".kazma"
    assert _is_throwaway(p) is True


def test_detection_never_raises_on_a_bad_path():
    assert _is_throwaway(Path("\x00nonsense")) in (True, False)


# ── the refusal ───────────────────────────────────────────────────────


def test_migration_refuses_a_temp_target(tmp_path, monkeypatch, caplog):
    """Legacy state must survive a run from a disposable copy."""
    import logging

    legacy = tmp_path / "home" / ".kazma"
    legacy.mkdir(parents=True)
    (legacy / "restic.pass").write_text("a-passphrase", encoding="utf-8")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr("kazma_core.paths.get_project_root",
                        lambda: Path(tempfile.gettempdir()) / "throwaway-copy")
    monkeypatch.delenv("KAZMA_USER_HOME", raising=False)

    with caplog.at_level(logging.WARNING):
        moved = migrate_legacy_user_home()

    assert moved is False, "must not migrate into temp"
    assert (legacy / "restic.pass").is_file(), "the only copy must survive"
    assert any("temporary location" in r.message for r in caplog.records), (
        "silence here would leave the operator wondering why state moved -- "
        "or did not"
    )


def test_an_explicit_override_is_still_honoured(tmp_path, monkeypatch):
    """KAZMA_USER_HOME is a deliberate choice; the guard only covers the
    inferred path. An operator who names a temp dir means it."""
    legacy = tmp_path / "home" / ".kazma"
    legacy.mkdir(parents=True)
    (legacy / "marker").write_text("x", encoding="utf-8")
    target = Path(tempfile.gettempdir()) / "explicit-target-kazma"

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setenv("KAZMA_USER_HOME", str(target))

    try:
        assert migrate_legacy_user_home() is True
        assert (target / "marker").is_file()
    finally:
        import shutil
        shutil.rmtree(target, ignore_errors=True)


def test_a_normal_install_still_migrates(tmp_path, monkeypatch):
    """The guard must not break the case the migration exists for.

    pytest's tmp_path lives UNDER the system temp dir, so the temp roots
    have to be pointed elsewhere or this fixture is itself "throwaway" --
    which is how this test first failed, correctly.
    """
    elsewhere = tmp_path / "not-temp"
    elsewhere.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(elsewhere))
    for var in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(var, str(elsewhere))

    legacy = tmp_path / "home" / ".kazma"
    legacy.mkdir(parents=True)
    (legacy / "themes").mkdir()
    project = tmp_path / "install"
    project.mkdir()

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr("kazma_core.paths.get_project_root", lambda: project)
    monkeypatch.delenv("KAZMA_USER_HOME", raising=False)

    assert migrate_legacy_user_home() is True
    assert (project / ".kazma" / "themes").is_dir()
    assert not legacy.exists()
    # A marker next to the old dir was itself a write outside the install
    # (live: C:\Users\balfa\.kazma.migrated.txt).
    assert not legacy.with_suffix(".kazma.migrated.txt").exists()


@pytest.mark.parametrize("var", ["TEMP", "TMP", "TMPDIR"])
def test_each_temp_variable_is_respected(tmp_path, monkeypatch, var):
    """Windows and POSIX disagree about which variable names temp, and a
    machine may set only one."""
    fake = tmp_path / "custom-temp"
    fake.mkdir()
    monkeypatch.setenv(var, str(fake))
    assert _is_throwaway(fake / "project" / ".kazma") is True
