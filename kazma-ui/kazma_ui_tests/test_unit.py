"""Unit tests for kazma-ui components."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestChatRouter:
    """Tests for chat router endpoints."""

    def test_create_chat_router(self, mock_agent, mock_config_store):
        """Test chat router creation."""
        from kazma_ui.chat import create_chat_router
        from fastapi.templating import Jinja2Templates
        from jinja2 import Environment, BaseLoader
        
        # Mock templates
        env = Environment(loader=BaseLoader())
        templates = Jinja2Templates(env=env)
        
        router = create_chat_router(mock_agent, templates)
        assert router is not None
        assert hasattr(router, 'routes')


class TestSSEChatRouter:
    """Tests for SSE chat router."""

    def test_create_sse_chat_router(self, mock_agent, mock_config_store):
        """Test SSE chat router creation."""
        from kazma_ui.sse_chat import create_sse_chat_router
        
        router = create_sse_chat_router(
            graph_holder={},
            graph_getter=lambda: None,
            checkpointer=None,
            system_prompt="Test",
            cost_breaker=mock_agent.cost_breaker,
            authority=mock_agent.authority,
            tracer=mock_agent.tracer,
            provider_profile=mock_agent.registry.get_active_profile(),
            llm_provider=mock_agent.llm,
            registry=mock_agent.registry,
        )
        assert router is not None


class TestAgentsRouter:
    """Tests for agents router."""

    def test_create_agents_router(self, mock_agent, mock_config_store):
        """Test agents router creation."""
        from kazma_ui.agents import create_agents_router
        from fastapi.templating import Jinja2Templates
        from jinja2 import Environment, BaseLoader
        
        env = Environment(loader=BaseLoader())
        templates = Jinja2Templates(env=env)
        
        router = create_agents_router(mock_agent, templates)
        assert router is not None


class TestSettingsRouter:
    """Tests for settings router."""

    def test_create_settings_router(self, mock_agent, mock_config_store):
        """Test settings router creation."""
        from kazma_ui.settings import create_settings_router
        from fastapi.templating import Jinja2Templates
        from jinja2 import Environment, BaseLoader
        
        env = Environment(loader=BaseLoader())
        templates = Jinja2Templates(env=env)
        
        router = create_settings_router(mock_agent, mock_config_store, templates)
        assert router is not None


class TestSkillsRouter:
    """Tests for skills router."""

    def test_create_skills_router(self, mock_agent):
        """Test skills router creation."""
        from kazma_ui.skills_ui import create_skills_router
        from fastapi.templating import Jinja2Templates
        from jinja2 import Environment, BaseLoader
        
        env = Environment(loader=BaseLoader())
        templates = Jinja2Templates(env=env)
        
        router = create_skills_router(mock_agent, templates)
        assert router is not None


class TestMCPRouter:
    """Tests for MCP router."""

    def test_create_mcp_router(self, mock_agent):
        """Test MCP router creation."""
        from kazma_ui.mcp_ui import create_mcp_router
        from fastapi.templating import Jinja2Templates
        from jinja2 import Environment, BaseLoader
        
        env = Environment(loader=BaseLoader())
        templates = Jinja2Templates(env=env)
        
        router = create_mcp_router(mock_agent, templates)
        assert router is not None


class TestProvidersRouter:
    """Tests for providers router."""

    def test_create_providers_router(self, mock_config_store):
        """Test providers router creation."""
        from kazma_ui.providers import create_providers_router
        
        router = create_providers_router(mock_config_store)
        assert router is not None


class TestModelsRouter:
    """Tests for models router."""

    def test_create_models_router(self, mock_config_store):
        """Test models router creation."""
        from kazma_ui.models_route import create_models_router
        
        router = create_models_router(config_store=mock_config_store)
        assert router is not None


class TestSessionManager:
    """Tests for session management."""

    def test_create_session(self):
        """Test session creation."""
        from kazma_ui.session_manager import SessionManager, ChatSession
        
        manager = SessionManager()
        session = manager.get_or_create("test-session")
        
        assert isinstance(session, ChatSession)
        assert session.session_id == "test-session"
        assert session.messages == []

    def test_session_isolation(self):
        """Test sessions are isolated."""
        from kazma_ui.session_manager import SessionManager
        
        manager = SessionManager()
        s1 = manager.get_or_create("session-1")
        s2 = manager.get_or_create("session-2")
        
        s1.messages.append({"role": "user", "content": "Hello"})
        
        assert len(s1.messages) == 1
        assert len(s2.messages) == 0

    def test_list_sessions(self):
        """Test listing sessions."""
        from kazma_ui.session_manager import SessionManager
        
        manager = SessionManager()
        manager.get_or_create("session-1")
        manager.get_or_create("session-2")
        
        sessions = manager.list_all()
        assert len(sessions) == 2

    def test_delete_session(self):
        """Test session deletion."""
        from kazma_ui.session_manager import SessionManager
        
        manager = SessionManager()
        manager.get_or_create("to-delete")
        assert manager.get("to-delete") is not None
        
        manager.delete("to-delete")
        assert manager.get("to-delete") is None


class TestAuth:
    """Tests for auth middleware."""

    def test_create_auth_middleware(self):
        """Test auth middleware creation."""
        from kazma_ui.auth import create_auth_middleware
        
        middleware = create_auth_middleware()
        assert callable(middleware)


class TestHealth:
    """Tests for health endpoints."""

    def test_liveness_endpoint(self):
        """Test /health/live endpoint structure."""
        from kazma_ui.health import liveness
        
        import asyncio
        result = asyncio.run(liveness())
        assert result["status"] == "alive"
        assert "timestamp" in result

    def test_readiness_endpoint_structure(self):
        """Test /health/ready endpoint structure."""
        from kazma_ui.health import readiness, check_config_store
        
        # Test individual checkers
        result = check_config_store()
        assert "status" in result
        assert "component" in result


class TestConstants:
    """Tests for constants module."""

    def test_constants_import(self):
        """Test constants module loads."""
        from kazma_core import constants
        
        assert hasattr(constants, 'SWARM_DISPATCH_TIMEOUT_SECONDS')
        assert hasattr(constants, 'TELEGRAM_MIN_CHAT_ID')
        assert hasattr(constants, 'VALID_OUTPUT_PLATFORMS')
        assert hasattr(constants, 'GRAPH_HITL_DANGER_TOOLS')


class TestExceptions:
    """Tests for exceptions module."""

    def test_exception_hierarchy(self):
        """Test exception classes."""
        from kazma_core.exceptions import (
            KazmaError, ConfigError, SwarmError, PlatformError,
            ValidationError, TimeoutError as KazmaTimeoutError,
            HITLError, CircuitBreakerOpenError, sanitize_error
        )
        
        # Test base exception
        e = KazmaError("test", "user msg")
        assert str(e) == "test"
        assert e.user_message == "user msg"
        
        # Test specific exceptions
        for exc_cls in [ConfigError, SwarmError, PlatformError, ValidationError, KazmaTimeoutError, HITLError, CircuitBreakerOpenError]:
            e = exc_cls("test")
            assert isinstance(e, KazmaError)
    
    def test_sanitize_error(self):
        """Test error sanitization."""
        from kazma_core.exceptions import sanitize_error
        
        # Check the actual sanitized messages
        result = sanitize_error(Exception("connection timeout"))
        assert "timed out" in result.lower() or "timeout" in result.lower()
        
        result = sanitize_error(Exception("401 unauthorized"))
        assert "authentication" in result.lower() or "unauthorized" in result.lower()
        
        result = sanitize_error(Exception("404 not found"))
        assert "not found" in result.lower()
        
        result = sanitize_error(Exception("random error"))
        assert "error occurred" in result.lower()


class TestConfigSchema:
    """Tests for config schema validation."""

    def test_kazma_config_creation(self):
        """Test KazmaConfig creation with defaults."""
        from kazma_core.config_schema import KazmaConfig
        
        config = KazmaConfig()
        assert config.swarm.enabled is True
        assert config.safety.enabled is True
        assert config.connectors.telegram is None

    def test_telegram_output_target_validation(self):
        """Test TelegramOutputTarget validation."""
        from kazma_core.config_schema import TelegramOutputTarget
        from pydantic import ValidationError
        
        # Valid - group/channel chat_id (negative)
        target = TelegramOutputTarget(bot_token="123456789:abcdefgh", chat_id=-100123456789)
        assert target.chat_id == -100123456789
        
        # Valid - private chat (positive)
        target = TelegramOutputTarget(bot_token="123456789:abcdefgh", chat_id=123456789)
        assert target.chat_id == 123456789
        
        # Invalid chat_id (0 is not valid)
        with pytest.raises(ValidationError):
            TelegramOutputTarget(bot_token="123456789:abcdefgh", chat_id=0)

    def test_telegram_consistency_check(self):
        """Test cross-section validation."""
        from kazma_core.config_schema import KazmaConfig, TelegramOutputTarget, TelegramConnectorConfig
        from pydantic import ValidationError
        
        # Matching tokens - should work
        config = KazmaConfig(
            swarm=KazmaConfig.model_fields['swarm'].default_factory().copy(update={
                'output_target': TelegramOutputTarget(bot_token="token123456789", chat_id=-100123456789)
            }),
            connectors=KazmaConfig.model_fields['connectors'].default_factory().copy(update={
                'telegram': TelegramConnectorConfig(token="token123456789")
            })
        )
        assert config.connectors.telegram.token == "token123456789"
        
        # Mismatched tokens - should fail
        with pytest.raises(ValidationError):
            KazmaConfig(
                swarm=KazmaConfig.model_fields['swarm'].default_factory().copy(update={
                    'output_target': TelegramOutputTarget(bot_token="token1111111111", chat_id=-100123456789)
                }),
                connectors=KazmaConfig.model_fields['connectors'].default_factory().copy(update={
                    'telegram': TelegramConnectorConfig(token="token2222222222")
                })
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])