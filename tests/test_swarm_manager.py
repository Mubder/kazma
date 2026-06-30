"""Tests for the unified SwarmManager engine.

Covers config loading, worker lifecycle, dispatch, broadcast, and validation.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from kazma_core.swarm.blackboard import BlackboardStore, SwarmDispatchContext
from kazma_core.swarm.config import OrchestratorConfig, SwarmConfig, WorkerConfig
from kazma_core.swarm.manager import SwarmManager
from kazma_core.swarm.worker import InProcessWorker, TelegramWorker

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_SWARM_YAML = {
    "swarm": {
        "enabled": True,
        "group_chat_id": -5553328924,
        "orchestrator": {
            "name": "Kazma Orchestrator",
            "profile": "default",
        },
        "workers": [
            {
                "name": "core",
                "type": "telegram_bot",
                "model": "mimo-v2.5-pro",
                "provider": "xiaomi",
                "profile": "core",
                "bot_token_env": "KAZMA_CORE_BOT_TOKEN",
                "role": "backend_core",
            },
            {
                "name": "brain",
                "type": "in_process",
                "model": "gpt-4o-mini",
                "provider": "openai",
                "role": "reasoning",
            },
        ],
    }
}


@pytest.fixture
def sample_yaml(tmp_path: Path) -> Path:
    """Write sample kazma.yaml to a temp dir and return its path."""
    path = tmp_path / "kazma.yaml"
    path.write_text(yaml.dump(SAMPLE_SWARM_YAML), encoding="utf-8")
    return path


@pytest.fixture
def worker_config_telegram() -> WorkerConfig:
    return WorkerConfig(
        name="core",
        type="telegram_bot",
        model="mimo-v2.5-pro",
        provider="xiaomi",
        profile="core",
        bot_token_env="KAZMA_CORE_BOT_TOKEN",
        role="backend_core",
    )


@pytest.fixture
def worker_config_inprocess() -> WorkerConfig:
    return WorkerConfig(
        name="brain",
        type="in_process",
        model="gpt-4o-mini",
        provider="openai",
        role="reasoning",
    )


@pytest.fixture
def full_config(worker_config_telegram, worker_config_inprocess) -> SwarmConfig:
    return SwarmConfig(
        enabled=True,
        group_chat_id=-5553328924,
        orchestrator=OrchestratorConfig(name="Kazma Orchestrator", profile="default"),
        workers=[worker_config_telegram, worker_config_inprocess],
    )


@pytest.fixture
def manager(full_config) -> SwarmManager:
    return SwarmManager(full_config)


# ===========================================================================
# 1. test_load_config_from_yaml
# ===========================================================================

class TestLoadConfigFromYaml:
    """SwarmConfig.from_yaml loads the swarm section correctly."""

    def test_load_config_from_yaml(self, sample_yaml: Path):
        config = SwarmConfig.from_yaml(sample_yaml)
        assert config is not None
        assert config.enabled is True
        assert config.group_chat_id == -5553328924
        assert config.orchestrator.name == "Kazma Orchestrator"
        assert len(config.workers) == 2
        assert config.workers[0].name == "core"
        assert config.workers[0].type == "telegram_bot"
        assert config.workers[1].name == "brain"
        assert config.workers[1].type == "in_process"

    def test_missing_swarm_section_returns_none(self, tmp_path: Path):
        path = tmp_path / "kazma.yaml"
        path.write_text("agent:\n  name: kazma\n", encoding="utf-8")
        assert SwarmConfig.from_yaml(path) is None

    def test_missing_file_returns_none(self, tmp_path: Path):
        assert SwarmConfig.from_yaml(tmp_path / "nonexistent.yaml") is None


# ===========================================================================
# 2. test_add_worker
# ===========================================================================

class TestAddWorker:
    """SwarmManager.add_worker registers workers correctly."""

    def test_add_worker(self):
        config = SwarmConfig(enabled=True, workers=[])
        mgr = SwarmManager(config)
        wc = WorkerConfig(name="test", type="in_process", role="tester")
        mgr.add_worker(wc)
        assert "test" in mgr.worker_names

    def test_add_telegram_worker(self, worker_config_telegram):
        config = SwarmConfig(enabled=True, workers=[])
        mgr = SwarmManager(config)
        mgr.add_worker(worker_config_telegram)
        worker = mgr.get_worker("core")
        assert isinstance(worker, TelegramWorker)
        assert worker.profile == "core"


# ===========================================================================
# 3. test_remove_worker
# ===========================================================================

class TestRemoveWorker:
    """SwarmManager.remove_worker unregisters workers."""

    def test_remove_worker(self, manager: SwarmManager):
        assert "core" in manager.worker_names
        removed = manager.remove_worker("core")
        assert removed.name == "core"
        assert "core" not in manager.worker_names

    def test_remove_nonexistent_raises(self, manager: SwarmManager):
        with pytest.raises(KeyError, match="nonexistent"):
            manager.remove_worker("nonexistent")


# ===========================================================================
# 4. test_dispatch_in_process
# ===========================================================================

class TestDispatchInProcess:
    """InProcessWorker dispatches via SubAgentManager.spawn."""

    @pytest.mark.asyncio
    async def test_dispatch_in_process(self):
        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.status = "success"
        mock_result.summary = "Task completed"
        mock_result.error = None
        mock_manager.spawn = AsyncMock(return_value=mock_result)

        worker = InProcessWorker(name="brain", role="reasoning", manager=mock_manager)
        await worker.start()

        result = await worker.dispatch("Analyze the codebase")

        assert result["worker"] == "brain"
        assert result["status"] == "success"
        assert result["output"] == "Task completed"
        assert result["error"] is None
        mock_manager.spawn.assert_called_once()

        await worker.stop()

    @pytest.mark.asyncio
    async def test_dispatch_in_process_with_context(self):
        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.status = "success"
        mock_result.summary = "Done"
        mock_result.error = None
        mock_manager.spawn = AsyncMock(return_value=mock_result)

        worker = InProcessWorker(name="brain", role="reasoning", manager=mock_manager)
        await worker.start()

        result = await worker.dispatch("Fix bug", context="In auth module")

        call_kwargs = mock_manager.spawn.call_args
        assert call_kwargs.kwargs["goal"] == "Fix bug"
        assert call_kwargs.kwargs["context"] == "In auth module"
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_dispatch_in_process_accepts_blackboard_context(self):
        mock_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.status = "success"
        mock_result.summary = "Done"
        mock_result.error = None
        mock_manager.spawn = AsyncMock(return_value=mock_result)

        worker = InProcessWorker(name="brain", role="reasoning", manager=mock_manager)
        await worker.start()

        result = await worker.dispatch(
            "Fix bug",
            context=SwarmDispatchContext(
                "In auth module",
                blackboard=BlackboardStore(),
            ),
        )

        call_kwargs = mock_manager.spawn.call_args
        assert call_kwargs.kwargs["goal"] == "Fix bug"
        assert call_kwargs.kwargs["context"] == "In auth module"
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_dispatch_in_process_no_manager_returns_error(self):
        """InProcessWorker without a manager returns error dict (does not raise)."""
        worker = InProcessWorker(name="orphan", role="test")
        with patch("kazma_core.swarm.worker.InProcessWorker._get_manager", side_effect=RuntimeError("SubAgentManager not initialised")):
            result = await worker.dispatch("task")
        assert result["status"] == "error"
        assert "SubAgentManager not initialised" in result["error"]
        assert result["worker"] == "orphan"


# ===========================================================================
# 5. test_dispatch_telegram
# ===========================================================================

class TestDispatchTelegram:
    """TelegramWorker dispatches via subprocess."""

    @pytest.mark.asyncio
    async def test_dispatch_telegram(self, tmp_path):
        worker = TelegramWorker(
            name="core",
            profile="core",
            bot_token_env="KAZMA_CORE_BOT_TOKEN",
            group_chat_id=-5553328924,
            role="backend_core",
        )
        await worker.start()

        # Mock the subprocess
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"Task done via telegram", b"")
        )
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            result = await worker.dispatch("Deploy to staging")

        assert result["worker"] == "core"
        assert result["status"] == "success"
        assert "Task done via telegram" in result["output"]
        assert result["error"] is None

        await worker.stop()

    @pytest.mark.asyncio
    async def test_dispatch_telegram_failure(self):
        worker = TelegramWorker(
            name="core",
            profile="core",
            bot_token_env="KAZMA_CORE_BOT_TOKEN",
        )
        await worker.start()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"Error: bot token invalid")
        )
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            result = await worker.dispatch("task")

        assert result["status"] == "error"
        assert "bot token invalid" in result["error"]


# ===========================================================================
# 6. test_broadcast
# ===========================================================================

class TestBroadcast:
    """SwarmManager.broadcast sends to all workers."""

    @pytest.mark.asyncio
    async def test_broadcast(self, manager: SwarmManager):
        # Mock both workers' dispatch
        for worker in manager._workers.values():
            worker.dispatch = AsyncMock(return_value={
                "worker": worker.name,
                "task_id": "mock-123",
                "status": "success",
                "output": f"Done by {worker.name}",
                "error": None,
            })

        results = await manager.broadcast("Deploy all services")

        assert len(results) == 2
        worker_names = {r["worker"] for r in results}
        assert "core" in worker_names
        assert "brain" in worker_names
        for r in results:
            assert r["status"] == "success"

    @pytest.mark.asyncio
    async def test_broadcast_empty(self):
        config = SwarmConfig(enabled=True, workers=[])
        mgr = SwarmManager(config)
        results = await mgr.broadcast("task")
        assert results == []

    @pytest.mark.asyncio
    async def test_broadcast_handles_worker_exception(self, manager: SwarmManager):
        """If a worker throws, broadcast catches it and returns error dict."""
        good_worker = manager._workers["brain"]
        good_worker.dispatch = AsyncMock(return_value={
            "worker": "brain",
            "task_id": "ok",
            "status": "success",
            "output": "done",
            "error": None,
        })
        bad_worker = manager._workers["core"]
        bad_worker.dispatch = AsyncMock(side_effect=RuntimeError("crashed"))

        results = await manager.broadcast("task")
        assert len(results) == 2
        error_result = [r for r in results if r["worker"] == "core"][0]
        assert error_result["status"] == "error"
        assert "crashed" in error_result["error"]


# ===========================================================================
# 7. test_worker_status
# ===========================================================================

class TestWorkerStatus:
    """SwarmManager.status reports health for each worker."""

    @pytest.mark.asyncio
    async def test_worker_status(self, manager: SwarmManager):
        await manager.start_all()
        statuses = await manager.status()
        assert len(statuses) == 2
        for s in statuses:
            assert "name" in s
            assert "role" in s
            assert "running" in s
            assert s["running"] is True

    @pytest.mark.asyncio
    async def test_worker_status_fields(self):
        worker = InProcessWorker(name="w1", role="test", model="m1", provider="p1")
        await worker.start()
        status = await worker.status()
        assert status == {
            "name": "w1",
            "role": "test",
            "model": "m1",
            "provider": "p1",
            "running": True,
        }


# ===========================================================================
# 8. test_duplicate_worker_rejected
# ===========================================================================

class TestDuplicateWorkerRejected:
    """Adding a worker with a duplicate name raises ValueError."""

    def test_duplicate_worker_rejected(self, manager: SwarmManager):
        wc = WorkerConfig(name="core", type="in_process", role="dup")
        with pytest.raises(ValueError, match="already registered"):
            manager.add_worker(wc)

    def test_duplicate_in_config_validation(self):
        wc = WorkerConfig(name="dup", type="in_process", role="a")
        config = SwarmConfig(enabled=True, workers=[wc, wc])
        errors = config.validate()
        assert any("Duplicate worker name" in e for e in errors)


# ===========================================================================
# 9. test_config_validation
# ===========================================================================

class TestConfigValidation:
    """WorkerConfig and SwarmConfig validate correctly."""

    def test_valid_config(self, full_config):
        errors = full_config.validate()
        assert errors == []

    def test_invalid_worker_type(self):
        wc = WorkerConfig(name="bad", type="unknown_type", role="x")
        errors = wc.validate()
        assert any("must be" in e for e in errors)

    def test_telegram_requires_profile(self):
        wc = WorkerConfig(name="t", type="telegram_bot", bot_token_env="X")
        errors = wc.validate()
        assert any("profile" in e for e in errors)

    def test_telegram_requires_bot_token_env(self):
        wc = WorkerConfig(name="t", type="telegram_bot", profile="p")
        errors = wc.validate()
        assert any("bot_token_env" in e for e in errors)

    def test_empty_name(self):
        wc = WorkerConfig(name="", type="in_process")
        errors = wc.validate()
        assert any("name is required" in e for e in errors)

    def test_get_worker(self, full_config):
        assert full_config.get_worker("core") is not None
        assert full_config.get_worker("nonexistent") is None


# ===========================================================================
# 10. test_start_stop_lifecycle
# ===========================================================================

class TestStartStopLifecycle:
    """SwarmManager.start_all / stop_all manage worker lifecycle."""

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self, manager: SwarmManager):
        # Initially not running
        for w in manager._workers.values():
            assert w._running is False

        await manager.start_all()
        for w in manager._workers.values():
            assert w._running is True

        await manager.stop_all()
        for w in manager._workers.values():
            assert w._running is False

    @pytest.mark.asyncio
    async def test_telegram_worker_start_checks_env(self):
        """TelegramWorker.start warns if bot token env var is missing."""
        worker = TelegramWorker(
            name="t", profile="p", bot_token_env="MISSING_VAR_XYZ"
        )
        # Should not raise, just warn
        await worker.start()
        assert worker._running is True
        await worker.stop()

    @pytest.mark.asyncio
    async def test_telegram_worker_stop_terminates_process(self):
        worker = TelegramWorker(
            name="t", profile="p", bot_token_env="X"
        )
        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock(return_value=0)
        worker._process = mock_proc
        worker._running = True

        await worker.stop()
        mock_proc.terminate.assert_called_once()
        assert worker._running is False
        assert worker._process is None
