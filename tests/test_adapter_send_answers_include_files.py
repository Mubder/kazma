"""An adapter's and a swarm route's answer is what the platform did (§51).

Audit 2026-10-26 AUD-032. Two places discarded a send's answer:

* each chat adapter's ``send()`` awaited ``_send_attachment`` and dropped its
  ``bool``, then answered True: ``send_file`` said "File sent" over an upload
  the platform refused (a 413);
* the swarm output routes and ``_send_swarm_reply`` discarded
  ``GatewayManager.send``, which answers False when no adapter took the
  message: a result routed to a dead adapter was logged "routed", and the
  direct Telegram fallback never ran.

The gate below reads the source; the behavioural tests drive the real adapters
over a mocked platform. Each has its negative control.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import httpx

from kazma_gateway.gateway import Attachment, OutboundMessage

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "kazma-gateway" / "kazma_gateway"

# Files whose ``manager.send(...)`` answer must be read.
_MANAGER_SEND_FILES = (
    GATEWAY / "agent_handler" / "swarm_output.py",
    GATEWAY / "agent_handler" / "swarm_dispatch.py",
)


# ── the gate ─────────────────────────────────────────────────────────────


def _bare_calls(source: str, matches) -> list[int]:
    """Lines where a matching call is a whole statement (its answer dropped)."""
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Expr):
            continue
        value = node.value.value if isinstance(node.value, ast.Await) else node.value
        if isinstance(value, ast.Call) and matches(value):
            found.append(node.lineno)
    return found


def _is_attachment_send(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr == "_send_attachment"


def _is_manager_send(call: ast.Call) -> bool:
    f = call.func
    return (
        isinstance(f, ast.Attribute) and f.attr == "send"
        and isinstance(f.value, ast.Name) and f.value.id == "manager"
    )


def test_no_adapter_drops_an_attachment_answer() -> None:
    findings: list[str] = []
    for path in sorted((GATEWAY / "adapters").glob("*.py")):
        for line in _bare_calls(path.read_text(encoding="utf-8"), _is_attachment_send):
            findings.append(f"{path.name}:{line}")
    assert findings == [], findings


def test_no_swarm_route_drops_the_manager_answer() -> None:
    findings: list[str] = []
    for path in _MANAGER_SEND_FILES:
        for line in _bare_calls(path.read_text(encoding="utf-8"), _is_manager_send):
            findings.append(f"{path.name}:{line}")
    assert findings == [], findings


def test_the_gate_sees_a_dropped_answer() -> None:
    """Negative control: the shapes the audit found are findings."""
    old_adapter = (
        "async def send(self, outbound):\n"
        "    for att in outbound.attachments:\n"
        "        await self._send_attachment(1, att)\n"
        "    return True\n"
    )
    old_route = (
        "async def route(manager, msg):\n"
        "    await manager.send(msg)\n"
        "    return True\n"
    )
    read = (
        "async def route(manager, msg):\n"
        "    return await manager.send(msg)\n"
    )
    assert _bare_calls(old_adapter, _is_attachment_send) == [3]
    assert _bare_calls(old_route, _is_manager_send) == [2]
    assert _bare_calls(read, _is_manager_send) == []


# ── behaviour: a refused upload fails the send ───────────────────────────


def _file() -> Attachment:
    return Attachment(kind="file", mime="application/pdf", filename="report.pdf", data=b"%PDF-1.4 x")


def _outbound(target: str, text: str = "here is the file") -> OutboundMessage:
    return OutboundMessage(
        target_id=target, text=text, attachments=[_file()],
        context_metadata={"chat_id": target.split(":", 1)[1], "channel_id": target.split(":", 1)[1]},
    )


def test_telegram_send_fails_when_the_upload_is_refused() -> None:
    from kazma_gateway.adapters.telegram import TelegramAdapter

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})
        return httpx.Response(413, json={"ok": False, "description": "Request Entity Too Large"})

    async def run() -> bool:
        adapter = TelegramAdapter(token="123:fake", allow_all=True)
        adapter._http = httpx.AsyncClient(
            base_url="https://api.telegram.org/bot123:fake",
            transport=httpx.MockTransport(handler),
        )
        try:
            return await adapter.send(_outbound("telegram:42"))
        finally:
            await adapter._http.aclose()

    assert asyncio.run(run()) is False


def test_telegram_send_with_a_taken_upload_is_sent() -> None:
    """Control: the same send with the upload accepted answers True."""
    from kazma_gateway.adapters.telegram import TelegramAdapter

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    async def run() -> bool:
        adapter = TelegramAdapter(token="123:fake", allow_all=True)
        adapter._http = httpx.AsyncClient(
            base_url="https://api.telegram.org/bot123:fake",
            transport=httpx.MockTransport(handler),
        )
        try:
            return await adapter.send(_outbound("telegram:42"))
        finally:
            await adapter._http.aclose()

    assert asyncio.run(run()) is True


def test_discord_send_fails_when_the_upload_is_refused() -> None:
    from kazma_gateway.adapters.discord import DiscordAdapter

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("content-type", "").startswith("multipart/"):
            return httpx.Response(413, json={"message": "Request entity too large"})
        return httpx.Response(200, json={"id": "1"})

    async def run() -> bool:
        adapter = DiscordAdapter(token="fake", allow_all=True)
        adapter._http = httpx.AsyncClient(
            base_url="https://discord.com/api/v10",
            transport=httpx.MockTransport(handler),
        )
        try:
            return await adapter.send(_outbound("discord:99"))
        finally:
            await asyncio.sleep(0)  # let the typing task finish on the mock
            await adapter._http.aclose()

    assert asyncio.run(run()) is False


def _slack_handler(upload_ok: bool):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/files.getUploadURLExternal"):
            if not upload_ok:
                return httpx.Response(200, json={"ok": False, "error": "file_too_large"})
            return httpx.Response(200, json={
                "ok": True, "upload_url": "https://files.slack.com/upload/x", "file_id": "F1",
            })
        if path.endswith("/files.completeUploadExternal"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"ok": True, "ts": "1.0"})
    return handler


def _slack_send(outbound: OutboundMessage, upload_ok: bool) -> bool:
    from kazma_gateway.adapters.slack import SlackAdapter

    async def run() -> bool:
        adapter = SlackAdapter(bot_token="xoxb-fake", app_token="xapp-fake", allow_all=True)
        adapter._http = httpx.AsyncClient(transport=httpx.MockTransport(_slack_handler(upload_ok)))
        try:
            return await adapter.send(outbound)
        finally:
            await asyncio.sleep(0)
            await adapter._http.aclose()

    return asyncio.run(run())


def test_slack_send_fails_when_the_upload_is_refused() -> None:
    assert _slack_send(_outbound("slack:C1"), upload_ok=False) is False
    assert _slack_send(_outbound("slack:C1"), upload_ok=True) is True


def test_slack_file_only_send_answers_the_upload() -> None:
    """A message with no text and no blocks is the upload alone."""
    assert _slack_send(_outbound("slack:C1", text=""), upload_ok=False) is False
    assert _slack_send(_outbound("slack:C1", text=""), upload_ok=True) is True


# ── behaviour: a swarm route answers what the adapter answered ───────────


class _Manager:
    def __init__(self, ok: bool) -> None:
        self.ok = ok
        self.sent: list[OutboundMessage] = []

    async def send(self, outbound: OutboundMessage) -> bool:
        self.sent.append(outbound)
        return self.ok


def test_discord_and_slack_routes_report_a_refused_send() -> None:
    from kazma_gateway.agent_handler.swarm_output import (
        DiscordSwarmOutputTarget,
        SlackSwarmOutputTarget,
    )

    async def run(target: Any, cfg: dict[str, Any], ok: bool) -> bool:
        return await target.send(_Manager(ok), "report", cfg)

    assert asyncio.run(run(DiscordSwarmOutputTarget(), {"platform": "discord", "chat_id": "9"}, False)) is False
    assert asyncio.run(run(SlackSwarmOutputTarget(), {"platform": "slack", "chat_id": "C"}, False)) is False
    # Control: an adapter that took it.
    assert asyncio.run(run(DiscordSwarmOutputTarget(), {"platform": "discord", "chat_id": "9"}, True)) is True


def test_telegram_route_falls_back_to_the_bot_when_the_adapter_refuses(monkeypatch) -> None:
    """A dead adapter's False now reaches the direct Bot API fallback."""
    from kazma_gateway.agent_handler.swarm_output import TelegramSwarmOutputTarget

    direct: list[str] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        direct.append(request.url.path)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    class _MockedClient(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.pop("verify", None)
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _MockedClient)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:primary")
    manager = _Manager(ok=False)
    ok = asyncio.run(TelegramSwarmOutputTarget().send(
        manager, "report", {"platform": "telegram", "chat_id": "-100"},
    ))
    assert len(manager.sent) == 1          # the gateway was asked first
    assert ok is True
    assert direct and direct[0].endswith("/sendMessage")


def test_telegram_route_with_no_fallback_reports_the_refusal(monkeypatch) -> None:
    from kazma_gateway.agent_handler.swarm_output import TelegramSwarmOutputTarget

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    ok = asyncio.run(TelegramSwarmOutputTarget().send(
        _Manager(ok=False), "report", {"platform": "telegram", "chat_id": "-100"},
    ))
    assert ok is False


def test_swarm_reply_answers_the_adapter() -> None:
    from kazma_gateway.agent_handler.swarm_dispatch import _send_swarm_reply
    from kazma_gateway.gateway import IncomingMessage

    class _Store:
        async def get(self, _thread_id: str) -> None:
            return None

    msg = IncomingMessage(
        platform="discord", sender_id="discord:u1", text="/swarm x",
        context_metadata={"channel_id": "c1"},
    )
    assert asyncio.run(_send_swarm_reply(msg, _Store(), _Manager(False), "t1", "r")) is False
    assert asyncio.run(_send_swarm_reply(msg, _Store(), _Manager(True), "t1", "r")) is True
