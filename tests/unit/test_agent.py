"""Tests for kazma_core.agent module."""

from __future__ import annotations

import pytest

from kazma_core.agent import AgentConfig, KazmaAgent, load_config


class TestAgentConfig:
    """Tests for AgentConfig dataclass."""

    def test_default_values(self) -> None:
        config = AgentConfig()
        assert config.name == "kazma"
        assert config.version == "0.1.0"
        assert config.language == "ar"
        assert config.rtl is True
        assert config.vector_dim == 1536

    def test_custom_values(self) -> None:
        config = AgentConfig(name="custom", version="2.0", language="en")
        assert config.name == "custom"
        assert config.version == "2.0"
        assert config.language == "en"


class TestLoadConfig:
    """Tests for YAML config loading."""

    def test_missing_config_returns_defaults(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        config = load_config()
        assert config.name == "kazma"

    def test_loads_from_yaml(self, tmp_path, monkeypatch) -> None:
        config_file = tmp_path / "kazma.yaml"
        config_file.write_text(
            "agent:\n  name: test-agent\n  version: 1.0\n"
        )
        monkeypatch.chdir(tmp_path)
        config = load_config(config_file)
        assert config.name == "test-agent"
        assert config.version == "1.0"


class TestKazmaAgent:
    """Tests for KazmaAgent class."""

    @pytest.mark.asyncio
    async def test_run_returns_response(self, agent: KazmaAgent) -> None:
        result = await agent.run("hello")
        assert "Echo" in result
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_shutdown(self, agent: KazmaAgent) -> None:
        agent._running = True
        await agent.shutdown()
        assert agent._running is False
