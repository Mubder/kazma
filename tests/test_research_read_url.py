"""Research tooling: read_url paging, save, chunks, truncate policy."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import importlib

from kazma_core.agent.graph_builder import (
    TOOL_RESULT_MAX_CHARS,
    TOOL_RESULT_RESEARCH_MAX_CHARS,
    truncate_tool_result,
)

ru = importlib.import_module("kazma_core.tools.read_url")


def test_research_tools_get_higher_truncate_cap() -> None:
    # Caps were raised: default tools 100k, research/file/read tools 200k.
    # A 150k input sits BETWEEN them — it truncates for a default tool but
    # passes through unchanged for a research tool, proving the differential.
    big = "Z" * 150_000
    cut = truncate_tool_result(big, tool_name="read_url")
    assert len(cut) == 150_000  # under the 200k research cap — unchanged
    assert "[truncated" not in cut

    # NOTE: shell_exec IS in _RESEARCH_TOOL_NAMES (execution output gets the
    # higher cap too) — use a non-research tool to see the default cap.
    short_tool = truncate_tool_result(big, tool_name="schedule_task")
    assert len(short_tool) <= TOOL_RESULT_MAX_CHARS + 80  # marker overhead
    assert len(short_tool) > 4000  # the default cap is 100k, not the old 4k
    assert "[truncated" in short_tool


def test_truncate_short_unchanged() -> None:
    assert truncate_tool_result("hello", tool_name="read_url") == "hello"


def test_file_search_does_not_inherit_research_cap() -> None:
    """Substring `"file" in name` used to give file_search the 200k research
    cap, which re-inflated a just-trimmed prompt to ~50k tokens mid-turn."""
    from kazma_core.agent.graph_helpers import TOOL_RESULT_FILE_MAX_CHARS

    big = "Z" * 80_000
    cut = truncate_tool_result(big, tool_name="file_search")
    assert "[truncated" in cut
    assert len(cut) <= TOOL_RESULT_FILE_MAX_CHARS + 80
    # Negative control: research tools still pass 150k unchanged.
    research = truncate_tool_result("Z" * 150_000, tool_name="read_url")
    assert "[truncated" not in research


def test_file_read_uses_file_cap_not_research() -> None:
    from kazma_core.agent.graph_helpers import TOOL_RESULT_FILE_MAX_CHARS

    big = "Y" * 80_000
    cut = truncate_tool_result(big, tool_name="file_read")
    assert "[truncated" in cut
    assert len(cut) <= TOOL_RESULT_FILE_MAX_CHARS + 80


@pytest.mark.asyncio
async def test_read_url_paging_uses_cache(tmp_path: Path, monkeypatch) -> None:
    ru.clear_url_cache()
    full = "HELLO-" + ("body " * 4000)
    with patch.object(ru, "_fetch_full_text", return_value=full) as mock_fetch:
        # First call fetches (min window is 200 chars)
        a = await ru.read_url("https://example.com/long", offset=0, max_chars=500)
        assert "chars 0:500" in a
        assert mock_fetch.await_count == 1
        # Put in cache manually as fetch mock bypasses cache put
        ru._cache_put("https://example.com/long", full)
        b = await ru.read_url("https://example.com/long", offset=500, max_chars=500)
        assert "chars 500:1000" in b


@pytest.mark.asyncio
async def test_read_url_to_file_and_chunks(tmp_path: Path) -> None:
    ru.clear_url_cache()
    body = ("# Title section\n\n" + ("paragraph text " * 200)) * 5
    ws = tmp_path / "workspace"
    ws.mkdir()

    async def fake_fetch(url: str) -> str:
        return body

    with (
        patch.object(ru, "_fetch_full_text", side_effect=fake_fetch),
        patch.object(ru, "_workspace_root", return_value=ws),
    ):
        msg = await ru.read_url_to_file("https://example.com/doc", path="sample.md")
        assert "Saved full extract" in msg
        path = ws / "research" / "sample.md"
        assert path.is_file()
        saved = path.read_text(encoding="utf-8")
        assert "Source: https://example.com/doc" in saved
        assert "Title section" in saved

        listing = await ru.list_research_chunks("research/sample.md", chunk_size=800)
        assert "chunk_index=0" in listing
        assert "chunks:" in listing

        chunk0 = await ru.read_research_chunk(
            "research/sample.md", chunk_index=0, chunk_size=800
        )
        assert "chunk 0/" in chunk0
        assert "Title section" in chunk0 or "paragraph" in chunk0

        outline = await ru.summarize_research_file(
            "research/sample.md", chunk_size=800, max_chunks=10
        )
        assert "Extractive summary" in outline

        digest = await ru.digest_research_file(
            "research/sample.md", chunk_size=800, max_output_chars=3000
        )
        assert "Research digest" in digest
        assert "chunks processed" in digest


@pytest.mark.asyncio
async def test_save_anywhere_under_workspace(tmp_path: Path) -> None:
    ru.clear_url_cache()
    ws = tmp_path / "ws"
    ws.mkdir()
    with (
        patch.object(ru, "_fetch_full_text", return_value="hello world content"),
        patch.object(ru, "_workspace_root", return_value=ws),
    ):
        msg = await ru.read_url_to_file("https://example.com/x", path="notes/out.md")
        assert "notes/out.md" in msg.replace("\\", "/")
        assert (ws / "notes" / "out.md").is_file()


@pytest.mark.asyncio
async def test_crawl_site_bounded(tmp_path: Path) -> None:
    from kazma_core.tools import web_research as wr

    ws = tmp_path / "ws"
    ws.mkdir()
    html = '<html><body><a href="/b">B</a><a href="https://evil.com/x">x</a></body></html>'

    async def fake_html(url: str):
        if url.endswith("/b"):
            return "<html><body>page b</body></html>", url
        return html, url

    async def fake_text(url: str) -> str:
        return f"content for {url}"

    with (
        patch.object(wr, "_fetch_html", side_effect=fake_html),
        patch.object(wr, "_fetch_full_text", side_effect=fake_text),
        patch.object(wr, "_workspace_root", return_value=ws),
        patch.object(ru, "_workspace_root", return_value=ws),
    ):
        # SSRF validate_url will run — use example.com which is public DNS
        out = await wr.crawl_site(
            "https://example.com/",
            max_pages=3,
            max_depth=1,
            same_domain_only=True,
            delay_ms=0,
            save=True,
        )
    assert "Crawl index" in out
    assert "Pages fetched" in out
