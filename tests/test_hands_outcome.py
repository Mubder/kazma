"""Hands usefulness eval — outcome, not a scripted LLM.

Fix examples/hands-demo via file_apply_patch_set(verify=True) until pytest is green.
No live model. If this fails, the coding loop is a plan note again.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kazma_core.tools.file_apply_patch import file_apply_patch_set
from kazma_core.workspace.binding import configure_workspace

_DEMO = Path(__file__).resolve().parent.parent / "examples" / "hands-demo"


def _pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    demo = tmp_path / "demo"
    shutil.copytree(_DEMO, demo)
    monkeypatch.setenv("KAZMA_WORKSPACE", str(demo))
    monkeypatch.setenv("KAZMA_FILE_CHECKPOINTS_DB", str(tmp_path / "ck.db"))
    monkeypatch.setattr(
        "kazma_core.stores.get_workspace_store",
        lambda: type("S", (), {"get_active_workspace": staticmethod(lambda: None)})(),
    )
    configure_workspace(workspace=str(demo))
    return demo


def test_hands_demo_is_red_on_purpose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    demo = _pin(tmp_path, monkeypatch)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(demo / "test_app.py"), "-q"],
        cwd=str(demo),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0, "hands-demo must fail until the agent patches add()"


@pytest.mark.asyncio
async def test_patch_set_verify_makes_hands_demo_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = _pin(tmp_path, monkeypatch)
    msg = await file_apply_patch_set(
        [
            {
                "path": str(demo / "app.py"),
                "old_string": "return a + b + 1",
                "new_string": "return a + b",
            }
        ],
        verify=True,
    )
    assert "Patched" in msg
    assert "TESTS PASSED" in msg
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(demo / "test_app.py"), "-q"],
        cwd=str(demo),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
