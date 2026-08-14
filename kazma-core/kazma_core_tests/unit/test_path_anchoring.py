"""Tests for O2 path anchoring — workspace scoping for file operations."""

import pytest
from pathlib import Path
from unittest.mock import patch
import tempfile


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
def anchored_workspace(workspace_setup):
    """Anchor the workspace-binding ladder at the temp workspace.

    Uses the product's own pin API (binding.configure_workspace) so every
    consumer — path_policy.check_path_access, file_write anchoring,
    vision_analyze — resolves the same temp root. The active WorkspaceStore
    row outranks the pin in the ladder AND sticks into it, so the store
    lookup is neutralized for the test's duration — without this the tests
    depended on the host machine's workspace store (a developer machine
    with the repo active failed; a fresh CI store passed).
    """
    from kazma_core.workspace.binding import configure_workspace

    workspace, test_file, outside_file = workspace_setup
    with patch(
        "kazma_core.stores.get_workspace_store",
        side_effect=RuntimeError("workspace store neutralized for test"),
    ):
        configure_workspace(str(workspace), allow_absolute=False)
        try:
            yield workspace, test_file, outside_file
        finally:
            configure_workspace(None)


@pytest.mark.asyncio
async def test_file_read_workspace_scoping(anchored_workspace):
    """file_read should reject paths outside workspace."""
    workspace, test_file, outside_file = anchored_workspace

    from kazma_core.tools.file_read import file_read

    # Should succeed for file inside workspace
    result = await file_read(str(test_file))
    assert "test content" in result or "Error" not in result

    # Should fail for file outside workspace
    result = await file_read(str(outside_file))
    assert "Safety" in result or "not allowed" in result


@pytest.mark.asyncio
async def test_file_write_workspace_scoping(anchored_workspace):
    """file_write should reject paths outside workspace."""
    workspace, test_file, outside_file = anchored_workspace

    from kazma_core.tools.file_write import file_write

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
async def test_vision_analyze_workspace_scoping(anchored_workspace):
    """vision_analyze should reject local image paths outside workspace."""
    workspace, test_file, outside_file = anchored_workspace

    from kazma_core.tools.vision_analyze import analyze_image

    # Create a dummy image file outside workspace
    img_outside = Path(workspace).parent / "outside.png"
    img_outside.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    # Should fail for image outside workspace (before even trying to load)
    result = await analyze_image(str(img_outside), "What is this?")
    assert "Safety" in result or "not allowed" in result


def test_workspace_scope_error_no_temp_fallback(anchored_workspace):
    """_workspace_scope_error should not allow /tmp or system temp directories."""
    workspace, test_file, outside_file = anchored_workspace

    from kazma_core.agent.tool_registry import _workspace_scope_error

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
