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
# Phase 1b: MCP force_danger parity (C1 regression helpers)

class TestMcpForceDangerParity:
    """MCP names must be forceable as danger even when not in static list."""

    def test_mcp_names_not_in_static_danger_list(self):
        from kazma_core.swarm.safety import SafetyMiddleware
        s = SafetyMiddleware(enabled=True, allow_headless_danger=False)
        for name in ("write_file", "run_command", "execute_code", "delete_file"):
            assert s.is_danger_tool(name) is False
            assert s.check_sync(name, force_danger=True) is False

    def test_builtin_names_still_danger(self):
        from kazma_core.swarm.safety import SafetyMiddleware
        s = SafetyMiddleware(enabled=True, allow_headless_danger=False)
        for name in ("file_write", "shell_exec", "file_delete"):
            assert s.is_danger_tool(name) is True
            assert s.check_sync(name) is False


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
        assert safety.check_sync("memory_search") is True

    def test_outbound_message_tool_blocked_without_bus(self):
        """``send_message`` dispatches to Telegram/Discord/Slack, so it is a
        danger tool and must block headless (audit F-04 reclassified it — it
        used to be tier 'write' and ran with no approval)."""
        safety = SafetyMiddleware(enabled=True, allow_headless_danger=False)
        assert safety.check_sync("send_message") is False
        assert safety.check_sync("send_file") is False

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


