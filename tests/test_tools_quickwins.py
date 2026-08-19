"""Tests for quick-win tools: web_search, read_url, export_session, truncation, and errors."""

from __future__ import annotations

import json
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _disable_commitment_gate(monkeypatch: pytest.MonkeyPatch):
    """Disable the commitment layer for tool-plumbing tests here.

    They execute fabricated tool names not in the side-effect registry —
    the unregistered-mutator fail-closed DENY (default ON since 2026-08-15)
    blocks them before the code under test is reached (deep-audit
    2026-08-19 CI triage; same fixture as tests/test_mcp_bridge.py).
    """
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")


# ── helpers to inject mock modules for missing optional deps ──────────────
def _make_mock_ddgs(ddgs_instance):
    """Create a fake duckduckgo_search module with DDGS returning *ddgs_instance*."""
    mod = ModuleType("duckduckgo_search")
    mod.DDGS = MagicMock(return_value=ddgs_instance)
    return mod


# ── helpers to disable the non-DDGS search rungs ──────────────────────────
# The web_search ladder is searxng -> duckduckgo -> bing -> wikipedia. A REAL
# SearXNG instance on this machine returns live results before the DDGS mock
# ever runs, so tests that want deterministic DDGS behavior must switch the
# other rungs off.
_OTHER_BACKENDS = (
    "kazma_core.tools.web_search._searxng_search",
    "kazma_core.tools.web_search._bing_search",
    "kazma_core.tools.web_search._wikipedia_search",
)


def _patch_backends_off():
    """All non-DDGS rungs return no results."""
    return [patch(name, return_value=(None, "off:test")) for name in _OTHER_BACKENDS]


def _patch_backends_raising(exc):
    """All four rungs raise *exc* so web_search's top-level error mapping fires."""
    names = _OTHER_BACKENDS + ("kazma_core.tools.web_search._ddg_search",)
    return [patch(name, side_effect=exc) for name in names]


def _make_mock_trafilatura(extract_return):
    """Create a fake trafilatura module whose extract() returns *extract_return*."""
    mod = ModuleType("trafilatura")
    mod.extract = MagicMock(return_value=extract_return)
    return mod


# ── helper for trafilatura raising an exception ───────────────────────────
def _make_mock_trafilatura_raises(exc):
    """Create a fake trafilatura module whose extract() raises *exc*."""
    mod = ModuleType("trafilatura")
    mod.extract = MagicMock(side_effect=exc)
    return mod

# ══════════════════════════════════════════════════════════════════════════
# web_search tests
# ══════════════════════════════════════════════════════════════════════════


class TestWebSearch:
    """Tests for the web_search tool."""

    @pytest.mark.asyncio
    async def test_web_search_returns_results(self) -> None:
        """web_search returns formatted markdown when DDGS returns results."""
        mock_results = [
            {"title": "Result 1", "href": "https://example.com/1", "body": "First result body."},
            {"title": "Result 2", "href": "https://example.com/2", "body": "Second result body."},
        ]

        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text = MagicMock(return_value=mock_results)

        fake_mod = _make_mock_ddgs(mock_ddgs)
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in _patch_backends_off():
                stack.enter_context(p)
            stack.enter_context(patch.dict(sys.modules, {"duckduckgo_search": fake_mod}))
            from kazma_core.tools.web_search import web_search

            result = await web_search("test query", max_results=2)

        assert "Result 1" in result
        assert "Result 2" in result
        assert "https://example.com/1" in result
        assert "test query" in result

    @pytest.mark.asyncio
    async def test_web_search_empty(self) -> None:
        """web_search returns a 'no results' message when DDGS returns empty."""
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text = MagicMock(return_value=[])

        fake_mod = _make_mock_ddgs(mock_ddgs)
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in _patch_backends_off():
                stack.enter_context(p)
            stack.enter_context(patch.dict(sys.modules, {"duckduckgo_search": fake_mod}))
            from kazma_core.tools.web_search import web_search

            result = await web_search("nonexistent query xyz")

        # All rungs disabled/empty: the actionable empty message (restored
        # 2026-08-15 — _format_empty was dead code; empty results again get
        # the rephrase/read_url/SearXNG guidance instead of an empty header).
        assert "No web results" in result
        assert "nonexistent query xyz" in result
        assert "read_url" in result


# ══════════════════════════════════════════════════════════════════════════
# read_url tests
# ══════════════════════════════════════════════════════════════════════════


