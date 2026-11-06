"""A file_search query that reads like a file name is matched as a name first
(resilience plan I).

Live: asked for "backup-benefits", file_search searched the text of *.py
files (its default glob) and answered "No matches" over the file
docs/backup-benefits.md the owner meant.
"""

from __future__ import annotations

import asyncio
import uuid

from kazma_core.agent.tool_builtins.filesystem import register_filesystem_tools
from kazma_core.agent.tool_registry import LocalToolRegistry
from kazma_core.workspace.binding import resolve_active_root


def _search(**kw):
    registry = LocalToolRegistry()
    register_filesystem_tools(registry)
    return asyncio.run(registry.get_tool("file_search").func(**kw))


def _tree():
    folder = resolve_active_root() / f"names-{uuid.uuid4().hex[:8]}"
    (folder / "docs").mkdir(parents=True)
    (folder / "docs" / "backup-benefits.md").write_text("# Why back up\n", encoding="utf-8")
    (folder / "tool.py").write_text("# backup-benefits are listed in the docs\n", encoding="utf-8")
    return folder


def test_a_file_name_like_query_finds_the_file_by_name():
    folder = _tree()
    out = _search(pattern="backup-benefits", path=str(folder))
    assert out.startswith("Files named like 'backup-benefits':")
    assert "backup-benefits.md" in out.splitlines()[1]
    # The text matches still follow, under their own heading.
    assert "Lines that contain it:" in out and "tool.py:1:" in out


def test_negative_control_text_search_alone_misses_the_file():
    folder = _tree()
    (folder / "tool.py").unlink()
    out = _search(pattern="backup-benefits", path=str(folder))
    assert "backup-benefits.md" in out, "named first even when no text holds it"
    # A plain word is a text search, as before.
    plain = _search(pattern="backup", path=str(folder))
    assert not plain.startswith("Files named like")


def test_a_regex_is_never_a_name():
    folder = _tree()
    out = _search(pattern=r"backup-\w+", path=str(folder))
    assert not out.startswith("Files named like")
    assert "tool.py:1:" in out
