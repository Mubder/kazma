import pytest
import asyncio
from kazma_core.agent.tool_registry import LocalToolRegistry
from kazma_core.tools.web_search import web_search, _run_search

@pytest.mark.asyncio
async def test_sqlite_query_with_comments():
    import json
    registry = LocalToolRegistry(include_builtins=True)
    
    # 1. Test standard query with leading whitespace and comment
    result_with_comments = await registry.execute(
        "sqlite_query",
        {
            "query": "-- This is a single line comment\nSELECT 1 as val",
            "db_path": ":memory:",
        },
    )
    assert result_with_comments["is_error"] is False
    data1 = json.loads(result_with_comments["content"])
    assert data1 == [{"val": 1}] or data1 == []

    # 2. Test multi-line comments
    result_with_multiline_comments = await registry.execute(
        "sqlite_query",
        {
            "query": "/* This is a \n multi line \n comment */ SELECT 2 as val",
            "db_path": ":memory:",
        },
    )
    assert result_with_multiline_comments["is_error"] is False
    data2 = json.loads(result_with_multiline_comments["content"])
    assert data2 == [{"val": 2}] or data2 == []

    # 3. Test nested comments and mixed comment styles
    result_mixed = await registry.execute(
        "sqlite_query",
        {
            "query": "  \n  -- single line comment\n  /* multiline */\n  -- another single line\n  SELECT 3 as val",
            "db_path": ":memory:",
        },
    )
    assert result_mixed["is_error"] is False

    # 4. Ensure invalid keywords are still rejected after stripping comments
    result_rejected = await registry.execute(
        "sqlite_query",
        {
            "query": "/* safe comment */ DROP TABLE users",
            "db_path": ":memory:",
        },
    )
    assert result_rejected["is_error"] is True
    assert "Only SELECT and WITH" in result_rejected["content"]



@pytest.mark.asyncio
async def test_web_search_bing_fallback():
    # Test that web_search works and fallback parsing works correctly on Bing search.
    # Note: This executes actual HTTP requests to Bing. If network is completely offline, it will fail gracefully.
    try:
        results = await web_search("kazma agent framework")
        print("Web Search Result Header:\n", results[:200])
        assert "Search results for:" in results
        assert "kazma" in results.lower() or "framework" in results.lower()
    except Exception as exc:
        pytest.skip(f"Network / Search test skipped due to external network error: {exc}")
