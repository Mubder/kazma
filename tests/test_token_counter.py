"""Unit tests for TokenCounter."""

from __future__ import annotations

from kazma_core.token_counter import TokenCounter


def test_count_empty():
    tc = TokenCounter(model="gpt-4o-mini", window=1000)
    assert tc.count([]) == 0


def test_count_heuristic_string_content():
    tc = TokenCounter(model="unknown-model-xyz", window=1000)
    # Force heuristic path if tiktoken has no encoder
    n = tc.count([{"role": "user", "content": "abcd"}])  # 4 chars → ~1 token + 4 overhead
    assert n >= 4  # at least message overhead


def test_should_compact_threshold():
    tc = TokenCounter(model="gpt-4o-mini", window=100)
    assert tc.threshold == 80
    # Fill enough messages to exceed threshold under heuristic
    msgs = [{"role": "user", "content": "x" * 400} for _ in range(5)]
    assert tc.should_compact(msgs) is True
    assert tc.should_compact([{"role": "user", "content": "hi"}]) is False


def test_multimodal_content_list():
    tc = TokenCounter(model="no-such-model", window=128000)
    n = tc.count(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello world"},
                    {"type": "image_url", "image_url": {"url": "http://x"}},
                ],
            }
        ]
    )
    assert n > 4
