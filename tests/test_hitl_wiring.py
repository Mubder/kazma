"""HITL wiring tests — verify the approval gate behaviors added in Phases 1-5.

Covers:
    - SafetyMiddleware fail-closed behavior (check_sync)
    - allow_headless_danger escape hatch
    - Bus adapter callback resolution (Telegram/Discord/Slack)
    - Approval prompt building (gateway agent_handler helpers)
    - HITL config extraction (get_hitl_config, requires_approval)
"""

from __future__ import annotations

import asyncio
import pytest

from kazma_core.safety.hitl import (
    DEFAULT_DANGER_TOOLS,
    get_hitl_config,
    get_tool_tier,
    requires_approval,
)
from kazma_core.swarm.safety import SafetyMiddleware


# ══════════════════════════════════════════════════════════════════════════
# Phase 2: SafetyMiddleware fail-closed gate
# ══════════════════════════════════════════════════════════════════════════


class TestSafetyFailClosed:
    """check_sync must block danger tools when no real bus is present."""

    def test_danger_tool_blocked_without_bus(self):
        """Danger tools are blocked when allow_headless_danger=False (default)."""
        safety = SafetyMiddleware(enabled=True, allow_headless_danger=False)
        assert safety.check_sync("shell_exec") is False
        assert safety.check_sync("file_write") is False
        assert safety.check_sync("file_delete") is False

    def test_safe_tool_allowed_without_bus(self):
        """Non-danger tools pass through even with no bus."""
        safety = SafetyMiddleware(enabled=True, allow_headless_danger=False)
        assert safety.check_sync("file_read") is True
        assert safety.check_sync("send_message") is True

    def test_disabled_safety_allows_all(self):
        """When safety is disabled, everything passes."""
        safety = SafetyMiddleware(enabled=False)
        assert safety.check_sync("shell_exec") is True

    def test_headless_escape_hatch_allows_danger(self):
        """allow_headless_danger=True permits danger tools (test/dev)."""
        safety = SafetyMiddleware(enabled=True, allow_headless_danger=True)
        assert safety.check_sync("shell_exec") is True

    def test_stats_track_rejections(self):
        """Blocked danger tools increment the rejected counter."""
        safety = SafetyMiddleware(enabled=True, allow_headless_danger=False)
        safety.check_sync("shell_exec")
        safety.check_sync("file_write")
        assert safety.stats()["rejected_count"] >= 2


# ══════════════════════════════════════════════════════════════════════════
# Phase 2: HITL config helpers
# ══════════════════════════════════════════════════════════════════════════


class TestHitlConfig:
    """get_hitl_config and requires_approval behavior."""

    def test_default_config(self):
        cfg = get_hitl_config({})
        assert cfg["enabled"] is True
        assert "file_write" in cfg["require_approval_for"]
        assert "shell_exec" in cfg["require_approval_for"]

    def test_custom_danger_list(self):
        cfg = get_hitl_config({
            "safety": {"hitl": {"require_approval_for": ["custom_tool"]}}
        })
        assert requires_approval("custom_tool", cfg) is True
        assert requires_approval("shell_exec", cfg) is False

    def test_disabled(self):
        cfg = get_hitl_config({"safety": {"hitl": {"enabled": False}}})
        assert requires_approval("shell_exec", cfg) is False

    def test_default_danger_tools(self):
        assert DEFAULT_DANGER_TOOLS == ["file_write", "file_delete", "shell_exec"]

    def test_tool_tiers(self):
        assert get_tool_tier("file_read") == "read"
        assert get_tool_tier("file_write") == "danger"
        assert get_tool_tier("unknown_tool") == "unknown"


# ══════════════════════════════════════════════════════════════════════════
# Phase 3/5: Bus adapter callback resolution
# ══════════════════════════════════════════════════════════════════════════


