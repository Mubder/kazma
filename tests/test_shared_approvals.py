"""Shared multi-replica HITL approval wait state."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from kazma_core.swarm import shared_approvals


@pytest.mark.asyncio
async def test_resolve_wakes_waiter() -> None:
    store: dict = {}

    mock_cs = MagicMock()
    mock_cs.get.side_effect = lambda k, d=None: store.get(k, d)
    mock_cs.set.side_effect = lambda k, v, category="general": store.__setitem__(k, v)

    with patch("kazma_core.config_store.get_config_store", return_value=mock_cs):
        shared_approvals.create_pending("task-abc")

        async def _approve_soon() -> None:
            await asyncio.sleep(0.05)
            shared_approvals.resolve("task-abc", True)

        asyncio.create_task(_approve_soon())
        ok = await shared_approvals.wait_for_resolution("task-abc", timeout=2.0)
        assert ok is True


@pytest.mark.asyncio
async def test_timeout_returns_false() -> None:
    store: dict = {}
    mock_cs = MagicMock()
    mock_cs.get.side_effect = lambda k, d=None: store.get(k, d)
    mock_cs.set.side_effect = lambda k, v, category="general": store.__setitem__(k, v)

    with patch("kazma_core.config_store.get_config_store", return_value=mock_cs):
        shared_approvals.create_pending("task-timeout")
        ok = await shared_approvals.wait_for_resolution("task-timeout", timeout=0.3)
        assert ok is False


@pytest.mark.asyncio
async def test_fanout_true_beats_earlier_false() -> None:
    """H-12: a False vote must not settle when expected_voters=2; True still wins.

    This is the swarm-bus shared wait (NOT web claim_gate).
    """
    store: dict = {}
    mock_cs = MagicMock()
    mock_cs.get.side_effect = lambda k, d=None: store.get(k, d)
    mock_cs.set.side_effect = lambda k, v, category="general": store.__setitem__(k, v)

    with patch("kazma_core.config_store.get_config_store", return_value=mock_cs):
        shared_approvals.create_pending("task-h12", expected_voters=2)

        async def _deny_then_approve() -> None:
            await asyncio.sleep(0.02)
            shared_approvals.resolve("task-h12", False)
            await asyncio.sleep(0.02)
            shared_approvals.resolve("task-h12", True)

        asyncio.create_task(_deny_then_approve())
        ok = await shared_approvals.wait_for_resolution("task-h12", timeout=2.0)
        assert ok is True


def test_client_tenant_spoof_blocked_in_multi_user(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.tenant_isolation import client_tenant_spoof_allowed

    monkeypatch.setenv("KAZMA_MULTI_USER", "1")
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    # multi_user_enabled reads env
    assert client_tenant_spoof_allowed() is False
