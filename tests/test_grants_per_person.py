"""Spaces S2-06: a standing approval belongs to the person and the chat.

"Allow tool", "Approve for task" and a YOLO window skip the approval cards
of a chat until they end. Once Spaces is enforced each is kept per person
(``safety.grant_holder``): Reem and Omar share a chat, and what Reem allowed
never skips Omar's card -- on the chat graph's gate, the swarm bus's and the
MCP gate alike (``hitl.standing_approval``, the one answer they ask).

* A grant made from Reem's card is Reem's, whoever answered it (S2-05: an
  approver answers in her place; the grant skips her cards, never theirs).
* ``/yolo off`` ends the person's own window and grants, not another's.
* Work with no person bound is granted nothing and skips nothing.
* A space's erase still finds its chats' grants (S1-27).
* Nothing changes on an install that does not enforce Spaces: the keys are
  the thread's, as before.

Control: the per-thread grant (the rule before S2-06) lets Reem's grant
skip Omar's card.
"""

from __future__ import annotations

import asyncio
import secrets
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.safety import grant_holder, hitl, hitl_grants, task_grants, yolo
from kazma_core.safety.unasked import BY_TASK_GRANT, BY_TOOL_GRANT, BY_YOLO
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bind_scope

pytestmark = pytest.mark.real_features


@pytest.fixture
def shared(monkeypatch):
    """Spaces enforced; Reem and Omar in one shared space, one chat."""
    from kazma_core.security.vault import reset_posture_cache

    monkeypatch.setattr(scope, "_enforced", True)
    monkeypatch.setenv("KAZMA_ALLOW_YOLO", "1")
    get_config_store().delete(identity.REGISTRY_KEY)

    def accounts(reg: dict[str, Any]) -> None:
        for account in ("reem", "omar"):
            reg["accounts"][account] = {"id": account, "name": account.title(), "role": "member",
                                        "enabled": True}

    try:
        identity.update_registry(accounts)
        space = people.create_shared_space("Team", by=OWNER_ACCOUNT_ID)["id"]
        for account in ("reem", "omar"):
            people.add_member(space, account, by=OWNER_ACCOUNT_ID)
        yield SimpleNamespace(space=space, thread=f"t{secrets.token_hex(8)}")
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)
        reset_posture_cache()


def _standing(shared: Any, person: str, tool: str = "file_write") -> str | None:
    """What every gate asks for *person*'s card in the chat."""
    with bind_scope(shared.space, person):
        return hitl.standing_approval(shared.thread, tool)


# ── each standing approval is the person's ──────────────────────────────


def test_a_tool_grant_skips_only_its_persons_cards(shared) -> None:
    with bind_scope(shared.space, "reem"):
        assert hitl_grants.grant_tool(shared.thread, "file_write", actor="reem")["active"]
    assert _standing(shared, "reem") == BY_TOOL_GRANT
    assert _standing(shared, "omar") is None
    with bind_scope(shared.space, "omar"):
        assert hitl_grants.list_grants(shared.thread) == []
        # The swarm bus asks the same grants (swarm.safety._grant_tool).
        from kazma_core.swarm.safety import _grant_tool

        assert not _grant_tool(shared.thread, "file_write")


def test_a_task_grant_skips_only_its_persons_cards(shared) -> None:
    with bind_scope(shared.space, "reem"):
        assert task_grants.grant_task(shared.thread, actor="reem")["active"]
    assert _standing(shared, "reem") == BY_TASK_GRANT
    assert _standing(shared, "omar") is None


def test_a_yolo_window_is_its_persons(shared) -> None:
    with bind_scope(shared.space, "reem"):
        assert yolo.enable_yolo(shared.thread, actor="reem")["active"]
    assert _standing(shared, "reem") == BY_YOLO
    assert _standing(shared, "omar") is None
    with bind_scope(shared.space, "omar"):
        assert not yolo.is_yolo_active(shared.thread)
        # The chat graph's config reads the window of the turn's person.
        from kazma_core.safety.yolo import yolo_status

        assert not yolo_status(shared.thread)["active"]


def test_yolo_off_ends_only_the_persons_own(shared) -> None:
    for person in ("reem", "omar"):
        with bind_scope(shared.space, person):
            yolo.enable_yolo(shared.thread, actor=person)
            hitl_grants.grant_tool(shared.thread, "file_write", actor=person)
    with bind_scope(shared.space, "omar"):
        yolo.disable_yolo(shared.thread, actor="omar")
    assert _standing(shared, "omar") is None
    assert _standing(shared, "reem") == BY_YOLO
    with bind_scope(shared.space, "reem"):
        assert hitl_grants.has_tool_grant(shared.thread, "file_write")


def test_control_the_per_thread_grant_skips_another_members_card(shared, monkeypatch) -> None:
    """The rule before S2-06: a grant was the chat's, for everyone in it."""
    monkeypatch.setattr(grant_holder, "holder", lambda thread_id, person=None: str(thread_id or ""))
    with bind_scope(shared.space, "reem"):
        hitl_grants.grant_tool(shared.thread, "file_write", actor="reem")
    assert _standing(shared, "omar") == BY_TOOL_GRANT


