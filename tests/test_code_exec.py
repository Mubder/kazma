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

    # math was the one import this file checked -- and one of the three that
    # still worked while the process-wide block broke the rest (2026-09-26:
    # 17 of 20 ordinary snippets died, an approved date calculation three
    # times). The standard library runs; the snippet's own escapes do not.
    STDLIB = {
        "from datetime import datetime, timedelta; print((datetime(2026, 9, 26) + timedelta(days=2)).strftime('%a'))": "Mon",
        "from datetime import datetime; print(datetime.strptime('2026-09-26', '%Y-%m-%d').year)": "2026",
        "import zoneinfo, datetime; print(datetime.datetime(2026, 9, 26, tzinfo=zoneinfo.ZoneInfo('UTC')).isoformat())": "2026-09-26T00:00:00+00:00",
        "import json; print(json.dumps({'a': [1, 2]}))": '{"a": [1, 2]}',
        "import re; print(re.findall(r'[0-9]+', 'a1b22'))": "['1', '22']",
        "import statistics; print(statistics.median([3, 1, 2]))": "2",
        "from decimal import Decimal; print(Decimal('1.10') + Decimal('2.20'))": "3.30",
        "import random; random.seed(7); print(len([random.random() for _ in range(3)]))": "3",
        "from collections import namedtuple; P = namedtuple('P', 'x'); print(P(5).x)": "5",
        "from dataclasses import dataclass\n@dataclass\nclass P:\n    x: int\nprint(P(6))": "P(x=6)",
        "from typing import NamedTuple\nclass P(NamedTuple):\n    x: int\nprint(P(7).x)": "7",
    }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", sorted(STDLIB))
    async def test_the_standard_library_runs_in_the_sandbox(self, code: str) -> None:
        result = await python_exec(code)
        assert "[Exit code: 0]" in result, result
        assert self.STDLIB[code] in result, result

    REFUSED = {
        "import os": "blocked",
        "from os import path": "blocked",
        "__import__('subprocess')": "blocked",
        "import builtins": "blocked",
        "exec('print(1)')": "exec() is disabled",
        "print(eval('1 + 1'))": "eval() is disabled",
        "compile('1', 'x', 'eval')": "compile() is disabled",
    }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", sorted(REFUSED))
    async def test_the_snippets_own_escapes_stay_refused(self, code: str) -> None:
        result = await python_exec(code)
        assert "[Exit code: 0]" not in result, result
        assert self.REFUSED[code] in result, result

    @pytest.mark.asyncio
    async def test_tracebacks_count_the_snippets_own_lines(self) -> None:
        result = await python_exec("x = 1\nraise ValueError('on line two')")
        assert 'File "snippet.py", line 2' in result, result

    @pytest.mark.asyncio
    async def test_a_main_guard_runs(self) -> None:
        result = await python_exec("if __name__ == '__main__':\n    print('ran as main')")
        assert "ran as main" in result, result

    def test_negative_control_a_process_wide_block_breaks_the_stdlib(self) -> None:
        """The pre-2026-09-26 design, reduced to its core: patch the builtins
        MODULE instead of the snippet's builtins, and `import json` dies in
        the import system's own exec. This is what the tests above catch."""
        import subprocess

        process_wide = (
            "import builtins as _b\n"
            "def _deny(*a, **k):\n"
            "    raise RuntimeError('exec() is disabled in the code_exec sandbox')\n"
            "_b.exec = _deny\n_b.compile = _deny\n"
            "import json\n"
        )
        proc = subprocess.run(
            [sys.executable, "-I", "-c", process_wide],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode != 0 and "exec() is disabled" in proc.stderr

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
        result = await python_exec("raise SystemExit(42)")
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

    def test_hitl_note_names_docker_when_forced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from kazma_core.tools.code_exec import jail_note_for_tool

        monkeypatch.setenv("KAZMA_CODE_EXEC_DOCKER", "force")
        reset_docker_probe()
        note = jail_note_for_tool("python_exec")
        assert "Docker" in note
        assert "no network" in note
        host = jail_note_for_tool("shell_exec")
        assert "HOST" in host

    def test_host_shell_blocked_under_docker_force(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from kazma_core.safety.post_hitl import host_shell_allowed

        monkeypatch.setenv("KAZMA_CODE_EXEC_DOCKER", "force")
        monkeypatch.delenv("KAZMA_HOST_SHELL", raising=False)
        assert host_shell_allowed() is False
        monkeypatch.setenv("KAZMA_HOST_SHELL", "1")
        assert host_shell_allowed() is True

    def test_hitl_note_names_host_when_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from kazma_core.tools.code_exec import jail_note_for_tool

        monkeypatch.setenv("KAZMA_CODE_EXEC_DOCKER", "0")
        monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
        reset_docker_probe()
        note = jail_note_for_tool("python_exec")
        assert "HOST" in note

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

    def test_limits_wrap_the_command_only_on_posix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POSIX limits come from the rlimit launcher, not preexec_fn (audit 2026-09-22).

        This lock used to check that ``preexec_fn=_set_limits`` was set only on
        Unix. preexec_fn is gone — it can deadlock the child in a threaded
        server — and test_static_gates forbids it; the platform split is here.
        """
        from pathlib import Path

        target = Path("snippet.py")
        monkeypatch.setattr(code_exec, "_IS_UNIX", False)
        assert code_exec._sandbox_argv(target) == [sys.executable, "-I", "snippet.py"]

        monkeypatch.setattr(code_exec, "_IS_UNIX", True)
        argv = code_exec._sandbox_argv(target)
        assert argv[0] == sys.executable and argv[-3:] == [sys.executable, "-I", "snippet.py"]
        assert str(code_exec.MEMORY_LIMIT_MB * 1024 * 1024) in argv
        assert str(code_exec.DEFAULT_TIMEOUT + 5) in argv

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
