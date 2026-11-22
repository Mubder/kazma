"""Spaces S2-12, piece 3: Development per person in a shared space with a
project folder (plan 4.11, decisions N1, N21, N28, N29).

* In a shared space with a project folder, each person who holds
  Development has its tools (the shell, git, GitHub, the project's files),
  working in that space's own folder; someone without it has none. The
  admin holds it as anyone does (N28: on at first). The admin's own tools
  are never there.
* A plain shared space has no Development, for anyone who holds it.
* N21, until S2-05: a person other than the admin who holds Development is
  refused their always-ask actions and folder grants there, before any card
  and with no effect, in their language. Their shell and git cards are
  their own, as before.

Controls: S1-20's rule (no host tool in another host space) refuses the
Development holder; without the interim refusal their always-ask call goes
on to a card they would answer themselves.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces import tools as host_tools
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bind_scope

pytestmark = pytest.mark.real_features

CODER, WRITER = "coder", "writer"


@pytest.fixture
def spaces(monkeypatch, tmp_path):
    """A shared space with a project folder and a plain one; the coder holds
    Development (turned on through People), the writer does not."""
    from kazma_core.security.vault import reset_posture_cache

    monkeypatch.setattr(scope, "_enforced", True)
    get_config_store().delete(identity.REGISTRY_KEY)

    def accounts(reg: dict[str, Any]) -> None:
        for account in (CODER, WRITER):
            reg["accounts"][account] = {"id": account, "name": account.title(), "role": "member",
                                        "enabled": True}

    identity.update_registry(accounts)
    folder = tmp_path / "project"
    folder.mkdir()
    dev = people.create_shared_space("Dev", kind="host", project_folder=str(folder), by=OWNER_ACCOUNT_ID)
    plain = people.create_shared_space("Family", by=OWNER_ACCOUNT_ID)
    for space in (dev, plain):
        for account in (CODER, WRITER):
            people.add_member(space["id"], account, by=OWNER_ACCOUNT_ID)
    people.set_features(CODER, {"development": True, "x_studio": True}, by=OWNER_ACCOUNT_ID)
    try:
        yield {"dev": dev["id"], "plain": plain["id"], "folder": folder}
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)
        reset_posture_cache()


def _allowance(space: str, account: str) -> host_tools.HostAllowance:
    with bind_scope(space, account):
        return host_tools.host_allowance()


def _offered(space: str, account: str) -> set[str]:
    from kazma_core.skills.switches import offered

    from tests._chat_space_cases import _registry

    with bind_scope(space, account):
        return {d["function"]["name"] for d in offered(_registry().get_tool_definitions())}


def _call(space: str, account: str, name: str, arguments: dict[str, Any] | None = None) -> tuple[dict, list]:
    from tests._chat_space_cases import _registry

    registry = _registry()
    entered: list[str] = []
    tool = registry.get_tool(name)

    async def recorder(*_a: Any, **_k: Any) -> str:
        entered.append(name)
        return "ran"

    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(registry._tools, name, replace(tool, func=recorder, is_async=True))
        with bind_scope(space, account):
            answer = asyncio.run(registry.execute(name, arguments or {}))
    return answer, entered


# ── Development, per person ─────────────────────────────────────────────


def test_a_development_holder_has_its_tools_in_the_project_space(spaces) -> None:
    allowance = _allowance(spaces["dev"], CODER)
    assert allowance.development and not allowance.admin
    offered = _offered(spaces["dev"], CODER)
    assert {"shell_exec", "git_status", "codebase_status"} <= offered
    assert not offered & host_tools.ADMIN, "the admin's own tools are never in a shared space"
    # A read-tier Development tool runs (no card stands in front of it).
    answer, entered = _call(spaces["dev"], CODER, "codebase_status")
    assert entered == ["codebase_status"], answer


def test_without_development_a_member_has_none_of_it(spaces) -> None:
    assert not _allowance(spaces["dev"], WRITER).development
    assert not _offered(spaces["dev"], WRITER) & host_tools.DEVELOPMENT
    answer, entered = _call(spaces["dev"], WRITER, "codebase_status")
    assert entered == [] and answer.get("refused"), answer


def test_the_admin_holds_it_as_anyone_does_and_never_their_own_tools_there(spaces) -> None:
    allowance = _allowance(spaces["dev"], OWNER_ACCOUNT_ID)
    assert allowance.development and not allowance.admin
    answer, entered = _call(spaces["dev"], OWNER_ACCOUNT_ID, "get_system_stats")
    assert entered == [] and "admin's personal space" in str(answer.get("content")), answer
    identity._set_development(OWNER_ACCOUNT_ID, False)
    try:
        assert not _allowance(spaces["dev"], OWNER_ACCOUNT_ID).development
    finally:
        identity._set_development(OWNER_ACCOUNT_ID, True)


def test_a_plain_shared_space_has_no_development_for_anyone(spaces) -> None:
    for account in (CODER, OWNER_ACCOUNT_ID):
        allowance = _allowance(spaces["plain"], account)
        assert not allowance.development and not allowance.admin
        answer, entered = _call(spaces["plain"], account, "codebase_status")
        assert entered == [] and answer.get("refused"), answer


def test_control_s1_20s_rule_refuses_the_holder(spaces, monkeypatch) -> None:
    """The rule before S2-12: another host space had no host tool at all."""
    real = host_tools.host_allowance

    def before() -> host_tools.HostAllowance:
        bound = scope.current_scope()
        if bound is not None and bound.space_id == spaces["dev"]:
            return host_tools._none("a shared space with a project folder has no host tools until it "
                                    "has its own folder (plan S1-34)")
        return real()

    monkeypatch.setattr(host_tools, "host_allowance", before)
    answer, entered = _call(spaces["dev"], CODER, "codebase_status")
    assert entered == [] and answer.get("refused")


def test_the_project_space_works_in_its_own_folder(spaces) -> None:
    from kazma_core.workspace.binding import resolve_active_root

    with bind_scope(spaces["dev"], CODER):
        assert resolve_active_root() == spaces["folder"].resolve()


# ── N21, until S2-05 ────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["x_post", "request_path_access"])
def test_a_holders_always_ask_action_is_refused_before_its_card(spaces, name) -> None:
    from kazma_core.safety.call_preflight import call_refusal

    with bind_scope(spaces["dev"], CODER):
        english = asyncio.run(call_refusal(name, {}, user_text="Please post this for me"))
        arabic = asyncio.run(call_refusal(name, {}, user_text="انشر هذا لو سمحت"))
    assert english and "admin's approval" in english and "later update" in english
    assert arabic and "موافقة المسؤول" in arabic and "تحديث لاحق" in arabic
    answer, entered = _call(spaces["dev"], CODER, name)
    assert entered == [] and answer.get("refused") and "admin's approval" in str(answer["content"]), answer


def test_the_graph_refuses_it_with_no_card(spaces) -> None:
    from kazma_core.agent.graph_tool_worker import _refuse_unrunnable_calls

    pending = [{"id": "c1", "name": "x_post", "arguments": {"text": "hi"}},
               {"id": "c2", "name": "file_read", "arguments": {"path": "a.txt"}}]
    with bind_scope(spaces["dev"], CODER):
        kept, refused = asyncio.run(_refuse_unrunnable_calls(pending, user_text="post it"))
    assert [tc["name"] for tc in kept] == ["file_read"]
    assert [r["name"] for r in refused] == ["x_post"] and refused[0]["outcome"] == "policy"
    assert "no approval was asked" in refused[0]["content"]


def test_control_without_the_interim_refusal_the_card_is_the_holders(spaces, monkeypatch) -> None:
    from kazma_core.agent.graph_tool_worker import _refuse_unrunnable_calls

    monkeypatch.setattr(host_tools, "approval_refusal", lambda *_a, **_k: None)
    pending = [{"id": "c1", "name": "x_post", "arguments": {"text": "hi"}}]
    with bind_scope(spaces["dev"], CODER):
        kept, refused = asyncio.run(_refuse_unrunnable_calls(pending, user_text="post it"))
    assert [tc["name"] for tc in kept] == ["x_post"] and refused == []


def test_the_interim_refusal_is_only_for_a_non_admin_holder_in_a_project_space(spaces) -> None:
    from kazma_core.safety.call_preflight import call_refusal

    for space, account in ((spaces["dev"], OWNER_ACCOUNT_ID),  # the admin answers their own
                           (spaces["dev"], WRITER),            # no Development: their own cards (N4)
                           (spaces["plain"], CODER)):          # a plain space: no Development there
        with bind_scope(space, account):
            assert asyncio.run(call_refusal("x_post", {}, user_text="post it")) is None, (space, account)
    # Their shell and git cards stay their own: never refused here.
    with bind_scope(spaces["dev"], CODER):
        assert host_tools.approval_refusal("shell_exec") is None
        assert host_tools.approval_refusal("git_push") is None


def test_with_spaces_off_nothing_is_refused(spaces, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    with bind_scope(spaces["dev"], CODER):
        assert host_tools.approval_refusal("x_post") is None