def test_work_with_no_person_is_granted_nothing(shared) -> None:
    assert grant_holder.holder(shared.thread) == ""
    assert not hitl_grants.grant_tool(shared.thread, "file_write")["active"]
    assert not task_grants.grant_task(shared.thread)["active"]
    assert not yolo.enable_yolo(shared.thread)["active"]
    assert hitl.standing_approval(shared.thread, "file_write") is None
    assert not [k for k in get_config_store().get_category("safety")
                if shared.thread in k], "nothing was written for no one"


# ── a grant from a card is the card's asker's ───────────────────────────


def test_a_grant_from_reems_card_is_reems_whoever_answered(shared) -> None:
    from kazma_ui.routes_direct.misc import _apply_scope_grants

    pending = [{"name": "file_write", "args": {}}]
    with bind_scope(shared.space, "omar"):  # Reem's approver pressed it
        scope_used = _apply_scope_grants(shared.thread, True, "tool", "omar", pending, "file_write",
                                         "", "reem")
    assert scope_used == "tool"
    assert _standing(shared, "reem") == BY_TOOL_GRANT
    assert _standing(shared, "omar") is None
    with bind_scope(shared.space, "omar"):
        _apply_scope_grants(shared.thread, True, "yolo", "omar", pending, "file_write", "", "reem")
    assert _standing(shared, "reem") == BY_YOLO
    with bind_scope(shared.space, "reem"):
        assert yolo.is_yolo_active(shared.thread)
    with bind_scope(shared.space, "omar"):
        assert not yolo.is_yolo_active(shared.thread)


def test_the_approve_route_grants_to_the_cards_asker(monkeypatch) -> None:
    """The real route: Reem's approver answers her card with "Allow tool";
    the grant skips Reem's next card and not the approver's."""
    from kazma_core.security.vault import reset_posture_cache
    from kazma_ui import space_binding

    from tests.test_approvals_reach_the_asker import _approve, _person, chat_paused

    monkeypatch.setattr(scope, "_enforced", True)
    get_config_store().delete(identity.REGISTRY_KEY)
    try:
        principals = {"reem": _person("reem-s206"), "omar": _person("omar-s206")}
        accounts = {who: space_binding.account_of(p) for who, p in principals.items()}
        space = people.create_shared_space("Team", by=OWNER_ACCOUNT_ID)["id"]
        for account in accounts.values():
            people.add_member(space, account, by=OWNER_ACCOUNT_ID)

        def set_approver(reg: dict[str, Any]) -> None:
            reg["policies"][accounts["reem"]] = {"approvers": [accounts["omar"]]}

        identity.update_registry(set_approver)
        team = SimpleNamespace(space=space, principals=principals, accounts=accounts)
        with chat_paused(team) as world:
            got = _approve(world, team, "omar", scope="tool")
            assert got.status_code == 200, got.text
            thread = world.thread
        with bind_scope(space, accounts["reem"]):
            assert hitl.standing_approval(thread, "file_write") == BY_TOOL_GRANT
        with bind_scope(space, accounts["omar"]):
            assert hitl.standing_approval(thread, "file_write") is None
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)
        reset_posture_cache()


# ── the rest ────────────────────────────────────────────────────────────


def test_an_erase_finds_its_chats_grants(shared) -> None:
    from kazma_core.spaces.space_data import _settings_keys

    with bind_scope(shared.space, "reem"):
        hitl_grants.grant_tool(shared.thread, "file_write", actor="reem")
        yolo.enable_yolo(shared.thread, actor="reem")
    keys = _settings_keys(shared.space, [shared.thread])
    assert f"hitl_grant.{shared.thread}~reem.file_write" in keys
    assert f"yolo.{shared.thread}~reem" in keys


def test_with_spaces_off_a_grant_is_the_chats_as_before(shared, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    with bind_scope(shared.space, "reem"):
        hitl_grants.grant_tool(shared.thread, "file_write", actor="reem")
        yolo.enable_yolo(shared.thread, actor="reem")
    safety = get_config_store().get_category("safety")
    assert f"hitl_grant.{shared.thread}.file_write" in safety
    assert f"yolo.{shared.thread}" in safety
    # Anyone in the chat: the chat's grant, as before Spaces.
    with bind_scope(shared.space, "omar"):
        assert hitl.standing_approval(shared.thread, "file_write") == BY_YOLO


def test_the_holder_is_one_key_segment() -> None:
    """An account id holds no '.' and no '~' (spaces.scope.valid_account_id),
    so one person's prefix never matches another's."""
    from kazma_core.spaces.scope import valid_account_id

    assert not valid_account_id("a.b") and not valid_account_id("a~b")
    assert grant_holder.thread_of("t1~reem") == "t1" and grant_holder.thread_of("t1") == "t1"


def test_a_resumed_turn_reads_the_askers_grants(shared) -> None:
    """The chat graph reads standing approvals in the turn's scope, which a
    resume binds to the card's asker (S2-05): the asker's grants apply."""
    with bind_scope(shared.space, "reem"):
        hitl_grants.grant_tool(shared.thread, "file_write", actor="reem")

    async def turn() -> str | None:
        from kazma_ui.hitl_answerers import GateWhose, as_the_asker

        with bind_scope(shared.space, "omar"), as_the_asker(GateWhose(shared.space, "reem", ())):
            task = asyncio.create_task(asyncio.to_thread(hitl.standing_approval, shared.thread,
                                                         "file_write"))
        return await task

    assert asyncio.run(turn()) == BY_TOOL_GRANT
