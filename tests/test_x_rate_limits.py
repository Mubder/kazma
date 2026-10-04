"""Rate-limit epochs must never be mistaken for delay seconds."""

from __future__ import annotations

import time

import pytest
from kazma_core.x_api.client import XApiError
from kazma_core.x_api.scheduled_fire import _parse_retry_wait


def test_reset_epoch_is_a_relative_wait(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1_800_000_000)
    error = XApiError("rate limited", status=429, rate_limit_reset=1_800_000_030)
    assert _parse_retry_wait(error) == 30


def test_typed_retry_after_does_not_depend_on_error_text():
    error = XApiError("translated error", status=429, retry_after_seconds=45)
    assert _parse_retry_wait(error) == 45


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -20])
def test_invalid_hints_cannot_break_scheduler(value):
    error = XApiError("rate limited", status=429, retry_after_seconds=value)
    assert 1 <= _parse_retry_wait(error) <= 86400


@pytest.mark.parametrize("headers,wait", [
    ({"retry-after": "30"}, 30),
    ({"retry-after": "Thu, 01 Jan 1970 00:02:00 GMT"}, 20),
    ({"x-rate-limit-reset": "130"}, 30),
    ({"retry-after": "bad", "x-rate-limit-reset": "130"}, 30),
    ({"retry-after": "nan", "x-rate-limit-reset": "130"}, 30),
])
def test_header_hints_keep_their_units(headers, wait, monkeypatch):
    from kazma_core.x_api.client import _rate_limit_hints

    monkeypatch.setattr(time, "time", lambda: 100)
    seconds, reset = _rate_limit_hints(headers)
    error = XApiError("rate limited", status=429, retry_after_seconds=seconds, rate_limit_reset=reset)
    assert _parse_retry_wait(error) == wait
