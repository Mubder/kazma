"""Kazma's base system prompt reaches the model on every call, with today's date.

Live 2026-10-23 (Telegram): asked about a certificate, Kazma said "today is
20-10-2026" -- the date of an old abort note in the chat. The supervisor added
the base system prompt only when the call held NO system message, and every
real turn carries system notes (working memory, the language lock, the Soul),
so from the web and every chat app the model never saw Kazma's identity,
product knowledge, dialect directive or the date. The date itself was built
into the prompt at boot. Driven here through the real supervisor graph with a
recording model; the old rule is the negative control.
"""

from __future__ import annotations

from datetime import date

import pytest

from kazma_core.agent.graph_helpers import _BASE_MARKER, _ensure_base_prompt

BASE = "BASE-PROMPT-MARKER: you are Kazma."


def _old_rule(messages: list[dict], base: str) -> list[dict]:
    """The supervisor's rule until 2026-10-23."""
    if not any(m.get("role") == "system" for m in messages):
        messages = [{"role": "system", "content": base}, *messages]
    return messages


def _turn_with_notes() -> list[dict]:
    return [
        {"role": "system", "content": "LANGUAGE LOCK (this turn ONLY): reply in Arabic."},
        {"role": "user", "content": "شنو تاريخ اليوم؟"},
    ]


async def _sent_messages(monkeypatch, messages: list[dict]) -> list[dict]:
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.llm_provider import LLMResponse
    from kazma_core.tracing import KazmaTracer

    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    seen: list[list[dict]] = []

    class RecordingLLM:
        async def chat(self, *, messages, tools=None, model=None, **kwargs):
            seen.append([dict(m) for m in messages])
            return LLMResponse(content="Done.", tool_calls=[], finish_reason="stop",
                               model="stub", usage={"total_tokens": 1}, cost_usd=0.0)

    graph = build_supervisor_graph(
        llm=RecordingLLM(), system_prompt=BASE, tool_definitions=[],
        tool_executor=LocalToolRegistry(include_builtins=False),
        cost_breaker=create_cost_breaker(), authority=create_authority(model="test", window=128000),
        tracer=KazmaTracer(backend="console"),
    )
    await graph.ainvoke({"messages": messages}, {"configurable": {"thread_id": "t"}})
    assert seen, "the model was not called"
    return seen[0]


@pytest.mark.asyncio
async def test_a_turn_with_system_notes_sends_the_base_prompt_and_todays_date(monkeypatch):
    monkeypatch.setenv("KAZMA_TZ", "Asia/Kuwait")
    from kazma_core import cultural_context_enrichment as cce

    monkeypatch.setattr(cce, "_daily_cache", {})
    sent = await _sent_messages(monkeypatch, _turn_with_notes())
    base = [m for m in sent if m.get("role") == "system" and BASE in str(m.get("content"))]
    assert len(base) == 1, "the base prompt must be sent exactly once"
    assert sent[0] is base[0], "the base prompt comes first"
    from datetime import datetime
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("Asia/Kuwait")).date().isoformat()
    assert "TODAY: " in base[0]["content"] and today in base[0]["content"]


def test_the_old_rule_left_the_base_prompt_out_of_a_turn_with_notes():
    """Negative control: the live shape got no base prompt at all."""
    assert not any(BASE in str(m.get("content")) for m in _old_rule(_turn_with_notes(), BASE))


def test_a_checkpoint_copy_is_replaced_never_stacked():
    stored = [
        {"role": "system", "content": f"{_BASE_MARKER}\n{BASE}\n\nTODAY: Monday 2026-10-20 (Asia/Kuwait)."},
        {"role": "system", "content": f"{BASE} (an older build's unmarked copy)"},
        {"role": "user", "content": "hi"},
    ]
    out = _ensure_base_prompt(stored, BASE, daily_note="TODAY: Friday 2026-10-23 (Asia/Kuwait).")
    bases = [m for m in out if BASE in str(m.get("content"))]
    assert len(bases) == 1 and out[0] is bases[0]
    assert "2026-10-23" in bases[0]["content"] and "2026-10-20" not in bases[0]["content"]


def test_the_daily_note_follows_the_day_and_the_operator_zone(monkeypatch):
    from kazma_core import cultural_context_enrichment as cce

    monkeypatch.setattr(cce, "_daily_cache", {})
    monkeypatch.setenv("KAZMA_TZ", "Asia/Kuwait")
    note = cce.daily_prompt_note()
    assert "(Asia/Kuwait)" in note and "current_datetime" in note
    assert cce._cultural_prompt_suffix(date(2026, 2, 25)).count("Kuwait National Day") == 1
