"""HTTP mouth — consume ``POST /api/chat/stream`` (the Web brain)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["ChatStreamEvent", "iter_sse_frames", "stream_chat_turn"]


class ChatStreamEvent:
    __slots__ = ("kind", "text", "tool", "data")

    def __init__(
        self,
        kind: str,
        *,
        text: str = "",
        tool: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        self.kind = kind
        self.text = text
        self.tool = tool
        self.data = data or {}


def iter_sse_frames(lines: list[str]) -> list[ChatStreamEvent]:
    """Parse raw SSE lines into events (sync helper for tests)."""
    events: list[ChatStreamEvent] = []
    event = "message"
    data_parts: list[str] = []

    def _flush() -> None:
        nonlocal event, data_parts
        if not data_parts:
            event = "message"
            return
        raw = "\n".join(data_parts)
        data_parts = []
        kind = event or "message"
        event = "message"
        payload: Any
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"content": raw}
        if not isinstance(payload, dict):
            payload = {"content": str(payload)}
        if kind == "token":
            events.append(ChatStreamEvent("token", text=str(payload.get("content") or "")))
        elif kind == "tool_call":
            events.append(
                ChatStreamEvent(
                    "tool_call",
                    tool=str(payload.get("tool_name") or payload.get("tool") or ""),
                    data=payload,
                )
            )
        elif kind == "tool_result":
            events.append(
                ChatStreamEvent(
                    "tool_result",
                    tool=str(payload.get("tool_name") or payload.get("tool") or ""),
                    text=str(payload.get("result") or payload.get("content") or "")[:400],
                    data=payload,
                )
            )
        elif kind == "error":
            events.append(ChatStreamEvent("error", text=str(payload.get("content") or raw)))
        elif kind == "done":
            events.append(ChatStreamEvent("done", data=payload))
        else:
            content = str(payload.get("content") or "")
            if content:
                events.append(ChatStreamEvent(kind, text=content, data=payload))

    for line in lines:
        if line == "":
            _flush()
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_parts.append(line[5:].lstrip())
    _flush()
    return events


async def stream_chat_turn(
    *,
    text: str,
    session_id: str,
    on_event: Callable[[ChatStreamEvent], Any] | None = None,
) -> str:
    """POST one user turn to the live Kazma server. Returns assembled text.

    Raises ``RuntimeError`` if no local API accepts the request.
    """
    import httpx

    from kazma_core.runtime.local_api import auth_headers, candidate_api_bases

    headers = {"Accept": "text/event-stream", **auth_headers()}
    body = {"message": text, "session_id": session_id}
    last_err = "no API candidates"
    assembled: list[str] = []

    for base in candidate_api_bases():
        url = f"{base}/api/chat/stream"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=2.0)) as client:
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code in (401, 403):
                        last_err = (
                            f"{url} returned {resp.status_code} — set KAZMA_SECRET "
                            "to the same secret the server is using."
                        )
                        continue
                    if resp.status_code >= 400:
                        last_err = f"{url} returned {resp.status_code}"
                        continue
                    buf: list[str] = []
                    async for line in resp.aiter_lines():
                        buf.append(line)
                        if line != "":
                            continue
                        for ev in iter_sse_frames(buf):
                            if ev.kind == "token" and ev.text:
                                assembled.append(ev.text)
                            if on_event is not None:
                                on_event(ev)
                            if ev.kind == "error" and ev.text:
                                raise RuntimeError(ev.text)
                        buf = []
                    return "".join(assembled)
        except httpx.ConnectError:
            last_err = f"nothing listening at {base}"
            continue
        except httpx.TimeoutException:
            last_err = f"timeout talking to {base}"
            continue
    raise RuntimeError(
        "Kazma server is not reachable. Start it with `kazma serve` "
        f"(tried {', '.join(candidate_api_bases())}). Last error: {last_err}"
    )
