"""A reload picks up a PATH the operator fixed while Kazma was running.

Live 2026-09-25: Docker Desktop's update dropped its CLI folder from PATH, the
operator put it back (``where.exe docker`` found it), and the server started
afterwards still logged "the docker CLI is not on PATH". Windows gives a
process its parent's environment, not the registry's; the guard had been up
since 04:25 and handed every server the 04:25 PATH. See
:mod:`kazma_core.path_refresh`.
"""

from __future__ import annotations

import ast
import logging
import os
import shutil
import stat
from pathlib import Path

import pytest

from kazma_core import path_refresh
from kazma_core.path_refresh import _merge_path as merge_path
from kazma_core.path_refresh import _os_path_settings as os_path_settings
from kazma_core.path_refresh import refresh_path_from_os

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKER_DIR = r"C:\Program Files\Docker\Docker\resources\bin"


# --------------------------------------------------------------------------
# merge_path: append only, Windows' idea of "the same folder"
# --------------------------------------------------------------------------


def test_only_missing_entries_are_appended_in_settings_order():
    current = r"C:\venv\Scripts;C:\Windows\system32"
    settings = [
        rf"C:\WINDOWS\System32\;{DOCKER_DIR}",  # machine PATH
        r"C:\Users\u\bin;c:\VENV\scripts",  # user PATH
    ]
    merged, added = merge_path(current, settings, sep=";")
    assert added == [DOCKER_DIR, r"C:\Users\u\bin"]
    # What the process had stays first, untouched: the venv still wins.
    assert merged == current + ";" + DOCKER_DIR + r";C:\Users\u\bin"


def test_blanks_quotes_and_padding_do_not_fool_the_comparison():
    current = 'C:\\a;;"C:\\Program Files\\x"'
    merged, added = merge_path(current, [' ;C:\\Program Files\\x; C:\\b ;'], sep=";")
    assert added == ["C:\\b"]
    assert merged == current + ";C:\\b"


def test_nothing_new_returns_the_path_unchanged():
    current = r"C:\a;C:\b"
    merged, added = merge_path(current, [r"c:\A\;C:\B"], sep=";")
    assert (merged, added) == (current, [])


def test_an_empty_path_gets_no_leading_separator():
    assert merge_path("", [r"C:\a;C:\b"], sep=";") == (r"C:\a;C:\b", [r"C:\a", r"C:\b"])
    assert merge_path(r"C:\a;", [r"C:\b"], sep=";") == (r"C:\a;C:\b", [r"C:\b"])


def test_an_entry_the_settings_lost_is_kept():
    """Removal is not adoption: the running tree may rely on it."""
    merged, added = merge_path(r"C:\gone;C:\kept", [r"C:\kept"], sep=";")
    assert (merged, added) == (r"C:\gone;C:\kept", [])


# --------------------------------------------------------------------------
# refresh_path_from_os: the incident, end to end on os.environ
# --------------------------------------------------------------------------


def _fake_docker(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / ("docker.exe" if os.name == "nt" else "docker")
    exe.write_text("", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


def test_a_folder_put_back_on_path_is_adopted(tmp_path, monkeypatch, caplog):
    """The 2026-09-25 shape: the settings have Docker, the process does not."""
    stale = tmp_path / "stale-bin"
    stale.mkdir()
    docker_dir = tmp_path / "Docker" / "resources" / "bin"
    exe = _fake_docker(docker_dir)
    monkeypatch.setenv("PATH", str(stale))
    monkeypatch.delenv("KAZMA_DOCKER_BIN", raising=False)
    assert shutil.which("docker") is None  # what the stale server saw

    with caplog.at_level(logging.INFO, logger="kazma_core.path_refresh"):
        added = refresh_path_from_os(read=lambda: [str(docker_dir)])

    assert added == [str(docker_dir)]
    assert os.environ["PATH"] == os.pathsep.join([str(stale), str(docker_dir)])
    assert Path(shutil.which("docker")).resolve() == exe.resolve()
    assert "PATH gained 1 entry from the OS settings" in caplog.text
    assert str(docker_dir) in caplog.text


def test_the_docker_lookup_then_finds_it_on_path(tmp_path, monkeypatch, caplog):
    """No fallback warning once the folder is back: PATH is the answer again."""
    from kazma_core import docker_cli

    docker_dir = tmp_path / "bin"
    exe = _fake_docker(docker_dir)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("KAZMA_DOCKER_BIN", raising=False)
    monkeypatch.setattr(docker_cli, "_candidates", lambda: [])
    assert docker_cli.find_docker_cli() is None  # negative control

    refresh_path_from_os(read=lambda: [str(docker_dir)])
    with caplog.at_level(logging.WARNING, logger="kazma_core.docker_cli"):
        found = docker_cli.find_docker_cli()
    assert found and Path(found).resolve() == exe.resolve()
    assert "not on PATH" not in caplog.text


def test_nothing_new_changes_nothing_and_says_nothing(monkeypatch, caplog):
    monkeypatch.setenv("PATH", os.pathsep.join(["/a", "/b"]))
    with caplog.at_level(logging.INFO, logger="kazma_core.path_refresh"):
        assert refresh_path_from_os(read=lambda: ["/b", "/a"]) == []
    assert os.environ["PATH"] == os.pathsep.join(["/a", "/b"])
    assert "PATH gained" not in caplog.text


def test_unreadable_settings_leave_the_path_alone(monkeypatch, caplog):
    """A server that cannot read the registry boots as it did before."""
    monkeypatch.setenv("PATH", "/only")

    def broken() -> list[str]:
        raise PermissionError("registry says no")

    with caplog.at_level(logging.WARNING, logger="kazma_core.path_refresh"):
        assert refresh_path_from_os(read=broken) == []
    assert os.environ["PATH"] == "/only"
    assert "could not compare PATH with the OS settings" in caplog.text


def test_the_default_reader_is_looked_up_at_call_time(monkeypatch):
    """Patching the module attribute reaches the call (no default-arg capture)."""
    monkeypatch.setenv("PATH", "/only")
    monkeypatch.setattr(path_refresh, "_os_path_settings", lambda: ["/new"])
    assert refresh_path_from_os() == ["/new"]


@pytest.mark.skipif(os.name != "nt", reason="the registry is Windows-only")
def test_the_real_settings_are_read_and_expanded():
    values = os_path_settings()
    assert values, "the machine PATH should always be readable"
    machine = values[0].lower()
    assert "system32" in machine
    assert "%systemroot%" not in machine  # REG_EXPAND_SZ was expanded


@pytest.mark.skipif(os.name == "nt", reason="off Windows the service manager owns PATH")
def test_off_windows_there_is_nothing_to_read():
    assert os_path_settings() == []


# --------------------------------------------------------------------------
# Wiring: every way of serving the app gets it, before anything spawns
# --------------------------------------------------------------------------


def test_building_the_app_adopts_os_path_additions(tmp_path, monkeypatch):
    """Behaviour, not source: the real app factory runs the refresh."""
    extra = tmp_path / "installed-while-running"
    extra.mkdir()
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))  # restored after
    monkeypatch.setattr(path_refresh, "_os_path_settings", lambda: [str(extra)])

    from kazma_ui.app import create_app

    create_app()
    assert os.environ["PATH"].split(os.pathsep)[-1] == str(extra)


