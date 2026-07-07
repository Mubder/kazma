"""Unit tests for VectorMemory degraded fallback and hot-reloading installer."""

from __future__ import annotations

import asyncio
import tempfile
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from kazma_core.config_store import get_config_store
from kazma_core.memory.vector_store import VectorMemory
from kazma_core.memory.fts5 import FTS5Memory
from kazma_core.system.installer import asynchronous_install_package, _hot_reload_memory, _active_installations


@pytest.mark.asyncio
async def test_vector_memory_degrades_gracefully() -> None:
    """Test that VectorMemory falls back to FTS5Memory on ImportError."""
    # Temporarily remove/block chromadb and sentence_transformers from sys.modules to simulate missing dependencies
    with patch.dict(sys.modules, {"chromadb": None, "sentence_transformers": None, "chromadb.utils": None}):
        with patch("kazma_core.observability.AlertDispatcher.broadcast_alert", new_callable=AsyncMock) as mock_alert:
            with tempfile.TemporaryDirectory() as tmpdir:
                mem = VectorMemory(path=tmpdir)
                
                assert mem.degraded is True
                assert mem._fallback is not None
                assert isinstance(mem._fallback, FTS5Memory)
                
                # Verify status updated in ConfigStore
                store = get_config_store()
                assert store.get("system.memory.status") == "DEGRADED"
                
                # Check alert broadcast called
                mock_alert.assert_called_once()
                
                # Clear fallback store for isolation
                mem._fallback.clear()
                
                # Test add
                doc_id = mem.add("Test fallback data", {"category": "test"})
                assert doc_id is not None
                
                # Test count
                assert mem.count == 1
                
                # Test search
                results = mem.search("fallback")
                assert len(results) == 1
                assert results[0]["text"] == "Test fallback data"
                assert results[0]["metadata"]["category"] == "test"


@pytest.mark.asyncio
async def test_installer_prevents_duplicate_runs() -> None:
    """Test that the installer registers active installation and skips duplicate starts."""
    _active_installations.clear()
    
    with patch("kazma_core.system.installer._run_install_task") as mock_run:
        await asynchronous_install_package("some-package")
        assert "some-package" in _active_installations
        
        # Second call should be skipped/noop
        await asynchronous_install_package("some-package")
        
        # Give control back to event loop
        await asyncio.sleep(0.01)
        mock_run.assert_called_once_with("some-package")
        
    _active_installations.clear()


@pytest.mark.asyncio
async def test_hot_reload_reindexes_data() -> None:
    """Test that hot-reloading migrates data from FTS5 to VectorMemory."""
    # Seed FTS5Memory with mock data
    fts = FTS5Memory()
    # Clear any previous FTS5 records
    fts._conn.execute(f"DELETE FROM {fts._table_name}")
    fts._conn.commit()
    
    fts.add("FTS5 migration item", {"topic": "migration"})
    assert fts.count() == 1

    # Mock VectorMemory
    mock_vector_mem = MagicMock()
    
    with patch("kazma_core.memory.vector_store.VectorMemory", return_callable=MagicMock) as mock_class:
        mock_class.return_value = mock_vector_mem
        
        await _hot_reload_memory()
        
        # Verify that mock_vector_mem.add was called to migrate the item
        mock_vector_mem.add.assert_called_once()
        args, kwargs = mock_vector_mem.add.call_args
        assert kwargs["text"] == "FTS5 migration item"
        assert kwargs["metadata"] == {"topic": "migration"}