class TestSafetyFailClosedAsync:
    """check (async) must mirror check_sync and block danger tools when no
    real bus adapter is wired.

    Regression guard: the async path must not call through to a
    fail-open adapter. ``NullBusAdapter.request_approval()`` returns
    ``False`` (fail-closed); SafetyMiddleware also short-circuits before
    the bus when only NullBus is present. Both paths must stay fail-closed.
    """

    @pytest.fixture(autouse=True)
    def _reset_bus(self):
        """Ensure each test starts with a fresh NullBusAdapter on the singleton."""
        from kazma_core.swarm.bus import NullBusAdapter, SwarmMessageBus, get_message_bus
        bus = get_message_bus()
        original = bus.adapter
        bus._adapter = NullBusAdapter()  # no real platform adapter
        yield
        bus._adapter = original

    @pytest.mark.asyncio
    async def test_async_danger_tool_blocked_without_bus(self):
        """Async path blocks danger tools when no real bus is present."""
        safety = SafetyMiddleware(enabled=True, allow_headless_danger=False)
        assert await safety.check("shell_exec") is False
        assert await safety.check("file_write") is False
        assert await safety.check("file_delete") is False
        assert safety.stats()["rejected_count"] >= 3

    @pytest.mark.asyncio
    async def test_async_safe_tool_allowed_without_bus(self):
        """Async path still lets non-danger tools through with no bus."""
        safety = SafetyMiddleware(enabled=True, allow_headless_danger=False)
        assert await safety.check("file_read") is True
        assert await safety.check("memory_search") is True

    @pytest.mark.asyncio
    async def test_async_outbound_message_tool_blocked_without_bus(self):
        """Async mirror of the sync case: ``send_message`` is danger (F-04)."""
        safety = SafetyMiddleware(enabled=True, allow_headless_danger=False)
        assert await safety.check("send_message") is False

    @pytest.mark.asyncio
    async def test_async_disabled_safety_allows_all(self):
        """Disabled safety short-circuits the async path too."""
        safety = SafetyMiddleware(enabled=False)
        assert await safety.check("shell_exec") is True

    @pytest.mark.asyncio
    async def test_async_headless_escape_hatch_allows_danger(self):
        """allow_headless_danger=True still permits danger tools (test/dev)."""
        safety = SafetyMiddleware(enabled=True, allow_headless_danger=True)
        assert await safety.check("shell_exec") is True


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

    def test_custom_danger_list_adds_but_cannot_un_gate(self):
        """A custom ``require_approval_for`` extends the gate; it is not a floor.

        Behaviour change from audit F-04. This list used to be the *whole*
        policy, so narrowing it silently un-gated `shell_exec` and every other
        destructive tool — "open by omission", the same hazard the HTTP layer
        already default-denies against. The list now *adds* to the tier
        classification rather than replacing it: a tool tiered ``danger`` in
        ``TOOL_TIERS`` always requires approval.

        To actually run a danger tool without prompting, use YOLO mode or an
        explicit per-tool grant — both are deliberate, audited, and revocable.
        """
        cfg = get_hitl_config({
            "safety": {"hitl": {"require_approval_for": ["custom_tool"]}}
        })
        assert requires_approval("custom_tool", cfg) is True
        assert requires_approval("shell_exec", cfg) is True
        # Read-only tools stay ungated regardless of the custom list.
        assert requires_approval("file_read", cfg) is False

    def test_disabled(self):
        cfg = get_hitl_config({"safety": {"hitl": {"enabled": False}}})
        assert requires_approval("shell_exec", cfg) is False

    def test_default_danger_tools(self):
        # Vault tools (vault_retrieve, vault_delete) were added to protect
        # secret access. The original 3 are always present.
        assert "file_write" in DEFAULT_DANGER_TOOLS
        assert "file_delete" in DEFAULT_DANGER_TOOLS
        assert "shell_exec" in DEFAULT_DANGER_TOOLS
        assert "vault_retrieve" in DEFAULT_DANGER_TOOLS
        assert "vault_delete" in DEFAULT_DANGER_TOOLS

    def test_canonical_danger_list_is_single_source(self):
        """Graph defaults, swarm bus, and hitl module must share one list."""
        from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS
        from kazma_core.swarm.safety import SafetyMiddleware, _EXTENDED_DANGER

        assert set(DEFAULT_DANGER_TOOLS) == set(CANONICAL_DANGER_TOOLS)
        assert set(_EXTENDED_DANGER) == set(CANONICAL_DANGER_TOOLS)

        safety = SafetyMiddleware(enabled=True, allow_headless_danger=False)
        for name in CANONICAL_DANGER_TOOLS:
            assert safety.is_danger_tool(name), f"{name} missing from SafetyMiddleware"

    def test_yaml_require_approval_matches_canonical(self):
        """kazma.yaml must not drift from CANONICAL_DANGER_TOOLS."""
        from pathlib import Path

        import yaml

        from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS

        root = Path(__file__).resolve().parents[1]
        yaml_path = root / "kazma.yaml"
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        listed = set(data["safety"]["hitl"]["require_approval_for"])
        assert listed == set(CANONICAL_DANGER_TOOLS), (
            f"yaml/list drift: missing={set(CANONICAL_DANGER_TOOLS) - listed} "
            f"extra={listed - set(CANONICAL_DANGER_TOOLS)}"
        )

    def test_tool_tiers(self):
        assert get_tool_tier("file_read") == "read"
        assert get_tool_tier("file_write") == "danger"
        assert get_tool_tier("file_apply_patch") == "danger"
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
            "message": "Agent wants to run: shell_exec(command=rm -rf /tmp/test)",
        }
        prompt = _build_approval_prompt(payload, "thread-123")
        assert "shell_exec" in prompt["text"]
        assert "thread-123" in prompt["text"]
        # Slash-less form for Slack (leading / would trigger slash-command interception)
        assert "hitl approve" in prompt["text"] or "/hitl" in prompt["text"]

    def test_prompt_markup_is_keyboard(self):
        """When TelegramAdapter is available, markup should be a keyboard dict."""
        from kazma_gateway.agent_handler import _build_approval_prompt

        payload = {"type": "hitl_approval", "tool": "file_write", "args": {}}
        prompt = _build_approval_prompt(payload, "thread-456")
        # markup may be None on non-Telegram platforms, but if present
        # it must have an inline_keyboard key.
        if prompt["markup"] is not None:
            assert "inline_keyboard" in prompt["markup"]

    def test_prompt_lists_each_tool_in_a_batch(self):
        from kazma_gateway.agent_handler import _build_approval_prompt

        payload = {
            "type": "hitl_approval",
            "tool": "2 tools",
            "args": {"tools": ["file_write", "file_write"]},
            "tools": [
                {"name": "file_write", "args": {"path": "notes/a.md"}},
                {"name": "file_write", "args": {"path": "drafts/b.md"}},
            ],
        }
        prompt = _build_approval_prompt(payload, "tid-batch")
        assert prompt["text"].count("Approval required") == 1
        assert "notes/a.md" in prompt["text"]
        assert "drafts/b.md" in prompt["text"]
        assert "hitl approve tid-batch" in prompt["text"]


