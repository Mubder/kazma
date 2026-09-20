"""A leaked tenant ContextVar must not follow one test into the next.

Nine of the twelve known full-suite failures came from here, and every one of
them passed when its file was run alone.

``test_phase2_remaining.py`` and ``test_remaining_gaps.py`` call
``kazma_core.tenant_context.set_current_tenant_id("acme" / "tenant-a" / "t1")``
and never reset it. pytest runs tests in one context, so the value survived
into everything that followed.

The damage was invisible at the point of failure, because ``SessionManager``
resolves the tenant differently on each side::

    put:  tenant_id = session.tenant_id or get_current_tenant_id() or "default"
    get:  tenant_id = get_current_tenant_id() or "default"

``ChatSession.tenant_id`` defaulted to the truthy ``"default"``, so ``put``
stored under ``default:<id>`` while ``get`` looked under ``tenant-a:<id>``. The
row was in the cache AND in the database -- a probe confirmed both -- and the
very next lookup still reported "No seasons yet".

Both halves are fixed. The conftest guard stops the leak, and the default is
now ``None`` so an unset tenant falls through to the same context ``get``
reads, which also closes the multi-tenant case where nothing leaked at all:
any deployment setting a tenant context was writing sessions somewhere it
would never look for them.

Two more things made it expensive to find. There used to be two different
modules exporting ``get_current_tenant_id`` over two different ContextVars
(``tenant_context``, default None, which SessionManager uses;
``safety.hitl``, default "default", which the memory tools use), so guarding
the obvious one changed nothing. That is now one variable re-exported under
both names — see ``test_the_tenant_context_var_is_one_object`` — because a
pair of mirror functions is not an invariant. And the repo's own
``tests/order_flake_bisect.py`` swept ``test_session_manager.py ->
test_session_directory.py``, which is backwards: the polluters sort BEFORE the
victims, so the curated pairs could never reproduce it.
"""

from __future__ import annotations

import importlib

import pytest

TENANT_MODULES = ("kazma_core.tenant_context", "kazma_core.safety.hitl")


@pytest.mark.parametrize("module", TENANT_MODULES)
def test_a_test_that_leaks_a_tenant_cannot_affect_the_next(module):
    """This test deliberately leaks; the next one proves it was contained."""
    mod = importlib.import_module(module)
    mod.set_current_tenant_id("leaked-tenant")
    assert mod.get_current_tenant_id() == "leaked-tenant"


@pytest.mark.parametrize("module", TENANT_MODULES)
def test_the_tenant_is_clean_again(module):
    """Runs after the leak above. The autouse guard in conftest restores it."""
    mod = importlib.import_module(module)
    current = mod.get_current_tenant_id()
    assert current != "leaked-tenant", (
        f"{module} leaked a tenant across tests — SessionManager.put and .get "
        "will then disagree about where a session lives, and reads return "
        "nothing while the row sits in both the cache and the database"
    )


def test_put_and_get_agree_under_a_leaked_tenant():
    """The asymmetry that turned a leak into a silent data-loss symptom."""
    from kazma_core.tenant_context import set_current_tenant_id
    from kazma_ui.session_manager import ChatSession, get_session_manager

    sm = get_session_manager()
    set_current_tenant_id("some-other-tenant")

    sess = ChatSession(session_id="rt-1", thread_id="rt-1", title="Round trip")
    sess.add_message("user", "hello")
    sm.put(sess)

    assert sm.get("rt-1") is not None, (
        "put and get resolved different tenants: put prefers "
        "session.tenant_id, get consults only the ContextVar, so a session "
        "written under one tenant is invisible under the other"
    )


def test_the_tenant_context_var_is_one_object():
    """There is exactly one tenant ContextVar. Two cannot drift if there is one.

    This assertion used to run the other way — it asserted the two vars were
    *distinct* and warned that "if these ever become the same object this test
    is obsolete". They are now the same object, deliberately: ``safety.hitl``
    re-exports ``tenant_context``'s var instead of defining its own, and the
    mirror functions that used to hold two vars in step are gone.

    Mirroring was never an invariant. It was two writes that happened to
    agree, and a direct ``.set`` on either module desynced them in silence.
    The failure mode is a stored secret reading back absent, which is
    indistinguishable from never having stored it — three shipped incidents.

    Keep this test. It is the lock that stops someone re-introducing a second
    var "for the different default"; the default difference is handled on
    read, in ``hitl.get_current_tenant_id``.
    """
    a = importlib.import_module("kazma_core.tenant_context")
    b = importlib.import_module("kazma_core.safety.hitl")
    assert a._current_tenant_id is b._current_tenant_id, (
        "safety.hitl defined its own tenant ContextVar again. It must "
        "re-export kazma_core.tenant_context._current_tenant_id — two vars "
        "drift, and a drifted tenant makes a stored secret read as absent."
    )


def test_the_none_floor_is_applied_on_read_not_on_store():
    """``tenant_context`` must keep None; only ``hitl`` floors it to 'default'.

    Storing the floor was the other half of the original bug: the vault's
    scoped resolver needs to tell "no tenant installed" (None) from an
    explicit ``default`` tenant, and a floored store makes every context-less
    caller look like an explicit default.
    """
    tc = importlib.import_module("kazma_core.tenant_context")
    hitl = importlib.import_module("kazma_core.safety.hitl")

    token = tc.set_current_tenant_id(None)
    try:
        assert tc.get_current_tenant_id() is None, (
            "tenant_context floored a None tenant — the vault can no longer "
            "distinguish an absent tenant from an explicit 'default'"
        )
        assert hitl.get_current_tenant_id() == "default", (
            "safety.hitl must still promise a non-None str to the memory tools"
        )
    finally:
        tc.reset_current_tenant_id(token)


def test_conftest_guards_both_modules():
    """Source guard: the fix must not silently regress to one module."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "conftest.py").read_text(
        encoding="utf-8"
    )
    for module in TENANT_MODULES:
        assert module in src, f"conftest no longer restores {module}"
