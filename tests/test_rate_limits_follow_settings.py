"""The chat apps' flood guard follows its setting (2026-11, FA-127).

``gateway.rate_limits`` (messages per person per minute) was read once when
the server started: a limit changed in Settings did nothing until a restart.
``follow_rate_limit_settings`` applies it at boot and again on every change
the settings store announces (AGENTS 48), keeping each person's count.

Measured on a real settings store: the next message after the change meets
the new limit. Reading once (no listener) is the negative control.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def store(tmp_path):
    from kazma_core.config_store import ConfigStore

    cs = ConfigStore(db_path=str(tmp_path / "settings.db"))
    cs.set("gateway.rate_limits", {"telegram": 30, "slack": 30})
    yield cs
    cs.close()


class _Gateway(SimpleNamespace):
    def set_rate_feedback(self, rfm):
        self._rate_feedback = rfm


def _messages_allowed(rfm, sender: str, n: int) -> int:
    return sum(0 if rfm.is_limited(sender) else 1 for _ in range(n))


def test_a_changed_limit_applies_to_the_next_message(store):
    from kazma_gateway.rate_feedback import follow_rate_limit_settings

    gateway = _Gateway(_rate_feedback=None)
    stop = follow_rate_limit_settings(gateway, store)
    try:
        rfm = gateway._rate_feedback
        assert _messages_allowed(rfm, "telegram:1", 5) == 5
        store.set("gateway.rate_limits", {"telegram": 3, "slack": 30})
        assert gateway._rate_feedback is rfm  # the same guard
        assert _messages_allowed(rfm, "telegram:2", 6) == 3
        assert _messages_allowed(rfm, "slack:9", 6) == 6
    finally:
        stop()


def test_emptying_the_setting_turns_the_guard_off(store):
    from kazma_gateway.rate_feedback import follow_rate_limit_settings

    gateway = _Gateway(_rate_feedback=None)
    stop = follow_rate_limit_settings(gateway, store)
    try:
        store.set("gateway.rate_limits", {})
        assert gateway._rate_feedback is None
    finally:
        stop()


def test_negative_control_read_once_keeps_the_old_limit(store):
    from kazma_gateway.rate_feedback import RateFeedbackManager, _limits_from

    rfm = RateFeedbackManager(limit=_limits_from(store), window_seconds=60, cooldown_seconds=30)
    store.set("gateway.rate_limits", {"telegram": 3})
    assert _messages_allowed(rfm, "telegram:2", 6) == 6
