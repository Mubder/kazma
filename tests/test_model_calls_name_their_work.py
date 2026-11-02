"""Every model call names the work it belongs to (2026-11, FA-260).

Research and Diagnose model calls were ledgered with no thread, so their
tokens and cost belonged to nothing: no chat's ``/cost`` and no run's line.
A Diagnose turn runs through ``run_agent_turn``, which binds its thread in
the context but puts it in no graph state; research's planner, gap critic
and synthesis passed no thread at all.

The ledger takes the thread from the caller's context when the caller does
not name one, and research names its job (``research:<job id>``), or the
chat that ran it. The ledger without the fallback is the negative control.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def ledger_rows(monkeypatch):
    from kazma_core.observability import llm_ledger

    rows: list[dict] = []
    monkeypatch.setattr(llm_ledger, "_record_if_enabled", lambda **fields: rows.append(fields))
    return rows


@pytest.mark.anyio
async def test_a_call_with_no_thread_takes_the_turns(ledger_rows):
    from kazma_core.observability.llm_ledger import arecord_llm_call
    from kazma_core.safety.hitl import reset_current_thread_id, set_current_thread_id

    token = set_current_thread_id("diagnose-inc-1")
    try:
        await arecord_llm_call(thread_id="", iteration=1, provider="Fake", model="m")
    finally:
        reset_current_thread_id(token)
    assert ledger_rows[-1]["thread_id"] == "diagnose-inc-1"


@pytest.mark.anyio
async def test_a_named_thread_wins(ledger_rows):
    from kazma_core.observability.llm_ledger import arecord_llm_call
    from kazma_core.safety.hitl import reset_current_thread_id, set_current_thread_id

    token = set_current_thread_id("chat-1")
    try:
        await arecord_llm_call(thread_id="research:job-9", iteration=1, provider="Fake", model="m")
    finally:
        reset_current_thread_id(token)
    assert ledger_rows[-1]["thread_id"] == "research:job-9"


@pytest.mark.anyio
async def test_negative_control_without_the_fallback_the_row_names_nothing(ledger_rows, monkeypatch):
    from kazma_core.observability.llm_ledger import arecord_llm_call
    from kazma_core.safety import hitl

    monkeypatch.setattr(hitl, "get_current_thread_id", lambda: None)
    await arecord_llm_call(thread_id="", iteration=1, provider="Fake", model="m")
    assert ledger_rows[-1]["thread_id"] == ""


@pytest.fixture
def research_calls(monkeypatch):
    from kazma_core.agent import resilient_chat as rc
    from kazma_core.web_acquire import research_llm

    seen: list[str] = []

    async def _fake(client, **kw):
        seen.append(kw.get("thread_id", ""))
        return {"content": '{"search_queries": ["a", "b", "c"], "gaps": []}'}

    monkeypatch.setattr(rc, "resilient_chat", _fake)
    monkeypatch.setattr(research_llm, "resolved_route", lambda: (object(), "m", None))
    return seen


@pytest.mark.anyio
async def test_the_research_planner_and_critic_name_their_work(research_calls):
    from kazma_core.tools.research_planner import critique_synthesis_gaps, plan_research_queries

    await plan_research_queries("tides of the gulf", work_id="research:job-1")
    await critique_synthesis_gaps("tides of the gulf", "a draft " * 40, work_id="research:job-1")
    assert research_calls and set(research_calls) == {"research:job-1"}, research_calls


def test_the_pipeline_names_its_job_for_every_model_call():
    """Source check on the one pipeline: each of its four model stages passes
    the work id (the behaviour of each stage is held above)."""
    import inspect

    from kazma_core.tools import research_pipeline

    body = inspect.getsource(research_pipeline._run_research_pipeline)
    assert 'f"research:{_job.job.job_id}"' in body
    for stage in ("plan_research_queries(", "critique_synthesis_gaps(", "synthesize_from_digests("):
        calls = body.split(stage)[1:]
        assert calls and all("work_id=work_id" in call.split("))")[0] for call in calls), stage
