"""Too many tools must trim the list, not throw it away.

Groq caps the `tools` array at 128. Kazma sends the whole registry — 174 on the
operator's box — so every Groq call returned::

    400 {"error":{"message":"'tools' : maximum number of items is 128"}}

That message contains the word "tool", so it landed in the existing
tool-schema fallback, which retries with **no tools at all**. The model then
answered politely and uselessly, having lost every capability it had, and
nothing in the reply said so. A silent downgrade from agent to chatbot is worse
than an error: eight of these in 24h, and the only trace was a WARNING in a log
the operator cannot see.

The distinction that matters: a COUNT limit is satisfiable by sending fewer
tools; a SCHEMA rejection is not. Only the first should trim.
"""

from __future__ import annotations

import pytest
from kazma_core.llm_provider import _parse_tool_count_limit


# ── count limits: trim ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "detail,expected",
    [
        ('{"error":{"message":"\'tools\' : maximum number of items is 128"}}', 128),
        ("at most 64 tools are supported", 64),
        ("tools: maximum of 200 allowed", 200),
        ("MAXIMUM NUMBER OF ITEMS IS 32", 32),
    ],
)
def test_a_named_count_limit_is_parsed(detail, expected):
    assert _parse_tool_count_limit(detail) == expected


def test_the_real_groq_body_is_parsed():
    """The exact string from the operator's log, not a paraphrase of it."""
    body = (
        "LLM call failed: Client error '400 Bad Request' for url "
        "'https://api.groq.com/openai/v1/chat/completions' | status=400 | "
        'response_body={"error":{"message":"\'tools\' : maximum number of '
        'items is 128","type":"invalid_request_error"}} | '
        "model=groq/compound-mini | tools=174"
    )
    assert _parse_tool_count_limit(body) == 128


# ── everything else: do not trim ────────────────────────────────────────────


@pytest.mark.parametrize(
    "detail",
    [
        "Invalid schema for function 'foo': required is not of type array",
        "tool_choice must be one of auto, none",
        "Function not found for account",
        "context_length_exceeded",
        "",
        None,
    ],
)
def test_a_non_count_rejection_does_not_trim(detail):
    """No number of tools makes a broken schema valid. These must keep falling
    through to the strip-all path, which is correct for them."""
    assert _parse_tool_count_limit(detail) is None


def test_a_zero_or_negative_limit_is_ignored():
    """`tools[:0]` is an empty list — the very outcome being avoided."""
    assert _parse_tool_count_limit("maximum number of items is 0") is None


def test_the_provider_retries_trimmed_before_giving_up():
    """Order matters: the count branch must be tried BEFORE the strip-all
    fallback, or the trim never happens.

    Reads `_chat_inner`, not `chat`: the GenAI span work made `chat` a thin
    wrapper and the retry ladder moved down one level. The invariant is about
    the ladder, so it follows the ladder.
    """
    import inspect

    from kazma_core.llm_provider import LLMProvider

    src = inspect.getsource(LLMProvider._chat_inner)
    trim_at = src.index("_parse_tool_count_limit")
    strip_at = src.index('payload.pop("tools", None)')
    assert trim_at < strip_at, (
        "the strip-all fallback runs first, so a count limit would still "
        "drop every tool"
    )
    assert "list(tools)[:tool_cap]" in src
