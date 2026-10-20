"""A chat app's "💡" hint follows what the agent did, and its switch.

Live 2026-10-20 (Telegram): "💡 I can read and summarize that -- point me to
the URL or file path." under a swarm's finished summary, and "💡 I can read
that URL for you" under a browser refusal that had already offered read_url.
The hint is a regex on the user's message, sent after the turn, and the
gateway never told it what the agent did; nor did it read
``gateway.suggestions.enabled``.
"""

from __future__ import annotations

from types import SimpleNamespace

from kazma_gateway.agent_handler.graph import _turn_tool_names
from kazma_gateway.gateway import _tool_hints
from kazma_gateway.suggestions import detect_tool_intent

ASK = "Use the swarm to research two facts about Failaka Island and summarize them in three lines."


def _msg(turn_tools=None):
    meta = {} if turn_tools is None else {"turn_tools": turn_tools}
    return SimpleNamespace(text=ASK, context_metadata=meta)


def test_no_hint_after_a_turn_that_used_tools():
    on = SimpleNamespace(enabled=True)
    assert _tool_hints(_msg(["dispatch_swarm", "check_swarm_task"]), on) == []


def test_no_hint_when_the_switch_is_off():
    assert _tool_hints(_msg([]), SimpleNamespace(enabled=False)) == []


def test_a_turn_with_no_tool_still_gets_its_hint():
    assert _tool_hints(_msg([]), SimpleNamespace(enabled=True)) == detect_tool_intent(ASK) != []


def test_negative_control_the_old_rule_hinted_under_the_summary():
    """The message alone, as the gateway judged it."""
    assert detect_tool_intent(ASK), "the old hint fired on this message"


def test_the_turn_records_its_own_tools_only():
    messages = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "tool_calls": [{"function": {"name": "web_search"}}]},
        {"role": "tool", "name": "web_search", "content": "..."},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": ASK},
        {"role": "assistant", "tool_calls": [{"name": "dispatch_swarm"}, {"function": {"name": "check_swarm_task"}}]},
        {"role": "tool", "name": "check_swarm_task", "content": "completed"},
        {"role": "assistant", "content": "three lines"},
    ]
    assert _turn_tool_names(messages) == ["dispatch_swarm", "check_swarm_task"]
    assert _turn_tool_names(messages[:5]) == []
