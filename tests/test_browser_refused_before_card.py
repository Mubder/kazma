"""A browser this host cannot start is refused before the card, and the
refusal is an ordinary error, never "effects unknown".

Live 2026-10-18 (Telegram), the turn after the ``shell_exec`` fix: the card
asked to approve ``browser_navigate {"url": "https://xcancel.com/KazmaAI"}``,
the owner approved, the strict browser network mode refused to launch on
Windows (``web_acquire.browser_isolation``), the tool returned that refusal
from its catch-all handler, and the effect journal ended the turn with "The
tool reported a failure after invocation. Its effects are unknown ... this
call must not be repeated."
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
from kazma_core.agent.graph_builder import tool_worker_node
from kazma_core.agent.state import initial_supervisor_state
from kazma_core.agent.tool_refusal import RefusedBeforeEffect
from kazma_core.agent.tool_registry import LocalToolRegistry
from kazma_core.web_acquire import browser_isolation
from kazma_core.web_acquire.result import AcquisitionError, BrowserLaunchRefused, FetchErrorCode

LIVE_URL = "https://xcancel.com/KazmaAI"
REFUSAL = browser_isolation._NO_ISOLATION


# ── the host's answer ───────────────────────────────────────────────────


def test_windows_refuses_the_strict_mode_and_takes_the_compatibility_mode(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(browser_isolation.os, "name", "nt")
    monkeypatch.delenv("KAZMA_BROWSER_NETWORK_MODE", raising=False)
    assert browser_isolation.launch_refusal() == REFUSAL
    monkeypatch.setenv("KAZMA_BROWSER_NETWORK_MODE", "compatibility")
    assert browser_isolation.launch_refusal() is None


def test_linux_needs_unshare(monkeypatch):
    import shutil

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(browser_isolation.os, "name", "posix")
    monkeypatch.delenv("KAZMA_BROWSER_NETWORK_MODE", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert browser_isolation.launch_refusal() == browser_isolation._NO_UNSHARE
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/unshare")
    assert browser_isolation.launch_refusal() is None


def test_an_invalid_mode_is_a_refusal(monkeypatch):
    monkeypatch.setenv("KAZMA_BROWSER_NETWORK_MODE", "sometimes")
    assert "KAZMA_BROWSER_NETWORK_MODE" in (browser_isolation.launch_refusal() or "")


def test_the_launch_itself_raises_the_typed_refusal(monkeypatch):
    """The preflight and the launch decide alike; the launch says so by type."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("KAZMA_BROWSER_NETWORK_MODE", raising=False)
    with pytest.raises(BrowserLaunchRefused) as refused:
        browser_isolation.network_command(["node", "run-driver"])
    assert str(refused.value) == REFUSAL
    assert isinstance(refused.value, AcquisitionError)
    # Playwright rebuilds errors as type(error)(message): the type survives.
    assert type(refused.value)(str(refused.value)).code is FetchErrorCode.UNAVAILABLE


# ── the tool and the registry ───────────────────────────────────────────


@pytest.fixture
def public_url(monkeypatch):
    async def _public(url, validator=None):
        return None

    monkeypatch.setattr("kazma_core.web_acquire.dns_worker.validate_public_url", _public)


def _browser_registry(monkeypatch) -> LocalToolRegistry:
    from kazma_core.agent.tool_registry import get_tool_registry
    from kazma_core.swarm import safety as swarm_safety

    monkeypatch.setattr(swarm_safety, "get_safety", lambda: type("S", (), {"enabled": False})())
    reg = LocalToolRegistry()
    reg._tools["browser_navigate"] = get_tool_registry()._tools["browser_navigate"]
    return reg


@pytest.mark.anyio
async def test_the_tool_refuses_before_acting(monkeypatch, public_url):
    from kazma_skills.native.browser_automation import tools as bt

    monkeypatch.setattr(browser_isolation, "launch_refusal", lambda: REFUSAL)
    monkeypatch.setattr(bt, "_state", {"playwright": None, "browser": None, "page": None})
    with pytest.raises(RefusedBeforeEffect) as refused:
        await bt.browser_navigate(LIVE_URL)
    assert "launch refused" in refused.value.content() and "Nothing was run" in refused.value.content()


