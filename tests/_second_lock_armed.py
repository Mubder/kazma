"""Run a Postgres suite with Spaces' second lock armed (S4-01 step 3).

A pytest plugin, registered by the root ``conftest.py``. Inert unless
``KAZMA_TEST_SECOND_LOCK=armed`` (with ``KAZMA_TEST_ALLOW_REAL_DB=1`` and a
throwaway ``KAZMA_DATABASE_URL`` whose login owns its tables, as the CI
Postgres job's does). Then, once for the session: every store's own schema is
made, ``row_security.apply`` puts the rules in place (the role made when the
login may), and the lock is armed -- each checkout of the shared pool goes
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


@pytest.fixture(scope="session", autouse=True)
def _second_lock_armed() -> Iterator[None]:
    if not ARMED:
        yield
        return
    url = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not url or (os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") or "") != "1":
        pytest.exit("KAZMA_TEST_SECOND_LOCK=armed needs a throwaway Postgres (KAZMA_DATABASE_URL + "
                    "KAZMA_TEST_ALLOW_REAL_DB=1)", returncode=4)
    from kazma_core.db import row_security
    from kazma_core.spaces import threads

    from tests.test_pg_app_role import _every_store_set_up

    _every_store_set_up(url)
    # The thread record store, as the server's start makes it before the
    # rules go in place: a missing one keeps apply from being complete
    # (S4-01 step 4 review M1).
    threads.ensure_store()
    done = row_security.apply(url, vector_table="")
    if not done.complete:
        pytest.exit("the second lock's rules could not be put in place: "
                    f"{done.refused or done.left or done.unresolved}", returncode=4)
    row_security._arm(True)
    try:
        yield
    finally:
        row_security._arm(False)
