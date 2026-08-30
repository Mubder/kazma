"""Conftest — shared pytest fixtures for Kazma tests.

The production-database shield (sqlite forced, DSNs stripped, dotenv
neutered, re-introduction guard) lives in the ROOT conftest.py so it
covers every testpath — see that file for the incident context.
"""

from __future__ import annotations

import os
import pathlib

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
def _isolated_agent_yaml(tmp_path, monkeypatch):
    """Point the live-agent config path at a temp file, per test.

    ``_isolated_config_store`` above already gives ConfigStore its own
    ``yaml_path``, but ``mcp_servers_store._resolve_yaml_path`` does not go
    through ConfigStore: with no live agent registered -- which is every test
    -- it falls back to ``agent_runner.CONFIG_FILE``, the checked-in
    ``kazma.yaml`` at the repository root.

    So ``tests/test_settings.py::test_mcp_add`` POSTed to the settings API and
    wrote a real ``test-mcp`` server into the real file. It then failed
    ``test_static_gates.py::test_shipped_mcp_servers_can_actually_run``,
    because ``echo`` is a shell builtin and not a runnable command -- one test
    breaking another through the operator's live configuration. Commit
    47112eb2 had already deleted that fixture once and added the gate that
    catches it; the gate works, and the suite kept re-creating what it guards
    against.
    """
    import kazma_core.agent_runner as agent_runner

    monkeypatch.setattr(
        agent_runner, "CONFIG_FILE", str(tmp_path / "kazma.yaml"), raising=False
    )


@pytest.fixture(scope="session", autouse=True)
def _repo_config_is_not_a_test_fixture():
    """Fail loudly if a test still mutates the checked-in ``kazma.yaml``.

    The fixture above closes the path that was actually being used. This is
    the net under it, because the interesting failure is the one nobody
    predicted: any future code that resolves the config path a different way
    lands here instead of in the operator's working tree, where it was found
    four times in one evening by hand.

    Restores the file so a run never leaves the tree dirty, and says so.
    """
    import hashlib

    target = pathlib.Path(__file__).resolve().parent.parent / "kazma.yaml"
    before = target.read_bytes() if target.is_file() else None
    yield
    if before is None or not target.is_file():
        return
    after = target.read_bytes()
    if after != before:
        target.write_bytes(before)
        raise AssertionError(
            "the test suite modified the checked-in kazma.yaml "
            f"(sha {hashlib.sha256(before).hexdigest()[:12]} -> "
            f"{hashlib.sha256(after).hexdigest()[:12]}). It has been restored, "
            "but a test is writing to the live agent configuration -- find it "
            "and give it an isolated path."
        )


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
def _isolated_memory_dbs(tmp_path):
    """Redirect the V2 memory DBs to a temp location for every test.

    Swarm engine tests (``test_swarm_*.py``) run the real dispatch path,
    which calls ``swarm_bridge.store_swarm_result``. Without this, those
    runs wrote ``worker → produced → "Task: …\\nResult: …"`` episodes
    straight into the production ``kazma-data/memory_state.db`` — the
    garbage beliefs visible in the Beliefs UI (alpha/beta/w3, "pipeline
    with fallback", "Vote on the answer", …). The env vars are read live
    on every call, so setting them per-test is enough.
    """
    state = str(tmp_path / "test_memory_state.db")
    ops = str(tmp_path / "test_memory_ops.db")
    prev_state = os.environ.get("KAZMA_MEMORY_STATE_DB")
    prev_ops = os.environ.get("KAZMA_MEMORY_OPS_DB")
    os.environ["KAZMA_MEMORY_STATE_DB"] = state
    os.environ["KAZMA_MEMORY_OPS_DB"] = ops
    yield
    for key, prev in (("KAZMA_MEMORY_STATE_DB", prev_state), ("KAZMA_MEMORY_OPS_DB", prev_ops)):
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev


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
