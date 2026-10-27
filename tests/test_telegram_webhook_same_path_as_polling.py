"""A Telegram update that arrives by webhook is handled as a polled one is
(audit 2026-10-26 AUD-065).

The webhook route parsed the update itself: it skipped the redelivery and
edit dedupe, the ``/cmd@otherbot`` normalization, the receive record, voice
and media, and callback queries -- so the approval buttons did nothing in
webhook mode. It now hands the update to ``TelegramAdapter._dispatch_update``,
the polling loop's own entry, after the secret check. The negative control
is the old route's handling, kept below, which fails each property.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

SECRET = "demo-webhook-secret"


def _adapter() -> Any:
    from kazma_gateway.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter(token="123:abc", allowed_users=[7], webhook_secret=SECRET)
    adapter._bot_username = "kazmabot"

    async def no_announce(update: dict) -> None:
        return None

    async def no_reaction(*_a: Any, **_k: Any) -> None:
        return None

    adapter._announce_if_added_to_group = no_announce  # type: ignore[method-assign]
    adapter._set_reaction = no_reaction  # type: ignore[method-assign]
    return adapter


def _message(uid: int, mid: int, text: str, *, user: int = 7) -> dict[str, Any]:
    return {
        "update_id": uid,
        "message": {
            "message_id": mid,
            "chat": {"id": user, "type": "private"},
            "from": {"id": user, "is_bot": False, "first_name": "B"},
            "date": 1,
            "text": text,
        },
    }


def _callback(uid: int) -> dict[str, Any]:
    return {
        "update_id": uid,
        "callback_query": {
            "id": "cb1",
            "from": {"id": 7, "is_bot": False},
            "data": "hitl:approve:abc",
            "message": {"message_id": 9, "chat": {"id": 7, "type": "private"}},
        },
    }


async def _post_all(adapter: Any, updates: list[dict[str, Any]], *, secret: str = SECRET) -> list[int]:
    from fastapi import FastAPI
    from kazma_gateway.adapters.telegram import telegram_webhook_router

    app = FastAPI()
    app.include_router(telegram_webhook_router(lambda: adapter), prefix="/hook")
    codes: list[int] = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for update in updates:
            resp = await client.post(
                "/hook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": secret}
            )
            codes.append(resp.status_code)
            # Let the update's processing finish before the next one, as
            # Telegram's own pacing would.
            for _ in range(20):
                pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                if not pending:
                    break
                await asyncio.gather(*pending, return_exceptions=True)
    return codes


def _drain(queue: asyncio.Queue) -> list[str]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait().text)
    return out


def test_a_webhook_update_is_deduped_normalized_and_recorded() -> None:
    adapter = _adapter()
    queue: asyncio.Queue = asyncio.Queue()
    adapter._queue = queue
    updates = [
        _message(1, 1, "hello"),
        _message(2, 1, "hello"),            # the same message again (an edit)
        _message(3, 2, "/status@otherbot"),  # another bot's command
        _message(4, 3, "/status@kazmabot"),  # ours, addressed
    ]
    codes = asyncio.run(_post_all(adapter, updates))
    assert codes == [200, 200, 200, 200]
    assert _drain(queue) == ["hello", "/status"]
    snap = adapter.diagnostics()
    assert snap["messages"] == 4
    assert snap["dropped"] == {"duplicate": 1, "other_bot": 1}
    assert snap["passed_on"] + sum(snap["dropped"].values()) == snap["messages"]


def test_an_approval_button_pressed_in_webhook_mode_is_handled() -> None:
    adapter = _adapter()
    adapter._queue = asyncio.Queue()
    pressed: list[dict[str, Any]] = []

    async def handle(callback_query: dict[str, Any]) -> None:
        pressed.append(callback_query)

    adapter._handle_callback_query = handle  # type: ignore[method-assign]
    asyncio.run(_post_all(adapter, [_callback(10)]))
    assert [c["data"] for c in pressed] == ["hitl:approve:abc"]


def test_the_secret_is_checked_before_anything_is_dispatched() -> None:
    adapter = _adapter()
    adapter._queue = asyncio.Queue()
    seen: list[dict] = []
    adapter._dispatch_update = lambda update, queue: seen.append(update) or True  # type: ignore[method-assign]
    codes = asyncio.run(_post_all(adapter, [_message(1, 1, "hi")], secret="wrong-secret"))
    assert codes == [401] and seen == []


def test_the_polling_loop_and_the_webhook_share_one_entry() -> None:
    import inspect

    from kazma_gateway.adapters import telegram

    listen = inspect.getsource(telegram.TelegramAdapter.listen)
    route = inspect.getsource(telegram.telegram_webhook_router)
    assert "self._dispatch_update(update, queue)" in listen
    assert "adapter._dispatch_update(" in route
    # The route no longer parses or enqueues on its own.
    assert "_parse_update(" not in route and "put_nowait(" not in route


# ── Negative control: the old route's own handling ─────────────────────


def _old_webhook_ingest(adapter: Any, update: dict[str, Any]) -> None:
    """What the route did before AUD-065 (parse, allowlist, enqueue)."""
    msg = adapter._parse_update(update)
    if msg is None:
        return
    if not adapter.actor_allowed(msg.context_metadata.get("user_id", 0)):
        return
    adapter._queue.put_nowait(msg)


def test_negative_control_the_old_route_fails_each_property() -> None:
    adapter = _adapter()
    queue: asyncio.Queue = asyncio.Queue()
    adapter._queue = queue
    pressed: list[dict[str, Any]] = []

    async def handle(callback_query: dict[str, Any]) -> None:
        pressed.append(callback_query)

    adapter._handle_callback_query = handle  # type: ignore[method-assign]
    for update in (
        _message(1, 1, "hello"),
        _message(2, 1, "hello"),
        _message(3, 2, "/status@otherbot"),
        _callback(4),
    ):
        _old_webhook_ingest(adapter, update)
    texts = _drain(queue)
    # Not deduped, another bot's command handed on, nothing recorded, and
    # the button never reached its handler.
    assert texts == ["hello", "hello", "/status@otherbot"]
    assert adapter.diagnostics()["messages"] == 0
    assert pressed == []
