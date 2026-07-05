"""Conftest — shared pytest fixtures for Kazma tests."""

from __future__ import annotations

import os
# Prevent local .env file leakage and environment variable pollution from breaking tests
os.environ.pop("KAZMA_SECRET", None)
import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: None

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


@pytest.fixture(autouse=True)
def _isolated_config_store(tmp_path):
    """Reset the ConfigStore singleton to an isolated temp DB per test.

    Gateway/core code now uses ``get_config_store()`` (the singleton).
    Without this fixture, tests that don't explicitly set the singleton
    would lazily create one pointing at the real ``kazma-data/settings.db``,
    leaking state across tests and potentially corrupting the dev DB.
    """
    from kazma_core.config_store import ConfigStore, reset_config_store, set_config_store

    isolated = ConfigStore(
        db_path=str(tmp_path / "test_settings.db"),
        yaml_path=str(tmp_path / "kazma.yaml"),
    )
    set_config_store(isolated)
    yield
    isolated.close()
    reset_config_store()


@pytest.fixture(autouse=True)
def _reset_swarm_singletons(tmp_path):
    """Reset swarm engine and worker registry singletons before each test.

    Without this, the SwarmEngine singleton (set via ``set_swarm_engine``)
    and the WorkerRegistry singleton (set via ``get_worker_registry``)
    persist across tests, causing worker-name conflicts (409 errors)
    and state leakage between test classes.

    Also redirects the registry file to an isolated temp file so that
    ``create_app()`` does not load workers from the real
    ``swarm_registry.json`` (which may contain data from prior runs).
    """
    # Reset swarm engine singleton
    try:
        from kazma_core.swarm.engine import set_swarm_engine
        set_swarm_engine(None)
    except Exception:
        pass

    # Reset worker registry singleton and redirect to temp file
    try:
        import kazma_core.swarm.registry as _reg_mod
        _reg_mod._REGISTRY_SINGLETON = None
        _reg_mod._DEFAULT_PATH = tmp_path / "test_swarm_registry.json"
    except Exception:
        pass

    yield

    # Clean up after test
    try:
        from kazma_core.swarm.engine import set_swarm_engine
        set_swarm_engine(None)
    except Exception:
        pass

    try:
        import kazma_core.swarm.registry as _reg_mod
        _reg_mod._REGISTRY_SINGLETON = None
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _clean_kazma_secret():
    """Ensure KAZMA_SECRET is cleared from the environment after each test.

    FastAPI app startup auto-generates KAZMA_SECRET if unset and stores it in
    os.environ. This leaks into subsequent tests in the same process, causing
    401 failures on clean client instances. This fixture isolates each test.
    """
    orig = os.environ.get("KAZMA_SECRET")
    yield
    if orig is None:
        os.environ.pop("KAZMA_SECRET", None)
    else:
        os.environ["KAZMA_SECRET"] = orig


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