# ══════════════════════════════════════════════════════════════════════════
# Phase 2: tool_registry HITL flag prevents double-gating
# ══════════════════════════════════════════════════════════════════════════


class TestToolRegistryHitlFlag:
    """The _hitl_approved flag skips the redundant bus check."""

    @pytest.mark.asyncio
    async def test_hitl_approved_flag_skips_gate(self):
        """When ContextVar _hitl_approved is set, file_write should execute (not blocked).

        The _hitl_approved key in LLM args is always stripped and never
        honored — only the ContextVar set by graph_builder is trusted.
        """
        from kazma_core.agent.tool_registry import LocalToolRegistry, _hitl_approved_ctx
        import tempfile
        from pathlib import Path

        registry = LocalToolRegistry(include_builtins=True)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            path = f.name

        try:
            # Set the ContextVar (as graph_builder does after interrupt() approval)
            token = _hitl_approved_ctx.set(True)
            try:
                # _hitl_approved in args should be stripped, not honored
                result = await registry.execute(
                    "file_write",
                    {"path": path, "content": "approved test", "_hitl_approved": True},
                )
            finally:
                _hitl_approved_ctx.reset(token)
            assert result["is_error"] is False
            assert "bytes" in result["content"]
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
    def __init__(self, next_nodes, tasks, values=None):
        self.next = next_nodes
        self.tasks = tasks
        self.values = values if values is not None else {}


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
                "message": "Agent wants to run: shell_exec(command=rm -rf /tmp)",
            })])],
        )
        graph = _MockGraph(snapshot)
        config = {"configurable": {"thread_id": "sse-test-1"}}

        frames = []
        try:
            async for frame in _stream_langgraph_events(graph, {"messages": []}, config):
                frames.append(frame)
        finally:
            from kazma_ui.sse_chat._streaming import mark_thread_unpaused

            mark_thread_unpaused("sse-test-1")

        # Pause journals the card. It must NOT emit terminal done/turn_complete
        # (that closed the HTTP tail and painted "Error: network error").
        approval_frames = [f for f in frames if "approval_required" in f]
        assert len(approval_frames) == 1, f"Expected 1 approval frame, got {len(approval_frames)}"
        # Verify the frame contains the thread_id and tool name.
        frame = approval_frames[0]
        assert "sse-test-1" in frame
        assert "shell_exec" in frame
        assert not any("event: done" in f for f in frames)
        assert not any("event: turn_complete" in f for f in frames)

    @pytest.mark.asyncio
    async def test_no_frame_when_graph_completes(self):
        """When the graph completes normally, no approval_required frame."""
        from kazma_ui.sse_chat import _stream_langgraph_events

        # snapshot.next is empty → graph completed normally
        snapshot = _MockSnapshot(
            next_nodes=(),
            tasks=[],
            values={"messages": [{"role": "assistant", "content": "Hello"}]},
        )
        graph = _MockGraph(snapshot)
        config = {"configurable": {"thread_id": "sse-test-2"}}

        frames = []
        async for frame in _stream_langgraph_events(graph, {"messages": []}, config):
            frames.append(frame)

        approval_frames = [f for f in frames if "approval_required" in f]
        assert len(approval_frames) == 0, "No approval frame on normal completion"
        # Custom LLM path has no stream events — backfill from checkpoint
        token_frames = [f for f in frames if "event: token" in f or '"content"' in f and "Hello" in f]
        assert any("Hello" in f for f in frames)

    @pytest.mark.asyncio
    async def test_empty_turn_emits_recovery_notice(self):
        """Never leave the UI with only Thinking… — emit a recovery token."""
        from kazma_ui.sse_chat import _stream_langgraph_events

        snapshot = _MockSnapshot(next_nodes=(), tasks=[], values={"messages": []})
        graph = _MockGraph(snapshot)
        config = {"configurable": {"thread_id": "sse-empty"}}

        frames = []
        async for frame in _stream_langgraph_events(graph, {"messages": []}, config):
            frames.append(frame)

        assert any("No assistant text" in f or "try again" in f.lower() for f in frames)
        assert any("event: done" in f or "done" in f for f in frames)

    @pytest.mark.asyncio
    async def test_hitl_payload_without_type_tag(self):
        """tool/args shape without type=hitl_approval still surfaces a card."""
        from kazma_ui.sse_chat import _stream_langgraph_events

        snapshot = _MockSnapshot(
            next_nodes=("tool_worker",),
            tasks=[_MockTask([_MockInterrupt({
                "tool": "shell_exec",
                "args": {"command": "ls"},
                "message": "need approval",
            })])],
        )
        graph = _MockGraph(snapshot)
        config = {"configurable": {"thread_id": "sse-fallback-type"}}

        frames = []
        try:
            async for frame in _stream_langgraph_events(graph, {"messages": []}, config):
                frames.append(frame)
        finally:
            from kazma_ui.sse_chat._streaming import mark_thread_unpaused

            mark_thread_unpaused("sse-fallback-type")

        assert any("approval_required" in f for f in frames)
        assert any("shell_exec" in f for f in frames)

    @pytest.mark.asyncio
    async def test_hard_steer_pause_is_not_terminal(self):
        """Hard-steer interrupt must not emit done — the attach stays up."""
        from kazma_ui.sse_chat import _stream_langgraph_events
        from kazma_ui.sse_chat._streaming import mark_thread_unpaused

        snapshot = _MockSnapshot(
            next_nodes=("supervisor",),
            tasks=[_MockTask([_MockInterrupt({
                "type": "hard_steer",
                "text": "do it in Arabic",
                "message": "The user paused this task to add a requirement.",
            })])],
            values={"messages": [{"role": "assistant", "content": "working on it"}]},
        )
        graph = _MockGraph(snapshot)
        config = {"configurable": {"thread_id": "sse-steer-1"}}
        frames = []
        try:
            async for frame in _stream_langgraph_events(
                graph, {"messages": []}, config
            ):
                frames.append(frame)
        finally:
            mark_thread_unpaused("sse-steer-1")
        assert not any("event: done" in f for f in frames)
        assert not any("event: turn_complete" in f for f in frames)
        assert not any("approval_required" in f for f in frames)


