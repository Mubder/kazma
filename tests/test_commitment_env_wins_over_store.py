"""The commitment kill-switch in the environment wins over a stored value
(audit 2026-10-26 AUD-096).

``get_commitment_config`` says "env -> ConfigStore -> defaults", and AGENTS.md
§20E names ``KAZMA_COMMITMENT_ENABLED=0`` as the layer's kill-switch. The
environment was read first and the store after it, so a stored
``agent.commitment.enabled: true`` (or a stored mode or scope switch) undid
what the operator's environment said. The negative control shows the stored
values are really read here: with no environment they are what comes back.
"""

from __future__ import annotations

from typing import Any

import pytest

from kazma_core.safety.commitment.config import get_commitment_config

_ENV = (
    "KAZMA_COMMITMENT_ENABLED",
    "KAZMA_COMMITMENT_MODE",
    "KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE",
    "KAZMA_COMMITMENT_SOUL_REQUIRES_CONFIRM",
)


class _Store:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


@pytest.fixture
def stored(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    store = _Store({
        "agent.commitment.enabled": True,
        "agent.commitment.mode": "yolo",
        "agent.commitment.swarm_scope_enforce": False,
        "agent.commitment.soul_requires_confirm": False,
    })
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: store)
    return store


def test_the_stored_values_are_read(stored) -> None:
    """Negative control: without the environment the store decides."""
    cfg = get_commitment_config()
    assert cfg["enabled"] is True
    assert cfg["mode"] == "yolo"
    assert cfg["swarm_scope_enforce"] is False
    assert cfg["soul_requires_confirm"] is False


def test_the_env_kill_switch_beats_a_stored_enabled(stored, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    assert get_commitment_config()["enabled"] is False

    from kazma_core.safety.commitment.constraints import is_commitment_enabled

    assert is_commitment_enabled() is False


def test_every_env_knob_beats_its_stored_value(stored, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_COMMITMENT_MODE", "strict")
    monkeypatch.setenv("KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE", "1")
    monkeypatch.setenv("KAZMA_COMMITMENT_SOUL_REQUIRES_CONFIRM", "1")
    cfg = get_commitment_config()
    assert cfg["mode"] == "strict"
    assert cfg["swarm_scope_enforce"] is True
    assert cfg["soul_requires_confirm"] is True
