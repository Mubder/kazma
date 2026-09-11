"""The IDE editor must receive file bytes, not the agent's view of them.

`IdeService.read_file` used to return `file_read` tool output verbatim.
`file_read` is LLM-facing and reformats on purpose: every line gets a
``"{LINE_NUM}|"`` prefix so the model can cite lines, and a repeat read inside
one turn is answered with an ``[ALREADY READ THIS TURN ...]`` banner instead of
the content.

Both are correct for a model and wrong for an editor. Opening a file in the web
IDE showed numbered, banner-prefixed text — and Save writes the buffer back, so
the visible bug was cosmetic and the real one was silent corruption of the
user's file. Found by opening `serve.py` in the live IDE, not by a test.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from kazma_core.ide.service import MAX_EDITOR_FILE_BYTES, IdeService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        "kazma_core.ide.service._resolve_workspace_root", lambda: tmp_path.resolve()
    )
    return tmp_path


async def test_read_returns_bytes_not_the_agent_format(workspace: Path) -> None:
    source = 'def main():\n    return "ok"\n'
    # write_bytes, not write_text: on Windows write_text applies universal
    # newlines, so "\n" would land as CRLF and the assertion would compare the
    # buffer against something that is not what is on disk.
    (workspace / "app.py").write_bytes(source.encode("utf-8"))

    result = await IdeService().read_file("app.py")

    assert result["ok"] is True
    assert result["content"] == source, "editor content must be the file, verbatim"
    assert "ALREADY READ THIS TURN" not in result["content"]
    assert not re.search(r"^\s*\d+\|", result["content"], re.M), "line-number prefixes leaked"


async def test_a_second_read_in_the_same_turn_still_returns_content(
    workspace: Path,
) -> None:
    """The dedup banner is the sharper half of the bug.

    `file_read` replaces the body with a "you already read this" notice, so the
    second open of a file in one turn handed the editor a paragraph of prose
    where the file should be. Switching tabs back and forth was enough.
    """
    source = "alpha\nbeta\n"
    (workspace / "notes.txt").write_bytes(source.encode("utf-8"))

    service = IdeService()
    first = await service.read_file("notes.txt")
    second = await service.read_file("notes.txt")

    assert first["content"] == source
    assert second["content"] == source


async def test_line_endings_survive(workspace: Path) -> None:
    """CRLF must not silently become LF — Save would rewrite every line."""
    (workspace / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")

    result = await IdeService().read_file("crlf.txt")

    assert result["content"] == "one\r\ntwo\r\n"


async def test_utf8_is_decoded_explicitly_not_by_platform_locale(
    workspace: Path,
) -> None:
    (workspace / "ar.txt").write_bytes("مرحبا — ≥\n".encode())

    result = await IdeService().read_file("ar.txt")

    assert result["content"] == "مرحبا — ≥\n"


async def test_undecodable_bytes_do_not_fail_the_open(workspace: Path) -> None:
    """One bad byte should cost one glyph, not the whole file."""
    (workspace / "messy.txt").write_bytes(b"good\xffbad\n")

    result = await IdeService().read_file("messy.txt")

    assert result["ok"] is True
    assert "good" in result["content"] and "bad" in result["content"]


async def test_binary_files_are_refused_rather_than_mangled(workspace: Path) -> None:
    """Decoding a PNG into a text buffer invites saving mojibake over it."""
    (workspace / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00")

    result = await IdeService().read_file("logo.png")

    assert result["ok"] is False
    assert "binary" in (result["error"] or "").lower()
    assert result["content"] == ""


async def test_oversized_files_are_refused_with_the_limit_named(
    workspace: Path,
) -> None:
    (workspace / "huge.log").write_bytes(b"x" * (MAX_EDITOR_FILE_BYTES + 1))

    result = await IdeService().read_file("huge.log")

    assert result["ok"] is False
    assert "too large" in (result["error"] or "").lower()


async def test_containment_still_refuses_paths_outside_the_workspace(
    workspace: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Reading raw bytes must not have loosened the traversal guard."""
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("nope", encoding="utf-8")

    result = await IdeService().read_file("../outside/secret.txt")

    assert result["ok"] is False
    assert result["content"] == ""


async def test_path_grants_are_still_consulted(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The workspace/grant check `file_read` applied must survive the change.

    Bypassing the LLM formatting is fine; bypassing the policy layer is not.
    """
    (workspace / "app.py").write_text("x = 1\n", encoding="utf-8")

    class _Denied:
        allowed = False
        reason = "denied by test"

    called: list[str] = []

    def _check(path: Any, mode: str) -> Any:
        called.append(mode)
        return _Denied()

    monkeypatch.setattr(
        "kazma_core.workspace.path_policy.check_path_access", _check
    )
    monkeypatch.setattr(
        "kazma_core.workspace.path_policy.denied_message",
        lambda *a, **k: "access denied",
    )

    result = await IdeService().read_file("app.py")

    assert called == ["read"], "path policy was not consulted"
    assert result["ok"] is False
    assert result["content"] == ""


async def test_missing_file_reports_cleanly(workspace: Path) -> None:
    result = await IdeService().read_file("nope.py")
    assert result["ok"] is False
    assert "not found" in (result["error"] or "").lower()
