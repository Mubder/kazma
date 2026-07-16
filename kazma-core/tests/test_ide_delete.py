"""Tests for IdeService.delete_file (danger-tier, traversal-checked)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from kazma_core.ide import get_ide_service, reset_ide_service


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """Isolated workspace for delete tests."""
    workspace = Path(os.getcwd()) / ".ide_test_delete"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir()
    monkeypatch.setenv("KAZMA_WORKSPACE", str(workspace))
    try:
        from kazma_core.tools.file_write import configure_workspace

        configure_workspace(workspace=str(workspace))
    except Exception:
        pass
    reset_ide_service()
    from kazma_core.swarm.safety import set_safety

    set_safety(
        __import__("kazma_core.swarm.safety", fromlist=["SafetyMiddleware"]).SafetyMiddleware()
    )
    svc = get_ide_service()
    # Create test files.
    (workspace / "file_to_delete.py").write_text("print('delete me')\n")
    (workspace / "dir_to_delete").mkdir()
    (workspace / "dir_to_delete" / "inner.py").write_text("x = 1\n")
    (workspace / "keep.py").write_text("print('keep')\n")
    yield workspace, svc
    shutil.rmtree(workspace, ignore_errors=True)


async def test_delete_file_blocked_by_traversal(ws):
    """delete_file must reject path traversal attempts."""
    _, svc = ws
    res = await svc.delete_file("../../etc/passwd")
    assert res["ok"] is False
    assert "escape" in res["error"].lower() or "outside" in res["error"].lower()


async def test_delete_nonexistent_file(ws):
    """delete_file on a nonexistent path returns an error."""
    _, svc = ws
    res = await svc.delete_file("does_not_exist.py")
    assert res["ok"] is False
    assert "not found" in res["error"].lower()


async def test_delete_file_fail_closed_without_approval(ws):
    """delete_file is HITL-gated — must be denied without an approval bus."""
    _, svc = ws
    res = await svc.delete_file("file_to_delete.py")
    # No bus adapter → SafetyMiddleware fails closed → delete denied.
    assert res["ok"] is False
    assert res["error"] is not None
    # File must still exist.
    assert (ws[0] / "file_to_delete.py").exists()


async def test_delete_directory_fail_closed_without_approval(ws):
    """delete_file on a directory is also HITL-gated."""
    _, svc = ws
    res = await svc.delete_file("dir_to_delete")
    assert res["ok"] is False
    # Directory must still exist.
    assert (ws[0] / "dir_to_delete").exists()
