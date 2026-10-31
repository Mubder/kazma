"""Research model calls answer without hidden reasoning, and the switch
reaches only a client that has it (2026-10-31).

Live: deepseek-flash spent all 8,000 tokens of a deep research report on
hidden reasoning, then 11,015 of the 16,000 retry, and the report was cut
off -- the third Kuwait research run failed at its last step after 3.8
minutes of searching and reading. ``reasoning="off"`` is DeepSeek's switch
(``LLMProvider.can_turn_off_reasoning``); the providers with a ``chat`` of
their own take no such argument, and a failover can reach one of them.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from kazma_core.agent import resilient_chat as rc
from kazma_core.llm_provider import LLMError


class _Answer:
    content = "ok"
    usage = {"prompt_tokens": 1, "completion_tokens": 1}
    model = "m"
    cost_usd = 0.0


class _Switchable:
    """DeepSeek through LLMProvider: takes the switch."""

    def __init__(self):
        self.calls: list[dict] = []

    def can_turn_off_reasoning(self):
        return True

    async def chat(self, messages, tools=None, model=None, max_tokens=None, reasoning=None):
        self.calls.append({"reasoning": reasoning})
        return _Answer()


class _OwnChat:
    """Anthropic-like: a chat of its own, no reasoning argument."""

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    async def chat(self, messages, tools=None, model=None, max_tokens=None):
        self.calls += 1
        if self.fail:
            raise LLMError("upstream 503", transient=True)
        return _Answer()


@pytest.fixture(autouse=True)
def _quiet_ledger(monkeypatch):
    async def record(**kwargs):
        return None

    monkeypatch.setattr(rc, "_record", record)


def _run(client, **kwargs):
    return asyncio.run(rc.resilient_chat(client, messages=[{"role": "user", "content": "x"}],
                                         max_attempts=1, max_tokens=10, reasoning="off", **kwargs))


def test_a_client_with_the_switch_gets_it():
    client = _Switchable()
    _run(client)
    assert client.calls == [{"reasoning": "off"}]


def test_a_client_without_it_is_called_without_it():
    client = _OwnChat()
    _run(client)
    assert client.calls == 1


def test_a_failover_to_a_provider_without_it_works():
    primary, fallback = _OwnChat(fail=True), _OwnChat()
    failover = SimpleNamespace(clients=(("claude-x", fallback),), cooldown_seconds=0)
    _run(primary, frozen_failover=failover, model="deepseek-flash")
    assert fallback.calls == 1


def test_negative_control_passing_it_through_breaks_the_failover(monkeypatch):
    monkeypatch.setattr(rc, "_kwargs_for", lambda client, kwargs: kwargs)
    primary, fallback = _OwnChat(fail=True), _OwnChat()
    failover = SimpleNamespace(clients=(("claude-x", fallback),), cooldown_seconds=0)
    with pytest.raises(Exception):
        _run(primary, frozen_failover=failover, model="deepseek-flash")


def test_the_report_and_the_planner_ask_for_no_reasoning(monkeypatch, tmp_path):
    from kazma_core.tools import research_planner, research_synthesize

    seen: dict[str, str | None] = {}

    async def fake(client, **kwargs):
        seen[kwargs.get("label")] = kwargs.get("reasoning")
        return SimpleNamespace(content='{"search_queries": ["q"]}' if kwargs.get("label") == "research-planner"
                               else "## Executive summary\nText.")

    monkeypatch.setattr(rc, "resilient_chat", fake)
    monkeypatch.setattr("kazma_core.web_acquire.research_llm.resolved_route", lambda: (object(), "m", None))
    asyncio.run(research_planner._llm_json("system", "user"))
    digest = tmp_path / "digest-src-01.md"
    digest.write_text("Source digest text " * 40, encoding="utf-8")
    monkeypatch.setattr(research_synthesize, "_resolve_under_workspace", lambda p: digest)
    report = asyncio.run(research_synthesize.synthesize_from_digests([str(digest)], question="q"))
    assert not report.startswith("Error:"), report
    assert seen == {"research-planner": "off", "research-synthesize": "off"}, seen
