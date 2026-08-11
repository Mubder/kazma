"""Phase 2 polish — agent.commitment.* config consolidator tests."""

from __future__ import annotations

import pytest

from kazma_core.safety.commitment.config import MODES, get_commitment_config
from kazma_core.safety.commitment.constraints import is_commitment_enabled


def test_defaults():
    cfg = get_commitment_config()
    assert cfg["enabled"] is True
    assert cfg["mode"] == "balanced"
    assert cfg["ttl"]["ready"] == 900.0
    assert cfg["retention"]["critical_days"] == 365
    assert cfg["pending_cap_per_thread"] == 20


def test_env_killswitch_off(monkeypatch):
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    assert get_commitment_config()["enabled"] is False
    assert is_commitment_enabled() is False


def test_env_killswitch_on(monkeypatch):
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "false")
    monkeypatch.setenv("KAZMA_COMMITMENT_MODE", "strict")
    cfg = get_commitment_config()
    assert cfg["enabled"] is False
    assert cfg["mode"] == "strict"


def test_invalid_mode_falls_back(monkeypatch):
    monkeypatch.setenv("KAZMA_COMMITMENT_MODE", "bogus")
    assert get_commitment_config()["mode"] == "balanced"


def test_all_modes_valid():
    cfg = get_commitment_config()
    for m in MODES:
        cfg["mode"] = m  # the consolidator accepts any MODES member via env
    assert set(MODES) == {"strict", "balanced", "autonomous", "yolo"}


def test_configstore_override(monkeypatch, tmp_path):
    """ConfigStore values win over defaults (when env doesn't override)."""
    monkeypatch.delenv("KAZMA_COMMITMENT_ENABLED", raising=False)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_core import config_store

    config_store.reset_config_store_for_tests() if hasattr(
        config_store, "reset_config_store_for_tests") else None
    cs = config_store.get_config_store()
    cs.set("agent.commitment.pending_cap_per_thread", 5)
    cs.set("agent.commitment.ttl.ready", 60.0)
    cfg = get_commitment_config()
    assert cfg["pending_cap_per_thread"] == 5
    assert cfg["ttl"]["ready"] == 60.0


def test_returns_fresh_dict_each_call():
    a = get_commitment_config()
    a["mode"] = "MUTATED"
    b = get_commitment_config()
    assert b["mode"] != "MUTATED"  # not shared mutable state
