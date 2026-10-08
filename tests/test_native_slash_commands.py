"""Native adapter commands retain authorization, ACK and reply contracts."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from kazma_core.agent.command_catalog import COMMANDS, normalize_telegram_command
from kazma_gateway.adapters.discord import DiscordAdapter
from kazma_gateway.adapters.discord_commands import COMMAND, register
from kazma_gateway.adapters.slack import SlackAdapter
from kazma_gateway.adapters.slack_commands import handle as slack_handle
from kazma_gateway.adapters.slack_commands import safe_response_url
from kazma_gateway.adapters.telegram_commands import reconcile_commands
from kazma_gateway.gateway import OutboundMessage
from kazma_ui.sse_chat._command_discovery import discovery_reply


@pytest.mark.parametrize("platform,key", [("Telegram", "command_menus"), ("Discord", "native_commands")])
def test_registration_diagnostics_show_readback_failures_without_scope_ids(platform, key):
    from kazma_gateway.connector_test import command_registration

    assert command_registration({key: {"private-id": "verified"}}, platform)[0] is True
    ok, detail = command_registration({key: {"private-id": "mismatch"}}, platform)
    assert ok is False
    assert "mismatch" in detail and "private-id" not in detail
    assert command_registration({}, platform)[0] is None


def test_slack_registration_diagnostics_do_not_claim_manifest_verified():
    from kazma_gateway.connector_test import command_registration

    ok, detail = command_registration({"native_commands": "Receiver enabled"}, "Slack")
    assert ok is None
    assert "cannot verify or edit" in detail


@pytest.mark.parametrize("command", [c.name for c in COMMANDS])
def test_telegram_addressing_preserves_arguments_and_rejects_another_bot(command):
    text = f"/{command}@KazmaBot  AbC\nKeep This"
    assert normalize_telegram_command(text, "kazmabot") == f"/{command}  AbC\nKeep This"
    assert normalize_telegram_command(text, "DifferentBot") is None
    assert normalize_telegram_command(text, "") is None
    assert normalize_telegram_command(f"/{command}", "") == f"/{command}"


@pytest.mark.parametrize("command", ["x", "model", "ide", "kb", "documents"])
def test_web_page_commands_explain_their_capability(command):
    reply = discovery_reply("/" + command)
    assert "](/" in reply and "subcommands" in reply
    assert discovery_reply("/" + command, "ar") != reply


def test_web_unknown_and_implemented_commands():
    assert "not available" in discovery_reply("/nonexistent")
    assert "not available" in discovery_reply("/config export")
    assert discovery_reply("/reset") is None
    assert discovery_reply("/research deep topic") is None
    assert "/x" in discovery_reply("/help")
    assert discovery_reply("/swarm config group x") is not None
    assert discovery_reply("/abort") is not None
    assert discovery_reply("/swarm audit this repository") is None


def discord_data():
    return {
        "id": "123456",
        "application_id": "789",
        "token": "private-continuation",
        "type": 2,
        "channel_id": "C",
        "guild_id": "G",
        "member": {"user": {"id": "U"}},
        "data": {"name": "kazma", "options": [{"name": "command", "value": "x help"}]},
    }


def slack_data():
    return {
        "type": "slash_commands",
        "envelope_id": "E",
        "accepts_response_payload": True,
        "payload": {
            "command": "/kazma",
            "text": "x help",
            "user_id": "U",
            "channel_id": "C",
            "team_id": "T",
            "response_url": "https://hooks.slack.com/commands/T/private-continuation",
        },
    }


def response():
    return httpx.Response(200, text="ok", request=httpx.Request("POST", "https://example.test"))


@pytest.mark.asyncio
async def test_discord_defer_deduplicate_and_complete_private_reply():
    adapter = DiscordAdapter("test", allowed_users=["U"], allowed_guilds=["G"])
    adapter._queue = asyncio.Queue()
    adapter._http = AsyncMock()
    adapter._http.post.return_value = response()
    adapter._http.patch.return_value = response()
    await adapter._handle_interaction(discord_data())
    assert adapter._http.post.call_args.kwargs["json"] == {"type": 5, "data": {"flags": 64}}
    await adapter._handle_interaction(discord_data())
    assert adapter._queue.qsize() == 1
    incoming = adapter._queue.get_nowait()
    assert incoming.text == "/x help"
    assert "private-continuation" not in json.dumps(incoming.context_metadata)
    outgoing = OutboundMessage(target_id="discord:C", text="result", context_metadata=incoming.context_metadata)
    assert await adapter.send(outgoing)
    assert adapter._http.patch.call_args.args[0].endswith("/messages/@original")
    assert adapter._http.patch.call_args.kwargs["json"]["content"] == "result"
    assert await adapter.send(outgoing)
    assert adapter._http.post.call_args.kwargs["json"]["flags"] == 64
    outgoing.context_metadata["components"] = [{"type": 1, "components": []}]
    assert await adapter.send(outgoing)
    assert adapter._http.post.call_args.kwargs["json"]["components"] == outgoing.context_metadata["components"]
    outgoing.context_metadata = incoming.context_metadata | {"user_id": "OTHER"}
    assert not await adapter.send(outgoing)
    outgoing.context_metadata = incoming.context_metadata
    adapter._native_replies.get("123456").expires = time.monotonic() - 1
    assert not await adapter.send(outgoing)


@pytest.mark.asyncio
async def test_discord_failed_ack_never_dispatches_or_logs_continuation(caplog):
    adapter = DiscordAdapter("test", allowed_users=["U"])
    adapter._queue, adapter._http = asyncio.Queue(), AsyncMock()
    adapter._http.post.side_effect = httpx.ReadTimeout("private-continuation")
    await adapter._handle_interaction(discord_data())
    assert adapter._queue.empty()
    assert adapter._receive.dropped["processing_failed"] == 1
    assert "private-continuation" not in caplog.text


@pytest.mark.asyncio
async def test_slack_failed_ack_never_dispatches(caplog):
    adapter = SlackAdapter("test", "app", allowed_users=["U"])
    adapter._queue = asyncio.Queue()
    ws = AsyncMock()
    ws.send.side_effect = TimeoutError("private-continuation")
    await slack_handle(adapter, ws, slack_data())
    assert adapter._queue.empty()
    assert adapter._receive.dropped["processing_failed"] == 1
    assert "private-continuation" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [True, False])
async def test_discord_private_component_keeps_followup_private_or_drops(valid):
    adapter = DiscordAdapter("test", allowed_users=["U"])
    adapter._queue, adapter._http = asyncio.Queue(), AsyncMock()
    adapter._http.post.return_value = response()
    data = discord_data()
    data.update(type=3, message={"flags": 64, "content": "Choose a model"})
    data["data"] = {"custom_id": "model_select:deepseek-chat"}
    if not valid:
        data.pop("token")
    await adapter._handle_interaction(data)
    if not valid:
        assert adapter._queue.empty()
        adapter._http.post.assert_not_awaited()
        return
    incoming = adapter._queue.get_nowait()
    assert incoming.context_metadata["native_reply_id"] == data["id"]
    assert "private-continuation" not in json.dumps(incoming.context_metadata)
    assert await adapter.send(
        OutboundMessage(target_id="discord:C", text="private result", context_metadata=incoming.context_metadata)
    )
    assert adapter._http.post.call_args.args[0].startswith("/webhooks/")
    assert adapter._http.post.call_args.kwargs["json"]["flags"] == 64


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [True, False])
async def test_slack_private_component_keeps_followup_private_or_drops(monkeypatch, valid):
    import websockets
    from kazma_gateway.adapters import slack_commands

    settle = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_commands, "settle_callback", settle)
    adapter = SlackAdapter("test", "app", allowed_users=["U"], allowed_channels=["C"], allowed_teams=["T"])
    adapter._queue, adapter._shutdown, adapter._http = asyncio.Queue(), asyncio.Event(), AsyncMock()
    adapter._http.post.return_value = httpx.Response(200, json={"ok": True, "url": "wss://socket.test"})
    adapter._http.__aenter__.return_value = adapter._http
    url = "https://hooks.slack.com/actions/T/private-continuation" if valid else "https://evil.test/actions/T/secret"
    payload = {
        "type": "block_actions",
        "team": {"id": "T"},
        "user": {"id": "U"},
        "channel": {"id": "C"},
        "container": {"is_ephemeral": True},
        "response_url": url,
        "actions": [{"value": "model_select:deepseek-chat"}],
    }
    ws = AsyncMock()

    async def recv():
        adapter._shutdown.set()
        return json.dumps({"type": "interactive", "envelope_id": "private-button", "payload": payload})

    ws.recv.side_effect = recv
    connection = AsyncMock()
    connection.__aenter__.return_value = ws
    monkeypatch.setattr(websockets, "connect", lambda *args, **kwargs: connection)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: adapter._http)
    await adapter._listen_socket_mode()
    await asyncio.sleep(0)  # Let the tracked card-update task run after the reader returns.
    assert json.loads(ws.send.call_args.args[0])["envelope_id"] == "private-button"
    if not valid:
        assert adapter._queue.empty()
        settle.assert_not_awaited()
        return
    settle.assert_awaited_once_with(adapter, payload, "private-button")
    incoming = adapter._queue.get_nowait()
    assert incoming.context_metadata["native_reply_id"] == "private-button"
    assert "private-continuation" not in json.dumps(incoming.context_metadata)
    adapter._http.post.return_value = response()
    assert await adapter.send(
        OutboundMessage(target_id="slack:C", text="private result", context_metadata=incoming.context_metadata)
    )
    assert adapter._http.post.call_args.args[0] == url
    assert adapter._http.post.call_args.kwargs["json"]["response_type"] == "ephemeral"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,reason",
    [
        ({"user": "OTHER"}, "user_not_allowed"),
        ({"guild": "OTHER"}, "server_not_allowed"),
        ({"channel": ""}, "no_channel"),
    ],
)
async def test_discord_native_denials(change, reason):
    adapter = DiscordAdapter("test", allowed_users=["U"], allowed_guilds=["G"])
    adapter._queue, adapter._http = asyncio.Queue(), AsyncMock()
    adapter._http.post.return_value = response()
    data = discord_data()
    if "user" in change:
        data["member"]["user"]["id"] = change["user"]
    if "guild" in change:
        data["guild_id"] = change["guild"]
    if "channel" in change:
        data["channel_id"] = change["channel"]
    await adapter._handle_interaction(data)
    assert adapter._queue.empty()
    assert adapter._http.post.call_args.kwargs["json"]["type"] == 4
    assert adapter._receive.dropped[reason] == 1


@pytest.mark.asyncio
async def test_discord_registration_upserts_one_command_and_verifies():
    adapter = DiscordAdapter("test", allowed_users=["U"], allowed_guilds=["G"])
    requests = []

    def mock(request):
        requests.append(request)
        return httpx.Response(200, json=COMMAND if request.method == "POST" else [COMMAND])

    async with httpx.AsyncClient(base_url="https://discord.test", transport=httpx.MockTransport(mock)) as client:
        adapter._http = client
        await register(adapter, "789")
    assert [r.method for r in requests] == ["POST", "GET"]
    assert adapter._receive.extra["native_commands"] == {"G": "verified"}


@pytest.mark.asyncio
async def test_slack_ack_deduplicate_and_private_delayed_reply():
    adapter = SlackAdapter("test", "app", allowed_teams=["T"], allowed_channels=["C"], allowed_users=["U"])
    adapter._queue, adapter._http = asyncio.Queue(), AsyncMock()
    adapter._http.post.return_value = response()
    ws = AsyncMock()
    await slack_handle(adapter, ws, slack_data())
    assert json.loads(ws.send.call_args.args[0])["envelope_id"] == "E"
    await slack_handle(adapter, ws, slack_data())
    assert adapter._queue.qsize() == 1
    incoming = adapter._queue.get_nowait()
    assert incoming.text == "/x help"
    assert "private-continuation" not in json.dumps(incoming.context_metadata)
    outgoing = OutboundMessage(target_id="slack:C", text="result", context_metadata=incoming.context_metadata)
    assert await adapter.send(outgoing)
    payload = adapter._http.post.call_args.kwargs["json"]
    assert payload["replace_original"] and payload["response_type"] == "ephemeral"
    assert await adapter.send(outgoing)
    assert not adapter._http.post.call_args.kwargs["json"]["replace_original"]
    outgoing.context_metadata["blocks"] = [{"type": "section", "text": {"type": "plain_text", "text": "Review"}}]
    assert await adapter.send(outgoing)
    assert adapter._http.post.call_args.kwargs["json"]["blocks"] == outgoing.context_metadata["blocks"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("user_id", "OTHER", "user_not_allowed"),
        ("channel_id", "OTHER", "channel_not_allowed"),
        ("team_id", "OTHER", "team_not_allowed"),
        ("team_id", "", "team_not_allowed"),
        ("response_url", "http://127.0.0.1/admin", "processing_failed"),
    ],
)
async def test_slack_denial_still_acknowledges(field, value, reason):
    adapter = SlackAdapter("test", "app", allowed_teams=["T"], allowed_channels=["C"], allowed_users=["U"])
    adapter._queue = asyncio.Queue()
    ws, data = AsyncMock(), slack_data()
    data["payload"][field] = value
    await slack_handle(adapter, ws, data)
    assert adapter._queue.empty()
    assert ws.send.await_count == 1
    assert adapter._receive.dropped[reason] == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.slack.com.evil/commands/a",
        "https://user@hooks.slack.com/commands/a",
        "https://hooks.slack.com:8080/commands/a",
        "https://hooks.slack.com/services/a",
        "http://hooks.slack.com/commands/a",
    ],
)
def test_slack_response_url_refuses_other_endpoints(url):
    assert not safe_response_url(url)


@pytest.mark.asyncio
async def test_native_queue_full_completes_busy_reply():
    adapter = DiscordAdapter("test", allowed_users=["U"])
    adapter._queue, adapter._http = asyncio.Queue(maxsize=1), AsyncMock()
    adapter._queue.put_nowait("occupied")
    adapter._http.post.return_value = response()
    adapter._http.patch.return_value = response()
    await adapter._handle_interaction(discord_data())
    assert adapter._receive.dropped["queue_full"] == 1
    assert "busy" in adapter._http.patch.call_args.kwargs["json"]["content"]


@pytest.mark.asyncio
async def test_telegram_registration_readback_locales_and_mismatch():
    menus = {}

    def mock(request):
        if request.method == "POST":
            body = json.loads(request.content)
            menus[(body["scope"]["type"], body["language_code"])] = body["commands"]
            return httpx.Response(200, json={"ok": True})
        params = request.url.params
        key = (json.loads(params["scope"])["type"], params["language_code"])
        return httpx.Response(200, json={"ok": True, "result": menus[key] if key != ("all_group_chats", "ar") else []})

    async with httpx.AsyncClient(base_url="https://telegram.test", transport=httpx.MockTransport(mock)) as client:
        result = await reconcile_commands(client)
    assert len(result) == 9
    assert result["all_group_chats:ar"] == "mismatch"
    assert result["all_private_chats:default"] == "verified"
    assert any(row["command"] == "x" for row in menus[("default", "")])


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["x", "help", "reset", "compact", "abort", "ide", "kb", "documents", "research"])
async def test_telegram_ingress_dispatches_addressed_commands_once(command):
    from kazma_gateway.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter("test", allowed_users=[42])
    adapter._bot_username = "KazmaBot"
    adapter._set_reaction = AsyncMock()
    queue = asyncio.Queue()
    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "text": f"/{command}@kazmabot AbC",
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42},
        },
    }
    await adapter._process_update(update, queue)
    assert queue.qsize() == 1
    assert queue.get_nowait().text == f"/{command} AbC"
    update["message"]["text"] = f"/{command}@AnotherBot AbC"
    update["message"]["message_id"] = 2
    await adapter._process_update(update, queue)
    assert queue.empty()
    assert adapter._receive.dropped["other_bot"] == 1
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_native_reply_context_cannot_redirect_the_next_ordinary_turn():
    from kazma_gateway.agent_handler.store import _build_initial_state, _InMemoryStore
    from kazma_gateway.gateway import IncomingMessage

    store = _InMemoryStore()
    native = IncomingMessage(
        platform="discord",
        sender_id="discord:U:C",
        text="/help",
        context_metadata={"thread_id": "test-native", "user_id": "U", "channel_id": "C", "native_reply_id": "123456"},
    )
    state = await _build_initial_state(native, store)
    assert (await store.get("test-native"))["native_reply_id"] == "123456"
    assert "native_reply_id" not in state
    ordinary = IncomingMessage(
        platform="discord",
        sender_id="discord:U:C",
        text="hello",
        context_metadata={"thread_id": "test-native", "user_id": "U", "channel_id": "C"},
    )
    await _build_initial_state(ordinary, store)
    assert "native_reply_id" not in await store.get("test-native")


@pytest.mark.asyncio
async def test_discord_files_remain_private():
    from kazma_gateway.gateway import Attachment

    adapter = DiscordAdapter("test", allowed_users=["U"])
    adapter._queue, adapter._http = asyncio.Queue(), AsyncMock()
    adapter._http.post.return_value = response()
    adapter._http.patch.return_value = response()
    await adapter._handle_interaction(discord_data())
    incoming = adapter._queue.get_nowait()
    outgoing = OutboundMessage(
        target_id="discord:C",
        text="",
        context_metadata=incoming.context_metadata,
        attachments=[Attachment(kind="file", mime="text/plain", filename="result.txt", data=b"result")],
    )
    assert await adapter.send(outgoing)
    call = adapter._http.post.call_args
    assert call.args[0].startswith("/webhooks/")
    assert json.loads(call.kwargs["data"]["payload_json"])["flags"] == 64
    assert call.kwargs["files"]["files[0]"][1] == b"result"


@pytest.mark.asyncio
async def test_slack_ack_without_payload_creates_private_reply_and_reports_file_limit():
    from kazma_gateway.gateway import Attachment

    adapter = SlackAdapter("test", "app", allowed_users=["U"])
    adapter._queue, adapter._http = asyncio.Queue(), AsyncMock()
    adapter._http.post.return_value = response()
    envelope = slack_data() | {"accepts_response_payload": False}
    ws = AsyncMock()
    await slack_handle(adapter, ws, envelope)
    assert "payload" not in json.loads(ws.send.call_args.args[0])
    incoming = adapter._queue.get_nowait()
    outgoing = OutboundMessage(
        target_id="slack:C",
        text="result",
        context_metadata=incoming.context_metadata,
        attachments=[Attachment(kind="file", mime="text/plain", filename="result.txt", data=b"result")],
    )
    assert not await adapter.send(outgoing)
    payload = adapter._http.post.call_args.kwargs["json"]
    assert not payload["replace_original"]
    assert payload["response_type"] == "ephemeral"
    assert "Files cannot be delivered" in payload["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    ["help", "x", "ide", "kb", "documents", "reset", "compact", "abort", "model", "_models_provider", "_models_select"],
)
async def test_graph_ingress_normalizes_before_control_dispatch(monkeypatch, command):
    from types import SimpleNamespace

    from kazma_gateway.agent_handler import graph as graph_module
    from kazma_gateway.gateway import IncomingMessage

    class ReachedControlDispatch(BaseException):
        pass

    seen = []

    async def probe(msg, store):
        seen.append(msg.text)
        raise ReachedControlDispatch

    monkeypatch.setattr(graph_module, "_build_initial_state", probe)
    manager = SimpleNamespace(send=AsyncMock(), adapters=[])
    handler = graph_module.create_graph_handler(graph=SimpleNamespace(), manager=manager)
    message = IncomingMessage(
        platform="telegram",
        sender_id="telegram:42",
        text=f"/{command}@KazmaBot",
        context_metadata={"chat_id": 42, "user_id": 42, "bot_username": "KazmaBot"},
    )
    with pytest.raises(ReachedControlDispatch):
        await handler(message)
    assert seen == ["/" + command]
    manager.send.assert_not_awaited()
    message.text = "/help@AnotherBot"
    await handler(message)
    assert seen == ["/" + command]


@pytest.mark.asyncio
async def test_unknown_gateway_command_returns_help_without_model_work():
    from types import SimpleNamespace

    from kazma_gateway.agent_handler.graph import create_graph_handler
    from kazma_gateway.gateway import IncomingMessage

    graph = SimpleNamespace(ainvoke=AsyncMock())
    manager = SimpleNamespace(send=AsyncMock(), adapters=[])
    handler = create_graph_handler(graph=graph, manager=manager)
    await handler(
        IncomingMessage(
            platform="discord",
            sender_id="discord:U:C",
            text="/nonexistent",
            context_metadata={"channel_id": "C", "user_id": "U"},
        )
    )
    graph.ainvoke.assert_not_awaited()
    assert "Unknown command" in manager.send.call_args.args[0].text
