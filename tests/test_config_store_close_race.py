"""A ConfigStore read racing a ConfigStore close (2026-09-20).

A full pytest run died at 19% with a Windows ``access violation`` and no
Python traceback. Nothing was on the stack except a background thread::

    config_store.py:1694 in get_category
    security/web_sessions.py:160 in purge_expired_sessions
    concurrent/futures/thread.py:59 in run

The memory worker's session-purge scheduler sleeps 120s after boot and then
reads the ConfigStore on a pool thread. Two minutes into a long run, that read
landed while the per-test ``_isolated_config_store`` fixture was calling
``close()`` on the same store. Every read in ConfigStore holds ``self._lock``
around ``_get_conn()`` *and* the statement; ``close()`` did not take the lock
at all, so it freed sqlite's native handle underneath a thread already down in
``sqlite3_step``. That is a use-after-free, not an exception:
``purge_expired_sessions`` already wraps its read in ``try/except`` and logs
"purge skipped — cannot read auth category", and the guard was never reached
because there was nothing to catch.

**Why these tests assert the invariant and never trigger the crash.**
The race is trivially reproducible — park a reader inside ``sqlite3_step``
with a sqlite progress handler, close the store from another thread, and the
interpreter faults with the two frames above. That was run against the
unfixed ``close()`` to confirm this diagnosis. It is useless as a regression
test: a use-after-free kills the process, so there is no assertion to fail and
the rest of the suite dies with it. What these tests pin instead is the pair
of properties whose conjunction makes the crash impossible:

  1. a read holds ``store._lock`` for the whole time its statement is live
     (:func:`test_a_read_holds_the_store_lock_for_the_whole_statement`, and
     the same through the frame that actually crashed,
     :func:`test_purge_expired_sessions_holds_the_store_lock_for_its_read`), and
  2. ``close()`` cannot proceed while another thread holds that lock
     (:func:`test_close_waits_for_whoever_holds_the_store_lock`).

Neither depends on timing to *provoke* anything: (1) parks the reader inside
the C call and inspects the lock from another thread, and (2) holds the lock
outright. Remove the lock from ``close()`` and (2) fails in under a second
with a message, rather than taking the run down.

The second, independent fix is that none of this can arise in the suite any
more: ``worker_bootstrap.background_schedulers_enabled()`` is False under
pytest, so the eight wall-clock cadences do not start at all.
"""

from __future__ import annotations

import functools
import threading

import pytest

from kazma_core.config_store import ConfigStore, get_config_store

#: How long a thread is given to prove it is NOT blocked. Only an unguarded
#: ``close()`` can finish inside it — it is microseconds of work — so this is
#: not a race, it is every chance the broken code needs to fail.
_UNBLOCKED_GRACE_SECONDS = 2.0

#: Upper bound on every join/wait, so a regression is a failure and never a
#: suite that hangs.
_JOIN_TIMEOUT_SECONDS = 30.0


class _ParkedStatement:
    """Hold a reader inside ``sqlite3_step`` until the test releases it.

    Installs a sqlite progress handler on *conn* that, on its first firing,
    signals :attr:`parked` and then blocks. The handler runs on the reader's
    thread while it is inside the C call, with the GIL released — the precise
    state the old ``close()`` freed the handle in.
    """

    def __init__(self, conn) -> None:
        self._conn = conn
        self.parked = threading.Event()
        self._release = threading.Event()
        self._fired = False

    def _handler(self) -> int:
        if not self._fired:
            self._fired = True
            self.parked.set()
            # Bounded: a bug elsewhere must not wedge the run.
            self._release.wait(timeout=_JOIN_TIMEOUT_SECONDS)
        return 0  # non-zero would abort the query

    def __enter__(self) -> _ParkedStatement:
        # n=1 → fire on every VDBE instruction, so any real query parks.
        self._conn.set_progress_handler(self._handler, 1)
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()
        try:
            self._conn.set_progress_handler(None, 0)
        except Exception:
            # Housekeeping, not an assertion — the connection may be closed.
            pass

    def release(self) -> None:
        self._release.set()


