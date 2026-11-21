"""Web Push: a finished answer goes to its own person's devices only (Spaces).

Every browser that subscribes is recorded with the account it is signed in
as. On an install that enforces Spaces a finished turn is pushed only to the
devices of its thread's opener -- the thread's record; a thread from before
Spaces is the owner's -- and only while that person is still a member of
the thread's space. A device that names no account gets nothing there.

Two people with their own spaces and the admin each subscribe a device
through the real route. A turn finishing in the first person's chat reaches
the first person's device alone, through the delivery broker's own terminal
hook; the owner's reaches the owner's alone.

Control: the plan of before -- every stored device, whatever the thread --
sends the first person's answer to the second person's and the owner's
devices.
"""

from __future__ import annotations

import asyncio
import types
import uuid
from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, people, scope, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests._module_stubs import stub_modules

ANSWER_WORD = "plantedanswer" + uuid.uuid4().hex[:6]
OWNER_WORD = "plantedowner" + uuid.uuid4().hex[:6]


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from kazma_ui.app import create_app

    with TestClient(create_app(), client=("127.0.0.1", 40000), base_url="http://127.0.0.1:9090") as client:
        yield client


def _as(monkeypatch, principal: dict[str, Any]) -> None:
    from kazma_ui import auth

    monkeypatch.setattr(auth, "get_request_principal", lambda request: principal)


def _admin() -> dict[str, Any]:
    return {"source": "secret", "role": "admin", "user_id": "", "actor": "secret"}


def _person(person: dict[str, Any]) -> dict[str, Any]:
    return {"source": "session", "role": "operator", "user_id": person["id"], "actor": "login"}


@pytest.fixture
def sent(monkeypatch):
    """A pywebpush that records each push by its endpoint, VAPID keys, and
    name lookups answered locally as a public address."""
    from kazma_core.security import ssrf
    from kazma_ui import push

    calls: list[dict[str, Any]] = []

    class WebPushException(Exception):
        pass

    fake = types.ModuleType("pywebpush")
    fake.webpush = lambda **kw: calls.append(kw)
    fake.WebPushException = WebPushException
    monkeypatch.setattr(push, "ensure_vapid_keys", lambda: ("pub", "priv"))
    monkeypatch.setattr(push, "push_available", lambda: True)
    monkeypatch.setattr(ssrf, "_resolve_host_ips", lambda host: ["93.184.216.34"])
    get_config_store().delete(push._SUBS_KEY)
    with stub_modules({"pywebpush": fake}):
        yield calls
    get_config_store().delete(push._SUBS_KEY)


def _endpoints(calls: list[dict[str, Any]], word: str) -> set[str]:
    return {c["subscription_info"]["endpoint"] for c in calls if word in c["data"]}


