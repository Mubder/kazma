"""Shared pytest fixtures for kazma-core tests."""

import asyncio
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_db_path() -> Generator[str, None, None]:
    """Create a temporary database file path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
async def temp_config_store(temp_db_path: str):
    """Create a ConfigStore with a temporary database."""
    from kazma_core.config_store import ConfigStore
    
    store = ConfigStore(temp_db_path)
    yield store
    await store.close()


@pytest.fixture
def in_memory_store():
    """Create an in-memory store for testing."""
    from kazma_core.config_store import _InMemoryStore
    
    store = _InMemoryStore(max_entries=100, ttl_seconds=60)
    yield store
    # Cleanup handled by store.clear() in tests


@pytest.fixture
def sample_hitl_config() -> dict:
    """Sample HITL configuration for testing."""
    return {
        "enabled": True,
        "require_approval_for": [
            "file_write",
            "file_delete", 
            "shell_exec",
            "code_exec",
            "python_exec",
        ],
        "timeout_seconds": 300,
    }


@pytest.fixture
def sample_swarm_config() -> dict:
    """Sample swarm configuration for testing."""
    return {
        "enabled": True,
        "default_pattern": "dispatch",
        "auto_route": True,
        "max_concurrent_tasks": 10,
    }