def _calls_in_order(func: ast.FunctionDef) -> list[str]:
    out: list[tuple[int, int, str]] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            out.append((node.lineno, node.col_offset, name))
    return [name for _, _, name in sorted(out)]


def _method(source: str, cls: str, name: str) -> ast.FunctionDef:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return item
    raise AssertionError(f"{cls}.{name} not found")


def _refresh_follows_logging(source: str) -> bool:
    calls = _calls_in_order(_method(source, "KazmaAppBuilder", "_bootstrap_services"))
    if "setup_logging" not in calls or "_adopt_process_environment" not in calls:
        return False
    after_logging = calls[calls.index("setup_logging") + 1:]
    # Straight after logging (a failed setup only logs a warning), and before
    # the first subsystem is imported or built.
    return [c for c in after_logging if c != "warning"][:1] == ["_adopt_process_environment"]


def test_the_refresh_runs_right_after_logging_is_configured():
    """Early enough to precede every spawn; late enough that its line is logged."""
    source = (REPO_ROOT / "kazma-ui" / "kazma_ui" / "app.py").read_text(encoding="utf-8")
    assert _refresh_follows_logging(source)
    adopt = _calls_in_order(_method(source, "KazmaAppBuilder", "_adopt_process_environment"))
    assert "refresh_path_from_os" in adopt


def test_the_order_check_catches_a_misplaced_refresh():
    """Negative control (§28): the check fails on the shapes it exists to stop."""
    missing = '''
class KazmaAppBuilder:
    def _bootstrap_services(self):
        try:
            setup_logging()
        except Exception as e:
            logger.warning("x", e)
        load_config()
'''
    late = '''
class KazmaAppBuilder:
    def _bootstrap_services(self):
        setup_logging()
        load_config()
        self._adopt_process_environment()
'''
    early = '''
class KazmaAppBuilder:
    def _bootstrap_services(self):
        self._adopt_process_environment()
        setup_logging()
        load_config()
'''
    good = '''
class KazmaAppBuilder:
    def _bootstrap_services(self):
        try:
            setup_logging()
        except Exception as e:
            logger.warning("x", e)
        self._adopt_process_environment()
        load_config()
'''
    assert not _refresh_follows_logging(missing)
    assert not _refresh_follows_logging(late)
    assert not _refresh_follows_logging(early)
    assert _refresh_follows_logging(good)


def test_the_env_files_line_now_reaches_the_log(monkeypatch, caplog):
    """It used to be logged before logging had a file, and so never landed."""
    from kazma_ui.app import KazmaAppBuilder

    monkeypatch.setattr(path_refresh, "_os_path_settings", lambda: [])
    builder = KazmaAppBuilder()
    builder._env_files_loaded = [r"C:\kazma\.env"]
    with caplog.at_level(logging.INFO, logger="kazma_ui.app"):
        builder._adopt_process_environment()
    assert r"[env] Loaded (lowest->highest precedence): C:\kazma\.env" in caplog.text

    caplog.clear()
    builder._env_files_loaded = []
    with caplog.at_level(logging.INFO, logger="kazma_ui.app"):
        builder._adopt_process_environment()
    assert "[env] No .env file found on the ladder" in caplog.text

    # A load that raised is not "no file found": it is said, at WARNING.
    caplog.clear()
    builder._env_load_error = "ImportError: No module named 'dotenv'"
    with caplog.at_level(logging.INFO, logger="kazma_ui.app"):
        builder._adopt_process_environment()
    assert "[env] .env files were NOT loaded: ImportError" in caplog.text
    assert "No .env file found" not in caplog.text
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_a_failed_env_load_is_kept_for_the_log(monkeypatch):
    """The real bootstrap records the failure it used to log only at DEBUG."""
    from kazma_ui.app import KazmaAppBuilder

    builder = KazmaAppBuilder()

    def boom() -> None:
        raise ImportError("No module named 'dotenv'")

    monkeypatch.setattr(builder, "_load_env_files", boom)
    monkeypatch.setattr(builder, "_bootstrap_services", lambda: None)
    builder._bootstrap_environment()
    assert builder._env_load_error == "ImportError: No module named 'dotenv'"
