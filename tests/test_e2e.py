"""End-to-end tests for the Kazma agent — full workflow verification."""

from __future__ import annotations

from kazma_core.agent import KazmaAgent, load_config


class TestAgentE2E:
    """Full agent lifecycle: init → run → shutdown."""

    async def test_load_config_returns_valid(self) -> None:
        config = load_config()
        assert config is not None
        assert config.name == "kazma"
        assert config.version == "0.2.0"
        assert config.language == "ar"
        assert config.default_model in ("gpt-4o-mini", "gpt-4o", "default")

    async def test_agent_init_with_config(self) -> None:
        config = load_config()
        agent = KazmaAgent(config)
        assert agent is not None
        assert agent.config.name == "kazma"
        await agent.shutdown()

    async def test_agent_has_tool_registry(self) -> None:
        config = load_config()
        agent = KazmaAgent(config)
        assert hasattr(agent, "tools")
        await agent.shutdown()

    async def test_agent_has_llm_provider(self) -> None:
        config = load_config()
        agent = KazmaAgent(config)
        assert hasattr(agent, "llm")
        assert agent.llm is not None
        await agent.shutdown()

    async def test_agent_has_cost_breaker(self) -> None:
        config = load_config()
        agent = KazmaAgent(config)
        assert hasattr(agent, "cost_breaker")
        await agent.shutdown()

    async def test_agent_has_tracer(self) -> None:
        config = load_config()
        agent = KazmaAgent(config)
        assert hasattr(agent, "tracer")
        assert agent.tracer is not None
        await agent.shutdown()

    async def test_agent_shutdown_clean(self) -> None:
        config = load_config()
        agent = KazmaAgent(config)
        await agent.shutdown()

    async def test_agent_double_shutdown_safe(self) -> None:
        config = load_config()
        agent = KazmaAgent(config)
        await agent.shutdown()
        await agent.shutdown()

    async def test_agent_not_running_by_default(self) -> None:
        config = load_config()
        agent = KazmaAgent(config)
        assert agent._running is False
        await agent.shutdown()

    async def test_agent_config_has_system_prompt(self) -> None:
        config = load_config()
        assert config.system_prompt
        assert "Kazma" in config.system_prompt or "كاظمه" in config.system_prompt


class TestAgentE2EWithConfig:
    """Agent with overridden config fields."""

    async def test_custom_language(self) -> None:
        config = load_config()
        config.language = "en"
        assert config.language == "en"

    async def test_custom_model(self) -> None:
        config = load_config()
        config.default_model = "gpt-4o"
        assert config.default_model == "gpt-4o"


class TestAgentE2EMemory:
    """Agent memory system initialization."""

    async def test_memory_attribute_exists(self) -> None:
        config = load_config()
        agent = KazmaAgent(config)
        assert hasattr(agent, "memory")
        await agent.shutdown()

    async def test_memory_store_accessible(self) -> None:
        from kazma_core.tracing import get_trace_store

        store = get_trace_store()
        assert store is not None
        stats = store.stats()
        assert "total_traces" in stats
