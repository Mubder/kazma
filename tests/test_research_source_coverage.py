"""A deep research covers what its plan asked (2026-10-31).

Live, a Kuwait history report through the pipeline had a COVID-19 hospital
cost study as its first source and NASA's oil fields page as its third: the
ranker scored the pooled links against the whole topic, by address alone
(`.gov` +4), so sub-questions whose searches found ordinary `.com`/`.org`
pages got no source. Its own report then named the earliest mentions, 2003
and the 2006-2014 crises as gaps, and the gap search never ran: it was gated
on the source count, and all 8 sources had been saved.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from kazma_core.tools import research_pipeline as pipeline, research_session as sessions
from kazma_core.web_acquire import resources
from kazma_core.web_acquire.rank import rank_by_coverage, rank_urls
from kazma_core.web_acquire.result import FetchResult

TOPIC = "A professional research paper on the history of Kuwait from the earliest mention to today"
LIVE = {
    "Kazimah Kuwait earliest historical mention Qurain": [
        "https://www.kazima.org/pages/text/8233", "https://www.britannica.com/place/Kuwait/History"],
    "Kuwait parliamentary crises 2006 2014 dissolution": [
        "https://carnegieendowment.org/research/2025/03/kuwaits-parliament-suspension",
        "https://www.aljazeera.com/news/2024/5/11/kuwait-emir-dissolves-parliament"],
    "Kuwait COVID-19 pandemic economic impact": [
        "https://pmc.ncbi.nlm.nih.gov/articles/PMC10921943/", "https://www.cdc.gov/covid/kuwait"],
}


def _searches_covered(chosen: list[str]) -> list[str]:
    return [q for q, urls in LIVE.items() if any(u in chosen for u in urls)]


def test_each_search_gets_its_best_source_before_any_gets_two():
    chosen = [r.url for r in rank_by_coverage(LIVE, limit=3)]
    assert _searches_covered(chosen) == list(LIVE)
    assert chosen[0] == "https://www.britannica.com/place/Kuwait/History"  # its query's words in its address


def test_negative_control_pooled_ranking_drops_a_sub_question():
    pooled = [u for urls in LIVE.values() for u in urls]
    chosen = [r.url for r in rank_urls(pooled, TOPIC, limit=3)]
    assert chosen[:2] == LIVE["Kuwait COVID-19 pandemic economic impact"]  # .gov first
    assert "Kuwait parliamentary crises 2006 2014 dissolution" not in _searches_covered(chosen)


def test_coverage_keeps_the_domain_cap_and_skips_what_was_taken():
    results = {"a": ["https://one.example/1", "https://one.example/2", "https://one.example/3"],
               "b": ["https://two.example/1"]}
    chosen = [r.url for r in rank_by_coverage(results, max_per_domain=2, exclude=["https://two.example/1"])]
    assert chosen == ["https://one.example/1", "https://one.example/2"]


@pytest.fixture
def controlled(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, '_RESEARCH', resources._CapacityPool('research pipelines', 1, 0))
    monkeypatch.setattr(resources, '_RESEARCH_SESSIONS', resources._CapacityPool('background research sessions', 2, 0))
    monkeypatch.setattr(sessions, '_db_path', lambda: tmp_path / 'sessions.db')
    monkeypatch.setattr(pipeline, '_get_ws_root', lambda: tmp_path)
    monkeypatch.setattr('kazma_core.tools.research_readiness.research_readiness', lambda **kw: {'ready': True})
    return tmp_path


async def _run_with_full_quota(controlled, monkeypatch, *, old_gate: bool):
    from kazma_core.tools import read_url, research_evidence, research_planner, research_synthesize
    from kazma_core.web_acquire import serp
    import kazma_core.web_acquire as acquisition

    initial = [f"https://source{i}.example/history" for i in range(4)]
    followups = [f"https://gap{i}.example/2006-crisis" for i in range(2)]

    async def plan(*args, **kwargs):
        return research_planner.ResearchPlan(search_queries=["initial"])

    async def search(query, **kwargs):
        return serp.SearchResult(ok=True, query=query, urls=followups if query == "2006 crises" else initial)

    async def fetch(url, **kwargs):
        return FetchResult(ok=True, url=url, text="Complete source evidence.")

    async def digest(path):
        return "Source digest"

    async def synthesize(*args, **kwargs):
        return "## Executive summary\nFixture synthesis.\n## Sources\n" + "\n".join(initial)

    gap_calls = []

    async def gaps(*args, **kwargs):
        gap_calls.append(1)
        return SimpleNamespace(method="fixture", needs_more=True, followup_queries=["2006 crises"],
                               gaps=["the 2006-2014 crises"], to_dict=lambda: {})

    monkeypatch.setattr(research_planner, "plan_research_queries", plan)
    monkeypatch.setattr(acquisition, "search", search)
    monkeypatch.setattr(read_url, "_workspace_root", lambda: controlled)
    monkeypatch.setattr(read_url, "_safe_workspace_path", lambda path, url: controlled / path)
    monkeypatch.setattr(read_url, "fetch_result", fetch)
    monkeypatch.setattr(read_url, "digest_research_file", digest)
    monkeypatch.setattr(pipeline, "_register_paper", lambda meta: None)
    monkeypatch.setattr(research_evidence, "extract_claims_from_file", lambda *a, **k: [])
    monkeypatch.setattr(research_synthesize, "synthesize_from_digests", synthesize)
    monkeypatch.setattr(research_planner, "critique_synthesis_gaps", gaps)
    monkeypatch.setenv("KAZMA_RESEARCH_GAP_LOOP", "1")
    if old_gate:  # the loop's condition as it was
        monkeypatch.setattr(pipeline, "_gap_search_due",
                            lambda loops, max_loops, saved, max_sources: loops < max_loops and saved < max_sources)

    report = await asyncio.wait_for(
        pipeline.run_research_pipeline("Kuwait history", depth="deep", max_sources=4), 60)
    directory, = (controlled / "research/reports").iterdir()
    records = json.loads((directory / "sources.json").read_text(encoding="utf-8"))
    return report, [row["url"] for row in records if row["ok"]], gap_calls


@pytest.mark.asyncio
async def test_a_full_quota_still_searches_the_gaps(controlled, monkeypatch):
    report, saved, gap_calls = await _run_with_full_quota(controlled, monkeypatch, old_gate=False)
    assert report.startswith("# Deep research complete")
    assert gap_calls == [1]
    assert {u for u in saved if "gap" in u} == {"https://gap0.example/2006-crisis", "https://gap1.example/2006-crisis"}
    assert len(saved) == 6  # 4 sources, then the gap fill's own budget of 2


@pytest.mark.asyncio
async def test_negative_control_the_old_gate_never_searches_a_full_quota(controlled, monkeypatch):
    report, saved, gap_calls = await _run_with_full_quota(controlled, monkeypatch, old_gate=True)
    assert report.startswith("# Deep research complete")
    assert gap_calls == [] and not [u for u in saved if "gap" in u]
