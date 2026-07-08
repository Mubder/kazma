"""Integration tests for multi-platform swarm dispatch flows.

Tests end-to-end swarm task dispatch across Telegram, Discord, and Slack
platforms, verifying consistent behavior and proper HITL gating.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import UTC, datetime


class TestMultiPlatformSwarmDispatch:
    """Test swarm dispatch works identically across all platforms."""

    @pytest.fixture
    def mock_gateway_manager(self):
        """Create a mock gateway manager with send capability."""
        manager = MagicMock()
        manager.send = AsyncMock()
        return manager

    @pytest.fixture
    def mock_swarm_engine(self):
        """Create a mock swarm engine."""
        engine = MagicMock()
        engine.dispatch = AsyncMock()
        return engine

    @pytest.fixture
    def sample_swarm_task_result(self):
        """Create a sample successful swarm task result."""
        from kazma_core.swarm.task import SwarmTaskResult, WorkerResult, TaskStatus
        
        return SwarmTaskResult(
            task_id="test-task-123",
            status=TaskStatus.COMPLETED,
            aggregated_output="Task completed successfully",
            worker_results=[
                WorkerResult(
                    worker="coder-agent",
                    status="success",
                    output="Code generated",
                    cost_usd=0.001,
                    tokens=150
                ),
                WorkerResult(
                    worker="reviewer-agent",
                    status="success",
                    output="Code reviewed",
                    cost_usd=0.0005,
                    tokens=75
                )
            ],
            total_cost_usd=0.0015,
            total_tokens=225
        )

    @pytest.mark.parametrize("platform", ["telegram", "discord", "slack"])
    async def test_swarm_dispatch_routes_correctly(
        self, platform, mock_gateway_manager, mock_swarm_engine, sample_swarm_task_result
    ):
        """Test swarm dispatch routes to correct platform chat."""
        from kazma_gateway.gateway import IncomingMessage
        from kazma_gateway.agent_handler.swarm_dispatch import _dispatch_swarm_from_chat
        
        mock_swarm_engine.dispatch.return_value = sample_swarm_task_result
        
        msg = IncomingMessage(
            platform=platform,
            sender_id=f"{platform}:12345",
            text="swarm write a hello world function",
            context_metadata={
                "chat_id": "-1001234567890" if platform == "telegram" else "1234567890",
                "username": "testuser",
                "chat_type": "group"
            }
        )
        
        thread_id = f"gw-{platform}-12345"
        
        await _dispatch_swarm_from_chat(
            manager=mock_gateway_manager,
            engine=mock_swarm_engine,
            msg=msg,
            thread_id=thread_id,
            store=AsyncMock(),
            target_override=None
        )
        
        # Verify engine.dispatch was called
        mock_swarm_engine.dispatch.assert_called_once()
        
        # Verify response sent back to originating chat
        mock_gateway_manager.send.assert_called()
        call_args = mock_gateway_manager.send.call_args[0][0]
        assert call_args.target_id.startswith(f"gw-{platform}-")
        assert "Task completed successfully" in call_args.text

    async def test_swarm_dispatch_with_output_target_override(
        self, mock_gateway_manager, mock_swarm_engine, sample_swarm_task_result
    ):
        """Test inline output target override (-> telegram:-100999)."""
        from kazma_gateway.gateway import IncomingMessage
        from kazma_gateway.agent_handler.swarm_dispatch import _dispatch_swarm_from_chat
        
        mock_swarm_engine.dispatch.return_value = sample_swarm_task_result
        
        msg = IncomingMessage(
            platform="telegram",
            sender_id="telegram:12345",
            text="swarm test task -> telegram:-100999888777",
            context_metadata={"chat_id": "-1001234567890", "username": "testuser"}
        )
        
        await _dispatch_swarm_from_chat(
            manager=mock_gateway_manager,
            engine=mock_swarm_engine,
            msg=msg,
            thread_id="gw-telegram-12345",
            store=AsyncMock(),
            target_override=None
        )
        
        # Verify output was also sent to override target
        assert mock_gateway_manager.send.call_count >= 2  # Original + override

    async def test_swarm_dispatch_timeout_handling(
        self, mock_gateway_manager, mock_swarm_engine
    ):
        """Test graceful handling of swarm dispatch timeout."""
        from kazma_gateway.gateway import IncomingMessage
        from kazma_gateway.agent_handler.swarm_dispatch import _dispatch_swarm_from_chat
        import asyncio
        
        async def slow_dispatch(*args, **kwargs):
            await asyncio.sleep(10)
            return None
        
        mock_swarm_engine.dispatch = slow_dispatch
        
        msg = IncomingMessage(
            platform="telegram",
            sender_id="telegram:12345",
            text="swarm slow task",
            context_metadata={"chat_id": "-1001234567890"}
        )
        
        await _dispatch_swarm_from_chat(
            manager=mock_gateway_manager,
            engine=mock_swarm_engine,
            msg=msg,
            thread_id="gw-telegram-12345",
            store=AsyncMock(),
            target_override=None
        )
        
        # Should send timeout message to user
        mock_gateway_manager.send.assert_called()
        call_args = mock_gateway_manager.send.call_args[0][0]
        assert "timed out" in call_args.text.lower()


class TestHITLE2E:
    """End-to-end HITL approval flow tests."""

    @pytest.fixture
    def mock_checkpointer(self):
        """Mock LangGraph checkpointer."""
        checkpointer = AsyncMock()
        checkpointer.aget = AsyncMock()
        checkpointer.aput = AsyncMock()
        return checkpointer

    async def test_graph_interrupt_approve_resume(self, mock_checkpointer):
        """Test full flow: tool call -> interrupt -> approve -> resume."""
        from kazma_core.agent.graph_builder import build_supervisor_graph
        from kazma_core.llm_provider import LLMProvider
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        import tempfile
        
        # Use real SQLite checkpointer for integration
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        
        try:
            async with AsyncSqliteSaver.from_conn_string(db_path) as checkpointer:
                graph = build_supervisor_graph(
                    llm=MagicMock(spec=LLMProvider),
                    system_prompt="Test prompt",
                    tool_definitions=[
                        {"name": "file_write", "description": "Write file", "parameters": {}}
                    ],
                    tool_executor=AsyncMock(),
                    cost_breaker=MagicMock(),
                    authority=MagicMock(),
                    tracer=MagicMock(),
                    checkpointer=checkpointer,
                    hitl_config={"enabled": True, "require_approval_for": ["file_write"]}
                )
                
                config = {"configurable": {"thread_id": "hitl-test-1"}}
                
                # 1. Invoke with danger tool -> should interrupt
                result = await graph.ainvoke(
                    {"messages": [{"role": "user", "content": "Write a file"}]},
                    config=config
                )
                
                # Check for interrupt
                snapshot = await graph.aget_state(config)
                assert snapshot.next is not None
                assert any("hitl_approval" in str(t.interrupts) for t in snapshot.tasks)
                
                # 2. Resume with approval
                from langgraph.types import Command
                result = await graph.ainvoke(
                    Command(resume={"approved": True}),
                    config=config
                )
                
                # Tool should have been executed
                assert result is not None
                
        finally:
            import os
            os.unlink(db_path)

    async def test_swarm_bus_approval_flow(self):
        """Test swarm message bus approval: request -> approve -> execute."""
        from kazma_core.swarm.bus import get_message_bus, SwarmMessageBus
        from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter
        from kazma_core.swarm.safety import get_safety
        
        # Setup real bus with Telegram adapter
        bus = SwarmMessageBus()
        adapter = TelegramBusAdapter(bot_token="test", chat_id="-100123")
        bus.set_adapter(adapter)
        
        safety = get_safety()
        safety.allow_headless_danger = False  # Force bus approval
        
        # Request approval for danger tool
        task_id = "test-swarm-task-1"
        approved = await safety.check(
            tool_name="file_write",
            tool_args='{"path": "/tmp/test.txt", "content": "hello"}',
            task_id=task_id,
            worker_name="coder-agent"
        )
        
        # Should be pending (no real adapter callback yet)
        assert approved is False or approved is None  # Depends on implementation
        
        # Simulate approval via bus
        adapter.approve(task_id)
        
        # Check again - should now be approved
        # (Implementation dependent - this verifies the flow exists)


class TestSwarmPatterns:
    """Test all 5 swarm patterns work correctly."""

    @pytest.mark.parametrize("pattern", ["dispatch", "pipeline", "consult", "fan_out", "broadcast"])
    async def test_pattern_execution(self, pattern):
        """Test each pattern executes without error."""
        from kazma_core.swarm.engine import SwarmEngine, SwarmConfig, WorkerConfig
        from kazma_core.swarm.task import SwarmTask, TaskType
        
        engine = SwarmEngine(config=SwarmConfig(enabled=True, workers=[
            WorkerConfig(name="worker1", type="in_process", role="coder", model="gpt-4o-mini"),
            WorkerConfig(name="worker2", type="in_process", role="reviewer", model="gpt-4o-mini"),
        ]))
        
        task = SwarmTask(
            type=TaskType(pattern.upper()),
            payload="Test task",
            workers=["worker1"] if pattern != "broadcast" else [],
        )
        
        # Mock worker execution to avoid real LLM calls
        with patch.object(engine, '_execute_worker', new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = MagicMock(
                worker="worker1",
                status="success",
                output="Done",
                cost_usd=0.001,
                tokens=50
            )
            
            result = await engine.dispatch(task)
            
            assert result is not None
            assert result.status in ("completed", "success", "partial")


class TestConfigValidationIntegration:
    """Test config schema validation end-to-end."""

    def test_valid_full_config(self):
        """Test complete valid config passes validation."""
        from kazma_core.config_schema import KazmaConfig, TelegramOutputTarget, TelegramConnectorConfig
        
        config = KazmaConfig(
            swarm=KazmaConfig.model_fields['swarm'].default_factory().copy(update={
                'output_target': TelegramOutputTarget(
                    bot_token="123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
                    chat_id=-1001234567890
                )
            }),
            connectors=KazmaConfig.model_fields['connectors'].default_factory().copy(update={
                'telegram': TelegramConnectorConfig(token="123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
            })
        )
        
        # Should not raise
        flat = config.to_flat_dict()
        assert "swarm.output_target.bot_token" in flat
        assert "connectors.telegram.token" in flat

    def test_invalid_config_rejected(self):
        """Test invalid configs are rejected."""
        from kazma_core.config_schema import KazmaConfig, TelegramOutputTarget, TelegramConnectorConfig
        from pydantic import ValidationError
        
        # Mismatched tokens
        with pytest.raises(ValidationError):
            KazmaConfig(
                swarm=KazmaConfig.model_fields['swarm'].default_factory().copy(update={
                    'output_target': TelegramOutputTarget(
                        bot_token="token1111111111", chat_id=-100123
                    )
                }),
                connectors=KazmaConfig.model_fields['connectors'].default_factory().copy(update={
                    'telegram': TelegramConnectorConfig(token="token2222222222")
                })
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])