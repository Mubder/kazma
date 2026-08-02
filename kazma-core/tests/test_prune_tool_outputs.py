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
