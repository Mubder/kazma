"""R2 research planner + gap critic."""

from __future__ import annotations

import pytest

from kazma_core.tools.research_planner import (
    critique_synthesis_gaps,
    fallback_queries,
    plan_research_queries,
    _parse_json_blob,
)


def test_fallback_queries_diverse():
    qs = fallback_queries("SQLite WAL", max_queries=6)
    assert len(qs) == 6
    assert qs[0] == "SQLite WAL"
    assert any("risk" in q or "limitation" in q or "criticism" in q for q in qs)


def test_parse_json_blob_fenced():
    raw = '```json\n{"search_queries": ["a", "b"], "sub_questions": ["q1"]}\n```'
    obj = _parse_json_blob(raw)
    assert obj is not None
    assert obj["search_queries"] == ["a", "b"]


@pytest.mark.asyncio
async def test_plan_research_fallback_when_llm_off(monkeypatch):
    monkeypatch.setenv("KAZMA_RESEARCH_LLM_PLANNER", "0")
    plan = await plan_research_queries("OAuth PKCE", max_queries=5, is_deep=True)
    assert plan.method == "fallback"
    assert len(plan.search_queries) >= 3
    assert "OAuth PKCE" in plan.search_queries[0]


@pytest.mark.asyncio
async def test_plan_research_llm_success(monkeypatch):
    monkeypatch.setenv("KAZMA_RESEARCH_LLM_PLANNER", "1")

    async def _fake(system, user, max_tokens=1200):
        return (
            '{"sub_questions":["What is X?","Risks of X?"],'
            '"search_queries":["X definition","X risks official docs","X alternatives"],'
            '"success_criteria":["cite 3 sources"]}'
        )

    monkeypatch.setattr(
        "kazma_core.tools.research_planner._llm_json",
        _fake,
    )
    plan = await plan_research_queries("Topic X", max_queries=5, is_deep=True)
    assert plan.method == "llm"
    assert "X definition" in plan.search_queries
    assert plan.sub_questions


@pytest.mark.asyncio
async def test_critique_heuristic_thin_sources(monkeypatch):
    monkeypatch.setenv("KAZMA_RESEARCH_GAP_LOOP", "1")
    monkeypatch.setenv("KAZMA_RESEARCH_LLM_CRITIC", "0")
    gap = await critique_synthesis_gaps(
        "Python GIL",
        "## Intro\nSome text without enough citations or sources section.\n" * 20,
    )
    assert gap.needs_more is True
    assert gap.followup_queries


@pytest.mark.asyncio
async def test_critique_llm_disabled_env(monkeypatch):
    monkeypatch.setenv("KAZMA_RESEARCH_GAP_LOOP", "0")
    gap = await critique_synthesis_gaps("t", "x" * 500)
    assert gap.method == "disabled"
    assert gap.needs_more is False
