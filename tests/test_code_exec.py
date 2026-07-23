"""Tests for the sandboxed Python code execution tool."""

from __future__ import annotations

import inspect
import os
import sys
import tempfile

import pytest
from kazma_core.tools import code_exec
from kazma_core.tools.code_exec import python_exec, use_docker_jail, reset_docker_probe


@pytest.fixture(autouse=True)
def _force_local_sandbox(monkeypatch: pytest.MonkeyPatch):
    """Unit tests use local sandbox so CI does not require Docker."""
    monkeypatch.setenv("KAZMA_CODE_EXEC_DOCKER", "0")
    reset_docker_probe()
    yield
    reset_docker_probe()


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
    async def test_python_exec_isolated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Isolated mode (-I) ignores caller-controlled import paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = os.path.join(tmpdir, "shadowmod.py")
            with open(module_path, "w", encoding="utf-8") as module_file:
                module_file.write("VALUE = 1\n")

            monkeypatch.setenv("PYTHONPATH", tmpdir)
            result = await python_exec("import shadowmod")

        assert "[Exit code:" in result
        assert "ModuleNotFoundError" in result

    @pytest.mark.asyncio
    async def test_python_exec_blocks_socket_import(self) -> None:
        """Network modules are blocked after HITL (defense-in-depth)."""
        result = await python_exec("import socket")
        assert "[Exit code:" in result
        assert "blocked" in result.lower() or "ImportError" in result

    @pytest.mark.asyncio
    async def test_python_exec_blocks_subprocess_import(self) -> None:
        result = await python_exec("import subprocess")
        assert "[Exit code:" in result
        assert "blocked" in result.lower() or "ImportError" in result

    @pytest.mark.asyncio
    async def test_python_exec_allows_math(self) -> None:
        result = await python_exec("import math; print(math.sqrt(16))")
        assert "[Exit code: 0]" in result
        assert "4.0" in result

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


class TestDockerJailConfig:
    """Docker jail selection without requiring a real daemon."""

    def test_force_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_CODE_EXEC_DOCKER", "0")
        reset_docker_probe()
        assert use_docker_jail() is False

    def test_force_docker_even_if_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_CODE_EXEC_DOCKER", "1")
        reset_docker_probe()
        assert use_docker_jail() is True

    @pytest.mark.asyncio
    async def test_docker_path_builds_network_none_cmd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[tuple] = []

        class _FakeProc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return (b"hi\n", b"")

            def kill(self) -> None:
                pass

            async def wait(self) -> int:
                return 0

        async def _fake_exec(*args, **kwargs):
            captured.append(args)
            return _FakeProc()

        monkeypatch.setenv("KAZMA_CODE_EXEC_DOCKER", "1")
        monkeypatch.setenv("KAZMA_CODE_EXEC_IMAGE", "python:3.12-slim")
        reset_docker_probe()
        monkeypatch.setattr(code_exec, "_docker_cli", lambda: "docker")
        monkeypatch.setattr(code_exec.asyncio, "create_subprocess_exec", _fake_exec)

        result = await python_exec("print('hi')")
        assert "docker" in result.lower() or "sandbox: docker" in result
        assert captured, "docker run was not invoked"
        args = captured[0]
        assert args[0] == "docker"
        assert "run" in args
        assert "--network" in args
        assert "none" in args
        assert "--memory" in args


class TestCodeExecWindowsPortability:
    """Tests ensuring code_exec.py works on Windows (and all platforms)."""

    def test_code_exec_imports_without_resource_error(self) -> None:
        """Module imports cleanly on Windows (no ModuleNotFoundError for 'resource')."""
        # If we got here, the import at top of file already succeeded.
        assert hasattr(code_exec, "python_exec")

    def test_no_hardcoded_python3_in_source(self) -> None:
        """Source must not contain a hardcoded 'python3' subprocess binary."""
        source = inspect.getsource(code_exec)
        # Allow references in comments/docstrings but not as a subprocess binary.
        # The subprocess binary must be sys.executable, never literal "python3".
        assert '"python3"' not in source, "Hardcoded \"python3\" found in code_exec.py source"

    def test_subprocess_uses_sys_executable(self) -> None:
        """Local sandbox path must invoke sys.executable, not 'python3'."""
        source = inspect.getsource(code_exec._run_local_subprocess)
        assert "sys.executable" in source, "local sandbox does not use sys.executable"
        assert '"python3"' not in source, "local sandbox still references 'python3'"

    def test_preexec_fn_conditional_on_platform(self) -> None:
        """preexec_fn must only be set on Unix, not unconditionally."""
        source = inspect.getsource(code_exec)
        # preexec_fn should not be passed unconditionally; it should be behind a
        # platform check or a conditional variable.
        assert "preexec_fn=_set_limits" not in source, (
            "preexec_fn=_set_limits is hardcoded unconditionally"
        )

    def test_no_posix_only_path_fallback(self) -> None:
        """PATH fallback must not be the POSIX-only /usr/bin:/bin."""
        source = inspect.getsource(code_exec)
        assert "/usr/bin:/bin" not in source, (
            "POSIX-only /usr/bin:/bin PATH fallback remains in code_exec.py"
        )

    @pytest.mark.asyncio
    async def test_python_exec_uses_sys_executable_at_runtime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """At runtime, subprocess must be invoked with sys.executable as first arg."""
        captured_args: list[tuple] = []

        class _FakeProc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return (b"ok", b"")

            def kill(self) -> None:
                pass

            async def wait(self) -> int:
                return 0

        async def _fake_create_subprocess_exec(*args, **kwargs):
            captured_args.append(args)
            return _FakeProc()

        monkeypatch.setenv("KAZMA_CODE_EXEC_DOCKER", "0")
        code_exec.reset_docker_probe()
        monkeypatch.setattr(code_exec.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
        await python_exec("print('test')")
        assert len(captured_args) == 1
        assert captured_args[0][0] == sys.executable
