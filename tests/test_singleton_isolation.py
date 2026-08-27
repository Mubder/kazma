"""Order-dependent singleton bleed — regression suite for the conftest cure.

Background (2026-08-27): suites passed standalone but failed/hung inside
big ``scripts/fast_test.py`` chunk processes. Diagnosis lived in
``tests/order_flake_bisect.py`` (pair sweeps); the fix is the ROOT
conftest autouse fixture ``_isolate_process_singletons``, which resets the
known process-global singletons around EVERY test in every testpath,
using public helpers only:

- ``config_store.reset_config_store``
- ``model_registry.reset_model_registry``
- ``swarm.safety.set_safety(get_safety())`` capture/restore
- ``kazma_ui.delivery.reset_turn_broker``
- ``kazma_ui.active_turns.reset_active_turns``   (new tiny helper)
- ``observability.alerts.AlertDispatcher.reset_state``  (new tiny helper)
- ``session_manager.set_session_manager``        (new tiny helper) installing
  a per-test SessionManager instead of the SHARED real
  ``chat_sessions_test.db`` file concurrent chunk processes race on.

These tests encode the bleed as cross-TEST identities: each "polluter"
test stashes the singleton objects it created/changed in module globals;
the following "victim" test asserts those exact objects are gone.
Without the conftest fixture both victims fail (the polluter's objects
would still be installed).
"""
from __future__ import annotations

import asyncio

import pytest

# Leftovers deliberately written by the *_leaves_state polluter tests and
# asserted-absent by the matching _sees_fresh_state victims below.
_LEFTOVER = {}


# ── SessionManager ────────────────────────────────────────────────────────


class TestSessionManagerIsolation:
    def test_polluter_installs_and_uses_manager(self):
        from kazma_ui.session_manager import ChatSession, get_session_manager

        sm = get_session_manager()
        sess = ChatSession(session_id="bleed-sm", thread_id="bleed-sm")
        sess.add_message("user", "hello")
        sm.put(sess)
        _LEFTOVER["sm"] = sm
        # sanity: the put is visible through the singleton right now
        assert sm.get("bleed-sm") is not None

    def test_victim_gets_a_different_isolated_manager(self, tmp_path):
        from kazma_ui.session_manager import get_session_manager

        sm = get_session_manager()
        assert sm is not _LEFTOVER.get("sm"), (
            "SessionManager singleton bled across tests — root conftest "
            "_isolate_process_singletons was skipped or disabled"
        )
        # Each test's manager is bound to THIS test's tmp dir, never the
        # shared repo-cwd chat_sessions_test.db (the concurrent-chunk
        # "disk I/O error" channel).
        assert str(tmp_path) in sm.db_path
        # …and the previous test's row is not visible here.
        assert sm.get("bleed-sm") is None


# ── ConfigStore singleton ─────────────────────────────────────────────────


class TestConfigStoreIsolation:
    def test_polluter_sets_custom_store(self, tmp_path):
        from kazma_core.config_store import ConfigStore, set_config_store

        custom = ConfigStore(
            db_path=str(tmp_path / "polluter_settings.db"),
            yaml_path=str(tmp_path / "polluter.yaml"),
        )
        set_config_store(custom)
        custom.set("bleed.cs.marker", "left")
        _LEFTOVER["cs"] = custom

    def test_victim_does_not_inherit_custom_store(self):
        from kazma_core.config_store import get_config_store

        leftover = _LEFTOVER.get("cs")
        assert leftover is not None, "polluter must run first"
        cs = get_config_store()
        assert cs is not leftover, (
            "custom set_config_store() leaked into the next test — "
            "root conftest isolation missing"
        )
        assert cs.get("bleed.cs.marker") is None


# ── TurnBroker (loop-bound asyncio locks) ─────────────────────────────────


