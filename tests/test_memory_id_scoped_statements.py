"""A memory statement that takes an id from a request or the model names its
tenant itself (Spaces S1-35).

The Memory page's edit, invalidate and Forget read the row's tenant and
check it first; the statement that then reads or changes the row named only
its id. The check alone kept another tenant's row out, so a route that ever
skipped it -- or raced it -- reached any tenant's memory. Each statement now
carries ``(? IS NULL OR tenant_id = ?)`` with ``tenant_view.narrowed_to``
(nothing is filtered on an install that does not enforce Spaces, or for the
owner there; ``tests/test_memory_id_only_statements.py`` holds every other
id-only statement to a declared reason).

Run on the two-tenant store of ``tests/test_memory_routes_tenant_scope.py``
(tenants ``alpha`` and ``beta``, memory tenants enforced): as alpha, with the
check taken away, beta's rows do not change; alpha's own do. Control: the
statement without its tenant (``narrowed_to`` answering every tenant) changes
beta's row.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tests.test_memory_routes_tenant_scope import _beta_rows, _seed


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    """The two-tenant database, where memory code looks for it."""
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    _seed(tmp_path)
    return tmp_path


def _as(tenant: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id

    token = set_current_tenant_id(tenant)
    try:
        return fn(*args, **kwargs)
    finally:
        reset_current_tenant_id(token)


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setenv("KAZMA_MEMORY_ENFORCE_TENANT", "1")
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)


def _without_the_check(monkeypatch) -> None:
    """The routes' own tenant check taken away: only the statement is left."""
    import kazma_ui.memory_api as memory_api

    monkeypatch.setattr(memory_api, "_belief_tenant_ok", lambda conn, bid: True)


def _every_tenant(monkeypatch, *modules: Any) -> None:
    """The statements as before S1-35: no tenant of their own."""
    for module in modules:
        monkeypatch.setattr(module, "narrowed_to", lambda tenant_id: None)


# ── the Memory page's edit ───────────────────────────────────────────────


def test_an_edit_reaches_only_the_callers_fact(seeded, enforced, monkeypatch) -> None:
    from kazma_ui.memory_api import _edit_belief_sync

    _without_the_check(monkeypatch)
    before = _beta_rows(seeded)
    assert _as("alpha", _edit_belief_sync, "beta9_lives", {"object": "written by alpha"}) == {
        "ok": False, "error": "not_found"}
    assert _beta_rows(seeded) == before
    assert _as("alpha", _edit_belief_sync, "alpha_b1", {"object": "Lyon"})["ok"] is True


def test_control_an_edit_whose_statement_names_no_tenant_changes_anothers(seeded, enforced, monkeypatch) -> None:
    import kazma_ui.memory_api as memory_api

    _without_the_check(monkeypatch)
    _every_tenant(monkeypatch, memory_api)
    before = _beta_rows(seeded)
    _as("alpha", memory_api._edit_belief_sync, "beta9_lives", {"object": "written by alpha"})
    assert _beta_rows(seeded)["beliefs"] != before["beliefs"]


# ── invalidate (the page, the tools) ─────────────────────────────────────


def test_an_invalidate_reaches_only_the_callers_fact(seeded, enforced) -> None:
    from kazma_core.memory.hygiene import invalidate_belief

    before = _beta_rows(seeded)
    assert invalidate_belief("beta9_lives", tenant_id="alpha")["ok"] is False
    assert _beta_rows(seeded) == before
    assert invalidate_belief("alpha_b1", tenant_id="alpha")["ok"] is True


def test_control_an_invalidate_whose_statement_names_no_tenant_changes_anothers(seeded, enforced,
                                                                               monkeypatch) -> None:
    import kazma_core.memory.hygiene as hygiene

    _every_tenant(monkeypatch, hygiene)
    before = _beta_rows(seeded)
    assert hygiene.invalidate_belief("beta9_lives", tenant_id="alpha")["ok"] is True
    assert _beta_rows(seeded)["beliefs"] != before["beliefs"]


# ── Forget ───────────────────────────────────────────────────────────────


def test_a_forget_reaches_only_the_callers_memory(seeded, enforced, monkeypatch) -> None:
    import kazma_core.memory.forget as forget

    # The check after the read taken away: only the statement keeps it out.
    monkeypatch.setattr(forget, "sees_every_tenant", lambda tenant_id: True)
    before = _beta_rows(seeded)
    assert forget.forget_episode("beta9_e0", tenant_id="alpha", journal=False)["ok"] is False
    assert _beta_rows(seeded) == before


def test_control_a_forget_whose_statement_names_no_tenant_takes_anothers(seeded, enforced, monkeypatch) -> None:
    import kazma_core.memory.forget as forget

    monkeypatch.setattr(forget, "sees_every_tenant", lambda tenant_id: True)
    _every_tenant(monkeypatch, forget)
    before = _beta_rows(seeded)
    assert forget.forget_episode("beta9_e0", tenant_id="alpha", journal=False)["ok"] is True
    assert _beta_rows(seeded)["episodes"] != before["episodes"]
