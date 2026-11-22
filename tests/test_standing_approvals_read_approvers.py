"""Spaces S2-05/S2-06, the second review: two loose ends of N4.

* Claiming one member's card superseded another member's identical card
  in the same chat (``hitl_gates._supersede_pending_twins`` matched by
  thread, tool and arguments, not by asker): Omar's question was dropped,
  unanswered. Now another asker's twin is their own question.
* ``standing_approval`` read the grant alone: YOLO, a task grant or "Allow
  tool" a person made BEFORE the admin sent their cards to approvers kept
  skipping their cards. Every grant now records who made it
  (``granted_by``), and for a person with approvers only one an approver
  made holds -- read when it is used (``approvals.approvers_now``).

Reem and Omar share a space (``tests/test_approvals_reach_the_asker.py``'s
world); each fix with today's code as its control.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

import pytest
from kazma_core.spaces import approvals, identity
from kazma_core.spaces.scope import bind_scope

from tests.test_approvals_reach_the_asker import _card, team  # noqa: F401 -- team is the shared fixture
from tests.test_approvals_review_fixes import _register, _set_approvers

pytestmark = pytest.mark.real_features


# ── twins of two people ─────────────────────────────────────────────────


def _claim_reems(team: Any, tmp_path: Path) -> tuple[str, str]:
    """Omar's and Reem's identical cards on one chat; Reem's is claimed.
    Returns the state of each row."""
    from kazma_core.safety import hitl_gates

    hitl_gates.set_db_path_for_tests(str(tmp_path / "hitl_gates.db"))
    try:
        thread = f"t{secrets.token_hex(6)}"
        omars = {**_card(team.space, team.accounts["omar"]), "thread_id": thread, "interrupt_id": "i-omar"}
        reems = {**_card(team.space, team.accounts["reem"]), "thread_id": thread, "interrupt_id": "i-reem"}
        _register(omars)
        _register(reems)
        assert hitl_gates.gate_for("i-omar").state == "pending"
        hitl_gates.claim_gate("i-reem", "approve", "reem")
        return hitl_gates.gate_for("i-omar").state, hitl_gates.gate_for("i-reem").state
    finally:
        hitl_gates.set_db_path_for_tests(None)


def test_claiming_one_members_card_leaves_anothers_identical_card(team, tmp_path) -> None:
    omars, reems = _claim_reems(team, tmp_path)
    assert reems == "claimed" and omars == "pending"


def test_control_the_supersede_by_arguments_dropped_omars_card(team, tmp_path, monkeypatch) -> None:
    from kazma_core.safety import hitl_gates

    real = hitl_gates._supersede_pending_twins

    def by_arguments(claimed: Any) -> None:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(hitl_gates, "_askers_differ", lambda *_a: False)
            real(claimed)

    monkeypatch.setattr(hitl_gates, "_supersede_pending_twins", by_arguments)
    omars, _reems = _claim_reems(team, tmp_path)
    assert omars == "superseded"


# ── standing approvals read the approvers when they are used ────────────


def _reems_grants(team: Any) -> str:
    """Reem's own YOLO window, task grant and "Allow tool" on a chat, made
    while she has no approvers. Returns the thread."""
    from kazma_core.safety.hitl_grants import grant_tool
    from kazma_core.safety.task_grants import grant_task
    from kazma_core.safety.yolo import enable_yolo

    thread = f"t{secrets.token_hex(6)}"
    with bind_scope(team.space, team.accounts["reem"]):
        grant_tool(thread, "file_write", actor="reem")
        grant_task(thread, actor="reem")
        enable_yolo(thread, actor="reem", force=True)
    return thread


def _standing(team: Any, thread: str, tool: str = "file_write") -> str | None:
    from kazma_core.safety.hitl import standing_approval

    with bind_scope(team.space, team.accounts["reem"]):
        return standing_approval(thread, tool)


def _clear(thread: str, team: Any, kind: str) -> None:
    from kazma_core.safety.hitl_grants import clear_grants
    from kazma_core.safety.task_grants import clear_task_grant
    from kazma_core.safety.yolo import disable_yolo

    with bind_scope(team.space, team.accounts["reem"]):
        if kind == "yolo":
            disable_yolo(thread, actor="test")
        elif kind == "task":
            clear_task_grant(thread)
        else:
            clear_grants(thread, actor="test")


@pytest.mark.parametrize("kind", ["yolo", "task", "tool"])
def test_her_own_grants_stop_once_the_admin_sets_approvers(team, kind: str) -> None:
    from kazma_core.safety.hitl_grants import grant_tool
    from kazma_core.safety.task_grants import grant_task
    from kazma_core.safety.unasked import BY_TASK_GRANT, BY_TOOL_GRANT, BY_YOLO
    from kazma_core.safety.yolo import enable_yolo

    thread = f"t{secrets.token_hex(6)}"
    with bind_scope(team.space, team.accounts["reem"]):
        if kind == "yolo":
            enable_yolo(thread, actor="reem", force=True)
        elif kind == "task":
            grant_task(thread, actor="reem")
        else:
            grant_tool(thread, "file_write", actor="reem")
    expected = {"yolo": BY_YOLO, "task": BY_TASK_GRANT, "tool": BY_TOOL_GRANT}[kind]
    assert _standing(team, thread) == expected  # before: her own grant holds
    _set_approvers(team.accounts["reem"], [team.accounts["omar"]])
    try:
        assert _standing(team, thread) is None, "her own grant does not hold now"
    finally:
        _set_approvers(team.accounts["reem"], [])
    assert _standing(team, thread) == expected, "approvers gone: it holds again"


def test_an_approvers_grant_for_her_holds(team) -> None:
    from kazma_core.safety.hitl_grants import grant_tool
    from kazma_core.safety.unasked import BY_TOOL_GRANT

    thread = f"t{secrets.token_hex(6)}"
    _set_approvers(team.accounts["reem"], [team.accounts["omar"]])
    try:
        # Omar answers Reem's card with "Allow tool": the grant is hers.
        with bind_scope(team.space, team.accounts["omar"]):
            assert grant_tool(thread, "file_write", actor="omar", person=team.accounts["reem"])["active"]
        assert _standing(team, thread) == BY_TOOL_GRANT
    finally:
        _set_approvers(team.accounts["reem"], [])


def test_control_reading_the_grant_alone_kept_her_yolo(team, monkeypatch) -> None:
    from kazma_core.safety.unasked import BY_YOLO

    thread = _reems_grants(team)
    _set_approvers(team.accounts["reem"], [team.accounts["omar"]])
    try:
        monkeypatch.setattr(approvals, "approvers_now", lambda person=None: None)
        assert _standing(team, thread) == BY_YOLO
    finally:
        _set_approvers(team.accounts["reem"], [])


def test_an_unreadable_store_holds_no_standing_approval(team, monkeypatch) -> None:
    thread = _reems_grants(team)

    def away() -> Any:
        raise identity.IdentityUnavailable("the accounts store is away")

    with bind_scope(team.space, team.accounts["reem"]):
        monkeypatch.setattr(identity, "read_registry", away)
        assert approvals.approvers_now() == frozenset()
    monkeypatch.undo()
    _clear(thread, team, "yolo")
    _clear(thread, team, "task")
    _clear(thread, team, "tool")


def test_with_spaces_off_every_grant_holds_as_before(team, monkeypatch) -> None:
    from kazma_core.spaces import scope

    monkeypatch.setattr(scope, "_enforced", False)
    assert approvals.approvers_now("anyone") is None