# ══════════════════════════════════════════════════════════════════════════
# Unresumable-interrupt elimination (audit H1/H2/F2/F9): checkpointer-less
# graphs must auto-deny instead of minting interrupt() pauses that can
# never be resumed by /api/approve.
# ══════════════════════════════════════════════════════════════════════════


def _mock_agent():
    """MagicMock KazmaAgent for bound-method graph-build tests.

    Mirrors the pattern in test_s4_coverage_gaps.py — the REAL method runs
    against the mock so build kwargs can be inspected.
    """
    from unittest.mock import MagicMock

    from kazma_core.agent_runner import AgentConfig, KazmaAgent

    agent = MagicMock(spec=KazmaAgent)
    agent._streaming_graph = None
    agent.config = AgentConfig(
        raw={"safety": {"hitl": {"enabled": True, "require_approval_for": ["shell_exec"]}}}
    )
    agent.llm = MagicMock()
    agent.system_prompt = "sys"
    agent.tools = MagicMock()
    agent.tools.get_tool_definitions.return_value = []
    agent.cost_breaker = MagicMock()
    agent.authority = MagicMock()
    agent.tracer = MagicMock()
    agent._snapshot_recorder = None
    return agent


class TestChildGraphAutoDeny:
    """build_child_graph (Fix 1): checkpointer-less children force auto_deny."""

    def test_no_checkpointer_injects_auto_deny(self):
        """Default (checkpointer-less) child graphs must carry auto_deny."""
        from unittest.mock import patch

        from kazma_core.agent_runner import KazmaAgent

        agent = _mock_agent()
        fake_graph = object()
        with (
            patch(
                "kazma_core.agent.graph_builder.build_supervisor_graph",
                return_value=fake_graph,
            ) as build,
            patch(
                "kazma_core.safety.hitl.get_hitl_config",
                return_value={"enabled": True, "require_approval_for": {"shell_exec"}},
            ),
        ):
            result = KazmaAgent.build_child_graph(agent)
        assert result is fake_graph
        kwargs = build.call_args.kwargs
        assert kwargs["checkpointer"] is None
        assert kwargs["hitl_config"]["auto_deny"] is True

    def test_checkpointer_backed_child_keeps_resumable_gate(self):
        """A caller passing a checkpointer keeps interrupt()-capable HITL."""
        from unittest.mock import patch

        from kazma_core.agent_runner import KazmaAgent

        agent = _mock_agent()
        cp = object()
        with (
            patch(
                "kazma_core.agent.graph_builder.build_supervisor_graph",
                return_value=object(),
            ) as build,
            patch(
                "kazma_core.safety.hitl.get_hitl_config",
                return_value={"enabled": True, "require_approval_for": {"shell_exec"}},
            ),
        ):
            KazmaAgent.build_child_graph(agent, checkpointer=cp)
        kwargs = build.call_args.kwargs
        assert kwargs["checkpointer"] is cp
        assert not kwargs["hitl_config"].get("auto_deny")

    def test_explicit_config_not_mutated_and_disabled_stays_none(self):
        """Caller-supplied config is copied (never mutated); disabled → None."""
        from unittest.mock import patch

        from kazma_core.agent_runner import KazmaAgent

        agent = _mock_agent()
        caller_cfg = {"enabled": True, "require_approval_for": {"shell_exec"}}
        with (
            patch(
                "kazma_core.agent.graph_builder.build_supervisor_graph",
                return_value=object(),
            ) as build,
        ):
            KazmaAgent.build_child_graph(agent, hitl_config=caller_cfg)
        assert caller_cfg.get("auto_deny") is None, "caller dict must not be mutated"
        assert build.call_args.kwargs["hitl_config"]["auto_deny"] is True

        with patch(
            "kazma_core.agent.graph_builder.build_supervisor_graph",
            return_value=object(),
        ) as build2:
            KazmaAgent.build_child_graph(agent, hitl_config={"enabled": False})
        assert build2.call_args.kwargs["hitl_config"] is None