class TestReadUrl:
    """Tests for the read_url tool."""

    @pytest.mark.asyncio
    async def test_read_url_extracts_content(self) -> None:
        """read_url extracts text from HTML using trafilatura."""
        html = "<html><body><p>Hello World</p></body></html>"

        mock_response = MagicMock()
        mock_response.text = html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        fake_trafilatura = _make_mock_trafilatura("Hello World")
        with (
            patch.dict(sys.modules, {"trafilatura": fake_trafilatura}),
            patch(
                "kazma_core.tools.read_url._fetch_via_optional_backends",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "kazma_core.tools.read_url._get_capped",
                new=AsyncMock(return_value=(mock_response, html, False)),
            ),
        ):
            # Deterministic seams (deep-audit 2026-08-19 CI triage): the old
            # test patched httpx.AsyncClient, but the fetch ladder builds
            # its client via the scraping factory and falls through to REAL
            # network rungs when that patch doesn't intercept — same lesson
            # as test_read_url_http_error below. Mock the optional-backends
            # rung and the capped GET instead.
            from kazma_core.tools.read_url import read_url

            result = await read_url("https://example.com/extract-test")

        assert "Hello World" in result

    @pytest.mark.asyncio
    async def test_read_url_http_error(self) -> None:
        """read_url returns a friendly error on HTTP failure.

        The fetch ladder (Jina -> Firecrawl -> httpx -> Playwright, §24B) has
        its own failover, so the tool's error contract is tested by having
        the ladder entry report an HTTP failure — patching httpx.AsyncClient
        just makes the ladder fall through to REAL network rungs.
        """
        from unittest.mock import patch as _patch

        with _patch(
            "kazma_core.tools.read_url._fetch_full_text",
            new=AsyncMock(return_value="Error: HTTP 404 Not Found"),
        ):
            from kazma_core.tools.read_url import read_url

            result = await read_url("https://example.com/404")

        assert "Error" in result


# ══════════════════════════════════════════════════════════════════════════
# Truncation middleware tests
# ══════════════════════════════════════════════════════════════════════════


class TestTruncation:
    """Tests for tool result truncation in tool_worker_node."""

    @pytest.mark.asyncio
    async def test_truncation_applied(self) -> None:
        """Results exceeding 4000 chars are truncated with a marker."""
        from kazma_core.agent.tool_registry import LocalToolRegistry
        from kazma_core.agent.graph_builder import truncate_tool_result

        registry = LocalToolRegistry(include_builtins=False)

        @registry.register(description="Test tool", category="test")
        async def long_output() -> str:
            return "X" * 5000

        result = await registry.execute("long_output", {})
        raw_content = result["content"]
        assert len(raw_content) == 5000  # registry doesn't truncate

        # Test through the actual production truncation function. The DEFAULT
        # cap is now 200k chars (file/research tools keep full output), so
        # pass an explicit cap to exercise the truncation logic itself.
        content = truncate_tool_result(raw_content, max_chars=1000)

        assert len(content) < 5000
        assert "[truncated 4000 chars]" in content

    @pytest.mark.asyncio
    async def test_truncation_short_unchanged(self) -> None:
        """Results under 4000 chars pass through unchanged."""
        from kazma_core.agent.tool_registry import LocalToolRegistry
        from kazma_core.agent.graph_builder import truncate_tool_result

        registry = LocalToolRegistry(include_builtins=False)

        @registry.register(description="Short tool", category="test")
        async def short_output() -> str:
            return "Hello, World!"

        result = await registry.execute("short_output", {})
        content = truncate_tool_result(result["content"])

        assert content == "Hello, World!"
        assert "[truncated" not in content


# ══════════════════════════════════════════════════════════════════════════
# Friendly error messages test
# ══════════════════════════════════════════════════════════════════════════


class TestFriendlyErrors:
    """Tests for user-friendly error messages."""

    @pytest.mark.asyncio
    async def test_error_friendly_message(self) -> None:
        """ConnectionError is mapped to a friendly message, not a traceback.

        The ladder catches per-backend errors and fails over, so to reach
        web_search's top-level mapping ALL backends must raise.
        """
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in _patch_backends_raising(ConnectionError("Network unreachable")):
                stack.enter_context(p)
            from kazma_core.tools.web_search import web_search

            result = await web_search("test")

        assert "Error" in result
        assert "Could not connect" in result
        # Must NOT contain raw traceback text
        assert "Network unreachable" not in result
        assert "Traceback" not in result


# ══════════════════════════════════════════════════════════════════════════
# export_session tests
# ══════════════════════════════════════════════════════════════════════════