def _seed_expired_sessions(store: ConfigStore, count: int) -> None:
    """Write *count* already-expired ``web_session.*`` rows into ``auth``.

    Same key, category and payload shape as
    :func:`web_sessions.create_session`, so the read under test is the read
    that crashed.
    """
    for i in range(count):
        store.set(
            f"web_session.{i:064x}",
            {
                "created_at": 0.0,
                "expires_at": 1.0,  # in the past — the purge must remove it
                "actor": "web",
                "username": None,
                "role": "viewer",
                "user_id": None,
                "tenant_id": "default",
            },
            category="auth",
        )


def _lock_is_held_by_another_thread(store: ConfigStore) -> bool:
    """True if ``store._lock`` is held, asked from a thread that does not hold it.

    ``_lock`` is an RLock, so this must not be asked on the reader's own
    thread — reentrant acquisition would succeed and prove nothing.
    """
    answer: list[bool] = []

    def _probe() -> None:
        acquired = store._lock.acquire(blocking=False)
        if acquired:
            store._lock.release()
        answer.append(not acquired)

    prober = threading.Thread(target=_probe, name="lock-probe", daemon=True)
    prober.start()
    prober.join(timeout=_JOIN_TIMEOUT_SECONDS)
    assert answer, "the lock probe thread never finished"
    return answer[0]


def _assert_parked_read_holds_the_lock(store: ConfigStore, reader, describe: str) -> None:
    """Run *reader* on a thread, park its statement, and inspect the lock.

    *reader* is a zero-arg callable that performs a ConfigStore read. Its
    return value and any exception are handed back to the caller.
    """
    outcome: dict[str, object] = {}
    held_while_parked: list[bool] = []

    def _run() -> None:
        try:
            outcome["value"] = reader()
        except BaseException as exc:  # noqa: BLE001 — reported, not swallowed
            outcome["error"] = exc

    thread = threading.Thread(target=_run, name="cfg-reader", daemon=True)
    with _ParkedStatement(store._get_conn()) as park:
        thread.start()
        assert park.parked.wait(timeout=_JOIN_TIMEOUT_SECONDS), (
            f"{describe} never reached sqlite — the progress handler did not "
            "fire, so this test is not inspecting the window it claims to"
        )
        held_while_parked.append(_lock_is_held_by_another_thread(store))
        park.release()
        thread.join(timeout=_JOIN_TIMEOUT_SECONDS)

    assert not thread.is_alive(), f"{describe} never returned"
    assert "error" not in outcome, outcome.get("error")
    assert held_while_parked == [True], (
        f"{describe} had a live sqlite statement while store._lock was FREE. "
        "Any thread could then call close() and free the native handle "
        "underneath it, which faults the interpreter instead of raising"
    )
    return outcome["value"]


def test_a_read_holds_the_store_lock_for_the_whole_statement(tmp_path):
    """Half one of the invariant: the lock covers the live statement."""
    store = ConfigStore(
        db_path=str(tmp_path / "race_settings.db"),
        yaml_path=str(tmp_path / "race.yaml"),
    )
    _seed_expired_sessions(store, 200)

    rows = _assert_parked_read_holds_the_lock(
        store, lambda: store.get_category("auth"), "get_category"
    )
    assert len(rows) == 200, "the parked read lost rows"
    store.close()


def test_purge_expired_sessions_holds_the_store_lock_for_its_read():
    """The same, through the frame that actually crashed."""
    from kazma_core.security.web_sessions import purge_expired_sessions

    store = get_config_store()  # the per-test isolated store
    _seed_expired_sessions(store, 50)

    removed = _assert_parked_read_holds_the_lock(
        store, purge_expired_sessions, "purge_expired_sessions"
    )
    assert removed == 50, "the purge did not remove every expired session"


