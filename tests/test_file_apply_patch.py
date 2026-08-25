"""Surgical file edits (industry stack part 7) — search-replace + unified diff."""

from __future__ import annotations

from pathlib import Path

import pytest

from kazma_core.tools.file_apply_patch import (
    PatchError,
    apply_search_replace,
    apply_unified_diff,
    file_apply_patch,
)


def test_search_replace_unique() -> None:
    src = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
    out = apply_search_replace(src, "    return 1", "    return 42")
    assert "return 42" in out
    assert "return 2" in out


def test_search_replace_ambiguous() -> None:
    src = "x = 1\nx = 1\n"
    with pytest.raises(PatchError, match="matched 2 times"):
        apply_search_replace(src, "x = 1", "x = 2")
    out = apply_search_replace(src, "x = 1", "x = 2", replace_all=True)
    assert out == "x = 2\nx = 2\n"


def test_search_replace_missing() -> None:
    with pytest.raises(PatchError, match="not found"):
        apply_search_replace("hello\n", "goodbye", "x")


def test_unified_diff_one_hunk() -> None:
    src = "alpha\nbeta\ngamma\n"
    patch = """\
--- a/x
+++ b/x
@@ -1,3 +1,3 @@
 alpha
-beta
+BETA
 gamma
"""
    out = apply_unified_diff(src, patch)
    assert out == "alpha\nBETA\ngamma\n"


def test_morph_begin_patch() -> None:
    src = "print('hi')\n"
    patch = """\
*** Begin Patch
*** Update File: hello.py
@@
-print('hi')
+print('hello')
*** End Patch
"""
    out = apply_unified_diff(src, patch)
    assert "hello" in out


def _pin_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(
        "kazma_core.stores.get_workspace_store",
        lambda: type(
            "S",
            (),
            {"get_active_workspace": staticmethod(lambda: None)},
        )(),
    )
    from kazma_core.workspace.binding import configure_workspace

    configure_workspace(workspace=str(tmp_path))


@pytest.mark.asyncio
async def test_file_apply_patch_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pin_workspace(tmp_path, monkeypatch)
    target = tmp_path / "a.py"
    target.write_text("n = 1\n", encoding="utf-8")
    msg = await file_apply_patch(str(target), old_string="n = 1", new_string="n = 2")
    assert "Patched" in msg
    assert target.read_text(encoding="utf-8") == "n = 2\n"


@pytest.mark.asyncio
async def test_file_apply_patch_outside_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_workspace(tmp_path, monkeypatch)
    outside = tmp_path.parent / "escape.py"
    outside.write_text("secret = 1\n", encoding="utf-8")
    msg = await file_apply_patch(str(outside), old_string="secret = 1", new_string="secret = 2")
    assert "not allowed" in msg.lower() or msg.startswith("Error") or msg.startswith("Safety")
    assert outside.read_text(encoding="utf-8") == "secret = 1\n"
