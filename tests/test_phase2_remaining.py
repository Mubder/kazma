"""Phase-2 remaining gaps: shared breakers, tenant MCP key, shell mutate."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kazma_core.safety.post_hitl import shell_mutate_allowed
from kazma_core.swarm.reliability import CircuitBreaker, CircuitState


def test_shell_mutate_off_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.delenv("KAZMA_SHELL_ALLOW_MUTATE", raising=False)
    assert shell_mutate_allowed() is False


def test_shell_mutate_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.setenv("KAZMA_SHELL_ALLOW_MUTATE", "1")
    assert shell_mutate_allowed() is True


def test_shared_breaker_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "1")
    store: dict = {}
    mock = MagicMock()
    mock.get.side_effect = lambda k, d=None: store.get(k, d)
    mock.set.side_effect = lambda k, v, category="general": store.__setitem__(k, v)

    with patch("kazma_core.config_store.get_config_store", return_value=mock):
        b = CircuitBreaker(failure_threshold=2, cooldown_seconds=30)
        b.record_failure()
        b.record_failure()  # trips open
        assert b.state == CircuitState.OPEN
        b.persist_shared("worker-x")
        loaded = CircuitBreaker.load_shared("worker-x")
        assert loaded is not None
        assert loaded.state == CircuitState.OPEN
        assert loaded.consecutive_failures >= 2


def test_mcp_config_key_tenant_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    from kazma_core.mcp_servers_store import _config_key
    from kazma_core.tenant_context import set_current_tenant_id

    set_current_tenant_id("acme")
    key = _config_key()
    assert "acme" in key
    assert key.endswith("mcp.servers") or "mcp.servers" in key