def test_close_waits_for_whoever_holds_the_store_lock(tmp_path):
    """Half two: close cannot free the handle while the lock is held.

    This is the assertion that fails if the lock is ever taken back out of
    ``ConfigStore.close()``. It fails in ~2s with a message instead of
    faulting the process, because nothing here has a live statement — only a
    held lock.
    """
    store = ConfigStore(
        db_path=str(tmp_path / "close_settings.db"),
        yaml_path=str(tmp_path / "close.yaml"),
    )
    _seed_expired_sessions(store, 5)
    store._get_conn()  # make sure there IS a handle for close() to free

    lock_taken = threading.Event()
    release_lock = threading.Event()
    closed = threading.Event()

    def _hold_lock() -> None:
        with store._lock:
            lock_taken.set()
            release_lock.wait(timeout=_JOIN_TIMEOUT_SECONDS)

    def _close() -> None:
        store.close()
        closed.set()

    holder = threading.Thread(target=_hold_lock, name="lock-holder", daemon=True)
    closer = threading.Thread(target=_close, name="cfg-closer", daemon=True)
    try:
        holder.start()
        assert lock_taken.wait(timeout=_JOIN_TIMEOUT_SECONDS)
        closer.start()
        closer.join(timeout=_UNBLOCKED_GRACE_SECONDS)
        assert closer.is_alive(), (
            "ConfigStore.close() returned while another thread held "
            "store._lock. Every read holds that lock across its live sqlite "
            "statement, so a close that ignores it frees the native handle "
            "under a reader — a Windows access violation, not an exception, "
            "and nothing can catch it"
        )
    finally:
        release_lock.set()
        holder.join(timeout=_JOIN_TIMEOUT_SECONDS)
        closer.join(timeout=_JOIN_TIMEOUT_SECONDS)

    assert closed.is_set(), "close() never completed after the lock was freed"
    assert store._conn is None, "close() left the connection attached"


def test_a_read_after_close_reopens_instead_of_failing(tmp_path):
    """Serialising the close must not turn a later read into an error.

    ``_get_conn()`` reopens lazily, so the scheduler's next sweep after a
    close is an ordinary read. This is why holding the lock is a complete fix
    and not merely a narrower window.
    """
    store = ConfigStore(
        db_path=str(tmp_path / "reopen_settings.db"),
        yaml_path=str(tmp_path / "reopen.yaml"),
    )
    _seed_expired_sessions(store, 3)
    store.close()
    assert store._conn is None

    assert len(store.get_category("auth")) == 3
    assert store._conn is not None
    store.close()


# ── The second fix: the cadences do not run under pytest at all ────────────


@pytest.fixture
def _no_scheduler_override(monkeypatch):
    monkeypatch.delenv("KAZMA_TEST_BACKGROUND_SCHEDULERS", raising=False)
    return monkeypatch


def test_background_schedulers_are_off_under_pytest(_no_scheduler_override):
    from kazma_core.memory import worker_bootstrap as wb

    assert wb.background_schedulers_enabled() is False
    _no_scheduler_override.setenv("KAZMA_TEST_BACKGROUND_SCHEDULERS", "1")
    assert wb.background_schedulers_enabled() is True, (
        "a test that genuinely wants to observe a cadence has no way to ask"
    )


def test_start_memory_worker_registers_handlers_but_starts_no_cadence(
    _no_scheduler_override,
):
    """Boot still wires the queue; only the wall-clock loops are held back."""
    from kazma_core.memory import task_queue
    from kazma_core.memory import worker_bootstrap as wb

    calls: list[str] = []
    monkeypatch = _no_scheduler_override
    monkeypatch.setattr(wb, "register_v2_handlers", lambda: calls.append("handlers"))
    monkeypatch.setattr(task_queue, "start_worker", lambda: calls.append("queue"))

    cadences = [
        "_start_macro_sleep_scheduler",
        "_start_backup_export_scheduler",
        "_start_reconsolidation_scheduler",
        "_start_commitment_gc_scheduler",
        "_start_session_purge_scheduler",
        "_start_daily_digest_scheduler",
        "_start_firing_ledger_scheduler",
        "_start_restore_drill_scheduler",
    ]
    for name in cadences:
        monkeypatch.setattr(wb, name, functools.partial(calls.append, name))

    wb.start_memory_worker()

    assert calls == ["handlers", "queue"], (
        "start_memory_worker started a background cadence under pytest: "
        f"{[c for c in calls if c in cadences]}"
    )
