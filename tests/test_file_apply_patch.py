"""Surgical file edits (industry stack part 7) — search-replace + unified diff."""

from __future__ import annotations

from pathlib import Path

from kazma_core.agent.tool_refusal import KnownEffectError
from tests._tool_answer import answer
import pytest

from kazma_core.tools.file_apply_patch import (
    PatchError,
    apply_search_replace,
    apply_unified_diff,
    file_apply_patch,
    file_apply_patch_set,
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
    msg = await answer(file_apply_patch(str(outside), old_string="secret = 1", new_string="secret = 2"))
    assert "not allowed" in msg.lower() or msg.startswith("Error") or msg.startswith("Safety")
    assert outside.read_text(encoding="utf-8") == "secret = 1\n"


@pytest.mark.asyncio
async def test_patch_set_rolls_back_on_second_hunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KAZMA_FILE_CHECKPOINTS_DB", str(tmp_path / "ck.db"))
    _pin_workspace(tmp_path, monkeypatch)
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("n = 1\n", encoding="utf-8")
    b.write_text("m = 1\n", encoding="utf-8")
    with pytest.raises(KnownEffectError) as raised:  # rolled back: nothing changed, a known outcome
        await file_apply_patch_set(
            [
                {"path": str(a), "old_string": "n = 1", "new_string": "n = 2"},
                {"path": str(b), "old_string": "does-not-exist", "new_string": "m = 9"},
            ]
        )
    msg = raised.value.content()
    assert msg.startswith("Error: patch 2 of 2")
    assert "No file was changed" in msg and "checkpoint" in msg.lower()
    assert a.read_text(encoding="utf-8").strip() == "n = 1"
    assert b.read_text(encoding="utf-8").strip() == "m = 1"


@pytest.mark.asyncio
async def test_patch_set_takes_one_path_for_every_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live 2026-10-20 the model passed the file once beside its patches: the
    card read "?" for each patch and the set was refused only after the user
    approved it ("every patch needs a path"), then asked for again."""
    monkeypatch.setenv("KAZMA_FILE_CHECKPOINTS_DB", str(tmp_path / "ck.db"))
    _pin_workspace(tmp_path, monkeypatch)
    page = tmp_path / "report.html"
    page.write_text(".item { a: 1; }\n.en .item { b: 2; }\n", encoding="utf-8")
    msg = await file_apply_patch_set(
        [{"old_string": ".item { a: 1; }", "new_string": ".item { a: 9; }"},
         {"old_string": ".en .item { b: 2; }", "new_string": ".en .item { b: 8; }"}],
        verify=False, path=str(page),
    )
    assert not msg.startswith("Error"), msg
    assert page.read_text(encoding="utf-8") == ".item { a: 9; }\n.en .item { b: 8; }\n"


def test_the_card_names_the_file_given_once():
    from kazma_gateway.agent_handler.hitl import _format_patch_preview

    card = _format_patch_preview("file_apply_patch_set", {
        "path": "reports/report.html",
        "patches": [{"old_string": "a", "new_string": "b"}],
    })
    assert "1. reports/report.html" in card and "1. ?" not in card


_TRANSLITERATION = "Kāẓima and Ḥasa; Ṣabāḥ rule, ʿAbd and Filakah.\n"


@pytest.mark.asyncio
async def test_a_set_that_rolls_back_changed_nothing_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live 2026-10-31: a research paper's 19 replace-all patches, one of
    whose letters did not occur. The checkpoint put the paper back, but the
    set RETURNED its error, the journal recorded "effects unknown, must not be
    repeated", and the turn failed. A set whose files are all back as they
    were is a known, correctable error."""
    monkeypatch.setenv("KAZMA_FILE_CHECKPOINTS_DB", str(tmp_path / "ck.db"))
    _pin_workspace(tmp_path, monkeypatch)
    paper = tmp_path / "paper.md"
    paper.write_bytes(_TRANSLITERATION.encode("utf-8"))
    letters = [("ā", "a"), ("ẓ", "z"), ("Ḥ", "H"), ("Ḍ", "D"), ("ʿ", "")]  # no Ḍ in the text
    with pytest.raises(KnownEffectError) as raised:
        await file_apply_patch_set(
            [{"old_string": old, "new_string": new, "replace_all": True} for old, new in letters],
            verify=False, path=str(paper),
        )
    text = raised.value.content()
    assert text.startswith("Error: patch 4 of 5") and "not found" in text
    assert "No file was changed" in text
    assert paper.read_bytes() == _TRANSLITERATION.encode("utf-8")


@pytest.mark.asyncio
async def test_a_set_whose_restore_did_not_hold_stays_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: a restore that leaves a file changed is not a known
    outcome; the set returns its error (the registry marks it uncertain) and
    names the file."""
    monkeypatch.setenv("KAZMA_FILE_CHECKPOINTS_DB", str(tmp_path / "ck.db"))
    _pin_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr("kazma_core.ide.file_checkpoints.restore_checkpoint", lambda cid: [])
    paper = tmp_path / "paper.md"
    paper.write_bytes(_TRANSLITERATION.encode("utf-8"))
    out = await file_apply_patch_set(
        [
            {"old_string": "ā", "new_string": "a", "replace_all": True},
            {"old_string": "Ḍ", "new_string": "D", "replace_all": True},
        ],
        verify=False, path=str(paper),
    )
    assert out.startswith("Error: patch 2 of 2") and "may not be as they were" in out
    assert str(paper) in out
