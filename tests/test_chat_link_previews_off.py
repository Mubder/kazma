"""Nothing Kazma posts to a chat app makes the platform fetch a link (FA-128).

The 2026-11 functional audit: no Telegram, Slack or Discord sender set a
preview flag (only the ops-alert pager did). A platform fetches a message's
links from its own servers the moment it lands, so a link a prompt-injected
page persuades the model to write -- carrying a memory in its query -- is
sent out with no click and no card: §45's rule (model output never makes the
browser reach another host), one surface behind.

Every send and edit call in the gateway (a ``.post``/``.patch`` with a JSON
body to a send or edit endpoint) sits in a function that passes the body
through ``kazma_gateway.outbound_flags``; and the real senders' bodies carry
the flags. Today's builders, planted, are the negative control.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import Any

import pytest
from kazma_gateway import outbound_flags

REPO = Path(__file__).resolve().parents[1]

#: What a send or edit endpoint's address holds.
_SEND_MARKERS = ("sendMessage", "editMessageText", "chat.postMessage", "chat.update",
                 "chat.postEphemeral", "/messages", "/webhooks/", "{method}")


def _gateway_sources() -> dict[str, str]:
    listed = subprocess.run(["git", "ls-files", "kazma-gateway/kazma_gateway"],
                            cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    return {rel: (REPO / rel).read_text(encoding="utf-8")
            for rel in listed if rel.endswith(".py") and "/tests/" not in rel and (REPO / rel).exists()}


def _address_text(call: ast.Call, assignments: dict[str, str]) -> str:
    if not call.args:
        return ""
    first = call.args[0]
    text = ast.unparse(first)
    for name in [n.id for n in ast.walk(first) if isinstance(n, ast.Name)]:
        text += " " + assignments.get(name, "")
    return text


def unflagged_sends(sources: dict[str, str]) -> list[str]:
    """``file:function`` for every send/edit call whose function never calls
    ``outbound_flags``."""
    found: list[str] = []
    for rel, text in sources.items():
        tree = ast.parse(text)
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            assignments = {target.id: ast.unparse(node.value)
                           for node in ast.walk(func) if isinstance(node, ast.Assign)
                           for target in node.targets if isinstance(target, ast.Name)}
            flagged = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                          and isinstance(n.func.value, ast.Name) and n.func.value.id == "outbound_flags"
                          for n in ast.walk(func))
            for call in ast.walk(func):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr in ("post", "patch", "put")):
                    continue
                if not any(k.arg == "json" for k in call.keywords):
                    continue  # a multipart file upload carries no text to preview
                if any(marker in _address_text(call, assignments) for marker in _SEND_MARKERS) and not flagged:
                    found.append(f"{rel}:{func.name}")
    return sorted(set(found))


def test_every_chat_app_send_and_edit_turns_previews_off():
    unflagged = unflagged_sends(_gateway_sources())
    assert not unflagged, (
        "These send or edit a chat-app message without kazma_gateway.outbound_flags: the "
        f"platform would fetch any link in it (FA-128): {unflagged}")


def test_the_old_builders_are_caught():
    """Negative control: the Telegram, Slack and Discord bodies before FA-128."""
    planted = {
        "telegram_send.py": (
            "async def send(http, chat_id, chunk):\n"
            "    payload = {'chat_id': chat_id, 'text': chunk}\n"
            "    await http.post('/sendMessage', json=payload)\n"),
        "slack.py": (
            "async def send(self, channel_id, chunk):\n"
            "    await self._http.post(f'{_SLACK_API}/chat.postMessage', json={'channel': channel_id, 'text': chunk})\n"),
        "discord_commands.py": (
            "async def reply(adapter, entry, chunk):\n"
            "    base = f'/webhooks/{entry.application}/{entry.credential}'\n"
            "    await adapter._http.patch(base + '/messages/@original', json={'content': chunk})\n"),
        "reads.py": (
            "async def history(client, channel_id):\n"
            "    return await client.get(f'/channels/{channel_id}/messages')\n"),
    }
    assert unflagged_sends(planted) == ["discord_commands.py:reply", "slack.py:send", "telegram_send.py:send"]


def test_the_helpers_set_each_platforms_flag():
    assert outbound_flags.telegram({"text": "x", "disable_web_page_preview": False}) == {
        "text": "x", "link_preview_options": {"is_disabled": True}}
    assert outbound_flags.telegram({"callback_query_id": "1"}, "answerCallbackQuery") == {"callback_query_id": "1"}
    assert outbound_flags.slack({"text": "x"})["unfurl_links"] is False
    assert "unfurl_links" not in outbound_flags.slack({"text": "x"}, "chat.update")
    assert outbound_flags.discord({"content": "x", "flags": 64})["flags"] == 64 | 4


# ── the real senders' bodies ────────────────────────────────────────────────

class _Response:
    status_code = 200
    headers: dict[str, str] = {}
    text = ""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        return None


class _Http:
    def __init__(self, body: dict[str, Any] | None = None) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.body = body or {"ok": True, "result": {"message_id": 1}, "id": "1", "ts": "1.0"}

    async def post(self, url: str, json: dict[str, Any] | None = None, **_kw: Any) -> _Response:
        self.sent.append((url, json or {}))
        return _Response(self.body)

    async def patch(self, url: str, json: dict[str, Any] | None = None, **_kw: Any) -> _Response:
        return await self.post(url, json=json)


async def test_telegram_chunks_go_out_without_previews():
    from kazma_gateway.adapters import telegram_send

    http = _Http()

    async def no_wait() -> None:
        return None

    sent = await telegram_send.send_chunks_with_retry(
        http=http, chat_id=42, chunks=["see https://collector.example/?m=secret"], parse_mode=None,
        reply_markup=None, rate_acquire=no_wait)
    assert sent is True
    (url, body), = http.sent
    assert url == "/sendMessage" and body["link_preview_options"] == {"is_disabled": True}


@pytest.mark.parametrize("platform", ["slack", "discord", "telegram"])
async def test_the_bus_senders_go_out_without_previews(platform):
    if platform == "slack":
        from kazma_gateway.adapters.slack_bus import SlackBusAdapter

        bus = SlackBusAdapter(bot_token="xoxb-demo-token", channel_id="C1")
        bus._http = _Http()
        await bus._post_message({"text": "https://collector.example/?m=secret"})
        (_url, body), = bus._http.sent
        assert body["unfurl_links"] is False and body["unfurl_media"] is False
    elif platform == "discord":
        from kazma_gateway.adapters.discord_bus import DiscordBusAdapter

        bus = DiscordBusAdapter(bot_token="demo-discord-token", channel_id="1")
        bus._http = _Http()
        await bus._post_message({"content": "https://collector.example/?m=secret"})
        (_url, body), = bus._http.sent
        assert body["flags"] & outbound_flags.DISCORD_SUPPRESS_EMBEDS
    else:
        from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter

        bus = TelegramBusAdapter(bot_token="demo-telegram-token", chat_id="42")
        bus._http = _Http()
        await bus._post({"chat_id": "42", "text": "https://collector.example/?m=secret"})
        (_url, body), = bus._http.sent
        assert body["link_preview_options"] == {"is_disabled": True}
