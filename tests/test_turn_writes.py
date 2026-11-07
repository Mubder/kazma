"""No duplicate writes or documents in one turn (resilience plan D4).

The 1.43-million-token DOCX turn wrote the same document again under another
name and generated it twice. Within one turn, file_write of content already
written to another file is refused before anything is written, naming that
file; a generate_pdf/generate_docx identical to an earlier one is refused,
naming its file. The turn's end (the read cache's reset) forgets it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from kazma_core.agent.tool_refusal import RefusedBeforeEffect
from kazma_core.safety.hitl import reset_current_thread_id, set_current_thread_id
from kazma_core.tools import turn_writes
from kazma_core.tools.file_read import clear_read_cache
from kazma_core.tools.file_write import file_write

REPORT = "# Kuwait\n\n" + "A long section of the report. " * 40


@pytest.fixture
def workspace(tmp_path):
    """A workspace of its own, bound as this test's, and a chat thread.

    Bound by the per-task scope, the ladder's first rung: a folder made
    under ``resolve_active_root()`` landed in the checkout whenever an
    earlier test in the process had left the active workspace there
    (``d4-*`` folders beside pyproject.toml, 2026-11-07).
    """
    from kazma_core.ide.workspace_scope import pin_workspace_path, reset_workspace_path

    folder = tmp_path / "d4"
    folder.mkdir()
    scope = pin_workspace_path(tmp_path)
    token = set_current_thread_id("thread-d4")
    yield folder
    clear_read_cache()
    reset_current_thread_id(token)
    reset_workspace_path(scope)


def _write(path, content):
    return asyncio.run(file_write(path, content))


def test_the_same_content_under_another_name_is_refused(workspace):
    assert "Wrote" in _write(str(workspace / "report.md"), REPORT)
    with pytest.raises(RefusedBeforeEffect, match="already written in this turn, to .*report.md"):
        _write(str(workspace / "report_v2.md"), REPORT)
    assert not (workspace / "report_v2.md").exists(), "nothing was written"


def test_negative_control_without_the_record_the_copy_is_written(workspace, monkeypatch):
    monkeypatch.setattr(turn_writes, "earlier_copy", lambda *a, **k: None)
    _write(str(workspace / "report.md"), REPORT)
    _write(str(workspace / "report_v2.md"), REPORT)
    assert (workspace / "report_v2.md").read_text(encoding="utf-8") == REPORT


def test_the_same_file_again_and_other_content_are_written(workspace):
    _write(str(workspace / "report.md"), REPORT)
    assert "Wrote" in _write(str(workspace / "report.md"), REPORT)
    assert "Wrote" in _write(str(workspace / "other.md"), REPORT + "\nmore")


def test_small_files_are_never_judged(workspace):
    _write(str(workspace / "a" / "__init__.py"), "")
    assert "Wrote" in _write(str(workspace / "b" / "__init__.py"), "")


def test_the_turns_end_forgets(workspace):
    _write(str(workspace / "report.md"), REPORT)
    clear_read_cache()  # graph_respond calls it at every turn's end
    assert "Wrote" in _write(str(workspace / "report_v2.md"), REPORT)


def test_another_chat_is_another_turn(workspace):
    _write(str(workspace / "report.md"), REPORT)
    token = set_current_thread_id("another-thread")
    try:
        assert "Wrote" in _write(str(workspace / "report_v2.md"), REPORT)
    finally:
        reset_current_thread_id(token)


# ── the same document twice ───────────────────────────────────────────


def test_the_same_document_twice_is_refused_naming_the_first(workspace, monkeypatch):
    from kazma_skills.native.document_generator import tools as gen

    made = []

    class _Service:
        async def generate(self, kind, payload, **kw):
            made.append(kind)
            path = workspace / f"doc{len(made)}.{kind}"
            return SimpleNamespace(ok=True, data=SimpleNamespace(export_path=str(path)), error=None)

    monkeypatch.setattr(gen, "DocumentService", _Service)
    monkeypatch.setattr(gen, "_output", lambda result, label: f"{label} saved: {result.data.export_path}")
    monkeypatch.setattr(gen, "_verify_pdf_content", lambda *a, **k: None)

    async def _keep(*a, **k):
        return ""

    monkeypatch.setattr(gen, "_keep_with_research", _keep)
    sections = [{"heading": "Kuwait", "body": "A long section of the report. " * 30}]
    first = asyncio.run(gen.generate_docx("Kuwait history", sections=sections))
    assert "doc1.docx" in first
    with pytest.raises(RefusedBeforeEffect, match="already generated in this turn, at .*doc1.docx"):
        asyncio.run(gen.generate_docx("Kuwait history", sections=sections))
    assert made == ["docx"], "the second call generated nothing"
    # A changed document is a new one.
    changed = [{"heading": "Kuwait", "body": sections[0]["body"] + " Revised."}]
    assert "doc2.docx" in asyncio.run(gen.generate_docx("Kuwait history", sections=changed))
