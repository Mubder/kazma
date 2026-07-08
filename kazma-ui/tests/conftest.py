"""Shared pytest fixtures for kazma-ui tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_agent():
    """Create a mock KazmaAgent for testing."""
    agent = MagicMock()
    agent.config = MagicMock()
    agent.config.raw = {}
    agent.config.version = "test"
    agent.llm = AsyncMock()
    agent.tools = AsyncMock()
    agent.tools.get_tool_definitions = MagicMock(return_value=[])
    agent.tools.execute = AsyncMock(return_value={"content": "ok"})
    agent.cost_breaker = MagicMock()
    agent.cost_breaker.should_halt = MagicMock(return_value=False)
    agent.cost_breaker.record_user_interaction = MagicMock()
    agent.system_prompt = "Test system prompt"
    agent.llm = AsyncMock()
    agent.llm = MagicMock()
    return agent


@pytest.fixture
def mock_config_store():
    """Create a mock ConfigStore."""
    from unittest.mock import MagicMock
    store = MagicMock()
    store.get = MagicMock(return_value=None)
    store.set = MagicMock()
    store.get_all = MagicMock(return_value={})
    return store


@pytest.fixture
def mock_session_manager():
    """Create a mock SessionManager."""
    from unittest.mock import MagicMock
    from kazma_ui.session_manager import ChatSession
    
    manager = MagicMock()
    manager.get_or_create = MagicMock(return_value=ChatSession(
        session_id="test-session",
        messages=[],
        total_cost=0.0,
        total_tokens=0
    ))
    manager.list_all = MagicMock(return_value=[])
    manager.get = MagicMock(return_value=None)
    manager.delete = MagicMock()
    return manager


@pytest.fixture
def sample_chat_session():
    """Create a sample ChatSession with messages."""
    from kazma_ui.session_manager import ChatSession
    from datetime import UTC, datetime
    
    return ChatSession(
        session_id="test-session",
        messages=[
            {"role": "user", "content": "Hello", "timestamp": datetime.now(UTC).isoformat()},
            {"role": "assistant", "content": "Hi there!", "timestamp": datetime.now(UTC).isoformat()},
        ],
        total_cost=0.001,
        total_tokens=50
    )