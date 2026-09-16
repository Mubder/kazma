"""ROOT conftest — applies to EVERY testpath (tests/ + all package suites).

Production-database shield (2026-08-14 incident): a dev-repo .env pointing
at the PRODUCTION Postgres + an app-building test let the suite write test
providers/settings into the live kazma_settings table (566 → 127 rows,
sk-test keys). This root-level conftest guarantees no test process — in
any suite — can reach a real database, regardless of .env files, shell
profiles, or loader tricks.

Loaded before any suite conftest (pytest walks from the root down).
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.pop("KAZMA_SECRET", None)

# The event-loop stall watchdog reports a loop that stopped responding, which
# only means something for a long-lived service. A test process is entitled to
# block, and a full 6,000-test run duly reported a 440s "stall" and wrote a
# stack dump nobody wanted. Off for the suite; its own tests start it
# explicitly with a threshold.
os.environ.setdefault("KAZMA_LOOP_STALL_WATCHDOG", "0")

# Live-operator-file shield (2026-08-27 incident): test runs boot the real
# app (create_app), whose logging attaches a daily-rotating handler to the
# LIVE <repo>/.kazma/kazma.log. The suite rotated it at midnight, and the
# production server's own first post-midnight rotation then collided on
# Windows (remove/rename of a file another process still held open) —
# killing the server's file handler at boot and leaving it console-only.
# Redirect test logging to a throwaway path BEFORE anything resolves the
# log file location.
import tempfile as _tempfile

os.environ.setdefault(
    "KAZMA_LOG_FILE",
    str(Path(_tempfile.mkdtemp(prefix="kazma-test-log-")) / "kazma.log"),
)

# Deliberate, explicit opt-out for a real-Postgres run (audit 2026-09-16 F-7).
#
# Everything below force-pins sqlite and strips every DSN so a developer's
# `.env` can never point the suite at a live database. That default is right
# and stays. But it is UNCONDITIONAL, which meant a CI job that sets
# KAZMA_DB_BACKEND=postgres + KAZMA_DATABASE_URL would silently run on sqlite
# anyway — a Postgres job that proves nothing, which is the same shape of
# defect this audit was about. One env var, set by nothing except that job:
_ALLOW_REAL_DB = (os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") or "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Ops alerts OFF for the suite (audit 2026-09-16).
#
# ops_alerts.alert() defaults to ENABLED, and any test that trips a
# production error path reaches it indirectly -- then _dispatch starts a
# daemon thread that runs asyncio.run(_deliver(...)), tries to send the
# operator a real Telegram message, fails, and logs a warning FROM that
# thread. Two things wrong with that under test: the suite should never
# attempt outbound delivery to a human, and the thread writes to stderr
# while pytest is tearing its fd capture down, which segfaults CPython.
#
# Measured on Linux: tests/test_reply_sink.py exits 139 (SIGSEGV) with
# alerts on and passes 17/17 with them off -- the crash reproduces from the
# single test that reaches an alert. tests/test_ops_alerts.py deletes this
# variable in its own fixture, so it still covers the enabled default.
#
# setdefault, not a hard set: an operator debugging alert delivery can still
# export KAZMA_OPS_ALERTS=1 for a run.
os.environ.setdefault("KAZMA_OPS_ALERTS", "0")

# Force the sqlite backend and strip every DSN variant BEFORE any kazma
# module can read them.
if not _ALLOW_REAL_DB:
    os.environ["KAZMA_DB_BACKEND"] = "sqlite"
for _dsn_key in (
    "KAZMA_DATABASE_URL",
    "DATABASE_URL",
    "KAZMA_DOCUMENTS_METADATA_BACKEND",
    "E2B_API_KEY",
    "KAZMA_E2B_API_KEY",
    "KAZMA_TEMPORAL_HOST",
    "TEMPORAL_ADDRESS",
):
    # The DSN keys are the point of the opt-in; the others (E2B, Temporal) are
    # third-party credentials and stay stripped either way.
    if _ALLOW_REAL_DB and _dsn_key in ("KAZMA_DATABASE_URL", "DATABASE_URL"):
        continue
    os.environ.pop(_dsn_key, None)

import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *args, **kwargs: None
dotenv.dotenv_values = lambda *args, **kwargs: {}

# Re-introduction guard: if anything loads the DSN back into the environ
# during the session (real load_dotenv reference, manual .env parser,
# C-level putenv through a reloaded module), remove it again immediately.
_real_setitem = os.environ.__class__.__setitem__


def _shielded_setitem(self, key, value):
    _real_setitem(self, key, value)
    if key in ("KAZMA_DATABASE_URL", "DATABASE_URL") and not _ALLOW_REAL_DB:
        import warnings

        warnings.warn(
            f"tests must not use a real database — {key} was re-introduced "
            "into the environment and has been removed again",
            stacklevel=2,
        )
        _real_setitem(self, "KAZMA_DB_BACKEND", "sqlite")
        del self[key]


os.environ.__class__.__setitem__ = _shielded_setitem


# ── Global shutdown-event reset ─────────────────────────────────────────────
# Tests that call create_app() + uvicorn (e2e, pipeline_sandbox, …) fire the
# app's lifespan shutdown → signal_shutdown(), setting the GLOBAL event for
# the rest of the process. Every later SSE/WS/stream test then sees
# is_shutting_down()==True and immediately breaks. Reset before each test.
import pytest as _pytest


@_pytest.fixture(autouse=True)
def _reset_shutdown_event():
    try:
        from kazma_core.shutdown import reset_shutdown

        reset_shutdown()
    except Exception:
        pass
    yield


@_pytest.fixture(autouse=True)
def _isolated_tenant_context():
    """Restore the tenant ContextVar after every test.

    ``safety.hitl._current_tenant_id`` is a ContextVar, and pytest runs tests
    in one context, so a test that calls ``set_current_tenant_id`` without
    resetting leaks its tenant into every test that follows. Three files did:
    test_phase2_remaining.py, test_remaining_gaps.py and
    test_memory_tenant_isolation.py (2, 8 and 8 sets, zero resets).

    The damage is invisible where you would look for it. ``SessionManager.put``
    keys rows as ``f"{tenant_id}:{session_id}"`` with ``tenant_id`` resolved
    from this ContextVar, so a leaked tenant makes a write and a subsequent
    read use different keys: the session is stored, and the very next lookup
    reports "No seasons yet". That produced nine of the twelve known full-suite
    failures -- across test_session_directory, test_session_store,
    test_singleton_isolation and test_tui_session_load -- every one of which
    passes on its own.

    It also defeated the repo's own tests/order_flake_bisect.py, which swept
    test_session_manager.py -> test_session_directory.py and found nothing:
    the polluters sort BEFORE the victim, so the curated pairs had the
    direction backwards.

    Fixing the three files would work until the fourth. This makes the leak
    structurally impossible instead.
    """
    # BOTH modules define a ContextVar named _current_tenant_id with an
    # identically-named getter, and they are NOT the same variable:
    # kazma_core.tenant_context (default None) is what SessionManager reads;
    # kazma_core.safety.hitl (default "default") is what the memory tools read.
    # Guarding only one leaves the other leaking, which is exactly the trap
    # this fixture was first written into.
    tokens = []
    for module in ("kazma_core.tenant_context", "kazma_core.safety.hitl"):
        try:
            import importlib

            mod = importlib.import_module(module)
            var = mod._current_tenant_id
            tokens.append((var, var.set(var.get())))
        except Exception:  # noqa: BLE001 -- never block a run over the guard
            pass

    try:
        yield
    finally:
        for var, token in reversed(tokens):
            try:
                var.reset(token)
            except Exception:  # noqa: BLE001 -- set in another context
                pass


# ── Live kazma.yaml guard ───────────────────────────────────────────────────
# persist_mcp_yaml historically re-dumped the WHOLE kazma.yaml (stripping
# every comment); any test reaching it with the default path round-tripped
# the operator's live config. The splice fix removes the root cause; this
# snapshot/restore is the belt-and-braces net for any future writer.
@_pytest.fixture(scope="session", autouse=True)
def _restore_live_kazma_yaml():
    yaml_path = Path(__file__).resolve().parent / "kazma.yaml"
    try:
        snapshot = yaml_path.read_bytes()
    except OSError:
        yield
        return
    yield
    try:
        if yaml_path.read_bytes() != snapshot:
            yaml_path.write_bytes(snapshot)
    except OSError:
        pass


# ── Process-global singleton isolation (order-dependence cure) ──────────────
# Symptom (2026-08-27): suites pass standalone but fail/hang inside big
# fast_test chunks. Diagnosis (tests/order_flake_bisect.py + manual):
#
# 1. IN-PROCESS bleed — package suites (kazma_*_tests) have NO isolation
#    fixtures of their own, so singletons they create lazily survive into
#    later tests in the same chunk process:
#      - kazma_ui.session_manager.get_session_manager() binds the SHARED
#        real file ``kazma-data/chat_sessions_test.db``; in pytest mode
#        reset_session_manager() DELETES that file per call — while another
#        concurrently-running chunk process holds it open this is the
#        Windows "disk I/O error" family (the historical session_directory
#        ERROR flake).
#      - TurnBroker._emit_locks holds asyncio.Lock instances bound to the
#        test's event loop; reusing the broker across tests on new loops
#        raises "bound to a different loop" or deadlocks (the approval-hang
#        class).
#      - AlertDispatcher._last_dispatch is class-level with a 300s dedup
#        window: one alert fired by an early test silently suppresses every
#        same-subsystem alert for minutes of process lifetime.
#      - active_turns registries keep "running" turns / live sockets after
#        their owner test finished.
# 2. CROSS-PROCESS contention on shared real data-dir files (above).
#
# This fixture gives EVERY testpath what tests/conftest.py gives only
# ``tests/``: capture-and-restore/reset of the known global singletons,
# using PUBLIC reset/set helpers only (reset_config_store,
# reset_model_registry, set_safety(get_safety()), reset_turn_broker,
# reset_active_turns(), AlertDispatcher.reset_state(), SessionManager via
# set_session_manager(reset_session_manager())). No private attribute is
# touched from here. Kill-switch for debugging: KAZMA_TEST_ISOLATION=0.
# Parked singleton instances swapped out by the isolation fixture below.
# They are deliberately NEVER closed or dropped mid-process: an explicit
# close() frees sqlite's native handle under a leftover daemon reader, and
# letting them be GC'd finalizes a connection on whichever thread happens
# to run collection — both hard-crash pytest (access violation). Holding a
# reference pins them until interpreter exit, which is single-threaded.
_PARKED: list = []


def _park(obj) -> None:
    if obj is not None:
        _PARKED.append(obj)


@_pytest.fixture(autouse=True)
def _isolate_process_singletons(tmp_path):
    import os as _os

    if _os.environ.get("KAZMA_TEST_ISOLATION") == "0":
        yield
        return

    # ---- setup: make each test START from pristine singletons -------------
    # Swap-and-park semantics (NEVER close/GC here — see _PARKED above):
    # capture whatever singleton is live, install a per-test tmp-dir-backed
    # instance via the public setter; teardown restores the captured one and
    # parks our creation.
    _prev_sm = None
    _created_sm = None
    try:
        from kazma_ui.session_manager import (
            SessionManager as _SM,
            peek_session_manager as _peek_sm,
            set_session_manager as _set_sm,
        )

        _prev_sm = _peek_sm()
        _created_sm = _SM(db_path=str(tmp_path / "chat_sessions.db"))
        _set_sm(_created_sm)
    except Exception:
        _prev_sm = None
        _created_sm = None

    _prev_cm = None
    _created_cm = None
    try:
        from kazma_core.config_store import (
            ConfigStore as _CS,
            peek_config_store as _peek_cm,
            set_config_store as _set_cm,
        )

        _prev_cm = _peek_cm()
        _created_cm = _CS(
            db_path=str(tmp_path / "settings.db"),
            yaml_path=str(tmp_path / "kazma.yaml"),
        )
        _set_cm(_created_cm)
    except Exception:
        _prev_cm = None
        _created_cm = None

    # SafetyMiddleware: capture whatever was active BEFORE the test so the
    # teardown can restore it even if the test swapped it via set_safety().
    try:
        from kazma_core.swarm.safety import get_safety

        _prev_safety = get_safety()
    except Exception:
        _prev_safety = None

    try:
        from kazma_core.model_registry import reset_model_registry

        reset_model_registry()
    except Exception:
        pass

    try:
        from kazma_ui.delivery import reset_turn_broker

        reset_turn_broker()
    except Exception:
        pass

    # ---- teardown: restore + park (never close/GC — see _PARKED) ---------
    yield

    try:
        from kazma_ui.session_manager import set_session_manager

        set_session_manager(_prev_sm)
        _park(_created_sm)
        _created_sm = None
    except Exception:
        pass

    try:
        from kazma_core.config_store import set_config_store

        set_config_store(_prev_cm)
        _park(_created_cm)
        _created_cm = None
    except Exception:
        pass

    try:
        from kazma_core.model_registry import reset_model_registry

        reset_model_registry()
    except Exception:
        pass

    try:
        from kazma_ui.delivery import reset_turn_broker

        reset_turn_broker()
    except Exception:
        pass

    try:
        from kazma_ui.active_turns import reset_active_turns

        reset_active_turns()
    except Exception:
        pass

    try:
        from kazma_core.observability.alerts import AlertDispatcher

        AlertDispatcher.reset_state()
    except Exception:
        pass

    # SafetyMiddleware: restore whatever was active BEFORE the test — tests
    # may have swapped it via set_safety(); don't let that swap leak.
    try:
        from kazma_core.swarm.safety import set_safety

        set_safety(_prev_safety)
    except Exception:
        pass
