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

Two more things made it expensive to find. There are two different modules
exporting ``get_current_tenant_id`` over two different ContextVars
(``tenant_context``, default None, which SessionManager uses;
``safety.hitl``, default "default", which the memory tools use), so guarding
the obvious one changed nothing. And the repo's own
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


def test_the_two_tenant_context_vars_are_distinct():
    """Guarding one and assuming you covered both is the trap here."""
    a = importlib.import_module("kazma_core.tenant_context")
    b = importlib.import_module("kazma_core.safety.hitl")
    assert a._current_tenant_id is not b._current_tenant_id, (
        "if these ever become the same object this test is obsolete; until "
        "then, any tenant guard must cover both"
    )


def test_conftest_guards_both_modules():
    """Source guard: the fix must not silently regress to one module."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "conftest.py").read_text(
        encoding="utf-8"
    )
    for module in TENANT_MODULES:
        assert module in src, f"conftest no longer restores {module}"
