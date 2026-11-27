"""A test's calls bound as the request or turn that makes them in Kazma (S4-01 step 5).

With Spaces' second lock armed (``KAZMA_TEST_SECOND_LOCK=armed``,
``tests/_second_lock_armed.py``), a statement to Kazma's Postgres that says no
space and no install door is refused before it is sent: there is no fallback
to the owner's space. A request is never unbound -- the tenant middleware
binds the space it runs in (the owner's when Spaces is off and nothing else
is said) -- and neither is a turn. So a test that calls a store or a flow
directly binds what that request or turn would, and a test's own setup and
checks, which are no space's, say so.

Nothing here arms anything: with the lock not armed, a bound call runs as it
always did.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from kazma_core.spaces.row_work import install_statements
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, Scope, bind_scope

#: What a test's own setup and checks are: no space's, the install's.
TESTS_OWN = "the test's own setup and checks: a test is no space"


def as_owner() -> AbstractContextManager[Scope]:
    """The owner's request: Spaces off, no space named (the tenant middleware's
    answer for every request then)."""
    return bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)


def in_space(space: str, asker: str = OWNER_ACCOUNT_ID) -> AbstractContextManager[Scope]:
    """A request or turn in *space*, as *asker*."""
    return bind_scope(space, asker)


@contextmanager
def tests_own(reason: str = TESTS_OWN) -> Iterator[None]:
    """The test's own statements -- seeding rows of several spaces, reading
    them back to check -- as the install's."""
    with install_statements(reason):
        yield


class _Own:
    def __init__(self, target: Any) -> None:
        self._target = target

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._target, name)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            with tests_own():
                return attr(*args, **kwargs)

        return call


def own(target: Any) -> Any:
    """*target* (a store) whose method calls are the test's own statements:
    ``own(store).persist_task(task)`` seeds, ``own(store).get_task(i)``
    checks. The product's own calls of the same store keep their door."""
    return _Own(target)


def not_armed(monkeypatch: Any) -> None:
    """The second lock not armed for this test: a test of how an install that
    does not enforce Spaces reads today ("Spaces off, as before") -- such an
    install is never armed: arming requires Spaces enforced, and switching
    Spaces off disarms or is refused (S4-01 step 6, a requirement there) --
    or a test of the lock itself that starts from the unarmed state. Every
    disarm in the tests is this call, so one search finds them all (step 5
    review L3; ``tests/test_armed_state_declared.py``)."""
    lock_armed(monkeypatch, False)


def lock_armed(monkeypatch: Any, value: bool) -> None:
    """The second lock armed, or not, for this test: a test of the lock
    itself. Every change of the armed state in the tests is this call or
    :func:`not_armed` (``tests/test_armed_state_declared.py``)."""
    from kazma_core.db import row_security

    monkeypatch.setattr(row_security, "_armed", bool(value))


def bound_like_a_request(app: Any) -> Any:
    """*app* with the real tenant middleware in front, as ``create_app`` puts
    it: each request runs bound to its space, never unbound."""
    from kazma_ui import auth

    app.middleware("http")(auth.create_tenant_middleware())
    return app
