"""Shared multi-replica HITL approval wait state."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from kazma_core.swarm import shared_approvals


def _mock_store() -> MagicMock:
    store: dict = {}
    mock_cs = MagicMock(spec=["get", "set", "delete"])
    mock_cs.get.side_effect = lambda k, d=None: store.get(k, d)
    mock_cs.set.side_effect = lambda k, v, category="general": store.__setitem__(k, v)
    mock_cs.delete.side_effect = lambda k: store.pop(k, None) is not None
    return mock_cs


@pytest.mark.asyncio
async def test_resolve_wakes_waiter() -> None:
    with patch("kazma_core.config_store.get_config_store", return_value=_mock_store()):
        shared_approvals.open_request("task-abc", tool="shell_exec", args="{}", deadline=time.time() + 5)

        async def _approve_soon() -> None:
            await asyncio.sleep(0.05)
            shared_approvals.resolve("task-abc", True, actor="telegram:1")

        asyncio.create_task(_approve_soon())
        row = await shared_approvals._wait_outcome("task-abc")
        assert (row["status"], row["actor"]) == ("approved", "telegram:1")


@pytest.mark.asyncio
async def test_timeout_returns_false() -> None:
    """The request's own deadline ends it as ``timeout``, never a deny."""
    with patch("kazma_core.config_store.get_config_store", return_value=_mock_store()):
        shared_approvals.open_request("task-timeout", tool="shell_exec", args="{}", deadline=time.time() + 0.3)
        row = await shared_approvals._wait_outcome("task-timeout")
        assert row["status"] == "timeout"


@pytest.mark.asyncio
async def test_fanout_true_beats_earlier_false() -> None:
    """H-12, re-pointed by the Swarm audit's stage 1 (2026-10-13): the first
    human decision is the decision -- a Deny is final (owner decision D7) and a
    later Approve changes nothing. What H-12 fixed stays fixed differently: no
    platform's wait or timeout is a vote, only a click or the one deadline."""
    with patch("kazma_core.config_store.get_config_store", return_value=_mock_store()):
        shared_approvals.open_request("task-h12", tool="shell_exec", args="{}", deadline=time.time() + 5)

        async def _deny_then_approve() -> None:
            await asyncio.sleep(0.02)
            shared_approvals.resolve("task-h12", False, actor="discord:1")
            await asyncio.sleep(0.02)
            shared_approvals.resolve("task-h12", True, actor="telegram:1")

        clicks = asyncio.create_task(_deny_then_approve())
        row = await shared_approvals._wait_outcome("task-h12")
        assert (row["status"], row["actor"]) == ("denied", "discord:1")
        await clicks  # the later Approve has been pressed
        assert shared_approvals.get("task-h12")["status"] == "denied"


def test_client_tenant_spoof_blocked_in_multi_user(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.tenant_isolation import client_tenant_spoof_allowed

    monkeypatch.setenv("KAZMA_MULTI_USER", "1")
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    # multi_user_enabled reads env
    assert client_tenant_spoof_allowed() is False
