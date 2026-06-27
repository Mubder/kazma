"""Conftest — shared pytest fixtures for Kazma tests."""

from __future__ import annotations

# Import i18n early so the Jinja2Templates patch (which injects the default
# ``t`` global) is applied before any test creates a Jinja2Templates instance.
import kazma_ui.i18n  # noqa: F401

import pytest
from kazma_core.agent import AgentConfig, KazmaAgent


@pytest.fixture
def agent_config() -> AgentConfig:
    """Return a default agent config for testing."""
    return AgentConfig(
        name="test-kazma",
        version="0.0.0-test",
        language="en",
        rtl=False,
    )


@pytest.fixture
def agent(agent_config: AgentConfig) -> KazmaAgent:
    """Return a KazmaAgent instance for testing."""
    return KazmaAgent(config=agent_config)
