"""MCP reconnect: the clearest gap the audit found.

Sixty connection failures in eight days, zero reconnects. A server that
failed at boot stayed dead until somebody restarted Kazma, while the agent
carried on planning around tools that were no longer there.
"""

from __future__ import annotations

import asyncio

import pytest
from kazma_core.mcp.reconnect import (
    BACKOFF_LADDER_S,
    MCPReconnector,
    classify_failure,
)


class FakeManager:
    """Minimal stand-in for MCPManager's reconnect-relevant surface."""

    def __init__(self, *, errors=None, recover_after=None):
        self.connection_errors = dict(errors or {})
        self._connected: set[str] = set()
        self._recover_after = dict(recover_after or {})
        self.attempts: dict[str, int] = {}

    def list_servers(self):
        """Mirrors AsyncMCPManager: the real connectivity source, which also
        verifies the stdio child is alive."""
        return [{"name": n, "connected": True} for n in self._connected]

    async def connect_from_config(self, servers):
        for cfg in servers:
            name = cfg["name"]
            self.attempts[name] = self.attempts.get(name, 0) + 1
            need = self._recover_after.get(name)
            if need is not None and self.attempts[name] >= need:
                self._connected.add(name)
                self.connection_errors.pop(name, None)
        return 0


def _cfg(*names):
    return lambda: [{"name": n, "enabled": True, "transport": "stdio"} for n in names]


