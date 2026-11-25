"""Whose statements these are: the three forms of ``spaces.row_work`` (S4-01,
step 0a).

Work for one row ran with nothing bound, or bound to another space. Each form
says the row's space; none changes anything while Spaces is off.
"""

from __future__ import annotations

import asyncio

import pytest
from kazma_core.spaces import identity, row_work, scope
from kazma_core.spaces.row_work import (
    Door,
    SpaceErasing,
    install_loops,
    install_statements,
    resolve_act_of,
    resolve_work_of,
    statements_of,
    work_of,
    work_of_async,
)
from kazma_core.spaces.scope import NoSpaceBound, Scope, bind_scope, current_scope, install_work
from kazma_core.tenant_context import get_current_tenant_id


def statement_space() -> str | None:
    """The space a statement is for now."""
    door = Door.now()
    return (door.space or None) if door is not None else None


@pytest.fixture
def enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)


@pytest.fixture
def family() -> str:
    """A shared space of two members; its maker is layla."""
    def add(reg: dict) -> None:
        for who in ("layla", "omar"):
            reg["accounts"][who] = {"id": who, "name": who, "role": "member", "enabled": True,
                                    "sign_in": {"kind": "local", "username": who}}
        reg["spaces"]["family"] = {"id": "family", "name": "Family", "kind": "sandboxed", "created_by": "layla"}
        reg["memberships"] += [{"account": "layla", "space": "family", "role": "owner"},
                               {"account": "omar", "space": "family", "role": "member"}]
    identity.update_registry(add)
    return "family"


# ── statements ───────────────────────────────────────────────────────────


def test_nothing_said_is_no_door_and_a_scope_is_its_space() -> None:
    assert Door.now() is None and statement_space() is None
    with bind_scope("family", "layla"):
        assert Door.now() == Door(space="family") and statement_space() == "family"
    with install_work("a sweep"):
        assert Door.now() == Door(reason="a sweep") and statement_space() is None


@pytest.mark.usefixtures("enforced")
def test_statements_of_says_the_rows_space_and_nobody_acts_as_it() -> None:
    with bind_scope("default", "owner"):
        with statements_of("family"):
            assert statement_space() == "family"
            assert current_scope() == Scope("default", "owner"), "who is acting does not change"
            assert get_current_tenant_id() == "default"
        assert statement_space() == "default"
    with statements_of("family"):
        assert statement_space() == "family"
        assert current_scope() is None and get_current_tenant_id() is None
        with pytest.raises(NoSpaceBound):
            scope.require_space_id()


def test_a_row_of_before_spaces_is_the_owners() -> None:
    for tenant in ("", None, "  "):
        with statements_of(tenant):
            assert statement_space() == "default"


def test_a_scope_bound_inside_wins_and_leaving_it_restores_what_was_said() -> None:
    with install_statements("a sweep"):
        with bind_scope("family", "layla"):
            assert statement_space() == "family"
            with statements_of("default"):
                assert statement_space() == "default"
        assert Door.now() == Door(reason="a sweep")
        with install_work("a deeper sweep"):
            assert Door.now() == Door(reason="a deeper sweep")
    assert Door.now() is None


def test_the_innermost_says_it() -> None:
    with install_statements("a listing"):
        with statements_of("family"):
            assert statement_space() == "family"
            with install_statements("a ping"):
                assert Door.now() == Door(reason="a ping")
            assert statement_space() == "family"


def test_install_statements_say_what_they_are_and_decorate_a_function() -> None:
    with pytest.raises(ValueError, match="must say"):
        with install_statements("  "):
            raise AssertionError("entered")

    @install_statements("a listing of every space")
    def listing() -> Door | None:
        return Door.now()

    assert listing() == listing() == Door(reason="a listing of every space"), "each call anew"
    assert Door.now() is None


def test_what_was_said_follows_a_thread_and_a_task() -> None:
    async def run() -> tuple[str | None, str | None]:
        with statements_of("family"):
            return await asyncio.to_thread(statement_space), await asyncio.create_task(_say())

    async def _say() -> str | None:
        return statement_space()

    assert asyncio.run(run()) == ("family", "family")


