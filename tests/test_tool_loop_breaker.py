"""Supervisor repetition-loop breaker (live incident 2026-08-27).

A resumed turn looped 26+ iterations calling one ``python_exec`` per
40k-hex-char slice of ``checkpoints.db`` (``substr(hex(checkpoint), 40001,
40000)`` → ``80001`` → ``120001`` …) and never produced a final answer —
the user watched tool calls scroll for minutes because ``max_iterations``
was configured to 100 and ``detect_tool_loop`` existed but was never wired.

These tests pin the digit-normalized signature + detector combination the
supervisor uses, without driving the full graph.
"""

from __future__ import annotations

from kazma_core.agent.long_task import (
    detect_tool_loop,
    normalized_tool_signature,
    tool_call_signature,
)


def _paging_calls(n: int) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": "python_exec",
                        "arguments": (
                            '{"code": "import sqlite3; con=sqlite3.connect('
                            "'kazma-data/checkpoints.db'); print(con.execute("
                            f"'SELECT substr(hex(checkpoint), {40001 + 40000 * i}, "
                            "40000)').fetchone())\"}"
                        ),
                    },
                }
            ],
        }
        for i in range(n)
    ]


def test_exact_signature_misses_paging_loop():
    """Sanity: the OLD exact-args signature never repeats on offset paging."""
    sigs = [
        tool_call_signature(
            m["tool_calls"][0]["function"]["name"],
            m["tool_calls"][0]["function"]["arguments"],
        )
        for m in _paging_calls(6)
    ]
    assert detect_tool_loop(sigs, window=10, max_repeats=4) is None


def test_normalized_signature_catches_paging_loop():
    msgs = _paging_calls(4)
    sigs = [
        normalized_tool_signature(
            m["tool_calls"][0]["function"]["name"],
            m["tool_calls"][0]["function"]["arguments"],
        )
        for m in msgs
    ]
    assert len(set(sigs)) == 1  # every page normalizes identically
    assert detect_tool_loop(sigs, window=10, max_repeats=4) is not None


def test_distinct_work_does_not_trip():
    """Different files / different tweet bodies are different signatures."""
    calls = [
        ("file_read", {"path": "a.py"}),
        ("file_read", {"path": "b.py"}),
        ("file_read", {"path": "c.py"}),
        ("file_read", {"path": "d.py"}),
        ("x_post", {"text": "hello world 1"}),
        ("x_post", {"text": "second tweet body"}),
    ]
    sigs = [normalized_tool_signature(n, a) for n, a in calls]
    assert detect_tool_loop(sigs, window=10, max_repeats=4) is None


def test_normalized_signature_shapes():
    assert normalized_tool_signature("t", {"a": 1}) == normalized_tool_signature(
        "t", {"a": 999}
    )
    assert normalized_tool_signature("t", {"a": 1}) != normalized_tool_signature(
        "t2", {"a": 1}
    )
    # Whitespace collapse (json.dumps escapes newlines; spaces collapse)
    assert normalized_tool_signature("t", {"q": "a  b c"}) == normalized_tool_signature(
        "t", {"q": "a b  c"}
    )
