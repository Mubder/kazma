"""Turning off "notify when a task finishes" stops the pushes (2026-09-28).

Settings wrote ``notifications.turn_complete`` and only the page read it: the
chat stopped subscribing new devices and showing its own notification, but
the server's Web Push sender never looked, so every device that had already
subscribed kept getting "Kazma -- task finished" after the owner switched it
off. ``kazma_ui.push.turn_complete_notifications_on`` is now the one reader,
for the page's route and for the sender.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from tests._module_stubs import stub_modules


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """A pywebpush that records what it would send, one subscribed device,
    and VAPID keys."""
    from kazma_ui import push

    calls: list[dict] = []

    class WebPushException(Exception):
        pass

    fake = types.ModuleType("pywebpush")
    fake.webpush = lambda **kw: calls.append(kw)
    fake.WebPushException = WebPushException
    monkeypatch.setattr(push, "ensure_vapid_keys", lambda: ("pub", "priv"))
    monkeypatch.setattr(
        push, "list_subscriptions",
        lambda: [{"endpoint": "https://push.example/1", "keys": {"p256dh": "k", "auth": "a"}}],
    )
    with stub_modules({"pywebpush": fake}):
        yield calls


def _switch(value: str | None) -> None:
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    if value is None:
        store.delete("notifications.turn_complete")
    else:
        store.set("notifications.turn_complete", value, category="notifications")


@pytest.mark.parametrize("value, expected", [(None, 1), ("1", 1), ("0", 0), ("off", 0)])
def test_the_sender_follows_the_switch(sent: list[dict], value: str | None, expected: int) -> None:
    from kazma_ui.push import notify_push_turn_complete

    _switch(value)
    try:
        delivered = asyncio.run(notify_push_turn_complete("The report is ready."))
    finally:
        _switch(None)
    assert delivered == expected
    assert len(sent) == expected


def test_negative_control_a_sender_that_ignores_the_switch_pushes(
    sent: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the reader forced on (the old sender never asked), the same
    switched-off setting still pushes: the test above can tell them apart."""
    from kazma_ui import push

    _switch("0")
    monkeypatch.setattr(push, "turn_complete_notifications_on", lambda: True)
    try:
        assert asyncio.run(push.notify_push_turn_complete("done")) == 1
    finally:
        _switch(None)


def test_the_page_and_the_sender_read_one_switch() -> None:
    """The page's route answers from the same reader the sender asks."""
    import inspect

    from kazma_ui import settings

    src = inspect.getsource(settings)
    route = src[src.index('"/api/notifications/turn-complete"'):]
    route = route[: route.index("@router.", 10)]
    assert "turn_complete_notifications_on()" in route
    assert "config_store.get(" not in route
