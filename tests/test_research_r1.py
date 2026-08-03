"""R0/R1 deep research: ranking, claims, rubric, fail-closed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kazma_core.tools.research_eval import (
    GOLDEN_TOPICS,
    score_report_markdown,
)
from kazma_core.tools.research_evidence import (
    extract_claims_heuristic,
    claims_to_markdown,
)
from kazma_core.web_acquire.rank import rank_urls, score_url


def test_golden_topics_present():
    assert len(GOLDEN_TOPICS) >= 5
    ids = {t["id"] for t in GOLDEN_TOPICS}
    assert "python-gil" in ids


def test_rank_prefers_docs_over_social():
    urls = [
        "https://www.facebook.com/posts/123",
        "https://docs.python.org/3/library/asyncio.html",
        "https://arxiv.org/abs/2301.00001",
        "https://random-blog.example/click/tag/seo",
    ]
    ranked = rank_urls(urls, "python asyncio", max_per_domain=2)
    assert ranked[0].url.startswith("https://docs.python.org") or "arxiv" in ranked[
        0
    ].url
    # facebook should rank low / filtered by demotion even if present
    tops = [r.url for r in ranked[:2]]
    assert not any("facebook" in u for u in tops)


def test_score_url_boosts_edu():
    sc_edu, _ = score_url("https://mit.edu/research/x", "research")
    sc_spam, _ = score_url("https://pinterest.com/pin/1", "research")
    assert sc_edu > sc_spam


def test_extract_claims_heuristic():
    body = """
# Source
URL: https://docs.example.com/a

The Python GIL prevents true parallel execution of bytecode in one process.
According to the documentation, free-threading builds remove this restriction.
Click here to buy our product now with free shipping worldwide today.
"""
    claims = extract_claims_heuristic(
        body, source_path="src.md", source_url="https://docs.example.com/a", max_claims=5
    )
    assert claims
    assert any("GIL" in c.text or "free-threading" in c.text for c in claims)
    md = claims_to_markdown(claims)
    assert "Evidence" in md


def test_rubric_pass_structure():
    report = """
# Research report: SQLite WAL

## Executive summary
WAL improves concurrency for readers.

## Background
SQLite WAL mode is documented at https://www.sqlite.org/wal.html

## Key findings
Writers do not block readers in the common case.

## Conclusions
Use WAL for multi-reader apps.

## Sources
- https://www.sqlite.org/wal.html
- https://www.sqlite.org/lockingv3.html
"""
    # pad to min length
    report = report + ("\nAdditional context about concurrency and durability. " * 40)
    rub = score_report_markdown(report, min_sources=2, min_chars=500)
    assert rub.checks.get("has_sources_section")
    assert rub.checks.get("has_headings")
    assert rub.score >= 50


def test_rubric_fails_error_only():
    rub = score_report_markdown("Error: nothing worked", min_sources=4)
    assert rub.ok is False
    assert rub.checks.get("not_error_only") is False


@pytest.mark.asyncio
async def test_pipeline_fail_closed_deep(monkeypatch, tmp_path):
    """Deep depth aborts when too few pages acquire."""
    import importlib

    import kazma_core.tools.research_pipeline as rp
    import kazma_core.web_acquire as wa

    monkeypatch.setattr(rp, "_get_ws_root", lambda: tmp_path)
    monkeypatch.setattr(rp, "_workspace_research_dir", lambda topic: tmp_path / "r")
    monkeypatch.setattr(rp, "_register_paper", lambda meta: None)

    async def _search(q, max_results=8, purpose="research"):
        from kazma_core.web_acquire.search import SearchResult

        return SearchResult(
            ok=True,
            query=q,
            markdown="- https://docs.python.org/3/\n- https://www.sqlite.org/wal.html",
            urls=[
                "https://docs.python.org/3/",
                "https://www.sqlite.org/wal.html",
            ],
        )

    monkeypatch.setattr(wa, "search", _search)

    async def fake_read_url_to_file(url, path=""):
        return "Error: blocked"

    ru = importlib.import_module("kazma_core.tools.read_url")
    monkeypatch.setattr(ru, "read_url_to_file", fake_read_url_to_file)

    out = await rp.run_research_pipeline(
        "Python GIL",
        depth="deep",
        max_sources=4,
        parallel_acquire=False,
    )
    assert out.startswith("Error:")
    assert (
        "aborted" in out.lower()
        or "could not acquire" in out.lower()
        or "no urls" in out.lower()
    )