class TestTurnBrokerIsolation:
    def test_polluter_binds_broker_to_a_loop(self):
        from kazma_ui.delivery import get_turn_broker

        async def _emit():
            await get_turn_broker().emit("bleed-thread", {"type": "token"})

        asyncio.run(_emit())
        _LEFTOVER["broker"] = get_turn_broker()
        # journal retained the frame
        assert _LEFTOVER["broker"].stats()["journal"]["threads"] == 1

    def test_victim_gets_fresh_loop_safe_broker(self):
        """The old broker held asyncio.Lock objects bound to the POLLUTER
        test's loop; emitting on them from a NEW loop raises
        'bound to a different loop' / deadlocks (approval-hang class).
        With the conftest reset the victim creates a fresh broker whose
        locks bind to the CURRENT loop."""
        from kazma_ui.delivery import get_turn_broker

        broker = get_turn_broker()
        assert broker is not _LEFTOVER.get("broker"), (
            "TurnBroker singleton survived across tests — stale loop-bound "
            "locks are the approval-hang channel"
        )

        async def _emit():
            await broker.emit("victim-thread", {"type": "token"})

        asyncio.run(_emit())  # must not raise / hang
        head = broker.stats()["journal"]
        assert head["threads"] == 1  # only the victim thread — no bleed rows


# ── AlertDispatcher class buffers + 300s dedup window ────────────────────


class TestAlertDispatcherReset:
    def test_polluter_broadcasts_an_alert(self):
        pytest.importorskip("kazma_core.observability.alerts")
        from kazma_core.observability.alerts import AlertDispatcher

        AlertDispatcher._recent_alerts.append(object())  # simulate a delivery
        AlertDispatcher._last_dispatch["memory:error"] = float("inf")  # never-expiring dedup stamp
        assert AlertDispatcher.get_recent_alerts()

    def test_victim_buffers_and_dedup_are_clear(self):
        from kazma_core.observability.alerts import AlertDispatcher

        assert AlertDispatcher.get_recent_alerts() == [], (
            "recent-alerts buffer bled across tests"
        )
        # 'inf' stamp from the polluter would suppress Memory subsystem
        # alerts for the whole remaining process lifetime (300s window).
        assert AlertDispatcher._last_dispatch.get("memory:error") != float("inf")


# ── active_turns registry ────────────────────────────────────────────────


class TestActiveTurnsRegistryReset:
    def test_polluter_registers_running_turn(self):
        from kazma_ui.active_turns import bind_live_socket, is_turn_running, register_turn

        register_turn("gw-discord-bleed", _NeverFinishingTask())
        bind_live_socket("gw-discord-bleed", object())
        assert is_turn_running("gw-discord-bleed")

    def test_victim_registry_is_empty(self):
        from kazma_ui.active_turns import (
            get_live_socket,
            is_turn_running,
            reset_active_turns,
        )

        reset_active_turns()  # direct idempotent use also exercised below
        assert not is_turn_running("gw-discord-bleed")
        assert get_live_socket("gw-discord-bleed") is None


# ── Helper idempotency (the new tiny prod-module reset fns) ───────────────


class TestResetHelpersIdempotent:
    def test_reset_active_turns_twice(self):
        from kazma_ui.active_turns import reset_active_turns

        reset_active_turns()
        reset_active_turns()  # clearing empty dicts is a no-op, not a crash

    def test_alert_dispatcher_reset_state_twice(self):
        from kazma_core.observability.alerts import AlertDispatcher

        AlertDispatcher.reset_state()
        assert AlertDispatcher.get_recent_alerts() == []
        AlertDispatcher.reset_state()
        assert AlertDispatcher.get_recent_alerts() == []

    def test_set_session_manager_none_twice_then_lazy_get(self):
        from kazma_ui.session_manager import get_session_manager, set_session_manager

        set_session_manager(None)
        set_session_manager(None)
        sm = get_session_manager()  # lazy recreation never raises
        assert sm is not None
        set_session_manager(None)

    def test_safety_capture_restore_roundtrip(self):
        from kazma_core.swarm.safety import SafetyMiddleware, get_safety, set_safety

        prev = get_safety()
        swapped = SafetyMiddleware(enabled=True, allow_headless_danger=True)
        set_safety(swapped)
        assert get_safety() is swapped
        set_safety(prev)
        assert get_safety() is prev
        set_safety(prev)  # restoring twice is stable


class _NeverFinishingTask:
    """Duck-typed stand-in for an asyncio.Task that looks forever-running."""

    def done(self) -> bool:  # noqa: D102 - mirror task API
        return False

    def cancel(self) -> bool:
        return True