class TestStreamingGraphAutoDeny:
    """get_streaming_graph (Fix 4, audit F2): the cached checkpointer-less
    streaming graph (voice WS + boot-window holder) must auto-deny."""

    def test_streaming_hitl_carries_auto_deny(self):
        from unittest.mock import patch

        from kazma_core.agent_runner import KazmaAgent

        agent = _mock_agent()
        fake_graph = object()
        with (
            patch(
                "kazma_core.agent.graph_builder.build_supervisor_graph",
                return_value=fake_graph,
            ) as build,
            patch(
                "kazma_core.safety.hitl.get_hitl_config",
                return_value={"enabled": True, "require_approval_for": {"shell_exec"}},
            ),
        ):
            result = KazmaAgent.get_streaming_graph(agent)
        assert result is fake_graph
        kwargs = build.call_args.kwargs
        assert kwargs.get("checkpointer") is None
        assert kwargs["hitl_config"]["enabled"] is True
        assert kwargs["hitl_config"]["auto_deny"] is True

    def test_streaming_hitl_disabled_stays_none(self):
        from unittest.mock import patch

        from kazma_core.agent_runner import KazmaAgent

        agent = _mock_agent()
        with (
            patch(
                "kazma_core.agent.graph_builder.build_supervisor_graph",
                return_value=object(),
            ) as build,
            patch(
                "kazma_core.safety.hitl.get_hitl_config",
                return_value={"enabled": False, "require_approval_for": set()},
            ),
        ):
            KazmaAgent.get_streaming_graph(agent)
        assert build.call_args.kwargs["hitl_config"] is None


