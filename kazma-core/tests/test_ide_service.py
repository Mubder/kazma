"""Tests for the transport-neutral IdeService (kazma_core.ide).

These lock in the two non-negotiable safety guarantees:

  1. Path traversal is blocked at the IdeService layer (``resolve`` raises
     ``ValueError`` for anything escaping the workspace root).
  2. Mutating/executing operations are fail-closed when no HITL approval
     bus is wired (the same ``SafetyMiddleware`` gate the agent/swarm use).

They also exercise the read/list/search/diff happy paths.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kazma_core.ide import get_ide_service, reset_ide_service


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """Point the workspace at a temp dir and reset the singleton per test.

    The workspace is created under the repo working directory (NOT pytest's
    symlinked ``Local\\Temp\\pytest-of-*`` root) so the path-traversal guard
    is exercised with real, non-junction paths.
    """
    import shutil

    workspace = Path(os.getcwd()) / ".ide_test_ws"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    monkeypatch.setenv("KAZMA_WORKSPACE", str(workspace))
    # Keep the file_write module's resolution consistent with the env var.
    try:
        from kazma_core.tools.file_write import configure_workspace

        configure_workspace(workspace=str(workspace))
    except Exception:
        pass
    reset_ide_service()
    # Ensure a fresh, fail-closed safety singleton (NullBus adapter).
    from kazma_core.swarm.safety import set_safety

    set_safety(__import__("kazma_core.swarm.safety", fromlist=["SafetyMiddleware"]).SafetyMiddleware())
    svc = get_ide_service()
    (workspace / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    (workspace / "sub").mkdir()
    (workspace / "sub" / "note.md").write_text("# title\n", encoding="utf-8")
    yield workspace, svc
    shutil.rmtree(workspace, ignore_errors=True)


async def test_list_path_happy(ws):
    _, svc = ws
    res = await svc.list_path("")
    assert res["ok"] is True
    assert "hello.py" in res["files"]


async def test_read_file_happy(ws):
    _, svc = ws
    res = await svc.read_file("hello.py")
    assert res["ok"] is True
    assert "print('hi')" in res["content"]
    assert res["lang"] == "python"


async def test_read_missing_file(ws):
    _, svc = ws
    res = await svc.read_file("nope.py")
    assert res["ok"] is False
    assert "not found" in res["error"].lower()


def test_resolve_blocks_traversal():
    import shutil
    from kazma_core.ide import get_ide_service, reset_ide_service
    from kazma_core.tools.file_write import configure_workspace

    workspace = Path(os.getcwd()) / ".ide_test_ws_traverse"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    os.environ["KAZMA_WORKSPACE"] = str(workspace)
    configure_workspace(workspace=str(workspace))
    reset_ide_service()
    svc = get_ide_service()
    try:
        # Relative escape to a sibling of the workspace dir.
        with pytest.raises(ValueError):
            svc.resolve("../escape.txt")
        # Absolute path clearly outside the workspace root.
        if os.path.sep == "\\":
            outside_abs = os.path.splitdrive(str(workspace))[0] + "\\__escape__.txt"
        else:
            outside_abs = "/etc/passwd"
        with pytest.raises(ValueError):
            svc.resolve(outside_abs)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


async def test_write_is_fail_closed_without_approval_bus(ws):
    """HITL gate must block file_write when no real approval bus is present."""
    _, svc = ws
    res = await svc.write_file("new.py", "x = 1\n")
    # No bus adapter -> SafetyMiddleware fails closed -> write denied.
    assert res["ok"] is False
    assert res["error"] is not None
    # And the file must NOT have been written.
    assert not (ws[0] / "new.py").exists()


async def test_run_is_fail_closed_without_approval_bus(ws):
    _, svc = ws
    res = await svc.run("echo hi")
    assert res["ok"] is False


async def test_diff_pure(ws):
    _, svc = ws
    res = await svc.diff("hello.py", "print('hi')\n", "print('hello')\n")
    assert res["ok"] is True
    assert res["changed"] is True
    assert "-print('hi')" in res["diff"]
    assert "+print('hello')" in res["diff"]


async def test_search_within_workspace(ws):
    _, svc = ws
    res = await svc.search("print", glob="*.py")
    assert res["ok"] is True
    assert any("hello.py" in m for m in res["matches"])
