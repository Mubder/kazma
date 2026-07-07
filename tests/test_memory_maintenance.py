"""Unit tests for Kazma's memory backup, restore, and optimization maintenance subsystem."""

from __future__ import annotations

import os
import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from kazma_core.system.maintenance import (
    get_memory_paths,
    create_memory_backup,
    restore_memory_backup,
    run_memory_maintenance,
    list_memory_backups,
)
from kazma_core.memory.fts5 import FTS5Memory


@pytest.fixture
def temp_memory_env(tmp_path, monkeypatch):
    """Setup a fully isolated temporary environment for memory paths and files."""
    fts5_path = tmp_path / "memory.db"
    vector_path = tmp_path / "vector_memory"
    backups_dir = tmp_path / "backups"

    vector_path.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KAZMA_FTS5_PATH", str(fts5_path))
    monkeypatch.setenv("KAZMA_VECTOR_PATH", str(vector_path))
    monkeypatch.setenv("KAZMA_BACKUPS_DIR", str(backups_dir))

    # Initialize a test FTS5 memory database with some data
    mem = FTS5Memory(db_path=str(fts5_path))
    mem.add("User resides in Kuwait.", {"category": "demographics"})
    mem.add("System runs on dark mode.", {"category": "ui"})
    mem.close()

    # Create valid SQLite file inside Vector memory to simulate ChromaDB database
    chroma_sqlite = vector_path / "chroma.sqlite3"
    conn = sqlite3.connect(chroma_sqlite)
    conn.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER);")
    conn.close()

    dummy_segment = vector_path / "segment_001.bin"
    with open(dummy_segment, "w", encoding="utf-8") as f:
        f.write("dummy segment data")

    yield fts5_path, vector_path, backups_dir


def test_get_memory_paths(temp_memory_env):
    """Test environment resolution of memory paths."""
    fts5_path, vector_path, backups_dir = temp_memory_env
    resolved_fts, resolved_vec, resolved_backups = get_memory_paths()

    assert resolved_fts == fts5_path
    assert resolved_vec == vector_path
    assert resolved_backups == backups_dir


def test_create_and_list_backups(temp_memory_env):
    """Test executing a zero-downtime hot backup and listing available backups."""
    fts5_path, vector_path, backups_dir = temp_memory_env

    # Execute hot backup
    manifest = create_memory_backup()

    assert manifest["name"].startswith("memory_")
    assert manifest["fts5_size"] > 0
    assert manifest["fts5_count"] == 2
    assert manifest["vector_size"] > 0

    # Ensure files exist in backup directory
    backup_folder = backups_dir / manifest["name"]
    assert backup_folder.exists()
    assert (backup_folder / "memory.db").exists()
    assert (backup_folder / "vector_memory" / "chroma.sqlite3").exists()
    assert (backup_folder / "backup_manifest.json").exists()

    # List backups
    backups = list_memory_backups()
    assert len(backups) == 1
    assert backups[0]["name"] == manifest["name"]
    assert backups[0]["fts5_count"] == 2


@pytest.mark.asyncio
async def test_restore_backup_with_rollback(temp_memory_env):
    """Test full restoration capability with rolling checkpoint backup support."""
    fts5_path, vector_path, backups_dir = temp_memory_env

    # 1. Trigger hot backup
    manifest = create_memory_backup()
    backup_name = manifest["name"]

    # 2. Modify/Delete active memory states
    mem = FTS5Memory(db_path=str(fts5_path))
    assert mem.count() == 2
    mem.clear()
    assert mem.count() == 0
    mem.close()

    # Empty Vector memory simulated file
    chroma_sqlite = vector_path / "chroma.sqlite3"
    with open(chroma_sqlite, "w", encoding="utf-8") as f:
        f.write("")

    # 3. Perform restoration
    # Mock hot reloading to avoid setting global singletons in tests
    with patch("kazma_core.system.maintenance._hot_reload_memory") as mock_reload:
        res = await restore_memory_backup(backup_name)
        assert res["status"] == "success"
        mock_reload.assert_called_once()

    # 4. Verify original database records and files are completely restored
    mem = FTS5Memory(db_path=str(fts5_path))
    assert mem.count() == 2
    results = mem.search("Kuwait")
    assert len(results) == 1
    assert results[0]["text"] == "User resides in Kuwait."
    mem.close()

    assert chroma_sqlite.exists()


def test_run_memory_maintenance(temp_memory_env):
    """Test full VACUUM and ANALYZE optimization sweep on databases."""
    fts5_path, vector_path, backups_dir = temp_memory_env

    # Perform optimization sweep
    results = run_memory_maintenance()

    assert "fts5" in results
    assert results["fts5"]["status"] == "success"
    assert results["fts5"]["reclaimed_bytes"] >= 0

    assert "vector" in results
    assert results["vector"]["status"] == "success"
    assert results["vector"]["reclaimed_bytes"] >= 0
