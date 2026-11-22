"""The S2-12 review, finding 6: work a person asked for in a shared space
runs as them while they are in it, and is refused once they left it --
never run as the space's acting account (its maker, the admin).

Covered where queued work runs later: a scheduled fire, a swarm task's
worker, a booked X post, a document job's stage. Work no person asked for
still runs as the space's acting account, and so does work of a principal
that never was a person (work from before Spaces).

Control: the rule of before, in which the departed person's scheduled job
fired as the admin, their swarm task's worker ran as the admin, and their
booked X post was posted as the admin.
"""

from __future__ import annotations

import asyncio
import io
from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bind_scope, current_scope

TEAM_NAME = "Team"


@pytest.fixture
def team(monkeypatch):
    from kazma_core.security.vault import reset_posture_cache

    monkeypatch.setattr(scope, "_enforced", True)
    get_config_store().delete(identity.REGISTRY_KEY)
    reem = people.create_person("Reem", "a-long-enough-password", by=OWNER_ACCOUNT_ID)["id"]
    space = people.create_shared_space(TEAM_NAME, by=OWNER_ACCOUNT_ID)["id"]
    people.add_member(space, reem, by=OWNER_ACCOUNT_ID)
    try:
        yield {"space": space, "reem": reem}
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)
        reset_posture_cache()


def _gone(team) -> None:
    identity.remove_member(team["space"], team["reem"])


def _work_scope_of_before(space_id: str, asker: str | None) -> tuple[str, str] | None:
    """``identity.work_scope`` as it was before the review."""
    space = str(space_id or "")
    registry = identity.read_registry()
    if space not in (registry.get("spaces") or {}):
        return None
    who = str(asker or "")
    member = any(m.get("account") == who and m.get("space") == space for m in registry.get("memberships") or ())
    return space, (who if who and member else identity.acting_account(space))


# ── the rule ────────────────────────────────────────────────────────────


def test_the_rule(team) -> None:
    space, reem = team["space"], team["reem"]
    assert identity.work_scope(space, reem) == (space, reem)
    assert identity.work_scope(space, None) == (space, OWNER_ACCOUNT_ID), "no person asked: the space's own"
    assert identity.work_scope(space, "operator") == (space, OWNER_ACCOUNT_ID), "never a person"
    _gone(team)
    with pytest.raises(identity.AskerLeft):
        identity.work_scope(space, reem)
    assert identity.asker_left(space, reem)
    assert identity.work_scope(space, None) == (space, OWNER_ACCOUNT_ID)
    people.delete_person(reem, by=OWNER_ACCOUNT_ID)
    with pytest.raises(identity.AskerLeft):
        identity.work_scope(space, reem)  # a deleted account too


# ── Scheduled ───────────────────────────────────────────────────────────


def _job(team) -> Any:
    from kazma_core.cron.store import ScheduledJob

    return ScheduledJob(job_id="j1", prompt="Remind me", timing="in 5 minutes", platform="web",
                        thread_id="t1", tenant_id=team["space"], creator=team["reem"])


def test_a_departed_persons_scheduled_job_does_not_fire(team) -> None:
    from kazma_core.cron.scheduler import _fire_binding

    _gone(team)
    binding = _fire_binding(_job(team))
    assert binding.refusal == "asker_left" and binding.asker != OWNER_ACCOUNT_ID, binding


def test_control_before_their_job_fired_as_the_admin(team, monkeypatch) -> None:
    from kazma_core.cron.scheduler import _fire_binding

    _gone(team)
    monkeypatch.setattr(identity, "work_scope", _work_scope_of_before)
    binding = _fire_binding(_job(team))
    assert binding.refusal == "" and binding.asker == OWNER_ACCOUNT_ID, binding


# ── the swarm ───────────────────────────────────────────────────────────


def test_a_departed_persons_swarm_task_runs_no_worker(team) -> None:
    from kazma_core.swarm import worker_dispatch

    _gone(team)
    with pytest.raises(worker_dispatch._TaskOfNoSpace, match="no longer in its space"):
        worker_dispatch._worker_scope(team["space"], team["reem"])


def test_control_before_their_worker_ran_as_the_admin(team, monkeypatch) -> None:
    from kazma_core.swarm import worker_dispatch

    _gone(team)
    monkeypatch.setattr(identity, "work_scope", _work_scope_of_before)
    with worker_dispatch._worker_scope(team["space"], team["reem"])():
        assert current_scope().asker == OWNER_ACCOUNT_ID


# ── X ───────────────────────────────────────────────────────────────────


def test_a_departed_persons_booked_post_is_refused(team) -> None:
    from kazma_core.spaces.scope import NoSpaceBound
    from kazma_core.x_api.ownership import x_work_scope

    _gone(team)
    with pytest.raises(NoSpaceBound, match="no longer in its space"):
        x_work_scope(team["space"], team["reem"])


def test_control_before_their_post_went_out_as_the_admin(team, monkeypatch) -> None:
    from kazma_core.x_api.ownership import x_work_scope

    _gone(team)
    monkeypatch.setattr(identity, "work_scope", _work_scope_of_before)
    with x_work_scope(team["space"], team["reem"])():
        assert current_scope().asker == OWNER_ACCOUNT_ID


# ── documents ───────────────────────────────────────────────────────────


def test_a_departed_persons_document_job_is_refused_in_the_real_worker(team, tmp_path) -> None:
    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.ingestion import DocumentIngestionService
    from kazma_core.documents.worker import DocumentWorker

    svc = DocumentIngestionService(config=DocumentConfig(
        storage_root=tmp_path / "documents", security_malware_scan="off", security_local_salvage=False,
        ocr_enabled=False))
    svc.jobs._jitter = lambda _base: 0.0
    try:
        with bind_scope(team["space"], team["reem"]):
            result = svc.ingest_stream(io.BytesIO(b"tides of the Gulf\n"), filename="tides.txt",
                                       tenant_id=team["space"], actor_id=team["reem"], workspace_id="w")
        _gone(team)
        claimed = svc.jobs.claim_next(owner="worker", lease_seconds=60)
        asyncio.run(DocumentWorker(svc.jobs, svc.stage_handlers())._process_claimed(claimed, "worker"))
        job = svc.jobs.get(tenant_id=team["space"], job_id=result.job_id)
        assert job.error_code == "asker_left", job
    finally:
        svc.close()


def test_work_of_the_owners_own_space_is_never_refused_so(team) -> None:
    from kazma_core.spaces.scope import OWNER_SPACE_ID

    assert not identity.asker_left(OWNER_SPACE_ID, "operator")
    assert not identity.asker_left(OWNER_SPACE_ID, team["reem"])