class _InvokeMockGraph:
    """Mock child graph for SubAgentManager.spawn tests."""

    async def ainvoke(self, state, config=None):
        return {
            "messages": [
                *state.get("messages", []),
                {"role": "assistant", "content": "done"},
            ],
        }


class TestSubAgentInheritAutoDeny:
    """Fix 2 (audit H2): safety_mode="inherit" forces auto_deny — child
    graphs are checkpointer-less, so an inherited interrupt() pause could
    never be resumed."""

    @pytest.mark.asyncio
    async def test_inherit_mode_forces_auto_deny(self):
        from unittest.mock import patch

        from kazma_core.agent.sub_agent import SubAgentManager

        received = []

        def capture_builder(tools=None, hitl_config=None):
            received.append(hitl_config)
            return _InvokeMockGraph()

        manager = SubAgentManager(graph_builder=capture_builder, max_concurrent=3)
        with patch(
            "kazma_core.safety.hitl.get_hitl_config",
            return_value={"enabled": True, "require_approval_for": {"shell_exec"}},
        ):
            result = await manager.spawn(goal="g", safety_mode="inherit")

        assert result.status == "success"
        cfg = received[0]
        assert cfg is not None
        assert cfg["enabled"] is True
        assert cfg["auto_deny"] is True

    def test_build_hitl_config_inherit_static(self):
        from unittest.mock import patch

        from kazma_core.agent.sub_agent import SubAgentManager

        with patch(
            "kazma_core.safety.hitl.get_hitl_config",
            return_value={"enabled": True, "require_approval_for": {"file_write"}},
        ):
            cfg = SubAgentManager._build_hitl_config("inherit")
        assert cfg["auto_deny"] is True


class _GateRecordingExecutor:
    """Records tool calls + the graph-gate ContextVar value at execute time."""

    def __init__(self):
        self.calls = []
        self.gate_ctx_values = []

    async def execute(self, name, args):
        from kazma_core.agent.tool_registry import _graph_hitl_gate_ctx

        self.calls.append((name, dict(args)))
        self.gate_ctx_values.append(bool(_graph_hitl_gate_ctx.get()))
        return {"content": f"executed {name}", "is_error": False}


class _NoopTracer2:
    def trace_tool_execution(self, **kw):
        pass


def _tw_state(tool_name, args):
    from kazma_core.agent.state import initial_supervisor_state

    s = initial_supervisor_state(thread_id="t-hitl")
    s["messages"] = [{"role": "user", "content": f"run {tool_name}"}]
    s["tool_calls_pending"] = [{"id": "c1", "name": tool_name, "arguments": args}]
    return s


def _done_results(out):
    return list(out.get("tool_calls_done", []))


