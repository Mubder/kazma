"""`atomic_update` must not deadlock against its own lock.

The empty-write secret guard (69578c77) taught `_prepare_value_for_storage`
to read what is currently on disk, so it could tell "no secret here" apart
from "a vault pointer I cannot open". That read, `_stored_raw`, takes the
ConfigStore lock.

`atomic_update` already holds that lock. It holds it across the whole
read-mutate-write, because the point of the method is that nothing interleaves
with the mutation. So the write half now asked for a lock its own caller
owned -- and `threading.Lock` is not reentrant.

The failure is worse than a hang in one call. The thread blocks forever
holding the lock, so every later ConfigStore operation in the process blocks
behind it. One swarm approval freezes the whole app.

None of the 8,000 tests failed. One of them stopped, and took the suite with
it: three consecutive full runs sat at the same byte of output, the last for
five hours, because a deadlock does not raise. That is why these tests carry
their own watchdog -- an assertion that never fires is not a passing test.
"""

from __future__ import annotations

import threading

import pytest

_DEADLINE_S = 20.0


@pytest.fixture
def store(tmp_path):
    from kazma_core.config_store import ConfigStore

    return ConfigStore(
        db_path=str(tmp_path / "cfg.db"),
        yaml_path=str(tmp_path / "nonexistent.yaml"),
    )


def _under_watchdog(fn):
    """Run *fn* in a thread; fail the test if it is still running at the deadline.

    A plain call would hang pytest itself, which is precisely the failure mode
    being tested -- the suite would stall rather than report.
    """
    box: dict[str, object] = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
            box["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(_DEADLINE_S)
    if t.is_alive():
        pytest.fail(
            f"atomic_update did not return within {_DEADLINE_S}s -- "
            "the ConfigStore lock is not reentrant"
        )
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box.get("value")


class TestTheLockItself:
    def test_the_lock_is_reentrant(self, store) -> None:
        """The direct statement of the invariant, with no I/O in the way.

        Written with a TIMED acquire rather than nested `with` blocks. The
        obvious spelling deadlocks pytest's own main thread against a
        non-reentrant lock, so the first draft of this test hung the runner
        instead of failing it -- the exact mistake it exists to catch.
        """
        assert store._lock.acquire(timeout=2.0)
        try:
            again = store._lock.acquire(timeout=2.0)
            assert again, "ConfigStore._lock is not reentrant"
            store._lock.release()
        finally:
            store._lock.release()


class TestAtomicUpdate:
    def test_a_dict_value_completes(self, store) -> None:
        """A dict routes through `_encrypt_nested_sensitive`, which reads raw."""
        result = _under_watchdog(
            lambda: store.atomic_update(
                "swarm.approval.task-1",
                lambda cur: {"status": "approved", "by": "local"},
                category="swarm",
            )
        )
        assert result == {"status": "approved", "by": "local"}
        assert store.get("swarm.approval.task-1") == {"status": "approved", "by": "local"}

    def test_a_list_value_completes(self, store) -> None:
        result = _under_watchdog(
            lambda: store.atomic_update("some.list", lambda cur: [{"a": 1}])
        )
        assert result == [{"a": 1}]

    def test_a_sensitive_key_completes(self, store) -> None:
        """A scalar secret takes the vault path and never reads raw.

        It passed even while broken. Kept deliberately: it marks the one
        branch of `_prepare_value_for_storage` that was always safe, so a
        future reader does not mistake this file for a blanket claim.
        """
        result = _under_watchdog(
            lambda: store.atomic_update("connectors.telegram.token", lambda cur: "tok-1")
        )
        assert result == "tok-1"

    def test_a_second_update_is_not_blocked_by_the_first(self, store) -> None:
        """The real damage: a held lock strands every later caller too."""
        _under_watchdog(lambda: store.atomic_update("k", lambda cur: {"n": 1}))
        result = _under_watchdog(
            lambda: store.atomic_update("k", lambda cur: {"n": (cur or {}).get("n", 0) + 1})
        )
        assert result == {"n": 2}

    def test_the_mutation_still_sees_the_previous_value(self, store) -> None:
        """Reentrancy must not have been bought by dropping the lock."""
        store.set("counter", {"n": 5})
        result = _under_watchdog(
            lambda: store.atomic_update("counter", lambda cur: {"n": cur["n"] + 1})
        )
        assert result == {"n": 6}


class TestTheGuardStillGuards:
    def test_an_empty_atomic_write_does_not_erase_a_secret(self, store) -> None:
        """The fix restores the call; it must not have disarmed it."""
        store.set("connectors.telegram.token", "real-token")
        returned = _under_watchdog(
            lambda: store.atomic_update("connectors.telegram.token", lambda cur: "")
        )
        assert store.get("connectors.telegram.token") == "real-token"
        assert returned == "real-token", "a vetoed update must report what still stands"

    def test_a_masked_placeholder_does_not_overwrite(self, store) -> None:
        """The guard's other veto, through the same ignored return value."""
        store.set("connectors.telegram.token", "real-token")
        _under_watchdog(
            lambda: store.atomic_update(
                "connectors.telegram.token", lambda cur: "********"
            )
        )
        assert store.get("connectors.telegram.token") == "real-token"

    def test_an_updater_may_still_store_null(self, store) -> None:
        """The veto must not swallow a deliberate null on a plain key."""
        store.set("plain.key", "was-here")
        _under_watchdog(lambda: store.atomic_update("plain.key", lambda cur: None))
        assert store.get("plain.key") is None

    def test_the_database_is_not_left_locked_after_a_veto(self, store) -> None:
        """A veto returns out of an open BEGIN IMMEDIATE; it must roll back."""
        store.set("connectors.telegram.token", "real-token")
        _under_watchdog(
            lambda: store.atomic_update("connectors.telegram.token", lambda cur: "")
        )
        # Any later write would block forever on a stranded write lock.
        result = _under_watchdog(lambda: store.atomic_update("after", lambda cur: {"ok": 1}))
        assert result == {"ok": 1}
