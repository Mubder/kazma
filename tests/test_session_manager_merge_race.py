"""`_merge_missing_from_db` must not swap the shared session cache.

Incident, 2026-09-21. The operator restarted, sent a Telegram task, and got:

    [Ops] A reply was produced but NOT saved to the transcript.
    session=90a9df0b-c4f turn=9ac56c7dd356 chars=0

with this underneath:

    File "session_manager.py", line 653, in put
        self._sessions.move_to_end(key)
    AttributeError: 'dict' object has no attribute 'move_to_end'

`_merge_missing_from_db` used to assign `self._sessions = scratch` (a plain
`dict`), call `_load_all_from_db`, and restore in a `finally`. SessionManager
is a process-wide singleton shared by the web, WS and gateway paths. Every
mutator takes `self._lock` — except that one, and `list_all()` calls it
*outside* the lock and then takes the lock immediately after.

So the lock protected nothing against it: a `put()` from another thread during
the swap window got the plain `dict` and `move_to_end` does not exist there.
The answer stayed in the checkpoint and never reached the transcript.

The loader now writes into a caller-supplied target, so the shared attribute is
never reassigned and there is no window to lose.
"""

from __future__ import annotations

import threading
import time

import pytest

from kazma_ui.session_manager import ChatSession, SessionManager


@pytest.fixture
def manager(tmp_path):
    return SessionManager(db_path=str(tmp_path / "race.db"))


def test_put_survives_a_concurrent_merge(manager) -> None:
    """The regression, reproduced: hammer put() while merging repeatedly.

    Against the old swap this raises AttributeError within a few iterations.
    """
    seed = ChatSession(session_id="seed", thread_id="seed", title="seed")
    seed.add_message("user", "hello")
    manager.put(seed)

    errors: list[BaseException] = []
    stop = threading.Event()

    def _writer() -> None:
        i = 0
        while not stop.is_set():
            try:
                s = ChatSession(
                    session_id=f"w{i}", thread_id=f"w{i}", title=f"w{i}"
                )
                s.add_message("user", "x")
                manager.put(s)
            except BaseException as exc:  # noqa: BLE001 — the point of the test
                errors.append(exc)
                return
            i += 1

    t = threading.Thread(target=_writer, daemon=True)
    t.start()
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not errors:
            manager._merge_missing_from_db(limit=50)
    finally:
        stop.set()
        t.join(timeout=5)

    assert not errors, (
        "put() failed while _merge_missing_from_db was running: "
        f"{errors[0]!r}. The shared session cache is being swapped out from "
        "under concurrent writers."
    )


def test_the_cache_is_never_replaced_by_a_plain_dict(manager) -> None:
    """The invariant behind the incident, stated directly.

    `put()` calls `move_to_end`, which only exists on OrderedDict. If anything
    reassigns `_sessions` to a plain dict — even briefly — a concurrent write
    is lost with an AttributeError rather than an error anyone would connect
    to session persistence.
    """
    from collections import OrderedDict

    assert isinstance(manager._sessions, OrderedDict)
    manager._merge_missing_from_db(limit=10)
    assert isinstance(manager._sessions, OrderedDict), (
        "_merge_missing_from_db left the cache as a plain dict"
    )


def test_merge_still_pulls_rows_it_did_not_have(manager, tmp_path) -> None:
    """The swap removal must not break what the merge is FOR."""
    s = ChatSession(session_id="fromdb", thread_id="fromdb", title="from db")
    s.add_message("user", "persisted")
    manager.put(s)

    # A second manager over the same file starts cold, then merges.
    other = SessionManager(db_path=str(tmp_path / "race.db"))
    other._sessions.clear()
    other._merge_missing_from_db(limit=50)

    keys = [k for k in other._sessions if k.endswith(":fromdb")]
    assert keys, "merge did not pull the row it exists to pull"