# ── work ─────────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("enforced")
@pytest.mark.parametrize(("asker", "acts"), [
    ("omar", "omar"),            # a member acts as themself
    ("", "layla"),               # no asker: the space's own account
    ("operator", "layla"),       # a principal of before Spaces
    ("nadia", "layla"),          # someone who is no member
])
def test_work_for_a_row_runs_in_its_space(family: str, asker: str, acts: str) -> None:
    with work_of(family, asker):
        assert current_scope() == Scope("family", acts)
        assert get_current_tenant_id() == "family"
    assert current_scope() is None


@pytest.mark.usefixtures("enforced")
def test_work_for_a_row_from_another_spaces_turn_is_the_rows(family: str) -> None:
    async def run() -> Scope | None:
        with bind_scope("default", "owner"):
            with (await work_of_async(family, "omar"))():
                return await asyncio.to_thread(current_scope)

    assert asyncio.run(run()) == Scope("family", "omar")


@pytest.mark.usefixtures("enforced")
def test_nothing_binds_a_space_of_no_install_or_one_being_erased(family: str) -> None:
    with pytest.raises(NoSpaceBound) as unknown:
        resolve_work_of("s00000000dead", "omar")
    assert not isinstance(unknown.value, SpaceErasing)
    identity.update_registry(lambda reg: reg["spaces"][family].update(erasing_at="now"))
    with pytest.raises(SpaceErasing):
        resolve_work_of(family, "omar")
    with pytest.raises(identity.AskerLeft):
        resolve_act_of(family, "omar")


@pytest.mark.usefixtures("enforced")
@pytest.mark.parametrize("who", ["", "operator", "nadia", "owner"])
def test_an_act_is_its_persons_or_refused(family: str, who: str) -> None:
    with resolve_act_of(family, "omar")():
        assert current_scope() == Scope("family", "omar")
    with pytest.raises(identity.AskerLeft):
        resolve_act_of(family, who)
    with pytest.raises(identity.AskerLeft):
        resolve_act_of("Not A Space!", "omar")


@pytest.mark.usefixtures("enforced")
def test_a_row_of_no_space_is_refused_and_an_unreadable_store_is_said(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(NoSpaceBound):
        resolve_work_of("Not A Space!", "owner")

    def away(*_a: object, **_k: object) -> None:
        raise identity.IdentityUnavailable("the settings store is away")

    monkeypatch.setattr(identity, "read_registry", away)
    with pytest.raises(identity.IdentityUnavailable):
        resolve_work_of("family", "omar")


def test_off_work_binds_nothing_and_reads_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An install without Spaces runs the same calls with nothing new bound."""
    def read(*_a: object, **_k: object) -> None:
        raise AssertionError("the identity store was read")

    monkeypatch.setattr(identity, "read_registry", read)

    async def threaded(*_a: object, **_k: object) -> None:
        raise AssertionError("a thread was taken")

    monkeypatch.setattr(asyncio, "to_thread", threaded)

    async def run() -> None:
        for enter in (resolve_work_of("acme", "x"), resolve_act_of("acme", "x"), await work_of_async("acme", "x"),
                      lambda: install_loops("loops")):
            with enter():
                assert current_scope() is None and get_current_tenant_id() is None
                assert scope.require_space_id() == "default", "unbound work is the owner's, as before"

    asyncio.run(run())


def test_loops_of_the_install_hold_no_persons_space(monkeypatch: pytest.MonkeyPatch) -> None:
    async def loop() -> tuple[Scope | None, Door | None]:
        return current_scope(), Door.now()

    async def started() -> tuple[Scope | None, Door | None]:
        with bind_scope("family", "layla"):
            with install_loops("the document loops"):
                task = asyncio.create_task(loop())
            return await task

    monkeypatch.setattr(scope, "_enforced", True)
    assert asyncio.run(started()) == (None, Door(reason="the document loops"))
    # Control, Spaces off: the scope a turn bound stays, as it always did.
    monkeypatch.setattr(scope, "_enforced", False)
    assert asyncio.run(started())[0] == Scope("family", "layla")


def test_the_module_reads_nothing_at_the_door() -> None:
    """The statement forms never reach the identity store."""
    assert "identity" not in row_work.statements_of.__wrapped__.__code__.co_names
