"""Stage 3 review, L8: a scheduled message whose chat is no longer its
person's ends quietly, recorded dropped.

Once Spaces is enforced, a chat whose chat-app account was unlinked (or
whose account was deleted or disabled) is dropped by the gateway (S3-07,
``chat_links.address_revoked``). The scheduler took that refusal for a
platform's: it retried the reminder for two hours and then paged the admin
that a deleted member's job was undeliverable. Now the delivery pass asks
the same question first: the delivery ends ``dropped``, the job is done with
nothing owed, nothing is sent, nothing is paged.

Through the real scheduler and cron store: Dana's reminder to her own linked
Telegram chat is refused once by the platform (it waits for another try);
she unlinks; the next pass ends it dropped.

Negative control: the delivery pass of before (no check) -- the reminder is
retried, ends undeliverable and pages the admin.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import kazma_core.cron.scheduler as scheduler_mod
import kazma_core.cron.store as store_mod
import pytest
from kazma_core.config_store import get_config_store
from kazma_core.cron.scheduler import CronScheduler, JobStatus, ScheduledJob, SQLiteCronStore
from kazma_core.spaces import chat_apps, chat_links, identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

PASSWORD = "a-long-enough-password"


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    chat_links._forget_attempts_for_tests()
    dana = people.create_person(f"dana-{uuid4().hex[:6]}", PASSWORD, by=OWNER_ACCOUNT_ID)
    uid = f"9{uuid4().int % 10**8}"
    code = chat_links.issue_code(dana["id"], "telegram")
    assert chat_links.redeem("telegram", uid, code["code"], direct=True,
                             target=f"telegram:{uid}").outcome == "linked"

    sm = importlib.import_module("kazma_core.tools.send_message")
    ops = importlib.import_module("kazma_core.observability.ops_alerts")
    state: dict[str, Any] = {"dana": dana, "uid": uid, "chat": f"telegram:{uid}",
                             "sent": [], "alerts": [], "refuse": 1}

    async def backend(target_id, text, **kw):
        state["sent"].append((target_id, text))
        if state["refuse"]:
            state["refuse"] -= 1
            return f"Error: telegram did not take the message for {target_id}"
        return f"sent:{target_id}"

    def alert(key, title, detail="", **kw):
        state["alerts"].append(key)
        return True

    monkeypatch.setitem(sm._message_backends, "telegram", backend)
    monkeypatch.setattr(ops, "alert", alert)
    monkeypatch.setattr(store_mod, "_delivery_backoff", lambda attempts: 0.0)
    yield state
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    chat_links._forget_links_for_tests()


async def _job(store) -> ScheduledJob:
    """The job as stored, read in the space it was booked in (the owner's: it
    was booked as on an install with one account), as a store read keeps to
    a bound space once Spaces is enforced."""
    with scope.bind_scope(scope.OWNER_SPACE_ID, scope.OWNER_ACCOUNT_ID):
        return await store.get("cron-drop")


async def _waiting_reminder(tmp_path, w, monkeypatch):
    """Dana's reminder to her own chat, refused once: waiting for another try."""
    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
    await store.init()
    # The booking and the fire are not what is tested: they run as on an
    # install with one account.
    monkeypatch.setattr(scope, "_enforced", False)
    await store.insert(ScheduledJob(
        job_id="cron-drop", timing="2026-11-23T09:00", prompt="water the beds", platform="telegram",
        thread_id="t", next_run=(datetime.now(UTC) - timedelta(seconds=5)).isoformat(),
        kind="reminder", delivery_target=w["chat"]))
    sched = CronScheduler(store=store, graph_builder=lambda: None)
    await sched._execute(await _job(store))
    job = await _job(store)
    assert job.status == JobStatus.DELIVERING and job.delivery_state == "retrying"
    monkeypatch.setattr(scope, "_enforced", True)
    return store, sched


async def _drain(sched, n=10):
    for _ in range(n):
        await sched._drain_deliveries()
        await sched._drain_alerts()


async def test_an_unlinked_chat_s_reminder_ends_dropped_quietly(tmp_path, world, monkeypatch) -> None:
    store, sched = await _waiting_reminder(tmp_path, world, monkeypatch)
    try:
        sent_before = len(world["sent"])
        assert chat_links.unlink(world["dana"]["id"], "telegram", world["uid"], by=world["dana"]["id"])
        await _drain(sched)
        job = await _job(store)
        assert job.delivery_state == "dropped", "the reminder was not ended dropped"
        assert job.status == JobStatus.DONE
        assert len(world["sent"]) == sent_before, "the reminder was sent to a chat no longer linked"
        assert not world["alerts"], f"the admin was paged about a dropped reminder: {world['alerts']}"
    finally:
        await store.close()


async def test_a_linked_chat_s_reminder_is_still_delivered(tmp_path, world, monkeypatch) -> None:
    store, sched = await _waiting_reminder(tmp_path, world, monkeypatch)
    try:
        await _drain(sched)
        job = await _job(store)
        assert job.delivery_state == "delivered" and job.status == JobStatus.DONE
        assert not world["alerts"]
    finally:
        await store.close()


async def test_negative_control_the_pass_of_before_retries_and_pages(tmp_path, world, monkeypatch) -> None:
    """Planted: the delivery pass without the check. The gateway's refusal
    looks like a platform's: retried, then undeliverable, then paged."""
    world["refuse"] = 99
    monkeypatch.setattr(scheduler_mod, "_dropped_for_its_person", lambda target: False)
    with pytest.raises(AssertionError, match="was not ended dropped"):
        await test_an_unlinked_chat_s_reminder_ends_dropped_quietly(tmp_path, world, monkeypatch)
    assert "cron.delivery_failed" in world["alerts"], "the control must reproduce the page"
