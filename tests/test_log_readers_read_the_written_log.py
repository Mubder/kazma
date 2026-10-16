"""Every reader of the server log reads the file the server writes.

Live 2026-10-16: asked who approved an X post, the agent called
``read_system_logs`` and answered "no log file found". The server writes
``paths.log_file()`` (``<install>/.kazma/kazma.log``, or ``KAZMA_LOG_FILE``,
through ``logging_config``); the tool looked in the home folder's ``.kazma``
and the workspace. Settings -> System -> Logs (``SettingsManager.get_logs``)
looked in the data dir and the process's working folder, and showed "No log
file found" on every install. The weekly firing ledger also built the
path itself, so it ignored ``KAZMA_LOG_FILE``.

The rule: the log's location is decided once, by ``paths.log_file()``; a
reader asks it. The gate below fails on a product module that names the
file ``kazma.log`` itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Modules that may name the file: the one that decides where it is, and the
#: data-dir registry, which declares the name an old build left there.
_MAY_NAME_THE_LOG = {
    "kazma-core/kazma_core/paths.py",
    "kazma-core/kazma_core/store_registry.py",
}

_PACKAGES = (
    "kazma-core/kazma_core",
    "kazma-gateway/kazma_gateway",
    "kazma-ui/kazma_ui",
    "kazma-cli/kazma_cli",
    "kazma-tui/kazma_tui",
    "kazma-skills/kazma_skills",
)


def _names_the_log(source: str) -> list[int]:
    """Lines where a string constant is the log's file name (a path part)."""
    tree = ast.parse(source)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and (node.value == "kazma.log" or node.value.endswith(("/kazma.log", "\\kazma.log")))
    ]


def test_only_paths_decides_where_the_log_is():
    offenders: list[str] = []
    for pkg in _PACKAGES:
        for path in sorted((ROOT / pkg).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel in _MAY_NAME_THE_LOG:
                continue
            for line in _names_the_log(path.read_text(encoding="utf-8")):
                offenders.append(f"{rel}:{line}")
    assert not offenders, (
        "These build the server log's path themselves; read paths.log_file() "
        "(the file logging_config writes): " + ", ".join(offenders)
    )


def test_the_gate_catches_a_guessed_location():
    """Negative control: the tool's and Settings' old candidate lists."""
    old_tool = (
        "candidate_paths = [\n"
        "    Path.home() / '.kazma' / 'kazma.log',\n"
        "    workspace / 'kazma.log',\n"
        "]\n"
    )
    old_settings = "log_paths.extend([_paths_data_dir() / 'kazma.log', Path.cwd() / 'kazma.log'])\n"
    assert len(_names_the_log(old_tool)) == 2
    assert len(_names_the_log(old_settings)) == 2
    assert _names_the_log("from kazma_core.paths import log_file\nlog_path = log_file()\n") == []


@pytest.fixture()
def install(tmp_path, monkeypatch):
    """The live shape: the log under the install's .kazma, decoys elsewhere."""
    import kazma_core.paths as paths

    home = tmp_path / "install" / ".kazma"
    home.mkdir(parents=True)
    log = home / "kazma.log"
    log.write_text(
        '{"level": "INFO", "message": "[gate-decision] approved x_post by web:admin"}\n',
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = tmp_path / "install" / "kazma-data"
    data.mkdir()
    for decoy in (workspace / "kazma.log", data / "kazma.log"):
        decoy.write_text("DECOY line\n", encoding="utf-8")
    monkeypatch.delenv("KAZMA_LOG_FILE", raising=False)
    monkeypatch.setattr(paths, "user_home", lambda: home)
    monkeypatch.setattr(paths, "data_dir", lambda: data)
    monkeypatch.chdir(workspace)
    return {"log": log, "workspace": workspace, "data": data}


@pytest.mark.asyncio
async def test_the_agent_tool_reads_the_written_log(install, monkeypatch):
    from kazma_skills.native.system_health_monitor import tools

    monkeypatch.setattr(tools, "_get_workspace", lambda: install["workspace"])
    out = await tools.read_system_logs(lines=20)
    assert "[gate-decision] approved x_post" in out
    assert "DECOY" not in out


@pytest.mark.asyncio
async def test_the_tool_names_where_it_looked_when_there_is_no_log(install, monkeypatch):
    from kazma_skills.native.system_health_monitor import tools

    install["log"].unlink()
    out = await tools.read_system_logs(lines=20)
    assert out.startswith("Error:") and str(install["log"]) in out
    assert "DECOY" not in out


def test_settings_logs_read_the_written_log(install):
    from kazma_core.config_store import get_config_store
    from kazma_core.settings_manager import SettingsManager

    result = SettingsManager(config_store=get_config_store()).get_logs(20)
    assert any("[gate-decision] approved x_post" in line for line in result["lines"])
    assert result["path"] == str(install["log"])


def test_the_firing_ledger_reads_the_written_log(install):
    from kazma_core.observability import firing_ledger as fl

    assert install["log"] in fl._log_paths()


def test_the_firing_ledger_follows_a_redirected_log(install, tmp_path, monkeypatch):
    """KAZMA_LOG_FILE moves the log; the old ledger kept reading .kazma."""
    from kazma_core.observability import firing_ledger as fl

    moved = tmp_path / "elsewhere" / "kazma.log"
    moved.parent.mkdir()
    moved.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("KAZMA_LOG_FILE", str(moved))
    assert moved in fl._log_paths()


def test_the_old_tool_lookup_misses_the_log(install, monkeypatch):
    """Negative control: the tool's candidate list before the fix."""
    workspace = install["workspace"]
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: install["log"].parent.parent.parent / "user"))
    candidates = [
        Path.home() / ".kazma" / "kazma.log",
        workspace / "kazma.log",
        workspace / "kazma-data" / "logs" / "kazma.log",
        workspace / "out.log",
        workspace / "server.log",
    ]
    found = next((c for c in candidates if c.exists()), None)
    # It reads the workspace's decoy (or nothing) -- never the server's log.
    assert found != install["log"]
