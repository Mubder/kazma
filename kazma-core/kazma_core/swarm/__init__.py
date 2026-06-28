"""Unified SwarmManager engine.

Provides a single orchestration layer for managing Kazma workers in two modes:
- in_process: lightweight sub-agent spawning (same model, fast)
- telegram_bot: persistent Hermes profile bots (separate process, different model)

Usage::

    from kazma_core.swarm import SwarmManager, SwarmConfig

    config = SwarmConfig.from_yaml("kazma.yaml")
    manager = SwarmManager(config)
    await manager.start_all()
    result = await manager.dispatch("core", "Fix the auth bug")
"""

from kazma_core.swarm.blackboard import BlackboardStore, SwarmDispatchContext
from kazma_core.swarm.config import SwarmConfig, WorkerConfig
from kazma_core.swarm.engine import SwarmEngine, get_swarm_engine, set_swarm_engine
from kazma_core.swarm.manager import SwarmManager
from kazma_core.swarm.task import (
    HandoffRecord,
    SwarmTask,
    TaskResult,
    TaskStatus,
    TaskType,
    WorkerCapabilities,
    WorkerResult,
)
from kazma_core.swarm.worker import InProcessWorker, SwarmWorker, TelegramWorker

__all__ = [
    "SwarmConfig",
    "SwarmManager",
    "SwarmEngine",
    "SwarmWorker",
    "WorkerConfig",
    "WorkerCapabilities",
    "BlackboardStore",
    "SwarmDispatchContext",
    "SwarmTask",
    "TaskResult",
    "TaskStatus",
    "TaskType",
    "WorkerResult",
    "HandoffRecord",
    "InProcessWorker",
    "TelegramWorker",
    "get_swarm_engine",
    "set_swarm_engine",
]
