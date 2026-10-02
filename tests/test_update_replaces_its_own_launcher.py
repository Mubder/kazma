"""``kazma update`` can replace ``kazma.exe`` while running from it (Windows).

Live 2026-10-02 ``kazma update --reinstall -y``, run as the boot check says,
failed with "failed to remove file ... Scripts/kazma.exe: The process cannot
access the file": the reinstall must replace the very launcher the update
runs from, and Windows refuses to delete or overwrite a running .exe. Every
attempt (uv, uv sync, pip) failed the same way and left Kazma's own install
half removed. Windows does let a running .exe be RENAMED, and uv tolerates a
launcher it meant to remove being gone (both measured that day, the second
by reinstalling a scratch project from its own launcher: exit 2 without the
move, 0 with it). So the reinstall moves the launchers aside first
(``update._launchers_moved_aside``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from kazma_cli import update


@pytest.fixture
def scripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "Scripts"
    d.mkdir()
    monkeypatch.setattr(update, "_launcher_names", lambda: ("kazma", "kazma-tui"))
    return d


def test_a_launcher_the_installer_rewrote_is_kept_and_one_it_did_not_is_put_back(scripts: Path) -> None:
    (scripts / "kazma.exe").write_bytes(b"old kazma")
    (scripts / "kazma-tui.exe").write_bytes(b"old tui")
    with update._launchers_moved_aside(scripts, windows=True):
        assert not (scripts / "kazma.exe").exists() and not (scripts / "kazma-tui.exe").exists()
        (scripts / "kazma.exe").write_bytes(b"new kazma")  # what the installer writes
    assert (scripts / "kazma.exe").read_bytes() == b"new kazma"
    assert (scripts / "kazma-tui.exe").read_bytes() == b"old tui"  # restored, not lost
    assert list(scripts.glob("*.old")) == []  # nothing running them: removed


def test_a_failed_reinstall_leaves_the_launchers_where_they_were(scripts: Path) -> None:
    (scripts / "kazma.exe").write_bytes(b"old kazma")
    with pytest.raises(RuntimeError):
        with update._launchers_moved_aside(scripts, windows=True):
            raise RuntimeError("the installer failed")
    assert (scripts / "kazma.exe").read_bytes() == b"old kazma"


def test_off_windows_nothing_moves(scripts: Path) -> None:
    (scripts / "kazma.exe").write_bytes(b"old kazma")
    with update._launchers_moved_aside(scripts, windows=False):
        assert (scripts / "kazma.exe").exists()


def test_the_reinstall_runs_inside_the_move(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    events: list[str] = []

    @contextmanager
    def recording(*a, **kw):
        events.append("moved aside")
        yield
        events.append("put back")

    monkeypatch.setattr(update, "_launchers_moved_aside", recording)
    monkeypatch.setattr(update, "_reinstall_local_unguarded", lambda cwd: events.append("install") or True)
    assert update._reinstall_local(str(tmp_path)) is True
    assert events == ["moved aside", "install", "put back"]


@pytest.mark.skipif(os.name != "nt", reason="the lock on a running .exe is Windows' rule")
def test_a_running_launcher_is_replaced(scripts: Path) -> None:
    """The real rule: a copy of ping.exe runs as kazma.exe while it is replaced."""
    ping = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "PING.EXE"
    if not ping.is_file():
        pytest.skip("no PING.EXE to run")
    launcher = scripts / "kazma.exe"
    shutil.copyfile(ping, launcher)
    proc = subprocess.Popen([str(launcher), "-n", "30", "127.0.0.1"], stdout=subprocess.DEVNULL)
    try:
        # CreateProcess maps the image before Popen returns: it is in use now.
        assert proc.poll() is None
        # Negative control: what the installer did on the live install.
        with pytest.raises(PermissionError):
            launcher.unlink()
        with update._launchers_moved_aside(scripts, windows=True):
            launcher.write_bytes(b"new kazma")
        assert launcher.read_bytes() == b"new kazma"
        assert proc.poll() is None  # still running, from its new name
        assert len(list(scripts.glob("kazma.exe.*.old"))) == 1  # in use: kept for later
    finally:
        proc.kill()
        proc.wait(timeout=10)
    # Once nothing runs it, the old copy goes (the image is released as the
    # process ends; poll to a deadline rather than bet on a sleep).
    deadline = time.monotonic() + 10
    while True:
        update._remove_old_launchers(scripts)
        if not list(scripts.glob("*.old")) or time.monotonic() > deadline:
            break
        time.sleep(0.1)
    assert list(scripts.glob("*.old")) == []
