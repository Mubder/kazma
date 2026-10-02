"""``kazma update`` never runs from a launcher it must replace (Windows).

On 2026-10-02 a reinstall run from ``kazma.exe`` failed half way on the live
install ("failed to remove file ... kazma.exe") and left Kazma's own package
half removed. The fix tried that morning renamed the launchers aside, and its
test used a running copy of ``ping.exe``, which Windows does let you rename.
A uv launcher cannot be renamed while it runs: Python keeps the zip appended
to it open. Tested live that afternoon, the rename failed on ``kazma.exe``
and only a fallback that did not need the launcher kept the install whole.
The update now refuses to run from a held launcher before it installs or
pulls anything (pip does the same from ``pip.exe``), and names the
``<python> -m kazma_cli update`` command to run instead.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _uv_launcher() -> Path | None:
    """A uv launcher of this environment: any console script will do."""
    scripts = Path(sys.executable).parent
    for name in ("pytest.exe", "kazma.exe"):
        if (scripts / name).is_file():
            return scripts / name
    return None


@pytest.fixture
def held_launcher(tmp_path: Path):
    """A copy of a real uv launcher, running a test that waits for a flag."""
    source = _uv_launcher()
    if os.name != "nt" or source is None:
        pytest.skip("uv launchers are Windows executables")
    launcher = tmp_path / "held.exe"
    shutil.copy2(source, launcher)
    flag, ready = tmp_path / "hold", tmp_path / "ready"
    flag.write_text("x", encoding="utf-8")
    (tmp_path / "test_hold.py").write_text(
        "import pathlib, time\n\n"
        "def test_hold():\n"
        f"    pathlib.Path(r'{ready}').write_text('x')\n"
        f"    flag = pathlib.Path(r'{flag}')\n"
        "    end = time.monotonic() + 60\n"
        "    while flag.exists() and time.monotonic() < end:\n"
        "        time.sleep(0.05)\n",
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [str(launcher), str(tmp_path / "test_hold.py"), "-q", "-p", "no:cacheprovider",
         "--rootdir", str(tmp_path)],
        cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    deadline = time.monotonic() + 90
    while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        if not ready.exists():
            pytest.fail("the launcher copy did not start its test")
        yield launcher
    finally:
        flag.unlink(missing_ok=True)
        proc.wait(timeout=60)


def test_a_running_uv_launcher_cannot_be_renamed(held_launcher: Path) -> None:
    """Why the rename-aside fix failed live (its ping.exe test passed)."""
    with pytest.raises(OSError):
        held_launcher.rename(held_launcher.with_name("held.exe.old"))


def test_the_update_sees_a_held_launcher(held_launcher: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_cli import update

    monkeypatch.setattr(update, "_launcher_names", lambda: ("held",))
    assert update._launchers_in_use(held_launcher.parent, windows=True) == [held_launcher]


def test_an_idle_launcher_is_free_and_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: nothing runs it, so nothing holds it; the probe
    (opening it for writing) writes nothing."""
    from kazma_cli import update

    exe = tmp_path / "kazma.exe"
    exe.write_bytes(b"MZ launcher bytes")
    before = (exe.read_bytes(), exe.stat().st_mtime_ns)
    monkeypatch.setattr(update, "_launcher_names", lambda: ("kazma",))
    assert update._launchers_in_use(tmp_path, windows=True) == []
    assert (exe.read_bytes(), exe.stat().st_mtime_ns) == before
    assert update._launchers_in_use(tmp_path, windows=False) == []


# ── the refusal, before anything is installed or pulled ──────────────────


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from kazma_cli import update

    installs: list[str] = []
    monkeypatch.setattr(update, "detect_install_type", lambda: "git")
    monkeypatch.setattr(update, "get_current_version", lambda: "0.0.0")
    monkeypatch.setattr(update, "_is_server_running", lambda port=9090: False)
    monkeypatch.setattr(update, "_reinstall_local", lambda cwd: installs.append(cwd) or True)
    monkeypatch.setattr(update, "persist_extras", lambda extras: None)
    monkeypatch.setattr(update, "detect_active_extras", lambda cwd=None: ["rag"])
    launcher = tmp_path / "kazma.exe"
    launcher.write_bytes(b"MZ")
    return update, installs, launcher


def test_run_from_a_held_launcher_refuses_and_names_the_command(cli, monkeypatch, capsys) -> None:
    update, installs, launcher = cli
    monkeypatch.setattr(sys, "argv", [str(launcher), "update", "--reinstall", "-y"])
    monkeypatch.setattr(update, "_launchers_in_use", lambda *a, **k: [launcher])
    with pytest.raises(SystemExit) as exited:
        update.run(["--reinstall", "-y"])
    assert exited.value.code == 1 and installs == []
    out = capsys.readouterr().out
    assert "This command runs from kazma.exe" in out
    assert any(line.strip().endswith("-m kazma_cli update --reinstall -y") for line in out.splitlines()), out


def test_run_with_python_reinstalls(cli, monkeypatch) -> None:
    """Negative control: run as ``python -m kazma_cli`` nothing holds the launcher."""
    update, installs, _launcher = cli
    monkeypatch.setattr(sys, "argv", [str(REPO / "kazma-cli" / "kazma_cli" / "__main__.py")])
    monkeypatch.setattr(update, "_launchers_in_use", lambda *a, **k: [])
    update.run(["--reinstall", "-y"])
    assert len(installs) == 1


def test_another_program_holding_a_launcher_is_named(cli, monkeypatch, capsys, tmp_path) -> None:
    update, installs, _launcher = cli
    tui = tmp_path / "kazma-tui.exe"
    tui.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "argv", [str(REPO / "kazma-cli" / "kazma_cli" / "__main__.py")])
    monkeypatch.setattr(update, "_launchers_in_use", lambda *a, **k: [tui])
    with pytest.raises(SystemExit):
        update.run(["--reinstall", "-y"])
    out = capsys.readouterr().out
    assert installs == [] and "Close what is running kazma-tui.exe" in out
    assert "This command runs from" not in out


def test_the_git_update_refuses_before_git_moves(cli, monkeypatch, capsys) -> None:
    update, _installs, launcher = cli
    monkeypatch.setattr(sys, "argv", [str(launcher)])
    monkeypatch.setattr(update, "_launchers_in_use", lambda *a, **k: [launcher])

    def no_git(*_a, **_k):
        raise AssertionError("git ran before the refusal")

    monkeypatch.setattr(update, "_run_cmd", no_git)
    assert update.do_git_update() is False
    assert "-m kazma_cli update -y" in capsys.readouterr().out


def test_python_m_kazma_cli_is_the_cli() -> None:
    """``python -m kazma_cli`` is the command the refusal names: it must run."""
    result = subprocess.run(
        [sys.executable, "-m", "kazma_cli", "update", "--help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "--reinstall" in result.stdout
