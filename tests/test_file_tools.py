"""Tests for file_read and file_write tools."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from kazma_core.tools.file_write import configure_workspace


class TestFileRead:
    """Tests for the file_read tool."""

    def setup_method(self) -> None:
        """Reset workspace config before each test."""
        configure_workspace(workspace=None, allow_absolute=False)

    @pytest.mark.asyncio
    async def test_file_read_returns_content(self, tmp_path: Path) -> None:
        """file_read returns line-numbered content from a file."""
        configure_workspace(workspace=str(tmp_path))
        test_file = tmp_path / "hello.txt"
        test_file.write_text("line one\nline two\nline three\n")

        from kazma_core.tools.file_read import file_read

        result = await file_read(str(test_file))

        assert "1|line one" in result
        assert "2|line two" in result
        assert "3|line three" in result

    @pytest.mark.asyncio
    async def test_file_read_with_offset(self, tmp_path: Path) -> None:
        """file_read respects offset and limit parameters."""
        configure_workspace(workspace=str(tmp_path))
        test_file = tmp_path / "ten_lines.txt"
        test_file.write_text("\n".join(f"line {i}" for i in range(1, 11)) + "\n")

        from kazma_core.tools.file_read import file_read

        result = await file_read(str(test_file), offset=5, limit=2)

        assert "5|line 5" in result
        assert "6|line 6" in result
        # Should NOT contain earlier lines
        assert "1|line 1" not in result
        assert "7|line 7" not in result

    @pytest.mark.asyncio
    async def test_file_read_not_found(self) -> None:
        """file_read returns a friendly error for missing files."""
        configure_workspace(workspace=".", allow_absolute=True)
        from kazma_core.tools.file_read import file_read

        result = await file_read("/nonexistent/path/xyz.txt")

        assert "Error" in result
        assert "not found" in result.lower()
        assert "Traceback" not in result

    @pytest.mark.asyncio
    async def test_read_file_returns_content(self, tmp_path: Path) -> None:
        """Alias test — same as test_file_read_returns_content for naming parity."""
        configure_workspace(workspace=str(tmp_path))
        test_file = tmp_path / "data.txt"
        test_file.write_text("hello world\n")

        from kazma_core.tools.file_read import file_read

        result = await file_read(str(test_file))
        assert "hello world" in result


class TestFileWrite:
    """Tests for the file_write tool."""

    def setup_method(self) -> None:
        """Reset workspace config before each test."""
        configure_workspace(workspace=None, allow_absolute=False)

    @pytest.mark.asyncio
    async def test_file_write_creates_file(self, tmp_path: Path) -> None:
        """file_write creates a file and returns a success message."""
        configure_workspace(workspace=str(tmp_path))
        target = tmp_path / "output.txt"

        from kazma_core.tools.file_write import file_write

        result = await file_write(str(target), "hello\nworld\n")

        assert "Wrote" in result
        assert "2 lines" in result
        assert target.read_text() == "hello\nworld\n"

    @pytest.mark.asyncio
    async def test_file_write_creates_parent_dirs(self, tmp_path: Path) -> None:
        """file_write creates intermediate directories automatically."""
        configure_workspace(workspace=str(tmp_path))
        target = tmp_path / "a" / "b" / "c" / "deep.txt"

        from kazma_core.tools.file_write import file_write

        result = await file_write(str(target), "nested content")

        assert "Wrote" in result
        assert target.exists()
        assert target.read_text() == "nested content"

    @pytest.mark.asyncio
    async def test_file_write_blocks_escape(self, tmp_path: Path) -> None:
        """file_write blocks paths that try to escape the workspace via ../.."""
        configure_workspace(workspace=str(tmp_path))

        from kazma_core.tools.file_write import file_write

        escape_path = str(tmp_path / ".." / ".." / "escaped.txt")
        result = await file_write(escape_path, "evil content")

        assert "Safety" in result
        assert "not allowed" in result

    @pytest.mark.asyncio
    async def test_file_write_permission_denied(self, tmp_path: Path) -> None:
        """file_write returns a friendly error on permission denied."""
        configure_workspace(workspace="/", allow_absolute=True)

        from kazma_core.tools.file_write import file_write

        result = await file_write("/root/kazma_test_no_permission.txt", "test")

        # Should get permission error (unless running as root)
        if hasattr(os, "getuid") and os.getuid() == 0:
            pytest.skip("Running as root — permission test not meaningful")
        assert "Error" in result
        assert "Permission" in result


class TestImports:
    """Verify tools are importable from the package."""

    def test_imports(self) -> None:
        """Both tools are importable from kazma_core.tools."""
        from kazma_core.tools import file_read, file_write

        assert callable(file_read)
        assert callable(file_write)
