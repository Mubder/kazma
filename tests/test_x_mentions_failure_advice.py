"""The X mentions poller says why a poll failed, and advises by the kind (2026-10-28).

Live 2026-10-28, a network outage: every poll line read "poll error (N
consecutive)" with the cause only in the traceback below it, and after five
the backoff line said "Check the X tier (Free cannot read mentions) and the
tokens" -- over failures that were timeouts. Now each line names the error,
and the tier/token advice is given only when X refused the account
(401/403).
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from kazma_core.x_api import mentions_fire
from kazma_core.x_api.client import XApiError

_LOGGER = "kazma_core.x_api.mentions_fire"


class _Stop(Exception):
    pass


def _run_loop(monkeypatch, error: Exception, polls: int) -> None:
    """Run the real loop until ``polls`` polls have failed with ``error``."""
    from kazma_core.x_api import health, stance

    monkeypatch.setattr(stance, "get_reply_config",
                        lambda: SimpleNamespace(poll_interval_s=60, can_draft=lambda: True))
    monkeypatch.setattr(health, "record_cycle", lambda *a, **k: None)

    async def failing_poll(**_kw):
        raise error

    monkeypatch.setattr(mentions_fire, "poll_once", failing_poll)
    slept = {"n": 0}

    async def fake_sleep(_s):
        slept["n"] += 1
        if slept["n"] >= polls:
            raise _Stop

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with pytest.raises(_Stop):
        asyncio.run(mentions_fire._loop(60.0))


def _lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == _LOGGER and r.levelno >= logging.ERROR]


def _backoff(caplog) -> str:
    (line,) = [m for m in _lines(caplog) if "backing off" in m]
    return line


def test_a_timeout_names_itself_and_gets_no_tier_advice(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    _run_loop(monkeypatch, XApiError("X API timed out.", transient=True), polls=5)
    polls = [m for m in _lines(caplog) if "poll error" in m]
    assert len(polls) == 5 and all("X API timed out." in m for m in polls)
    advice = _backoff(caplog)
    assert "could not be reached" in advice
    assert "tier" not in advice and "tokens" not in advice


@pytest.mark.parametrize("status", [401, 403])
def test_a_refusal_gets_the_tier_and_token_advice(monkeypatch, caplog, status):
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    _run_loop(monkeypatch, XApiError("Forbidden", status=status), polls=5)
    assert all(f"(HTTP {status})" in m for m in _lines(caplog) if "poll error" in m)
    advice = _backoff(caplog)
    assert f"HTTP {status}" in advice and "tier" in advice and "tokens" in advice


def test_a_rate_limit_and_another_error_are_named(monkeypatch, caplog):
    assert "429" in mentions_fire._failure_advice(XApiError("Too many", status=429))
    other = mentions_fire._failure_advice(ValueError("bad since_id"))
    assert "ValueError: bad since_id" in other and "tier" not in other


def test_negative_control_the_old_advice_blamed_the_tier_for_a_timeout(monkeypatch, caplog):
    """The old except body, run on the same timeout."""
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    log = logging.getLogger(_LOGGER)
    errors = 0
    for _ in range(5):
        try:
            raise XApiError("X API timed out.", transient=True)
        except Exception:
            errors += 1
            log.exception("[x-mentions] poll error (%d consecutive)", errors)
            if errors >= 5:
                log.error("[x-mentions] %d consecutive failures — backing off %.0fs. "
                          "Check the X tier (Free cannot read mentions) and the tokens.",
                          errors, 3600.0)
    polls = [m for m in _lines(caplog) if "poll error" in m]
    assert polls and not any("timed out" in m for m in polls)  # the cause was not in the line
    assert "tier" in _backoff(caplog)  # and the advice pointed at the tier
