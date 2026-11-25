"""A page that acts on another space's rows says whose they are (S4-01 step 0a:
rows 6, 16, 31-35).

A move writes the space it moves into, a member's leaving takes back what they
had in the space they left, the admin's page counts every space's documents,
and the runs list reads every space's jobs. Each ran in the space of whoever's
page asked. The statements are the row's own now, and the install's own ones
(the garbage collection, the totals) say so; with Spaces off nothing is bound.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.documents import ingestion
from kazma_core.documents.ingestion import set_ingestion_service
from kazma_core.memory import forget
from kazma_core.spaces import identity, moves, people, scope, space_limits
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, Scope, bind_scope

from tests._documents_seed import seed_document, seeded_service
from tests._space_door import watched


def _clean() -> None:
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    store.delete(ingestion.MOVES_KEY)


@pytest.fixture
def world(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Spaces on: reem, in two shared spaces besides their own."""
    from kazma_core.security.vault import reset_posture_cache

    monkeypatch.setattr(scope, "_enforced", True)
    _clean()
    reem = people.create_person("Reem", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    made = SimpleNamespace(svc=seeded_service(tmp_path / "documents"), reem=reem["id"], mine=reem["personal_space"])
    for name in ("family", "team"):
        space = people.create_shared_space(name.title(), by=OWNER_ACCOUNT_ID)["id"]
        people.add_member(space, made.reem, by=OWNER_ACCOUNT_ID)
        setattr(made, name, space)
    set_ingestion_service(made.svc)
    try:
        yield made
    finally:
        set_ingestion_service(None)
        made.svc.close()
        _clean()
        reset_posture_cache()


@pytest.fixture
def off(tmp_path: Any) -> Any:
    """An install without Spaces, with two tenants' documents."""
    made = SimpleNamespace(svc=seeded_service(tmp_path / "documents"), reem="alice", mine="acme", family="beta",
                           team="gamma")
    set_ingestion_service(made.svc)
    try:
        yield made
    finally:
        set_ingestion_service(None)
        made.svc.close()
        _clean()


# ── a document's move (row 6) ────────────────────────────────────────────


def _move(world: Any, *, retiring_fails: bool = False) -> Any:
    doc = seed_document(world.svc, tenant_id=world.mine, owner=world.reem, marker="trip notes", title="Trip")
    real = world.svc._tombstone

    def tombstone(**kw: Any) -> Any:
        if retiring_fails and kw["tenant_id"] == world.mine:
            raise RuntimeError("the source could not be retired")
        return real(**kw)

    world.svc._tombstone = tombstone
    with watched((world.svc, "ingest_stream"), (world.svc, "_tombstone"), (world.svc.audit, "record")) as calls:
        def moved() -> None:
            world.svc.move_document(document_id=doc.document_id, from_tenant=world.mine, to_tenant=world.family,
                                    actor_id=world.reem, workspace_id="w", taken_out=False)

        if retiring_fails:
            with pytest.raises(RuntimeError, match="retired"):
                moved()
        else:
            moved()
    return calls


def test_a_move_writes_the_target_in_the_target(world: Any) -> None:
    with bind_scope(world.mine, world.reem):  # the page is the source's
        calls = _move(world)
    assert calls.scopes("ingest_stream") == [Scope(world.family, world.reem)]
    assert calls.scopes("_tombstone") == [Scope(world.mine, world.reem)], "the source is retired in the source"
    moved = {c.kwargs["action"]: c.space for c in calls if c.name == "record" and "moved" in c.kwargs["action"]}
    assert moved == {"moved_out": world.mine, "moved_in": world.family}


def test_a_copy_is_taken_back_where_it_is(world: Any) -> None:
    with bind_scope(world.mine, world.reem):
        calls = _move(world, retiring_fails=True)
    assert calls.scopes("_tombstone") == [Scope(world.mine, world.reem), Scope(world.family, world.reem)]


@pytest.mark.parametrize("meanwhile", ["removed", "erasing"])
def test_a_move_is_its_persons_or_refused(world: Any, monkeypatch: pytest.MonkeyPatch, meanwhile: str) -> None:
    """Between the rule and the lock they were removed, or the target started
    to be erased: the copy went in as the space's maker."""
    doc = seed_document(world.svc, tenant_id=world.mine, owner=world.reem, marker="trip notes", title="Trip")
    real = ingestion._move_lock.__enter__

    def waited(self: Any) -> None:
        real(self)
        if meanwhile == "removed":
            identity.remove_member(world.family, world.reem)
        else:
            identity.update_registry(lambda reg: reg["spaces"][world.family].update(erasing_at="now"))

    monkeypatch.setattr(ingestion._move_lock, "__enter__", waited)
    with watched((world.svc, "ingest_stream")) as calls, bind_scope(world.mine, world.reem):
        with pytest.raises(ingestion.DocumentIngestionError) as refused:
            moves.move_document(doc.document_id, from_space=world.mine, to_space=world.family, by=world.reem,
                                workspace_id="w", service=world.svc)
    assert refused.value.code == "not_yours" and not calls, "the catalog's words for it, EN and AR"
    assert not get_config_store().get(ingestion.MOVES_KEY), "nothing under way"
    record = world.svc.repository.get_document(tenant_id=world.mine, document_id=doc.document_id)
    assert record is not None and not record.deleted_at, "it is where it was"


@pytest.mark.parametrize("meanwhile", ["removed", "erasing", "unreadable"])
def test_a_copy_made_while_they_were_taken_out_is_taken_back(world: Any, monkeypatch: pytest.MonkeyPatch,
                                                             meanwhile: str) -> None:
    """They were taken out while the copy itself was made: the copy stayed in
    the space they are not in, and the source was retired."""
    doc = seed_document(world.svc, tenant_id=world.mine, owner=world.reem, marker="trip notes", title="Trip")
    real = world.svc.ingest_stream
    copies: list[Any] = []

    def ingest(*args: Any, **kwargs: Any) -> Any:
        copies.append(real(*args, **kwargs))
        if meanwhile == "removed":
            identity.remove_member(world.family, world.reem)
        elif meanwhile == "erasing":
            identity.update_registry(lambda reg: reg["spaces"][world.family].update(erasing_at="now"))
        else:
            def away(*_a: Any, **_k: Any) -> Any:
                raise identity.IdentityUnavailable("the settings store is away")

            monkeypatch.setattr(identity, "read_registry", away)
        return copies[-1]

    monkeypatch.setattr(world.svc, "ingest_stream", ingest)
    expected = identity.IdentityUnavailable if meanwhile == "unreadable" else ingestion.DocumentIngestionError
    with bind_scope(world.mine, world.reem), pytest.raises(expected) as refused:
        moves.move_document(doc.document_id, from_space=world.mine, to_space=world.family, by=world.reem,
                            workspace_id="w", service=world.svc)
    if meanwhile != "unreadable":
        assert refused.value.code == "not_yours"
    kept = world.svc.repository.get_document(tenant_id=world.mine, document_id=doc.document_id)
    assert kept is not None and not kept.deleted_at, "it is where it was"
    copy = world.svc.repository.get_document(tenant_id=world.family, document_id=copies[0].document_id,
                                             include_deleted=True)
    assert copy is not None and copy.deleted_at, "and nowhere else"


def test_a_person_taken_out_of_the_source_takes_nothing_out(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = seed_document(world.svc, tenant_id=world.family, owner=world.reem, marker="trip notes", title="Trip")
    real = ingestion._move_lock.__enter__

    def waited(self: Any) -> None:
        real(self)
        identity.remove_member(world.family, world.reem)

    monkeypatch.setattr(ingestion._move_lock, "__enter__", waited)
    with bind_scope(world.family, world.reem), pytest.raises(ingestion.DocumentIngestionError) as refused:
        moves.move_document(doc.document_id, from_space=world.family, to_space=world.mine, by=world.reem,
                            workspace_id="w", service=world.svc)
    assert refused.value.code == "not_yours"


def test_the_moves_refusal_has_words_and_a_status() -> None:
    from kazma_ui.documents_api import _STATUS_BY_CODE
    from kazma_ui.i18n.catalog import documents as catalog

    words = next(v["documents.move_err.not_yours"] for v in vars(catalog).values()
                 if isinstance(v, dict) and "documents.move_err.not_yours" in v)
    assert words["en"] and words["ar"] and _STATUS_BY_CODE["not_yours"] == 403


def test_off_a_move_binds_nothing(off: Any) -> None:
    calls = _move(off)
    assert {c.scope for c in calls} == {None}
    assert calls.scopes("ingest_stream") == [None]


# ── a member leaves (row 16) ─────────────────────────────────────────────


def _leaving_calls(call: Any) -> Any:
    from kazma_core.documents import principal

    with watched((forget, "forget_entry"), (principal, "take_back_access")) as calls:
        call()
    return calls


@pytest.mark.parametrize("page", ["their own", "the admin's"])
def test_what_goes_with_a_member_goes_in_the_space_they_left(world: Any, page: str) -> None:
    who = (world.mine, world.reem) if page == "their own" else (OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
    by = world.reem if page == "their own" else OWNER_ACCOUNT_ID

    def left() -> None:
        with bind_scope(*who):
            if page == "their own":
                people.leave_space(world.family, by=by)
            else:
                people.remove_member(world.family, world.reem, by=by)

    calls = _leaving_calls(left)
    there = Scope(world.family, OWNER_ACCOUNT_ID)  # the space's own account: they are out of it
    assert calls.scopes("forget_entry") == [there] and calls.scopes("take_back_access") == [there]


def test_an_accounts_deletion_is_each_spaces_own(world: Any) -> None:
    def deleted() -> None:
        with bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
            people.delete_person(world.reem, by=OWNER_ACCOUNT_ID)

    calls = _leaving_calls(deleted)
    assert {s.space_id for s in calls.scopes("forget_entry")} == {world.family, world.team}
    assert {s.space_id for s in calls.scopes("take_back_access")} == {world.family, world.team}


def test_leaving_a_space_that_is_being_erased_still_ends(world: Any) -> None:
    """Nothing binds it; its own statements run, and the erase takes the rest."""
    identity.update_registry(lambda reg: reg["spaces"][world.family].update(erasing_at="now"))

    def left() -> None:
        people._after_leaving(world.family, world.reem)

    calls = _leaving_calls(left)
    assert calls.of("forget_entry") == [world.family] == calls.of("take_back_access")
    assert calls.scopes("forget_entry") == [None]


def test_shares_are_really_taken_back(world: Any) -> None:
    seed_document(world.svc, tenant_id=world.family, owner=OWNER_ACCOUNT_ID, marker="shared notes")
    world.svc.grant_member_access(tenant_id=world.family, member=world.reem, force=True)
    with bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert people.remove_member(world.family, world.reem, by=OWNER_ACCOUNT_ID)["documents"] == 1


def test_leaving_says_when_whose_it_is_cannot_be_read(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def away(*_a: Any, **_k: Any) -> Any:
        raise identity.IdentityUnavailable("the settings store is away")

    monkeypatch.setattr(identity, "read_registry", away)
    with pytest.raises(people.PeopleRefused) as refused:
        people._after_leaving(world.family, world.reem)
    assert refused.value.code == "forget_failed"


def test_off_leaving_binds_nothing(off: Any) -> None:
    calls = _leaving_calls(lambda: people._after_leaving("default", "nobody"))
    assert calls.scopes("forget_entry") == [None] and calls.scopes("take_back_access") == [None]


# ── counts and the install's own (rows 31-34) ────────────────────────────


def test_every_spaces_document_bytes_are_read_as_its_own(world: Any) -> None:
    seed_document(world.svc, tenant_id=world.family, owner=world.reem, marker="family notes")
    with watched((world.svc.repository, "tenant_referenced_blob_bytes")) as calls:
        with bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):  # the admin's page
            assert space_limits._documents_used(world.family) == len("family notes")
            world.svc.metrics_snapshot(tenant_id=world.family)
    said = {(c.space, c.kwargs["tenant_id"]) for c in calls}
    assert said == {(world.family, world.family)}
    assert set(calls.scopes("tenant_referenced_blob_bytes")) == {Scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)}


def test_the_collection_and_its_audit_row_are_the_installs(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(world.svc, "_require_writes_enabled", lambda: None)
    with watched((world.svc.gc, "_mark"), (world.svc.audit, "record")) as calls:
        with bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
            world.svc.run_maintenance(dry_run=True)
    assert calls.of("_mark") == ["<install>"]
    [audit] = [c for c in calls if c.name == "record" and c.kwargs.get("tenant_id") == "system"]
    assert audit.space == "<install>"


def test_off_the_collection_binds_nothing(off: Any) -> None:
    with watched((off.svc.gc, "_mark"), (off.svc.audit, "record")) as calls:
        off.svc.run_maintenance(dry_run=True)
    assert calls and {c.scope for c in calls} == {None}


def test_a_lost_audit_row_and_unread_totals_are_warnings(off: Any, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    off.svc.close()  # every statement fails from here
    off.svc.audit.record(tenant_id="acme", event_type="index", action="reconcile")
    assert off.svc._storage_bytes() == (0, 0)
    said = {r.name.rsplit(".", 1)[-1]: r.levelname for r in caplog.records if "failed" in r.getMessage()
            or "not recorded" in r.getMessage()}
    assert said.get("audit") == "WARNING" and said.get("ingestion") == "WARNING", said


# ── the runs list (row 35) ───────────────────────────────────────────────


def _listed(world: Any) -> Any:
    from kazma_core.runs.adapters.documents import ADAPTER

    for tenant in (world.mine, world.family):
        seed_document(world.svc, tenant_id=tenant, owner=world.reem, marker=f"notes of {tenant}")
    with watched((world.svc.jobs, "recent_jobs"), (world.svc.repository, "get_document")) as calls:
        runs = asyncio.run(ADAPTER.list_runs(time.time(), window_s=3600))
    return runs, calls


def test_the_supervisors_list_reads_each_title_in_its_space(world: Any) -> None:
    runs, calls = _listed(world)
    assert {r.space for r in runs} == {world.mine, world.family}
    assert calls.of("recent_jobs") == ["<install>"]
    assert {(c.space, c.kwargs["tenant_id"]) for c in calls if c.name == "get_document"} == {
        (world.mine, world.mine), (world.family, world.family)}


def test_a_list_inside_a_space_stays_that_spaces(world: Any) -> None:
    """Never the install's by call: a bound page or tool would see every
    space's jobs (H6)."""
    with bind_scope(world.mine, world.reem):
        _runs, calls = _listed(world)
    assert {c.space for c in calls} == {world.mine}


def test_off_the_list_is_every_tenants_with_nothing_bound(off: Any) -> None:
    runs, calls = _listed(off)
    assert {r.space for r in runs} == {off.mine, off.family}
    assert {c.scope for c in calls} == {None}
