"""Run a Postgres suite with Spaces' second lock armed (S4-01 step 3).

A pytest plugin, registered by the root ``conftest.py``. Inert unless
``KAZMA_TEST_SECOND_LOCK=armed`` (with ``KAZMA_TEST_ALLOW_REAL_DB=1`` and a
throwaway ``KAZMA_DATABASE_URL`` whose login owns its tables, as the CI
Postgres job's does). Then, once for the session: every store's own schema is
made, ``row_security.apply`` puts the rules in place (the role made when the
login may), and the lock is armed -- again at each module, since a test may
take the space role's rights away or disarm -- each checkout of the shared pool goes
through the door of its space, the install's, or none (refused). A test that
reaches the database with nothing bound fails with ``NoSpaceBound``: the
caller, product or test, must say whose statements these are.

Step 3 runs the documents, chats and swarm suites so; step 5 runs every
Postgres suite a second time armed.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

ARMED = (os.environ.get("KAZMA_TEST_SECOND_LOCK") or "").strip().lower() == "armed"


def _url() -> str:
    url = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not url or (os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") or "") != "1":
        pytest.exit("KAZMA_TEST_SECOND_LOCK=armed needs a throwaway Postgres (KAZMA_DATABASE_URL + "
                    "KAZMA_TEST_ALLOW_REAL_DB=1)", returncode=4)
    return url


def _in_place_and_armed(url: str) -> None:
    """The rules in place and the lock armed, as a start would leave them --
    every table's rule, but not the checkpoints' backfill: the session's
    thread records are its own data folder's, not the shared test database's,
    whose rows other tests stamped, so they have nothing true to say about
    them (a whole pass with no thread listed and stamped rows is unresolved:
    S4-01 step 4 review M1)."""
    from kazma_core.db import row_security

    done = row_security.apply(url, tables=list(row_security.rules("")), vector_table="")
    if not done.complete:
        pytest.exit("the second lock's rules could not be put in place: "
                    f"{done.refused or done.left or done.unresolved}", returncode=4)
    row_security._arm(True)


@pytest.fixture(scope="session", autouse=True)
def _second_lock_armed() -> Iterator[None]:
    if not ARMED:
        yield
        return
    from kazma_core.db import row_security

    from tests.test_pg_app_role import _every_store_set_up

    url = _url()
    _every_store_set_up(url)
    _in_place_and_armed(url)
    try:
        yield
    finally:
        row_security._arm(False)


@pytest.fixture(scope="module", autouse=True)
def _second_lock_armed_again() -> None:
    """Each module starts with the rules in place and the lock armed: a test
    that takes the space role's rights away (the armed flows' teardown, the
    remove drill) or disarms must not leave the modules after it with a lock
    that is not the armed one."""
    if ARMED:
        _in_place_and_armed(_url())
