"""An empty account is permanent, and must not be retried as a blip.

Live, 2026-09-16 (Telegram):

    [LLMProvider] stream HTTP 429: {"error":{"code":"1113","message":
        "Insufficient balance or no resource package. Please recharge."}}
    Rate limited (429) — retrying after 30.0s with exponential backoff
    ...
    ⚠️ I lost the connection to the model mid-turn (it was retried
    automatically but kept failing). Please send your message again —
    transient network/rate-limit blips usually clear on the next turn.

Three retries, ~90 seconds, and then advice that can never work: the balance
does not refill because you asked again. The operator needed "this provider is
out of credit", which is a different action entirely (top up, or switch
provider) and was nowhere in the message.
"""

from __future__ import annotations

import pytest

from kazma_core.llm_provider import _BALANCE_EXHAUSTED_SIGNALS, _is_balance_exhausted


@pytest.mark.parametrize(
    "body",
    [
        # The exact live body.
        '{"error":{"code":"1113","message":"Insufficient balance or no resource '
        'package. Please recharge."}}',
        # Providers this project ships support for.
        '{"error":{"type":"insufficient_quota","message":"You exceeded your '
        'current quota, please check your plan and billing details."}}',
        '{"error":{"message":"Your credit balance is too low to access the '
        'Anthropic API."}}',
        '{"error":{"message":"Insufficient credits. Add more using https://…"}}',
        '{"code":"Arrearage","message":"Access denied, please make sure your '
        'account is in good standing."}',
    ],
)
def test_exhausted_balance_is_recognised(body):
    assert _is_balance_exhausted(body) is True


@pytest.mark.parametrize(
    "body",
    [
        # A REAL rate limit must stay retryable — that path works and is
        # valuable; this change must not swallow it.
        '{"error":{"message":"Rate limit reached for gpt-4o-mini in '
        'organization org-x on requests per min (RPM): Limit 3, Used 3."}}',
        '{"error":{"code":"1302","message":"Too many concurrent requests."}}',
        '{"error":{"message":"Service temporarily unavailable, retry later."}}',
        "",
    ],
)
def test_ordinary_rate_limits_are_not_misread(body):
    assert _is_balance_exhausted(body) is False


def test_signals_are_lowercase_so_matching_is_case_insensitive():
    """The check lowercases the body; a capitalised signal would never fire."""
    assert all(s == s.lower() for s in _BALANCE_EXHAUSTED_SIGNALS)
    assert _is_balance_exhausted("INSUFFICIENT BALANCE, PLEASE RECHARGE") is True


def test_both_call_paths_classify_it_as_permanent():
    """chat() and chat_stream() must agree: this is not transient.

    The streaming path is the one that fired live (Kazma streams first), and
    it raised with transient=True, which is what produced the "send your
    message again" advice.
    """
    import inspect

    from kazma_core import llm_provider

    src = inspect.getsource(llm_provider)
    # Both raise sites must be guarded and must be permanent.
    assert src.count("_is_balance_exhausted(detail)") >= 2, (
        "both the blocking and streaming paths must detect an empty balance; "
        "the streaming path is the one that fired live"
    )
    assert src.count("has no credit left") >= 2
    # And the message must tell the operator what to actually do.
    assert "top up that provider" in src
