"""Unit tests for prune_tool_outputs function in summarizer.py."""

import pytest
from kazma_core.summarizer import estimate_tokens, prune_tool_outputs


def test_prune_tool_outputs_under_budget_unchanged():
    messages = [
        {"role": "system", "content": "You are Kazma."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    pruned = prune_tool_outputs(messages, max_tokens=1000)
    assert pruned == messages


def test_prune_tool_outputs_truncates_older_tool_bodies():
    large_output = "X" * 1000  # ~250 tokens
    messages = [
        {"role": "system", "content": "You are Kazma."},
        {"role": "user", "content": "Search files"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1", "function": {"name": "file_search", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "tc1", "content": large_output},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "tc2", "function": {"name": "file_search", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "tc2", "content": large_output},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "tc3", "function": {"name": "file_search", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "tc3", "content": large_output},
        # The outputs above belong to an earlier turn; this turn starts here.
        {"role": "user", "content": "Search again"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "tc4", "function": {"name": "file_search", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "tc4", "content": large_output},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "tc5", "function": {"name": "file_search", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "tc5", "content": large_output},
    ]

    # Force pruning with a low max_tokens threshold
    pruned = prune_tool_outputs(messages, max_tokens=300, keep_recent_tool_outputs=2)

    # Total tokens should be reduced
    assert estimate_tokens(pruned) < estimate_tokens(messages)

    # Recent tool outputs (last 2) should remain intact
    assert pruned[-1]["content"] == large_output
    assert pruned[-3]["content"] == large_output

    # Older tool output should be truncated
    pruned_older_tool = [m for m in pruned if m.get("role") == "tool"][0]
    assert "[Tool output truncated" in pruned_older_tool["content"]


def test_prune_tool_outputs_caps_recent_large_outputs_when_over_budget():
    massive_output = "Y" * 10000  # ~2500 tokens
    messages = [
        {"role": "system", "content": "You are Kazma."},
        {"role": "user", "content": "Read files"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1", "function": {"name": "file_read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "tc1", "content": massive_output},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "tc2", "function": {"name": "file_read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "tc2", "content": massive_output},
    ]

    # Force pruning with budget lower than 2 massive outputs
    pruned = prune_tool_outputs(messages, max_tokens=1000, keep_recent_tool_outputs=3)

    # Even though len(tools) <= 3, the massive outputs should be capped to fit budget
    assert estimate_tokens(pruned) < estimate_tokens(messages)
    assert "[Tool output truncated" in pruned[3]["content"]



def _turn(question: str, outputs: list[str], *, prefix: str) -> list[dict]:
    msgs: list[dict] = [{"role": "user", "content": question}]
    for n, out in enumerate(outputs):
        call = f"{prefix}{n}"
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": call, "function": {"name": "x_status", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": call, "content": out})
    return msgs


def _live_shape() -> list[dict]:
    """The 2026-10-18 Telegram turn: a long chat over its budget, then a turn
    whose first result is x_status at 1,571 characters and three more."""
    older = [{"role": "system", "content": "You are Kazma."}]
    for t in range(12):
        older += _turn(f"earlier request {t}", ["O" * 3000] * 2, prefix=f"o{t}_")
    status = "{" + "S" * 1569 + "}"
    current = _turn("start again", [status, "P" * 300, "D" * 200, "W" * 400], prefix="c")
    return older + current, status


def test_the_running_turns_outputs_are_never_cut_while_older_ones_can_be():
    messages, status = _live_shape()
    pruned = prune_tool_outputs(messages, max_tokens=8000, keep_recent_tool_outputs=3)
    assert estimate_tokens(pruned) <= 8000
    outputs = [m["content"] for m in pruned if m.get("role") == "tool"]
    assert outputs[-4] == status, "the turn's x_status reached the model whole"
    assert all("truncated" not in o for o in outputs[-4:])
    assert all("truncated" in o for o in outputs[:-4])


def test_negative_control_the_old_rule_cut_the_running_turn():
    """The rule before 2026-10-18 kept the newest 3 outputs of the whole chat
    and then capped every output past 1,500, the running turn's included."""
    messages, status = _live_shape()
    tools = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    old = [dict(m) for m in messages]
    for i in tools[:-3]:
        old[i]["content"] = old[i]["content"][:250]
    if estimate_tokens(old) > 8000:
        for i in tools:
            old[i]["content"] = old[i]["content"][:1500]
    assert old[tools[-4]]["content"] != status


def test_a_huge_output_of_the_running_turn_is_cut_only_as_far_as_needed():
    big = "R" * 40000
    messages = [{"role": "system", "content": "s"}] + _turn("read it", [big, big, "small"], prefix="r")
    pruned = prune_tool_outputs(messages, max_tokens=6000, keep_recent_tool_outputs=3)
    outputs = [m["content"] for m in pruned if m.get("role") == "tool"]
    assert outputs[0].startswith("R" * 8000) and "narrower request" in outputs[0]
    assert outputs[-1] == "small"