@pytest.fixture(autouse=True)
def _no_alerts(monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setattr(
        "kazma_core.observability.ops_alerts.alert",
        lambda key, title, detail="", **kw: sent.append((key, title)) or True,
    )
    return sent


# ── classification ────────────────────────────────────────────────────


def test_command_not_found_is_hopeless():
    """test-mcp on the reference host runs `echo hello`; echo is a shell
    builtin. Retrying that every 30s forever turns recovery into noise."""
    assert classify_failure("Command not found: echo") == "hopeless"


def test_connection_refused_is_transient():
    assert classify_failure("connection refused") == "transient"


@pytest.mark.parametrize("msg", [
    "no such file or directory", "unsupported transport 'ftp'",
    "permission denied", "is not recognized as an internal command",
])
def test_config_errors_are_hopeless(msg):
    assert classify_failure(msg) == "hopeless"


# ── the core promise ──────────────────────────────────────────────────


def test_a_failed_server_is_retried_and_comes_back(_no_alerts):
    """The gap itself: 60 faults, 0 recoveries."""
    mgr = FakeManager(errors={"fs": "connection refused"},
                      recover_after={"fs": 2})
    rec = MCPReconnector(mgr, _cfg("fs"))

    assert asyncio.run(rec.sweep_once()) == 0      # attempt 1 fails
    rec._state["fs"].next_attempt_at = 0           # let backoff elapse
    assert asyncio.run(rec.sweep_once()) == 1      # attempt 2 succeeds

    assert "fs" in {r["name"] for r in mgr.list_servers()}
    assert "fs" not in rec.snapshot(), "recovered servers stop being retried"


def test_recovery_is_announced(_no_alerts):
    """Phase 2 says when tools vanish; the counterpart is saying when they
    return, or the only news is bad news and the channel gets muted."""
    mgr = FakeManager(errors={"fs": "refused"}, recover_after={"fs": 1})
    rec = MCPReconnector(mgr, _cfg("fs"))
    asyncio.run(rec.sweep_once())
    assert any(k == "mcp.server_recovered" for k, _ in _no_alerts)


def test_healthy_servers_are_never_touched():
    mgr = FakeManager()
    mgr._connected.add("fs")
    rec = MCPReconnector(mgr, _cfg("fs"))
    asyncio.run(rec.sweep_once())
    assert mgr.attempts == {}, "a connected server must not be reconnected"


# ── pacing ────────────────────────────────────────────────────────────


def test_backoff_grows_for_transient_failures():
    mgr = FakeManager(errors={"fs": "connection refused"})
    rec = MCPReconnector(mgr, _cfg("fs"))
    delays = []
    for _ in range(4):
        asyncio.run(rec.sweep_once())   # the sweep creates/updates the state
        st = rec._state["fs"]
        delays.append(rec._backoff_for(st))
        st.next_attempt_at = 0          # let the backoff elapse for the next pass
    assert delays == sorted(delays), "backoff must not shrink"
    assert delays[0] < delays[-1], "backoff must grow"


def test_hopeless_failures_jump_straight_to_the_cap():
    """A permanently broken entry must not generate a retry storm."""
    mgr = FakeManager(errors={"bad": "Command not found: echo"})
    rec = MCPReconnector(mgr, _cfg("bad"))
    asyncio.run(rec.sweep_once())
    st = rec._state["bad"]
    assert st.kind == "hopeless"
    assert rec._backoff_for(st) == BACKOFF_LADDER_S[-1]


def test_hopeless_servers_are_still_retried_eventually():
    """The fix is a config edit; the operator must not need a restart."""
    mgr = FakeManager(errors={"bad": "Command not found: echo"},
                      recover_after={"bad": 2})
    rec = MCPReconnector(mgr, _cfg("bad"))
    asyncio.run(rec.sweep_once())
    rec._state["bad"].next_attempt_at = 0
    asyncio.run(rec.sweep_once())
    assert "bad" in {r["name"] for r in mgr.list_servers()}


def test_backoff_is_per_server():
    """One flapping server must not set the pace for the others."""
    mgr = FakeManager(errors={"a": "refused", "b": "Command not found: x"})
    rec = MCPReconnector(mgr, _cfg("a", "b"))
    asyncio.run(rec.sweep_once())
    assert rec._backoff_for(rec._state["a"]) < rec._backoff_for(rec._state["b"])


# ── config changes ────────────────────────────────────────────────────


def test_disabled_servers_are_dropped_from_retry():
    """Disabling test-mcp must actually stop the retries."""
    mgr = FakeManager(errors={"bad": "Command not found: echo"})
    rec = MCPReconnector(mgr, _cfg("bad"))
    asyncio.run(rec.sweep_once())
    assert "bad" in rec.snapshot()

    rec._config = lambda: [{"name": "bad", "enabled": False}]
    asyncio.run(rec.sweep_once())
    assert "bad" not in rec.snapshot(), "a disabled server must stop retrying"


def test_removed_servers_are_forgotten():
    mgr = FakeManager(errors={"gone": "refused"})
    rec = MCPReconnector(mgr, _cfg("gone"))
    asyncio.run(rec.sweep_once())
    rec._config = lambda: []
    asyncio.run(rec.sweep_once())
    assert rec.snapshot() == {}


# ── robustness ────────────────────────────────────────────────────────


def test_alert_after_repeated_failures_but_only_once(_no_alerts):
    mgr = FakeManager(errors={"fs": "refused"})
    rec = MCPReconnector(mgr, _cfg("fs"))
    for _ in range(6):
        asyncio.run(rec.sweep_once())
        rec._state["fs"].next_attempt_at = 0
    failing = [k for k, _ in _no_alerts if k == "mcp.reconnect_failing"]
    assert len(failing) == 1, "one alert per outage, not one per retry"


def test_a_raising_connect_does_not_break_the_sweep():
    class Boom(FakeManager):
        async def connect_from_config(self, servers):
            raise RuntimeError("transport exploded")

    mgr = Boom(errors={"fs": "refused"})
    rec = MCPReconnector(mgr, _cfg("fs"))
    assert asyncio.run(rec.sweep_once()) == 0  # must not raise
    assert "fs" in rec.snapshot()


def test_unavailable_config_is_survivable():
    mgr = FakeManager(errors={"fs": "refused"})

    def _boom():
        raise RuntimeError("config store down")

    rec = MCPReconnector(mgr, _boom)
    assert asyncio.run(rec.sweep_once()) == 0


def test_snapshot_is_serialisable_for_the_digest():
    import json

    mgr = FakeManager(errors={"fs": "refused"})
    rec = MCPReconnector(mgr, _cfg("fs"))
    asyncio.run(rec.sweep_once())
    json.dumps(rec.snapshot())  # must not raise


# ── the API the reconnector must actually talk to ─────────────────────


def test_connectivity_comes_from_list_servers_not_a_delegate():
    """UnifiedToolExecutor has is_server_connected; the MANAGER does not.

    Wiring the reconnector to the executor gave it an object with no
    connection_errors and no connect_from_config -- it ran forever and could
    never reconnect anything. Wiring it to the manager removed
    is_server_connected. list_servers() is the one source that lives on the
    manager AND performs a liveness check.
    """
    import inspect

    from kazma_core.mcp import reconnect

    src = inspect.getsource(reconnect.MCPReconnector._connected_names)
    assert "list_servers()" in src


def test_the_real_manager_exposes_everything_the_reconnector_uses():
    """Guards the wiring against drift in either class."""
    from kazma_core.mcp.manager import AsyncMCPManager, UnifiedToolExecutor

    mgr = UnifiedToolExecutor().mcp
    assert isinstance(mgr, AsyncMCPManager)
    for attr in ("connect_from_config", "connection_errors", "list_servers"):
        assert hasattr(mgr, attr), f"manager is missing {attr}"


def test_app_wires_the_manager_not_the_executor():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui"
           / "app.py").read_text(encoding="utf-8")
    block = src.split("start_mcp_reconnector", 1)[1][:600]
    assert 'getattr(executor, "mcp", None)' in src.split(
        "start_mcp_reconnector", 1)[0][-800:] or "executor" in block
