"""Gateway approval identities survive TTL and cannot be granted by a command."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kazma_gateway.gateway import IncomingMessage


class CheckpointReached(BaseException):
    """Stop before any graph resume or tool execution."""


def _message(sender: str, text: str = "/hitl approve", platform: str = "telegram"):
    return IncomingMessage(platform, sender, text, context_metadata={"chat_id": 42})


@pytest.mark.parametrize("admin_source", ["qualified", "bare", "allowlist"])
@pytest.mark.parametrize("owner", ["discord:42:900", "discord:42:901", "discord:900:900"])
async def test_real_discord_admin_identity_preserves_channel_owner(monkeypatch, admin_source, owner):
    from kazma_core.config_store import get_config_store
    from kazma_core.sessions.directory import record_thread_owner
    from kazma_gateway.adapters.discord_parse import parse_message_create
    from kazma_gateway.agent_handler import hitl
    from kazma_gateway.agent_handler.graph import _sender_is_gateway_admin

    monkeypatch.delenv("KAZMA_GATEWAY_ADMINS", raising=False)
    if admin_source == "allowlist":
        get_config_store().set("connectors.discord.allowed_users", "42")
    else:
        monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "discord:42" if admin_source == "qualified" else "42")
    msg = parse_message_create({"author": {"id": "42"}, "channel_id": "900", "content": "/hitl approve"})
    assert msg is not None and msg.sender_id == "discord:42:900"
    assert _sender_is_gateway_admin(msg)
    record_thread_owner("discord-admin-review", owner)

    async def stop(*_args):
        raise CheckpointReached()

    monkeypatch.setattr(hitl, "_check_graph_interrupt", stop)
    delivery = SimpleNamespace(get=AsyncMock(return_value={}))
    manager = SimpleNamespace(send=AsyncMock())
    graph = SimpleNamespace(aget_state=AsyncMock(), ainvoke=AsyncMock())
    if owner == msg.sender_id:
        with pytest.raises(CheckpointReached):
            await hitl._handle_hitl_resume(msg, graph, {}, "discord-admin-review", delivery, manager)
        manager.send.assert_not_awaited()
    else:
        assert await hitl._handle_hitl_resume(msg, graph, {}, "discord-admin-review", delivery, manager)
        graph.aget_state.assert_not_awaited()
        graph.ainvoke.assert_not_awaited()
        assert "authorized" in manager.send.call_args.args[0].text.lower()


@pytest.mark.parametrize("admin", ["discord:900", "900", "telegram:42"])
def test_discord_channel_identity_cannot_grant_user_admin(monkeypatch, admin):
    from kazma_gateway.allowlists import is_gateway_admin

    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", admin)
    assert not is_gateway_admin("discord:42:900", "discord")


@pytest.mark.parametrize("sender,admins,owner", [
    ("telegram:42", "telegram:admin", "telegram:42"),
    ("", "telegram:42", "telegram:42"),
    ("telegram:42", "telegram:42", ""),
    ("telegram:42", "telegram:42", "telegram:other"),
    ("telegram:42", "telegram:42", "discord:42"),
])
async def test_unauthorized_approval_does_not_read_graph(monkeypatch, sender, admins, owner):
    from kazma_core.sessions.directory import record_thread_owner
    from kazma_gateway.agent_handler.hitl import _handle_hitl_resume

    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", admins)
    if owner:
        record_thread_owner("approval-test", owner)
    graph = SimpleNamespace(aget_state=AsyncMock(), ainvoke=AsyncMock())
    manager = SimpleNamespace(send=AsyncMock())
    store = SimpleNamespace(get=AsyncMock(return_value={}))
    assert await _handle_hitl_resume(_message(sender), graph, {}, "approval-test", store, manager)
    graph.aget_state.assert_not_awaited()
    graph.ainvoke.assert_not_awaited()
    assert "authorized" in manager.send.call_args.args[0].text.lower()


async def test_approval_denies_when_durable_owner_store_is_unavailable(monkeypatch):
    from kazma_core.config_store import get_config_store
    from kazma_gateway.agent_handler.hitl import _handle_hitl_resume

    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "telegram:42")
    store = get_config_store()
    original_get = store.get

    def unavailable_owner(key, *args, **kwargs):
        if key.startswith("session.owner."):
            raise RuntimeError("synthetic owner-store outage")
        return original_get(key, *args, **kwargs)

    monkeypatch.setattr(store, "get", unavailable_owner)
    graph = SimpleNamespace(aget_state=AsyncMock(), ainvoke=AsyncMock())
    manager = SimpleNamespace(send=AsyncMock())
    delivery = SimpleNamespace(get=AsyncMock(return_value={}))
    assert await _handle_hitl_resume(_message("telegram:42"), graph, {},
                                   "gw-telegram-42", delivery, manager)
    graph.aget_state.assert_not_awaited()
    graph.ainvoke.assert_not_awaited()
    assert "authorized" in manager.send.call_args.args[0].text.lower()


@pytest.mark.parametrize("sender_id", ["42", "12345678"])
async def test_legacy_dm_owner_reaches_checkpoint_with_readable_store(monkeypatch, sender_id):
    from kazma_gateway.agent_handler import hitl

    sender = f"telegram:{sender_id}"
    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", sender)

    async def stop(*_args):
        raise CheckpointReached()

    monkeypatch.setattr(hitl, "_check_graph_interrupt", stop)
    delivery = SimpleNamespace(get=AsyncMock(return_value={}))
    manager = SimpleNamespace(send=AsyncMock())
    with pytest.raises(CheckpointReached):
        await hitl._handle_hitl_resume(_message(sender), object(), {},
                                       f"gw-telegram-{sender_id}", delivery, manager)
    manager.send.assert_not_awaited()


@pytest.mark.parametrize("platform", ["telegram", "discord", "slack"])
@pytest.mark.parametrize("cross_thread", [False, True])
async def test_owner_admin_reaches_checkpoint_after_session_expiry_and_store_reopen(
    monkeypatch, platform, cross_thread,
):
    from kazma_core.config_store import get_config_store
    from kazma_core.sessions.directory import record_thread_owner
    from kazma_gateway.agent_handler import hitl

    sender = f"{platform}:42"
    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", sender)
    record_thread_owner("durable-approval", sender)
    get_config_store().close()  # ownership must be read from durable storage

    async def stop(*_args):
        raise CheckpointReached()

    monkeypatch.setattr(hitl, "_check_graph_interrupt", stop)
    store = SimpleNamespace(get=AsyncMock(return_value={}))
    manager = SimpleNamespace(send=AsyncMock())
    msg = _message(sender, "/hitl approve durable-approval", platform)
    with pytest.raises(CheckpointReached):
        await hitl._handle_hitl_resume(msg, object(), {},
                                       "other-mouth" if cross_thread else "durable-approval",
                                       store, manager)
    manager.send.assert_not_awaited()


async def test_approval_command_cannot_mint_its_own_owner(monkeypatch):
    from kazma_core.sessions.directory import thread_owner
    from kazma_gateway.agent_handler.graph import create_graph_handler
    from kazma_gateway.agent_handler.store import _InMemoryStore

    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "telegram:42")
    graph = SimpleNamespace(aget_state=AsyncMock(), ainvoke=AsyncMock())
    manager = SimpleNamespace(send=AsyncMock(), adapters=[])
    msg = _message("telegram:42")
    msg.context_metadata["thread_id"] = "unknown-approval-thread"
    await create_graph_handler(graph=graph, manager=manager, store=_InMemoryStore())(msg)
    assert thread_owner("unknown-approval-thread") == ""
    graph.aget_state.assert_not_awaited()
    assert "authorized" in manager.send.call_args.args[0].text.lower()


@pytest.mark.parametrize("text", [
    "/yolo", "/yolo on", "/unrestricted", "unrestricted on",
    "/long yolo on", "long yolo research\nRun a task",
])
async def test_non_admin_cannot_enable_yolo_through_early_handler(monkeypatch, text):
    from kazma_core.agent.long_task import is_long_task_active
    from kazma_core.safety.yolo import is_yolo_active
    from kazma_gateway.agent_handler.graph import create_graph_handler
    from kazma_gateway.agent_handler.store import _InMemoryStore

    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "telegram:admin")
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.setenv("KAZMA_ALLOW_YOLO", "1")
    graph = SimpleNamespace(aget_state=AsyncMock(), ainvoke=AsyncMock())
    manager = SimpleNamespace(send=AsyncMock(), adapters=[])
    msg = _message("telegram:42", text)
    await create_graph_handler(graph=graph, manager=manager, store=_InMemoryStore())(msg)
    assert not is_yolo_active("gw-telegram-42")
    assert not is_long_task_active("gw-telegram-42")
    graph.ainvoke.assert_not_awaited()
    assert "admin" in manager.send.call_args.args[0].text.lower()


async def test_normal_native_topic_turn_records_owner_outside_graph():
    from kazma_core.sessions.directory import thread_owner
    from kazma_gateway.agent_handler.store import _build_initial_state, _InMemoryStore

    msg = _message("telegram:42", "hello")
    msg.context_metadata["message_thread_id"] = 999
    state = await _build_initial_state(msg, _InMemoryStore())
    assert thread_owner(state["thread_id"]) == "telegram:42"
    assert "sender_id" not in state
    assert "user_id" not in state
    assert "chat_id" not in state


@pytest.mark.parametrize("command", ["/yolo on", "/unrestricted", "/long yolo on"])
async def test_admin_can_enable_yolo_and_chat_member_can_disable_it(monkeypatch, command):
    from kazma_core.safety.yolo import is_yolo_active
    from kazma_gateway.agent_handler.graph import create_graph_handler
    from kazma_gateway.agent_handler.store import _InMemoryStore

    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "telegram:42")
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.setenv("KAZMA_ALLOW_YOLO", "1")
    manager = SimpleNamespace(send=AsyncMock(), adapters=[])
    handler = create_graph_handler(graph=object(), manager=manager, store=_InMemoryStore())
    await handler(_message("telegram:42", command))
    assert is_yolo_active("gw-telegram-42")
    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "telegram:other")
    await handler(_message("telegram:42", "/yolo off"))
    assert not is_yolo_active("gw-telegram-42")


@pytest.mark.parametrize("action", ["approve", "deny"])
async def test_real_checkpoint_resume_after_restart_records_owner_decision(monkeypatch, tmp_path, action):
    import aiosqlite
    from kazma_core.agent.turn import _ainvoke
    from kazma_core.config_store import get_config_store
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_core.safety.hitl_gates import gate_for
    from kazma_core.sessions.directory import record_thread_owner
    from kazma_gateway.agent_handler import hitl
    from kazma_ui.hitl_gate_bridge import gate_pending_from_payload
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.graph import END, StateGraph
    from langgraph.types import interrupt

    thread = "persisted-approval"
    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "telegram:42")
    record_thread_owner(thread, "telegram:42")
    executions = []

    def wait_for_decision(state):
        decision = interrupt({"type": "hitl_approval", "kind": "security",
                              "thread_id": thread, "tool": "file_write", "args": {}})
        if decision.get("approved"):
            executions.append("synthetic approved operation")
        return {"messages": [{"role": "assistant", "content": "Finished"}]}

    builder = StateGraph(dict)
    builder.add_node("wait", wait_for_decision)
    builder.set_entry_point("wait")
    builder.add_edge("wait", END)
    checkpoint_file = str(tmp_path / "checkpoints.db")
    config = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
    connection = await aiosqlite.connect(checkpoint_file)
    try:
        saver = AsyncSqliteSaver(connection)
        await saver.setup()
        graph = builder.compile(checkpointer=saver)
        async with workspace_path_scope(tmp_path):
            await _ainvoke(graph, {"messages": []}, config)
        pending = await hitl._check_graph_interrupt(graph, config)
        await gate_pending_from_payload(pending)
        gate_id = pending["interrupt_id"]
    finally:
        await connection.close()
    get_config_store().close()

    connection = await aiosqlite.connect(checkpoint_file)
    try:
        graph = builder.compile(checkpointer=AsyncSqliteSaver(connection))
        manager = SimpleNamespace(send=AsyncMock())
        store = SimpleNamespace(get=AsyncMock(return_value={}))
        assert await hitl._handle_hitl_resume(_message("telegram:42", f"/hitl {action}"),
                                              graph, {}, thread, store, manager)
        assert not (await graph.aget_state(config)).next
        assert len(executions) == (1 if action == "approve" else 0)
        gate = gate_for(gate_id)
        assert gate.actor == "telegram:42"
        assert gate.decision == action
        assert gate.state == "settled"
    finally:
        await connection.close()
