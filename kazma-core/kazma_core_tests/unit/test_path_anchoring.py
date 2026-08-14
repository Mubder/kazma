"""Tests for O2 path anchoring — workspace scoping for file operations."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile
import os
import sys
import importlib


@pytest.fixture
def workspace_setup():
    """Set up a temporary workspace for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "workspace"
        workspace.mkdir()
        
        # Create a test file inside workspace
        test_file = workspace / "test.txt"
        test_file.write_text("test content")
        
        # Create a file outside workspace
        outside_file = Path(tmpdir) / "outside.txt"
        outside_file.write_text("outside content")
        
        yield workspace, test_file, outside_file


@pytest.fixture
def fw_module():
    """Import the file_write module directly (bypassing __init__.py exports)."""
    return importlib.import_module("kazma_core.tools.file_write")


@pytest.mark.asyncio
async def test_file_read_workspace_scoping(workspace_setup, fw_module):
    """file_read should reject paths outside workspace."""
    workspace, test_file, outside_file = workspace_setup
    
    from kazma_core.tools.file_read import file_read
    
    # Mock the workspace resolution
    with patch.object(fw_module, '_get_workspace', return_value=workspace):
        with patch.object(fw_module, '_ALLOW_ABSOLUTE', False):
            # Should succeed for file inside workspace
            result = await file_read(str(test_file))
            assert "test content" in result or "Error" not in result
            
            # Should fail for file outside workspace
            result = await file_read(str(outside_file))
            assert "Safety" in result or "not allowed" in result


@pytest.mark.asyncio
async def test_file_write_workspace_scoping(workspace_setup, fw_module):
    """file_write should reject paths outside workspace."""
    workspace, test_file, outside_file = workspace_setup
    
    from kazma_core.tools.file_write import file_write
    
    # Mock the workspace resolution
    with patch.object(fw_module, '_get_workspace', return_value=workspace):
        with patch.object(fw_module, '_ALLOW_ABSOLUTE', False):
            # Should succeed for file inside workspace
            new_file = workspace / "new.txt"
            result = await file_write(str(new_file), "new content")
            assert "Wrote" in result
            assert new_file.read_text() == "new content"
            
            # Should fail for file outside workspace
            outside_new = Path(workspace).parent / "outside_new.txt"
            result = await file_write(str(outside_new), "outside content")
            assert "Safety" in result or "not allowed" in result


@pytest.mark.asyncio
async def test_vision_analyze_workspace_scoping(workspace_setup, fw_module):
    """vision_analyze should reject local image paths outside workspace."""
    workspace, test_file, outside_file = workspace_setup
    
    from kazma_core.tools.vision_analyze import analyze_image
    
    # Mock the workspace resolution
    with patch.object(fw_module, '_get_workspace', return_value=workspace):
        with patch.object(fw_module, '_ALLOW_ABSOLUTE', False):
            # Create a dummy image file outside workspace
            img_outside = Path(workspace).parent / "outside.png"
            img_outside.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            
            # Should fail for image outside workspace (before even trying to load)
            result = await analyze_image(str(img_outside), "What is this?")
            assert "Safety" in result or "not allowed" in result


@pytest.mark.asyncio
async def test_workspace_scope_error_no_temp_fallback(workspace_setup, fw_module):
    """_workspace_scope_error should not allow /tmp or system temp directories."""
    workspace, test_file, outside_file = workspace_setup
    
    from kazma_core.agent.tool_registry import _workspace_scope_error
    
    # Mock the workspace resolution
    with patch.object(fw_module, '_get_workspace', return_value=workspace):
        with patch.object(fw_module, '_ALLOW_ABSOLUTE', False):
            # Path inside workspace should be allowed
            err = _workspace_scope_error(test_file, str(test_file), "reads")
            assert err is None
            
            # Path outside workspace should be denied
            err = _workspace_scope_error(outside_file, str(outside_file), "reads")
            assert err is not None
            assert "Safety" in err
            
            # Path in temp directory should be denied (no temp fallback)
            tmp_file = Path(tempfile.gettempdir()) / "test.txt"
            tmp_file.touch()
            try:
                err = _workspace_scope_error(tmp_file, str(tmp_file), "reads")
                assert err is not None
                assert "Safety" in err
            finally:
                tmp_file.unlink(missing_ok=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
