"""Tests for the sandboxed Python code execution tool."""

from __future__ import annotations

import os
import tempfile

import pytest
from kazma_core.tools.code_exec import python_exec


class TestPythonExec:
    """Tests for python_exec tool."""

    @pytest.mark.asyncio
    async def test_python_exec_hello_world(self) -> None:
        """Simple print statement returns output."""
        result = await python_exec("print('hello world')")
        assert "[Exit code: 0]" in result
        assert "hello world" in result

    @pytest.mark.asyncio
    async def test_python_exec_syntax_error(self) -> None:
        """Bad syntax returns stderr with line info."""
        result = await python_exec("def foo(")
        assert "[Exit code:" in result
        assert "SyntaxError" in result

    @pytest.mark.asyncio
    async def test_python_exec_runtime_error(self) -> None:
        """NameError returns traceback in output."""
        result = await python_exec("print(undefined_variable)")
        assert "[Exit code:" in result
        assert "NameError" in result

    @pytest.mark.asyncio
    async def test_python_exec_timeout(self) -> None:
        """Infinite loop is killed after timeout."""
        result = await python_exec("while True: pass", timeout=2)
        assert "timed out" in result
        assert "124" in result or "2s" in result

    @pytest.mark.asyncio
    async def test_python_exec_multiline(self) -> None:
        """Multi-line script works correctly."""
        code = """
import math
for i in range(5):
    print(f"{i}: {math.factorial(i)}")
"""
        result = await python_exec(code)
        assert "[Exit code: 0]" in result
        assert "0: 1" in result
        assert "4: 24" in result

    @pytest.mark.asyncio
    async def test_python_exec_large_output(self) -> None:
        """Output exceeding 4000 chars is truncated."""
        code = "print('x' * 5000)"
        result = await python_exec(code)
        assert "truncated" in result
        assert len(result) < 5000

    @pytest.mark.asyncio
    async def test_python_exec_isolated(self) -> None:
        """Isolated mode (-I) blocks site-packages."""
        # numpy is not in isolated mode's path
        result = await python_exec("import numpy")
        assert "[Exit code:" in result
        # Should fail with ModuleNotFoundError or ImportError
        assert "Error" in result or "error" in result.lower()

    @pytest.mark.asyncio
    async def test_python_exec_cleanup(self) -> None:
        """Temp directory is cleaned up after execution."""
        # Track temp dirs before
        tmp_base = tempfile.gettempdir()
        before = {d for d in os.listdir(tmp_base) if d.startswith("kazma_exec_")}

        await python_exec("print('cleanup test')")

        # Small delay for cleanup
        import asyncio

        await asyncio.sleep(0.1)

        after = {d for d in os.listdir(tmp_base) if d.startswith("kazma_exec_")}
        # No new temp dirs should remain
        new_dirs = after - before
        assert len(new_dirs) == 0, f"Temp dirs not cleaned: {new_dirs}"

    @pytest.mark.asyncio
    async def test_python_exec_empty_code(self) -> None:
        """Empty code returns an error."""
        result = await python_exec("")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_python_exec_exit_code_format(self) -> None:
        """Output format includes exit code."""
        result = await python_exec("import sys; sys.exit(42)")
        assert "[Exit code: 42]" in result