class TestBusAdapterCallbacks:
    """All three platform bus adapters resolve approve/reject callbacks."""

    @pytest.mark.asyncio
    async def test_discord_adapter_approve(self):
        from kazma_gateway.adapters.discord_bus import DiscordBusAdapter

        adapter = DiscordBusAdapter(bot_token="fake", channel_id="123")
        task_id = "task-abc"

        # Simulate a pending approval
        event = asyncio.Event()
        adapter._pending_approvals[task_id] = event

        # Resolve via handle_callback (as the Discord interaction handler would)
        resolved = adapter.handle_callback(f"swarm_approve_{task_id}")
        assert resolved == task_id
        assert event.is_set()
        assert adapter._pending_results[task_id] is True

    @pytest.mark.asyncio
    async def test_discord_adapter_reject(self):
        from kazma_gateway.adapters.discord_bus import DiscordBusAdapter

        adapter = DiscordBusAdapter(bot_token="fake", channel_id="123")
        task_id = "task-xyz"

        event = asyncio.Event()
        adapter._pending_approvals[task_id] = event

        resolved = adapter.handle_callback(f"swarm_reject_{task_id}")
        assert resolved == task_id
        assert event.is_set()
        assert adapter._pending_results[task_id] is False

    @pytest.mark.asyncio
    async def test_discord_adapter_unknown_callback(self):
        from kazma_gateway.adapters.discord_bus import DiscordBusAdapter

        adapter = DiscordBusAdapter(bot_token="fake", channel_id="123")
        assert adapter.handle_callback("something_else") is None

    @pytest.mark.asyncio
    async def test_slack_adapter_approve(self):
        from kazma_gateway.adapters.slack_bus import SlackBusAdapter

        adapter = SlackBusAdapter(bot_token="fake", channel_id="C123")
        task_id = "slack-task"

        event = asyncio.Event()
        adapter._pending_approvals[task_id] = event

        resolved = adapter.handle_callback(f"swarm_approve_{task_id}")
        assert resolved == task_id
        assert event.is_set()
        assert adapter._pending_results[task_id] is True

    @pytest.mark.asyncio
    async def test_slack_adapter_reject(self):
        from kazma_gateway.adapters.slack_bus import SlackBusAdapter

        adapter = SlackBusAdapter(bot_token="fake", channel_id="C123")
        task_id = "slack-deny"

        event = asyncio.Event()
        adapter._pending_approvals[task_id] = event

        resolved = adapter.handle_callback(f"swarm_reject_{task_id}")
        assert resolved == task_id
        assert adapter._pending_results[task_id] is False

    @pytest.mark.asyncio
    async def test_telegram_adapter_approve(self):
        from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter

        adapter = TelegramBusAdapter(bot_token="fake", chat_id="123")
        task_id = "tg-task"

        event = asyncio.Event()
        adapter._pending_approvals[task_id] = event

        resolved = adapter.handle_callback(f"swarm_approve_{task_id}")
        assert resolved == task_id
        assert event.is_set()
        assert adapter._pending_results[task_id] is True

    @pytest.mark.asyncio
    async def test_approval_timeout_returns_false_discord(self):
        """request_approval returns False on timeout (no callback)."""
        from kazma_gateway.adapters.discord_bus import DiscordBusAdapter
        from kazma_core.swarm.bus import ApprovalRequest

        adapter = DiscordBusAdapter(bot_token="fake", channel_id="123")
        # Monkeypatch _post_message to avoid real HTTP; return None (no msg id)
        adapter._post_message = lambda payload: asyncio.sleep(0, result=None)  # type: ignore

        # Use a very short timeout for the test
        import kazma_gateway.adapters.discord_bus as db_mod
        original_timeout = db_mod._APPROVAL_TIMEOUT
        db_mod._APPROVAL_TIMEOUT = 0.5
        try:
            approval = ApprovalRequest(
                worker_name="test",
                task_description="test task",
                proposed_output="danger tool",
                task_id="timeout-task",
            )
            result = await adapter.request_approval(approval)
            assert result is False  # timed out
        finally:
            db_mod._APPROVAL_TIMEOUT = original_timeout


# ══════════════════════════════════════════════════════════════════════════
# Phase 4: Gateway approval prompt helper
# ══════════════════════════════════════════════════════════════════════════


class TestApprovalPrompt:
    """_build_approval_prompt produces text + markup for the platform."""

    def test_prompt_contains_tool_and_args(self):
        from kazma_gateway.agent_handler import _build_approval_prompt

        payload = {
            "type": "hitl_approval",
            "tool": "shell_exec",
            "args": {"command": "rm -rf /tmp/test"},
            "message": "Agent wants to run: shell_exec",
        }
        prompt = _build_approval_prompt(payload, "thread-123")
        assert "shell_exec" in prompt["text"]
        assert "thread-123" in prompt["text"]
        assert "/hitl" in prompt["text"]

    def test_prompt_markup_is_keyboard(self):
        """When TelegramAdapter is available, markup should be a keyboard dict."""
        from kazma_gateway.agent_handler import _build_approval_prompt

        payload = {"type": "hitl_approval", "tool": "file_write", "args": {}}
        prompt = _build_approval_prompt(payload, "thread-456")
        # markup may be None on non-Telegram platforms, but if present
        # it must have an inline_keyboard key.
        if prompt["markup"] is not None:
            assert "inline_keyboard" in prompt["markup"]


