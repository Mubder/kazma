"""File tools keep a file's own line endings (``kazma_core/tools/text_newlines.py``).

Text mode translated newlines both ways, so on Windows every tool that
rewrote a file turned LF into CRLF (a whole-file diff for one line), on
Linux CRLF into LF, and the checkpoint rollback wrote a CRLF file back as
``\\r\\r\\n``. Every test compares BYTES, on both styles, so it fails on
whichever platform the old text-mode path corrupted.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kazma_core.agent.tool_builtins import filesystem
from kazma_core.ide.workspace_scope import workspace_path_scope
from kazma_core.tools.text_newlines import (
    existing_newline,
    in_newline_style,
    newline_of,
    read_exact,
)

STYLES = pytest.mark.parametrize("nl", ["\n", "\r\n"], ids=["LF", "CRLF"])


def _bytes(text: str, nl: str) -> bytes:
    return in_newline_style(text, nl).encode("utf-8")


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("KAZMA_FILE_CHECKPOINTS_DB", str(tmp_path / "checkpoints.db"))
    from kazma_core.ide import file_checkpoints

    file_checkpoints.reset_file_checkpoint_store()
    yield root
    file_checkpoints.reset_file_checkpoint_store()


def test_the_platform_text_mode_really_changes_one_style(tmp_path):
    """Negative control: without newline="" the round trip is not exact here.

    Whichever style this platform's text mode rewrites is the one the tests
    below would catch if a tool went back to it.
    """
    victim = b"a\nb\n" if os.linesep == "\r\n" else b"a\r\nb\r\n"
    p = tmp_path / "v.txt"
    p.write_bytes(victim)
    p.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    assert p.read_bytes() != victim


def test_the_helpers():
    assert newline_of("a\r\nb\r\n") == "\r\n"
    assert newline_of("a\nb\n") == "\n"
    assert newline_of("no break") == "\n"
    assert in_newline_style("a\nb\r\nc", "\r\n") == "a\r\nb\r\nc"
    assert in_newline_style("a\r\nb\nc", "\n") == "a\nb\nc"


@STYLES
def test_existing_newline(tmp_path, nl):
    p = tmp_path / "f.txt"
    assert existing_newline(p) is None  # missing
    p.write_bytes(b"single line")
    assert existing_newline(p) is None
    p.write_bytes(_bytes("a\nb\n", nl))
    assert existing_newline(p) == nl
    assert read_exact(p) == in_newline_style("a\nb\n", nl)


@STYLES
async def test_file_apply_patch_search_replace(ws, nl):
    from kazma_core.tools.file_apply_patch import file_apply_patch

    p = ws / "m.py"
    p.write_bytes(_bytes("x = 1\ny = 2\nz = 3\n", nl))
    async with workspace_path_scope(ws):
        out = await file_apply_patch("m.py", old_string="y = 2\n", new_string="y = 20\nw = 4\n")
    assert out.startswith("Patched"), out
    assert p.read_bytes() == _bytes("x = 1\ny = 20\nw = 4\nz = 3\n", nl)


@STYLES
async def test_file_apply_patch_unified_diff(ws, nl):
    from kazma_core.tools.file_apply_patch import file_apply_patch

    p = ws / "m.py"
    p.write_bytes(_bytes("a\nb\nc\n", nl))
    diff = "--- a/m.py\n+++ b/m.py\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n"
    async with workspace_path_scope(ws):
        out = await file_apply_patch("m.py", patch=diff)
    assert out.startswith("Patched"), out
    assert p.read_bytes() == _bytes("a\nB\nc\n", nl)


@STYLES
def test_an_add_file_hunk_keeps_the_style(nl):
    from kazma_core.tools.file_apply_patch import apply_unified_diff

    out = apply_unified_diff(in_newline_style("a\nb\n", nl), "@@\n+c\n+d\n")
    assert out == in_newline_style("a\nb\nc\nd\n", nl)


@STYLES
async def test_file_write_overwrite_keeps_the_style(ws, nl):
    from kazma_core.tools.file_write import file_write

    p = ws / "notes.txt"
    p.write_bytes(_bytes("old\ntext\n", nl))
    async with workspace_path_scope(ws):
        out = await file_write("notes.txt", "new\ncontent\n")
    assert out.startswith("Wrote"), out
    assert p.read_bytes() == _bytes("new\ncontent\n", nl)


async def test_file_write_new_file_takes_the_platform_default(ws):
    from kazma_core.tools.file_write import file_write

    async with workspace_path_scope(ws):
        await file_write("fresh.txt", "a\nb\n")
    assert (ws / "fresh.txt").read_bytes() == _bytes("a\nb\n", os.linesep)


@STYLES
async def test_file_append_keeps_the_style(ws, nl):
    cap = type("C", (), {})()
    cap.tools = {}

    def register(**_kw):
        def deco(fn):
            cap.tools[fn.__name__] = fn
            return fn

        return deco

    cap.register = register
    filesystem.register_filesystem_tools(cap)
    p = ws / "log.txt"
    p.write_bytes(_bytes("one\n", nl))
    async with workspace_path_scope(ws):
        out = await cap.tools["file_append"]("log.txt", "two\nthree\n")
    assert out.startswith("Appended"), out
    assert p.read_bytes() == _bytes("one\ntwo\nthree\n", nl)


@STYLES
async def test_a_rollback_restores_the_exact_bytes(ws, nl):
    """Live shape: a CRLF file came back from a rollback as \\r\\r\\n."""
    from kazma_core.tools.file_apply_patch import file_apply_patch_set

    p = ws / "a.py"
    q = ws / "b.py"
    original = _bytes("keep\nme\n", nl)
    p.write_bytes(original)
    q.write_bytes(_bytes("bee\n", nl))
    async with workspace_path_scope(ws):
        out = await file_apply_patch_set(
            [
                {"path": "a.py", "old_string": "keep", "new_string": "CHANGED"},
                {"path": "b.py", "old_string": "not there", "new_string": "x"},
            ],
            verify=False,
        )
    assert "restored checkpoint" in out, out
    assert p.read_bytes() == original
