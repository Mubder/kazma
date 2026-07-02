"""Conftest — shared pytest fixtures for Kazma tests."""

from __future__ import annotations

# Import i18n early so the Jinja2Templates patch (which injects the default
# ``t`` global) is applied before any test creates a Jinja2Templates instance.
import kazma_ui.i18n  # noqa: F401
import pytest
from kazma_core.agent import AgentConfig, KazmaAgent


@pytest.fixture(autouse=True)
def _init_model_registry(tmp_path):
    """Initialize the ModelRegistry singleton for tests that create KazmaAgent."""
    from kazma_core.config_store import ConfigStore
    from kazma_core.model_registry import initialize_model_registry, reset_model_registry

    db_path = str(tmp_path / "test_registry.db")
    cs = ConfigStore(db_path=db_path)
    initialize_model_registry(cs)
    yield
    reset_model_registry()


@pytest.fixture(autouse=True)
def _safety_headless_danger():
    """Allow danger tools in test/headless mode.

    SafetyMiddleware.check_sync() is fail-closed by default (blocks
    danger tools when no real bus adapter is wired). Tests that exercise
    file_write/shell_exec/etc. through the tool registry need this escape
    hatch enabled. This autouse fixture sets it for the whole suite and
    restores the prior instance afterwards.
    """
    from kazma_core.swarm.safety import SafetyMiddleware, get_safety, set_safety

    prev = get_safety()
    test_safety = SafetyMiddleware(enabled=True, allow_headless_danger=True)
    set_safety(test_safety)
    yield
    set_safety(prev)


@pytest.fixture(autouse=True)
def _reset_workspace_singleton():
    """Reset the file_write workspace singleton before each test.

    The workspace guard (``_WORKSPACE_ROOT`` / ``_ALLOW_ABSOLUTE`` in
    ``file_write.py``) is a module-level global. Tests that call
    ``configure_workspace()`` leave it set, polluting later tests that
    write to temp files outside the workspace. This resets it to a
    permissive default so temp-file writes always work in the suite.
    """
    from kazma_core.tools.file_write import configure_workspace

    configure_workspace(workspace=None, allow_absolute=True)
    yield
    configure_workspace(workspace=None, allow_absolute=False)


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
