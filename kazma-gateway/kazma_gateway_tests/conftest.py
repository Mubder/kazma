"""Shared pytest fixtures for kazma-gateway tests."""

import pytest


@pytest.fixture
def mock_agent_manager():
    """Create a mock AgentManager for testing."""
    from unittest.mock import AsyncMock, MagicMock
    
    manager = MagicMock()
    manager.config = MagicMock()
    manager.config.raw = {}
    manager.agent = MagicMock()
    manager.agent.tools = MagicMock()
    manager.agent.tools.execute = AsyncMock()
    manager.agent.tools.get_tool_names = MagicMock(return_value=[])
    return manager


@pytest.fixture
def mock_swarm_engine():
    """Create a mock SwarmEngine for testing."""
    from unittest.mock import AsyncMock, MagicMock
    
    engine = MagicMock()
    engine.dispatch = AsyncMock()
    engine.approve_checkpoint = AsyncMock()
    engine.reject_checkpoint = AsyncMock()
    return engine


@pytest.fixture
def sample_swarm_task():
    """Create a sample SwarmTask for testing."""
    from kazma_core.swarm.task import SwarmTask, TaskType
    
    return SwarmTask(
        type=TaskType.DISPATCH,
        payload="Test task",
        metadata={},
    )