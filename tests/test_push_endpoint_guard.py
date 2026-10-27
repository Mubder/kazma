"""AUD-103: a push subscription cannot point the server at an internal host,
and concurrent subscription changes are not lost.

``subscribe`` stored any URL a browser sent and every finished turn POSTed to
it, so ``{"endpoint": "http://169.254.169.254/..."}`` made the server send
requests to the metadata service or another internal address. And
subscribe, unsubscribe and the 410 prune each loaded the list, changed it and
saved it: two at once lost one of the changes.
"""

from __future__ import annotations

import asyncio
import json
import threading
import types

import pytest

from tests._module_stubs import stub_modules

_PUBLIC = "93.184.216.34"


@pytest.fixture
def dns(monkeypatch):
    """Answer name lookups locally: one internal name, the rest public."""
    from kazma_core.security import ssrf

    def resolve(host: str) -> list[str]:
        return ["10.0.0.5"] if host == "internal.example" else [_PUBLIC]

    monkeypatch.setattr(ssrf, "_resolve_host_ips", resolve)


@pytest.fixture
def subs_key():
    from kazma_core.config_store import get_config_store
    from kazma_ui import push

    store = get_config_store()
    store.delete(push._SUBS_KEY)
    yield push._SUBS_KEY
    store.delete(push._SUBS_KEY)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://169.254.169.254/latest/meta-data/",
        "https://127.0.0.1:5432/",
        "https://localhost/push",
        "https://internal.example/push",
        "http://fcm.googleapis.com/fcm/send/abc",  # not https
        "file:///etc/passwd",
    ],
)
def test_an_internal_or_plain_endpoint_is_refused(dns, subs_key, endpoint) -> None:
    from kazma_ui.push import list_subscriptions, subscribe

    out = subscribe({"endpoint": endpoint, "keys": {}})
    assert out["status"] == "error", out
    assert list_subscriptions() == []


def test_a_public_push_service_is_kept(dns, subs_key) -> None:
    from kazma_ui.push import list_subscriptions, subscribe

    out = subscribe({"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {}})
    assert out == {"status": "ok", "count": 1}
    assert [s["endpoint"] for s in list_subscriptions()] == [
        "https://fcm.googleapis.com/fcm/send/abc"
    ]


def test_a_stored_internal_endpoint_is_not_sent_to(dns, subs_key, monkeypatch) -> None:
    """A subscription stored before the check (an older build) is skipped."""
    from kazma_ui import push

    calls: list[dict] = []

    class WebPushException(Exception):
        pass

    fake = types.ModuleType("pywebpush")
    fake.webpush = lambda **kw: calls.append(kw)
    fake.WebPushException = WebPushException
    monkeypatch.setattr(push, "turn_complete_notifications_on", lambda: True)
    monkeypatch.setattr(push, "ensure_vapid_keys", lambda: ("pub", "priv"))
    monkeypatch.setattr(
        push, "list_subscriptions",
        lambda: [
            {"endpoint": "http://169.254.169.254/x", "keys": {}},
            {"endpoint": "https://fcm.googleapis.com/fcm/send/ok", "keys": {}},
        ],
    )
    with stub_modules({"pywebpush": fake}):
        delivered = asyncio.run(push.notify_push_turn_complete("done"))
    assert delivered == 1
    assert [c["subscription_info"]["endpoint"] for c in calls] == [
        "https://fcm.googleapis.com/fcm/send/ok"
    ]


def test_concurrent_subscribes_lose_nothing(dns, subs_key) -> None:
    from kazma_ui.push import list_subscriptions, subscribe

    n = 12
    start = threading.Barrier(n)

    def one(i: int) -> None:
        start.wait()
        subscribe({"endpoint": f"https://fcm.googleapis.com/fcm/send/{i}", "keys": {}})

    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(list_subscriptions()) == n


def test_negative_control_the_old_load_change_save_loses_one(subs_key) -> None:
    """The old shape, two writers interleaved: the second save drops the first."""
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    first = json.loads(store.get(subs_key) or "[]")
    second = json.loads(store.get(subs_key) or "[]")
    first.append({"endpoint": "https://a.example/1"})
    store.set(subs_key, json.dumps(first))
    second.append({"endpoint": "https://a.example/2"})
    store.set(subs_key, json.dumps(second))
    assert len(json.loads(store.get(subs_key))) == 1
