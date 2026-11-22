"""Spaces S1-02: the tenant fallbacks outside memory, area by area.

Each fallback to ``"default"`` -- the owner's space -- either asks the scope
(``require_space_id``: the bound space; the owner's while Spaces is not
enforced; ``NoSpaceBound`` with nothing bound once it is), reads a stored
record's space (``space_of_record``: a record that names none was written
before Spaces and is the owner's, decision N19), or is declared work of the
install or the machine, with why. The count of those still written out is
the ratchet in ``tests/test_no_space_no_data.py``; this file holds what each
area now does, with the old fallback as the control.
"""

from __future__ import annotations

import pytest
from kazma_core.spaces import scope
from kazma_core.spaces.scope import NoSpaceBound, bind_scope, space_of_record


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


def test_a_record_with_no_space_is_the_owners() -> None:
    for empty in (None, "", "  "):
        assert space_of_record(empty) == scope.OWNER_SPACE_ID
    assert space_of_record("family") == "family"
    assert space_of_record(" family ") == "family"


# ── agent, swarm, runtime ───────────────────────────────────────────────


def test_a_turn_s_state_is_made_in_the_bound_space() -> None:
    from kazma_core.agent.state import initial_supervisor_state

    assert initial_supervisor_state(max_iterations=10)["tenant_id"] == "default"  # one operator, as before
    with bind_scope("family", "layla"):
        assert initial_supervisor_state(max_iterations=10)["tenant_id"] == "family"
    assert initial_supervisor_state(max_iterations=10, tenant_id="acme")["tenant_id"] == "acme"


def test_a_turn_s_state_made_with_no_space_is_refused_once_enforced(enforced) -> None:
    from kazma_core.agent.state import initial_supervisor_state

    with pytest.raises(NoSpaceBound):
        initial_supervisor_state(max_iterations=10)
    with bind_scope("family", "layla"):
        assert initial_supervisor_state(max_iterations=10)["tenant_id"] == "family"


def test_control_the_old_default_made_it_the_owners(enforced) -> None:
    """``tenant_id: str = "default"`` as it was: unbound work, enforced, got
    the owner's space."""

    def old(tenant_id: str = "default") -> str:
        return tenant_id

    assert old() == scope.OWNER_SPACE_ID


def test_the_live_client_s_tenant_is_the_state_s_or_the_scope_s(enforced) -> None:
    from kazma_core.runtime.live_llm import _turn_tenant
    from kazma_core.tenant_context import get_current_tenant_id

    with _turn_tenant({"tenant_id": "family"}):
        assert get_current_tenant_id() == "family"
    with pytest.raises(NoSpaceBound), _turn_tenant({}):
        pass


def test_a_swarm_task_of_no_tenant_is_the_owners_before_spaces() -> None:
    from kazma_core.swarm.worker_dispatch import _worker_scope
    from kazma_core.tenant_context import get_current_tenant_id

    with _worker_scope("", None)():
        assert get_current_tenant_id() == scope.OWNER_SPACE_ID
    with _worker_scope("acme", None)():
        assert get_current_tenant_id() == "acme"
