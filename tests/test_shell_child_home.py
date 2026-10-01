"""A shell command keeps its own files out of the workspace (2026-10-02).

``shell_exec`` gave its child HOME, USERPROFILE and the temp variables of the
workspace itself. Every tool a command ran kept its user-level files in the
repository the agent was working in: the live install folder (the agent's
workspace) held 143 MB of uv cache under ``AppData/Local/uv`` and temp files,
where a ``git add -A`` would commit them. The child now gets a private home
per workspace under the system temp folder -- still not the operator's own
home, which a command must not read.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from kazma_core.safety.post_hitl import _tool_home_for, restricted_child_env

_CHILD = textwrap.dedent(
    """
    import os, tempfile
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".tmp") as fh:
        fh.write("scratch")
    cache = os.path.join(os.path.expanduser("~"), ".cache", "tool")
    os.makedirs(cache, exist_ok=True)
    open(os.path.join(cache, "index"), "w").write("cached")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        os.makedirs(os.path.join(local, "uv", "cache"), exist_ok=True)
        open(os.path.join(local, "uv", "cache", "wheel"), "w").write("w")
    """
)


@pytest.fixture()
def workspace(tmp_path: Path):
    ws = tmp_path / "repo"
    ws.mkdir()
    yield ws
    shutil.rmtree(_tool_home_for(str(ws)), ignore_errors=True)


def _run_child(env: dict[str, str], cwd: Path) -> None:
    subprocess.run([sys.executable, "-c", _CHILD], env=env, cwd=str(cwd), check=True, timeout=60)


def _inside(path: str, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def test_a_commands_home_and_temp_are_its_own(workspace: Path) -> None:
    env = restricted_child_env(cwd=str(workspace))
    names = ["HOME", "USERPROFILE", "TEMP", "TMP", "TMPDIR"]
    if os.name == "nt":
        names += ["APPDATA", "LOCALAPPDATA"]
    for name in names:
        assert not _inside(env[name], workspace), f"{name} is inside the workspace: {env[name]}"
        assert "kazma-tool-home" in Path(env[name]).parts, f"{name} is not the private home: {env[name]}"
        assert Path(env[name]).resolve() != Path.home().resolve(), f"{name} is the operator's home"
    assert env["HOME"] == str(_tool_home_for(str(workspace)))
    # The same workspace keeps its home (caches last); another gets its own.
    assert restricted_child_env(cwd=str(workspace))["HOME"] == env["HOME"]
    other = workspace.parent / "other"
    other.mkdir()
    try:
        assert restricted_child_env(cwd=str(other))["HOME"] != env["HOME"]
    finally:
        shutil.rmtree(_tool_home_for(str(other)), ignore_errors=True)


def test_a_command_leaves_nothing_in_the_workspace(workspace: Path) -> None:
    _run_child(restricted_child_env(cwd=str(workspace)), workspace)
    assert sorted(p.relative_to(workspace).as_posix() for p in workspace.rglob("*")) == []
    home = _tool_home_for(str(workspace))
    assert (home / ".cache" / "tool" / "index").is_file()


def test_the_old_environment_filled_the_workspace(workspace: Path) -> None:
    """Negative control: HOME and TEMP as the workspace, as before."""
    env = restricted_child_env(cwd=str(workspace))
    for name in ("HOME", "USERPROFILE", "TEMP", "TMP", "TMPDIR"):
        env[name] = str(workspace)
    env.pop("APPDATA", None)
    env.pop("LOCALAPPDATA", None)
    _run_child(env, workspace)
    left = {p.relative_to(workspace).as_posix() for p in workspace.rglob("*") if p.is_file()}
    assert ".cache/tool/index" in left
    assert any(name.endswith(".tmp") for name in left)