# ══════════════════════════════════════════════════════════════════════════
# Phase 2: tool_registry HITL flag prevents double-gating
# ══════════════════════════════════════════════════════════════════════════


class TestToolRegistryHitlFlag:
    """The _hitl_approved flag skips the redundant bus check."""

    @pytest.mark.asyncio
    async def test_hitl_approved_flag_skips_gate(self):
        """When _hitl_approved=True, file_write should execute (not blocked)."""
        from kazma_core.agent.tool_registry import LocalToolRegistry
        import tempfile
        from pathlib import Path

        registry = LocalToolRegistry(include_builtins=True)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            path = f.name

        try:
            # _hitl_approved=True should bypass the bus gate entirely
            result = await registry.execute(
                "file_write",
                {"path": path, "content": "approved test", "_hitl_approved": True},
            )
            assert result["is_error"] is False
            assert "chars" in result["content"]
        finally:
            Path(path).unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════════════════
# Phase 1: SSE approval_required frame emission
# ══════════════════════════════════════════════════════════════════════════


class _MockInterrupt:
    """Mock LangGraph interrupt object."""
    def __init__(self, value):
        self.value = value


class _MockTask:
    """Mock LangGraph PregelTask."""
    def __init__(self, interrupts):
        self.interrupts = interrupts


class _MockSnapshot:
    """Mock StateSnapshot."""
    def __init__(self, next_nodes, tasks):
        self.next = next_nodes
        self.tasks = tasks


class _MockGraph:
    """Mock graph that pauses at an interrupt (astream_events yields nothing)."""

    def __init__(self, snapshot):
        self._snapshot = snapshot

    async def astream_events(self, input_state, config=None, version="v2"):
        # Simulate the graph pausing immediately at interrupt() — no events.
        return
        yield  # make it an async generator

    async def aget_state(self, config=None):
        return self._snapshot


class TestSseApprovalFrame:
    """Verify _stream_langgraph_events emits approval_required on interrupt."""

    @pytest.mark.asyncio
    async def test_emits_approval_required_frame(self):
        """When the graph is interrupted, an approval_required SSE frame is yielded."""
        from kazma_ui.sse_chat import _stream_langgraph_events

        snapshot = _MockSnapshot(
            next_nodes=("worker",),
            tasks=[_MockTask([_MockInterrupt({
                "type": "hitl_approval",
                "tool": "shell_exec",
                "args": {"command": "rm -rf /tmp"},
                "message": "Agent wants to run: shell_exec",
            })])],
        )
        graph = _MockGraph(snapshot)
        config = {"configurable": {"thread_id": "sse-test-1"}}

        frames = []
        async for frame in _stream_langgraph_events(graph, {"messages": []}, config):
            frames.append(frame)

        # Should have an approval_required frame AND a done frame.
        approval_frames = [f for f in frames if "approval_required" in f]
        assert len(approval_frames) == 1, f"Expected 1 approval frame, got {len(approval_frames)}"
        # Verify the frame contains the thread_id and tool name.
        frame = approval_frames[0]
        assert "sse-test-1" in frame
        assert "shell_exec" in frame

    @pytest.mark.asyncio
    async def test_no_frame_when_graph_completes(self):
        """When the graph completes normally, no approval_required frame."""
        from kazma_ui.sse_chat import _stream_langgraph_events

        # snapshot.next is empty → graph completed normally
        snapshot = _MockSnapshot(next_nodes=(), tasks=[])
        graph = _MockGraph(snapshot)
        config = {"configurable": {"thread_id": "sse-test-2"}}

        frames = []
        async for frame in _stream_langgraph_events(graph, {"messages": []}, config):
            frames.append(frame)

        approval_frames = [f for f in frames if "approval_required" in f]
        assert len(approval_frames) == 0, "No approval frame on normal completion"