@pytest.mark.anyio
async def test_a_launch_refused_inside_the_action_is_still_a_refusal(monkeypatch, public_url):
    """The preflight saw a launchable host, the launch refused anyway (a
    setting changed between them): the typed refusal is not an effect."""
    from kazma_skills.native.browser_automation import tools as bt

    monkeypatch.setattr(browser_isolation, "launch_refusal", lambda: None)
    monkeypatch.setattr(bt, "_state", {"playwright": None, "browser": None, "page": None})

    def _refused(url):
        raise BrowserLaunchRefused(REFUSAL)

    monkeypatch.setattr(bt, "_navigate_sync", _refused)
    out = await _browser_registry(monkeypatch).execute("browser_navigate", {"url": LIVE_URL})
    assert out["is_error"] and "launch refused" in out["content"], out
    assert not out.get("effect_uncertain"), out


@pytest.mark.anyio
async def test_negative_control_a_navigation_that_failed_stays_uncertain(monkeypatch, public_url):
    """A browser that started and then failed may have done something."""
    from kazma_skills.native.browser_automation import tools as bt

    monkeypatch.setattr(browser_isolation, "launch_refusal", lambda: None)
    monkeypatch.setattr(bt, "_state", {"playwright": None, "browser": None, "page": None})

    def _failed(url):
        raise AcquisitionError(FetchErrorCode.LIMIT, "Local browser stderr exceeded its 64KiB budget")

    monkeypatch.setattr(bt, "_navigate_sync", _failed)
    out = await _browser_registry(monkeypatch).execute("browser_navigate", {"url": LIVE_URL})
    assert out["is_error"] and out.get("effect_uncertain") is True, out


@pytest.mark.anyio
async def test_the_registry_refuses_before_the_bus_card(monkeypatch, public_url):
    monkeypatch.setattr(browser_isolation, "launch_refusal", lambda: REFUSAL)
    out = await _browser_registry(monkeypatch).execute("browser_navigate", {"url": LIVE_URL})
    assert out["is_error"] and "Nothing was run" in out["content"], out
    assert not out.get("effect_uncertain"), out


# ── the graph: no card ──────────────────────────────────────────────────


class _Exec:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, name: str, arguments: dict) -> dict:
        self.calls.append(name)
        return {"content": "ok", "is_error": False}


class _Tracer:
    def __getattr__(self, _name):
        return lambda *a, **k: None


async def _turn(monkeypatch) -> tuple[list[Any], _Exec, dict]:
    cards: list[Any] = []

    def _card(payload):
        cards.append(payload)
        return {"approved": True}

    monkeypatch.setattr("langgraph.types.interrupt", _card, raising=False)
    state = initial_supervisor_state()
    state["tool_calls_pending"] = [{"id": "b1", "name": "browser_navigate", "arguments": {"url": LIVE_URL}}]
    executor = _Exec()
    result = await tool_worker_node(state, tool_executor=executor, tracer=_Tracer(),
                                    hitl_config={"enabled": True, "require_approval_for": ["browser_navigate"]})
    return cards, executor, result


@pytest.mark.anyio
async def test_the_tool_worker_asks_no_approval_for_a_browser_this_host_cannot_start(monkeypatch):
    monkeypatch.setattr(browser_isolation, "launch_refusal", lambda: REFUSAL)
    cards, executor, result = await _turn(monkeypatch)
    assert cards == [], "no approval card for a call that cannot run"
    assert executor.calls == [], "nothing dispatched"
    done = result.get("tool_calls_done") or []
    assert [r["name"] for r in done] == ["browser_navigate"]
    assert done[0]["is_error"] and done[0].get("outcome") == "policy"
    assert "no approval was asked" in done[0]["content"]


@pytest.mark.anyio
async def test_negative_control_a_launchable_browser_still_gets_its_card(monkeypatch):
    monkeypatch.setattr(browser_isolation, "launch_refusal", lambda: None)
    cards, executor, _ = await _turn(monkeypatch)
    assert len(cards) == 1 and executor.calls == ["browser_navigate"]