class TestExportSession:
    """Tests for the export_session tool.

    The export_session function must accept messages as an explicit
    parameter (not via a module-global list) so that concurrent sessions
    do not corrupt each other's export data (VAL-CRIT-005).
    """

    @pytest.mark.asyncio
    async def test_export_session_json(self) -> None:
        """export_session with format='json' returns valid JSON with messages."""
        from kazma_core.tools.export_session import export_session

        test_messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        result = await export_session(format="json", messages=test_messages)
        data = json.loads(result)

        assert "exported_at" in data
        assert data["message_count"] == 2
        assert len(data["messages"]) == 2
        assert data["messages"][0]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_export_session_markdown(self) -> None:
        """export_session with format='markdown' returns formatted markdown."""
        from kazma_core.tools.export_session import export_session

        test_messages = [
            {"role": "user", "content": "What is AI?"},
            {"role": "assistant", "content": "AI is artificial intelligence."},
        ]

        result = await export_session(format="markdown", messages=test_messages)

        assert "# Session Export" in result
        assert "User" in result
        assert "What is AI?" in result
        assert "AI is artificial intelligence" in result
        assert "**Messages:** 2" in result

    @pytest.mark.asyncio
    async def test_export_session_unknown_format(self) -> None:
        """export_session with unknown format returns an error."""
        from kazma_core.tools.export_session import export_session

        result = await export_session(format="csv", messages=[{"role": "user", "content": "test"}])
        assert "Error" in result
        assert "csv" in result

    @pytest.mark.asyncio
    async def test_export_session_empty(self) -> None:
        """export_session with no messages returns an error."""
        from kazma_core.tools.export_session import export_session

        result = await export_session(format="json", messages=[])
        assert "Error" in result
        assert "No session" in result

    @pytest.mark.asyncio
    async def test_export_session_markdown_with_tool_calls(self) -> None:
        """export_session markdown includes tool calls and skips system messages."""
        from kazma_core.tools.export_session import export_session

        test_messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Search for X"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "web_search", "arguments": '{"query":"X"}'}}],
            },
            {"role": "tool", "content": "Result here", "name": "web_search"},
        ]
        result = await export_session(format="markdown", messages=test_messages)

        # System should be skipped
        assert "You are helpful" not in result
        # Tool call should appear
        assert "web_search" in result

    @pytest.mark.asyncio
    async def test_export_session_no_module_global(self) -> None:
        """VAL-CRIT-005: export_session.py must not expose a module-global
        _session_messages list or set_session_messages/get_session_messages
        accessors that share state across concurrent sessions."""
        import kazma_core.tools.export_session as es_module

        # No module-level mutable list
        assert not hasattr(es_module, "_session_messages")
        # No global accessor functions that mutate shared state
        assert not hasattr(es_module, "set_session_messages")
        assert not hasattr(es_module, "get_session_messages")

    @pytest.mark.asyncio
    async def test_export_session_concurrent_isolation(self) -> None:
        """VAL-CRIT-005: Two concurrent export_session calls with different
        message lists must each export only their own messages.

        This is the core regression test for the shared-global bug: before
        the fix, both tasks would read the same module-global list and the
        second set_session_messages() call would clobber the first.
        """
        import asyncio

        from kazma_core.tools.export_session import export_session

        messages_a = [
            {"role": "user", "content": "session-A-message"},
            {"role": "assistant", "content": "reply-A"},
        ]
        messages_b = [
            {"role": "user", "content": "session-B-message"},
            {"role": "assistant", "content": "reply-B"},
        ]

        # Interleave the two exports to maximize the chance of catching
        # shared-state corruption. Each task exports its own message list.
        results = await asyncio.gather(
            export_session(format="json", messages=messages_a),
            export_session(format="json", messages=messages_b),
        )

        data_a = json.loads(results[0])
        data_b = json.loads(results[1])

        # Each export must contain ONLY its own messages.
        assert data_a["message_count"] == 2
        assert data_b["message_count"] == 2
        assert data_a["messages"][0]["content"] == "session-A-message"
        assert data_b["messages"][0]["content"] == "session-B-message"
        # Cross-contamination check
        assert "session-B-message" not in json.dumps(data_a)
        assert "session-A-message" not in json.dumps(data_b)


class TestReadUrlEdgeCases:
    """Additional read_url edge cases for coverage."""

    @pytest.mark.asyncio
    async def test_read_url_empty(self) -> None:
        """read_url returns error for empty URL."""
        from kazma_core.tools.read_url import read_url

        result = await read_url("")
        assert "Error" in result
        assert "No URL" in result

    @pytest.mark.asyncio
    async def test_read_url_connection_error(self) -> None:
        """read_url returns friendly message on ConnectionError."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from kazma_core.tools.read_url import read_url

            result = await read_url("https://down.example.com")

        assert "Error" in result
        assert "Could not connect" in result

    @pytest.mark.asyncio
    async def test_read_url_timeout(self) -> None:
        """read_url returns friendly message on TimeoutError."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=TimeoutError("timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from kazma_core.tools.read_url import read_url

            result = await read_url("https://slow.example.com")

        assert "Error" in result
        assert "timed out" in result

    @pytest.mark.asyncio
    async def test_read_url_no_content_extracted(self) -> None:
        """read_url returns error when trafilatura extracts nothing."""
        mock_response = MagicMock()
        mock_response.text = "<html><body></body></html>"
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        fake_trafilatura = _make_mock_trafilatura(None)
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.dict(sys.modules, {"trafilatura": fake_trafilatura}),
        ):
            from kazma_core.tools.read_url import read_url

            result = await read_url("https://empty.example.com")

        assert "Error" in result
        assert "extract" in result.lower() or "empty" in result.lower()


class TestWebSearchEdgeCases:
    """Additional web_search edge cases for coverage."""

    @pytest.mark.asyncio
    async def test_web_search_timeout(self) -> None:
        """web_search returns friendly message on TimeoutError."""
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text = MagicMock(side_effect=TimeoutError("timed out"))

        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in _patch_backends_raising(TimeoutError("timed out")):
                stack.enter_context(p)
            from kazma_core.tools.web_search import web_search

            result = await web_search("test")

        assert "Error" in result
        assert "timed out" in result
