"""Spaces S3-05: a web turn's notices go to its asker's own chat app.

A web turn has no chat of its own, so it stamps a delivery address on its
routing block (``send_message.web_gateway_block``, called by the web chat
and live voice inside the request's scope) for a reminder booked in it. The
Scheduled page and the ``schedule_task`` tool take the same address
(``resolve_delivery_target``). Once Spaces is enforced it is the asker's own
linked chat (``chat_links.address_of``: the direct chat a person linked
from), never the owner's for anyone else:

* two people, two targets: the owner's web turn stamps the owner's chat,
  a member's stamps the member's;
* a person who linked no chat app gets no address, and their booking is
  refused (``no_own_address``) saying how to link one;
* a member's reminder is booked with their own address and fires into it:
  N41's refusal is lifted for a person's own address only -- a booking or a
  fire addressed to someone else's chat is still refused;
* before Spaces, every web turn stamps the operator's Telegram, as always.

Negative control: the owner's Telegram stamped on every web turn.
"""

from __future__ import annotations

import asyncio

import pytest
from kazma_core.cron import scheduler as sm
from kazma_core.spaces import chat_links, identity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID
from kazma_core.tools import send_message

from tests._scheduled_space_cases import OWNER_CHAT
from tests._space_matrix import Matrix
from tests.test_cron_spaces import _fire, _store, enforced, matrix, sent  # noqa: F401 -- fixtures

A_TELEGRAM = "730001"
A_CHAT = f"telegram:{A_TELEGRAM}"


@pytest.fixture
def linked(matrix, enforced, monkeypatch) -> Matrix:  # noqa: F811 -- the fixtures above
    """Person A linked to Telegram A_TELEGRAM; the owner's operator chat
    set (the owner's address before Spaces, and their fallback)."""
    from kazma_core.config_store import get_config_store

    monkeypatch.setattr(send_message, "operator_telegram_target", lambda: OWNER_CHAT)

    def link(reg: dict) -> None:
        reg["links"].append({"platform": "telegram", "user_id": A_TELEGRAM,
                             "account": matrix.account_a, "target": A_CHAT})

    identity.update_registry(link)
    chat_links.refresh_links()
    yield matrix
    get_config_store().delete(chat_links.CODES_KEY)
    chat_links._forget_links_for_tests()


def _block_of(space: str, account: str) -> str:
    with scope.bind_scope(space, account):
        return send_message.web_gateway_block(f"web-{account}")["delivery_target"]


# ── the web turn's address ──────────────────────────────────────────────


def test_two_people_two_targets(linked) -> None:
    assert _block_of(OWNER_SPACE_ID, OWNER_ACCOUNT_ID) == OWNER_CHAT
    assert _block_of(Matrix.SPACE_A, linked.account_a) == A_CHAT, "a member's web turn stamped another chat"


def test_the_owner_s_own_link_wins_over_the_operator_chat(linked) -> None:
    def link(reg: dict) -> None:
        reg["links"].append({"platform": "telegram", "user_id": "740002", "account": OWNER_ACCOUNT_ID})

    identity.update_registry(link)
    assert _block_of(OWNER_SPACE_ID, OWNER_ACCOUNT_ID) == "telegram:740002"


def test_a_person_with_no_link_gets_no_address(linked) -> None:
    assert _block_of(Matrix.SPACE_B, linked.account_b) == "", "never the owner's address"


def test_work_that_names_no_person_gets_no_address(linked) -> None:
    assert send_message.resolve_delivery_target() == ""


def test_a_discord_link_is_reached_through_the_chat_it_was_made_from(linked) -> None:
    def link(reg: dict) -> None:
        reg["links"] = [l for l in reg["links"] if l.get("account") != linked.account_a]
        reg["links"].append({"platform": "discord", "user_id": "55", "account": linked.account_a,
                             "target": "discord:9001"})

    identity.update_registry(link)
    assert _block_of(Matrix.SPACE_A, linked.account_a) == "discord:9001"


def test_before_spaces_every_web_turn_stamps_the_operator_telegram(linked, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    assert _block_of(Matrix.SPACE_A, linked.account_a) == OWNER_CHAT


# ── a member's reminder reaches them ────────────────────────────────────


def test_a_member_s_reminder_is_booked_and_fires_into_their_own_chat(linked, sent, tmp_path) -> None:  # noqa: F811
    async def run() -> None:
        store = await _store(tmp_path)
        sched = sm.CronScheduler(store=store, graph_builder=None)
        try:
            with scope.bind_scope(Matrix.SPACE_A, linked.account_a):
                target = send_message.resolve_delivery_target()
                booked = await sched.schedule(timing="2h", prompt="water my plants", kind="reminder",
                                              delivery_target=target)
            job = await store.get(booked["job_id"])
            assert job is not None and job.delivery_target == A_CHAT
            done = await _fire(sched, store, booked["job_id"])
            assert done.last_outcome != "no_own_address", done.last_result
        finally:
            await store.close()

    asyncio.run(run())
    assert [t for t, _ in sent] == [A_CHAT], sent
    assert "water my plants" in sent[0][1]


def test_a_booking_for_someone_else_s_chat_is_still_refused(linked, tmp_path) -> None:
    async def run() -> str:
        store = await _store(tmp_path)
        sched = sm.CronScheduler(store=store, graph_builder=None)
        try:
            with scope.bind_scope(Matrix.SPACE_A, linked.account_a), pytest.raises(sm.CronLimitError) as no:
                await sched.schedule(timing="2h", prompt="a note", kind="reminder",
                                     delivery_target=OWNER_CHAT)
            assert await store.count_jobs() == 0
            return no.value.code
        finally:
            await store.close()

    assert asyncio.run(run()) == "no_own_address"


def test_a_member_with_no_link_is_told_how_to_link(linked, tmp_path) -> None:
    async def run() -> str:
        store = await _store(tmp_path)
        sched = sm.CronScheduler(store=store, graph_builder=None)
        try:
            with scope.bind_scope(Matrix.SPACE_B, linked.account_b), pytest.raises(sm.CronLimitError) as no:
                assert sm.needs_own_address()
                await sched.schedule(timing="2h", prompt="a note", kind="reminder",
                                     delivery_target=send_message.resolve_delivery_target())
            return str(no.value)
        finally:
            await store.close()

    said = asyncio.run(run())
    assert "My account" in said and "Chat apps" in said, said


# ── negative control ────────────────────────────────────────────────────


def test_negative_control_the_owner_s_telegram_on_every_web_turn(linked, monkeypatch) -> None:
    """Planted: the address of before -- the operator's Telegram for every
    web turn. A member's web turn stamps the owner's chat: the check above
    fails on its own assertion."""
    monkeypatch.setattr(send_message, "own_address", lambda: send_message.operator_telegram_target())
    with pytest.raises(AssertionError, match="stamped another chat"):
        test_two_people_two_targets(linked)