@pytest.fixture
def world(client, sent, monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)
    one = people.create_person("push-one", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    two = people.create_person("push-two", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    devices = {}
    for who, principal, space in (
        ("one", _person(one), one["personal_space"]),
        ("two", _person(two), two["personal_space"]),
        ("owner", _admin(), OWNER_SPACE_ID),
    ):
        _as(monkeypatch, principal)
        endpoint = f"https://push.example/{who}-{uuid.uuid4().hex[:6]}"
        answer = client.post(
            "/api/push/subscribe",
            json={"subscription": {"endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}}},
            headers={"X-Kazma-Space": space},
        )
        assert answer.status_code == 200 and answer.json().get("status") == "ok", answer.text
        devices[who] = endpoint
    one_thread = "push-" + uuid.uuid4().hex
    threads.record_thread(one_thread, one["personal_space"], one["id"])
    try:
        yield {"one": one, "two": two, "devices": devices, "one_thread": one_thread}
    finally:
        threads.forget_thread(one_thread)
        get_config_store().delete(identity.REGISTRY_KEY)


def _push(summary: str, thread_id: str | None) -> int:
    from kazma_ui.push import notify_push_turn_complete

    return asyncio.run(notify_push_turn_complete(summary, thread_id=thread_id))


def test_each_subscription_records_its_account(world) -> None:
    from kazma_ui.push import list_subscriptions

    by_endpoint = {s["endpoint"]: s.get("_account") for s in list_subscriptions()}
    assert by_endpoint[world["devices"]["one"]] == world["one"]["id"]
    assert by_endpoint[world["devices"]["two"]] == world["two"]["id"]
    assert by_endpoint[world["devices"]["owner"]] == OWNER_ACCOUNT_ID


def test_a_finished_answer_reaches_its_own_persons_device_only(world, sent) -> None:
    assert _push(f"the answer {ANSWER_WORD}", world["one_thread"]) == 1
    assert _endpoints(sent, ANSWER_WORD) == {world["devices"]["one"]}


def test_the_owners_answer_reaches_the_owners_device_only(world, sent) -> None:
    # A thread with no record is from before Spaces: the owner's.
    assert _push(f"the owner's answer {OWNER_WORD}", "push-unrecorded-" + uuid.uuid4().hex) == 1
    assert _endpoints(sent, OWNER_WORD) == {world["devices"]["owner"]}


def test_the_delivery_brokers_terminal_frame_pushes_to_the_opener_only(
    world, sent, monkeypatch
) -> None:
    """The one choke point both transports flow through: a done frame on the
    first person's thread."""
    from kazma_ui import delivery

    pending: list[asyncio.Task] = []
    monkeypatch.setattr(delivery, "spawn_background",
                        lambda coro, name=None: pending.append(asyncio.ensure_future(coro)))

    async def _finish() -> None:
        broker = delivery.TurnBroker()
        await broker.emit(world["one_thread"], {"type": "done",
                                               "data": {"content": f"done {ANSWER_WORD}"}})
        await asyncio.gather(*pending)

    asyncio.run(_finish())
    assert _endpoints(sent, ANSWER_WORD) == {world["devices"]["one"]}


def test_a_turn_with_no_thread_reaches_nobody(world, sent) -> None:
    assert _push(f"no thread {ANSWER_WORD}", None) == 0
    assert sent == []


def test_a_device_with_no_account_gets_nothing(world, sent) -> None:
    from kazma_ui.push import subscribe

    anonymous = "https://push.example/anon-" + uuid.uuid4().hex[:6]
    assert subscribe({"endpoint": anonymous, "keys": {"p256dh": "k", "auth": "a"}})["status"] == "ok"
    _push(f"the owner's answer {OWNER_WORD}", "push-unrecorded-" + uuid.uuid4().hex)
    assert anonymous not in _endpoints(sent, OWNER_WORD)


def test_a_deleted_person_hears_nothing_more(world, sent) -> None:
    people.delete_person(world["one"]["id"], by=OWNER_ACCOUNT_ID)
    assert _push(f"after leaving {ANSWER_WORD}", world["one_thread"]) == 0
    assert sent == []


def test_not_enforced_every_device_hears_as_before(world, sent, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    assert _push(f"one account {ANSWER_WORD}", world["one_thread"]) == 3
    assert _endpoints(sent, ANSWER_WORD) == set(world["devices"].values())


def test_control_the_plan_of_before_pushes_one_persons_answer_to_everyone(
    world, sent, monkeypatch
) -> None:
    """Every stored device, whatever the thread: the second person and the
    owner get the first person's answer."""
    from kazma_ui import push

    def _old_plan(thread_id=None):
        return push.ensure_vapid_keys(), push.list_subscriptions()

    monkeypatch.setattr(push, "_push_plan", _old_plan)
    _push(f"the answer {ANSWER_WORD}", world["one_thread"])
    heard = _endpoints(sent, ANSWER_WORD)
    assert world["devices"]["two"] in heard and world["devices"]["owner"] in heard


def test_the_sender_asks_whose_thread_before_it_sends() -> None:
    """Source gate: every push goes through ``_push_plan`` with the turn's
    thread, and the delivery broker hands its thread over; nothing else in
    the product reads the stored devices to send to them."""
    import inspect
    from pathlib import Path

    from kazma_ui import delivery, push

    sender = inspect.getsource(push.notify_push_turn_complete)
    assert "_push_plan, thread_id" in sender
    assert "thread_id=thread_id" in inspect.getsource(delivery.TurnBroker._push_terminal)
    root = Path(__file__).resolve().parents[1]
    readers = []
    for folder in ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-tui"):
        for path in (root / folder).rglob("*.py"):
            if "tests" in path.parts or path.name == "push.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "notifications.push.subscriptions" in text or "list_subscriptions(" in text:
                readers.append(path.relative_to(root).as_posix())
    # The settings rules name the key; nothing else reads it.
    assert set(readers) <= {
        "kazma-core/kazma_core/settings_restore.py",
        "kazma-core/kazma_core/settings_scope.py",
    }, readers
