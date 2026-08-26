"""Offset-commit tests for the Telegram adapter (at-least-once delivery).

The committed getUpdates offset advances only after an update's chain
task completes; uncommitted updates are redelivered by Telegram while an
earlier chain is still in flight and must be skipped at dispatch.
"""

from __future__ import annotations

import asyncio

from kazma_gateway.adapters.telegram import TelegramAdapter


def _mk_adapter() -> TelegramAdapter:
    return TelegramAdapter(token="0:test", allow_all=True)


def _text_update(update_id: int, chat_id: int, message_id: int) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "text": f"m{message_id}",
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": 7, "username": "alice"},
        },
    }


async def _settle() -> None:
    """Let dispatched chain tasks run to completion (a few loop ticks)."""
    for _ in range(20):
        await asyncio.sleep(0)


async def _noop_reaction(chat_id, message_id, emoji) -> None:  # noqa: ANN001
    return None


async def test_offset_advances_only_after_chain_completion(monkeypatch):
    adapter = _mk_adapter()
    monkeypatch.setattr(adapter, "_set_reaction", _noop_reaction)
    queue: asyncio.Queue = asyncio.Queue()

    adapter._max_seen_update_id = 2  # normally recorded by _poll
    adapter._dispatch_update_to_chain(_text_update(1, 100, 11), queue)
    adapter._dispatch_update_to_chain(_text_update(2, 200, 22), queue)
    # Chains dispatched but not completed — offset must NOT advance.
    assert adapter._offset == 0

    await _settle()
    assert queue.qsize() == 2
    assert adapter._offset == 2  # max completed update_id


async def test_offset_holds_behind_earlier_pending_update(monkeypatch):
    """A finished later update must not commit past a running earlier one."""
    adapter = _mk_adapter()
    gate = asyncio.Event()

    async def gated_process(update, queue):  # noqa: ANN001
        if update["update_id"] == 1:
            await gate.wait()

    monkeypatch.setattr(adapter, "_process_update", gated_process)
    queue: asyncio.Queue = asyncio.Queue()

    adapter._max_seen_update_id = 3  # normally recorded by _poll
    adapter._dispatch_update_to_chain(_text_update(1, 100, 11), queue)
    adapter._dispatch_update_to_chain(_text_update(2, 200, 22), queue)  # chat B — free
    adapter._dispatch_update_to_chain(_text_update(3, 100, 12), queue)  # chat A — behind 1
    await _settle()
    # Update 2 finished, but 1 (and chained 3) still run: offset holds.
    assert adapter._offset == 0

    gate.set()
    await _settle()
    assert adapter._offset == 3


async def test_redelivered_uncommitted_update_is_skipped(monkeypatch):
    """Telegram redelivers unconfirmed updates — dispatch must dedup."""
    adapter = _mk_adapter()
    gate = asyncio.Event()
    processed: list[int] = []

    async def gated_process(update, queue):  # noqa: ANN001
        processed.append(update["update_id"])
        if update["update_id"] == 1:
            await gate.wait()

    monkeypatch.setattr(adapter, "_process_update", gated_process)
    queue: asyncio.Queue = asyncio.Queue()

    adapter._max_seen_update_id = 1  # normally recorded by _poll
    upd = _text_update(1, 100, 11)
    adapter._dispatch_update_to_chain(upd, queue)
    await asyncio.sleep(0)  # chain started, gated
    assert 1 in adapter._unacked_updates

    # Redelivery poll before commit — must not spawn a second chain.
    adapter._dispatch_update_to_chain(upd, queue)
    gate.set()
    await _settle()

    assert processed == [1]
    assert adapter._offset == 1
    assert 1 not in adapter._unacked_updates


async def test_failed_chain_still_commits_offset(monkeypatch):
    """A raising chain must still commit — redelivery would poison-loop."""
    adapter = _mk_adapter()

    async def boom(update, queue):  # noqa: ANN001
        raise RuntimeError("poison update")

    monkeypatch.setattr(adapter, "_process_update", boom)
    adapter._max_seen_update_id = 1  # normally recorded by _poll
    adapter._dispatch_update_to_chain(_text_update(1, 100, 11), asyncio.Queue())
    await _settle()
    assert adapter._offset == 1


async def test_poll_confirms_only_committed_offset():
    """getUpdates must use committed+1 and never advance on read."""
    adapter = _mk_adapter()
    adapter._offset = 7  # committed prefix
    captured: dict = {}

    class FakeResp:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {"ok": True, "result": [{"update_id": 8}, {"update_id": 9}]}

    class FakeHttp:
        async def get(self, path, params=None):  # noqa: ANN001
            captured["path"] = path
            captured["params"] = params
            return FakeResp()

    adapter._http = FakeHttp()  # type: ignore[assignment]
    updates = await adapter._poll()

    assert captured["path"] == "/getUpdates"
    assert captured["params"]["offset"] == 8  # committed + 1
    assert [u["update_id"] for u in updates] == [8, 9]
    assert adapter._offset == 7  # read does NOT commit
    assert adapter._max_seen_update_id == 9


async def test_queue_full_sends_busy_notice(monkeypatch):
    """A dropped message must produce immediate user feedback."""
    adapter = _mk_adapter()
    sent: list[tuple[str, dict]] = []

    class FakeHttp:
        async def post(self, path, json=None):  # noqa: ANN001
            sent.append((path, json))
            return None

    monkeypatch.setattr(adapter, "_ensure_http", lambda: FakeHttp())
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    queue.put_nowait(object())  # full

    await adapter._process_update(_text_update(1, 100, 11), queue)

    assert len(sent) == 1
    path, payload = sent[0]
    assert path == "/sendMessage"
    assert payload["chat_id"] == 100
    assert "resend" in payload["text"]
