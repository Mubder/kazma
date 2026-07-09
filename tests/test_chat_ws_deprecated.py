"""S4: WebSocket chat path is deprecated (410) — use SSE for HITL."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_chat_websocket_handler_closes_410():
    from kazma_ui.chat import chat_websocket_handler

    ws = MagicMock()
    ws.close = AsyncMock()
    agent = MagicMock()

    with pytest.warns(DeprecationWarning, match="deprecated"):
        await chat_websocket_handler(ws, agent)

    ws.close.assert_awaited_once()
    kwargs = ws.close.await_args.kwargs
    assert kwargs.get("code") == 4100
    assert "stream" in (kwargs.get("reason") or "").lower()