class TestToolWorkerUnbackedGate:
    """Fix 3 (audit F9): disabled/None hitl_config must not set the graph
    gate ContextVar, and ALWAYS_HITL_TOOLS must fail closed (deny) rather
    than mint an unresumable interrupt."""

    @pytest.mark.asyncio
    async def test_disabled_config_does_not_set_gate_ctx(self):
        """A safe tool under {"enabled": False} executes with the registry
        gate NOT suppressed (previously the truthy dict set it)."""
        from kazma_core.agent.graph_builder import tool_worker_node

        exe = _GateRecordingExecutor()
        state = _tw_state("file_read", {"path": "a.txt"})
        out = await tool_worker_node(
            state,
            tool_executor=exe,
            tracer=_NoopTracer2(),
            hitl_config={"enabled": False, "require_approval_for": {"shell_exec"}},
        )
        assert [c[0] for c in exe.calls] == ["file_read"]
        assert exe.gate_ctx_values == [False], "disabled config must not set the gate ContextVar"
        assert out["next_node"] in ("supervisor", "respond")

    @pytest.mark.asyncio
    async def test_always_hitl_tool_denied_on_disabled_config(self):
        """x_post with a DISABLED config is denied with a clear error —
        not executed, not interrupted."""
        from kazma_core.agent.graph_builder import tool_worker_node

        exe = _GateRecordingExecutor()
        state = _tw_state("x_post", {"content": "hello"})
        out = await tool_worker_node(
            state,
            tool_executor=exe,
            tracer=_NoopTracer2(),
            hitl_config={"enabled": False, "require_approval_for": set()},
        )
        assert exe.calls == [], "x_post must not execute"
        dones = _done_results(out)
        assert len(dones) == 1
        assert dones[0]["is_error"] is True
        # Context-integrity S1-3: x_post is now denied EARLIER by the
        # commitment layer's proposal gate (no resolvable proposal_id) —
        # still a hard deny, never executed, never interrupted.
        assert "DENIED" in dones[0]["content"] or "requires a proposal_id" in dones[0]["content"]

    @pytest.mark.asyncio
    async def test_always_hitl_tool_denied_on_none_config(self):
        """x_post with hitl_config=None is denied — previously this raised
        an unresumable GraphInterrupt on checkpointer-less graphs."""
        from kazma_core.agent.graph_builder import tool_worker_node

        exe = _GateRecordingExecutor()
        state = _tw_state("x_delete_post", {"post_id": "1"})
        out = await tool_worker_node(
            state, tool_executor=exe, tracer=_NoopTracer2(), hitl_config=None
        )
        assert exe.calls == []
        dones = _done_results(out)
        assert dones[0]["is_error"] is True
        assert "DENIED" in dones[0]["content"]

    @pytest.mark.asyncio
    async def test_auto_deny_config_denies_danger_tool(self):
        """enabled + auto_deny (child graphs, streaming graph) denies danger
        tools directly — the tool_worker half of Fixes 1/2/4."""
        from kazma_core.agent.graph_builder import tool_worker_node

        exe = _GateRecordingExecutor()
        state = _tw_state("shell_exec", {"command": "echo hi"})
        out = await tool_worker_node(
            state,
            tool_executor=exe,
            tracer=_NoopTracer2(),
            hitl_config={
                "enabled": True,
                "require_approval_for": {"shell_exec"},
                "auto_deny": True,
            },
        )
        assert exe.calls == []
        dones = _done_results(out)
        assert dones[0]["is_error"] is True
        assert "auto-denied" in dones[0]["content"]

    @pytest.mark.asyncio
    async def test_enabled_backed_config_still_interrupts(self):
        """Guard: an enabled config WITHOUT auto_deny keeps the resumable
        interrupt() gate (the checkpointed main/SSE graph path). Outside a
        graph runner the interrupt call surfaces as RuntimeError (bare
        call) / GraphInterrupt (inside a graph) — either way the tool must
        NOT have executed."""
        import pytest as _pytest
        from langgraph.errors import GraphInterrupt

        from kazma_core.agent.graph_builder import tool_worker_node

        exe = _GateRecordingExecutor()
        state = _tw_state("shell_exec", {"command": "echo hi"})
        with _pytest.raises((GraphInterrupt, RuntimeError)):
            await tool_worker_node(
                state,
                tool_executor=exe,
                tracer=_NoopTracer2(),
                hitl_config={
                    "enabled": True,
                    "require_approval_for": {"shell_exec"},
                },
            )
        assert exe.calls == []
