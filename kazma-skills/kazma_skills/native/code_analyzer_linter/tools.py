"""Code Analyzer Linter Native Skill — tools for linting, formatting, and running tests."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from kazma_core.agent.tool_registry import _workspace_scope_error
from kazma_core.tools.file_write import _get_workspace

logger = logging.getLogger(__name__)


async def lint_code(path: str) -> str:
    """Execute static checks on Python files using ruff linter to detect errors and unused imports.

    Args:
        path: Path to the file or directory to lint.

    Returns:
        Structured linter warnings or success messages.
    """
    p = Path(path).expanduser().resolve()
    scope_err = _workspace_scope_error(p, path, "searches")
    if scope_err:
        return scope_err

    if not p.exists():
        return f"Error: Path not found: {path}"

    ruff_path = shutil.which("ruff")
    if not ruff_path:
        # Fallback to basic python compile check
        if p.is_file() and p.suffix == ".py":
            import py_compile
            try:
                py_compile.compile(str(p), doraise=True)
                return f"[py_compile Fallback] File compiled successfully. No syntax errors in: {path}"
            except py_compile.PyCompileError as e:
                return f"[py_compile Fallback] Syntax error in file: {e}"
        return "Error: ruff linter command not found. Run 'pip install ruff' to enable complete static checks."

    cmd = [ruff_path, "check", str(p)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        # ruff check returns exit code 1 if errors found, which is normal behavior
        output = res.stdout.strip() or res.stderr.strip()
        if res.returncode == 0:
            return f"Linter passed! No issues detected in: {path}\n{output}"
        return f"Linter found potential issues (Exit code {res.returncode}):\n{output}"
    except Exception as e:
        logger.error("Error executing ruff check on %s: %s", path, e)
        return f"Error executing linter: {e}"


async def format_code(path: str) -> str:
    """Format source code files using ruff format to maintain styling guidelines.

    Args:
        path: Path to the file or directory to format.

    Returns:
        Success or failure message.
    """
    p = Path(path).expanduser().resolve()
    scope_err = _workspace_scope_error(p, path, "writes")
    if scope_err:
        return scope_err

    if not p.exists():
        return f"Error: Path not found: {path}"

    ruff_path = shutil.which("ruff")
    if not ruff_path:
        return "Error: ruff formatter command not found. Run 'pip install ruff' to enable code formatting."

    cmd = [ruff_path, "format", str(p)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = res.stdout.strip() or res.stderr.strip()
        if res.returncode == 0:
            return f"Formatting successfully completed on: {path}\n{output}"
        return f"Formatting failed (Exit code {res.returncode}):\n{output}"
    except Exception as e:
        logger.error("Error formatting code on %s: %s", path, e)
        return f"Error formatting code: {e}"


async def run_unit_tests(test_path: str) -> str:
    """Execute tests in the test path using pytest and return a structured summary of successes or traceback errors.

    Args:
        test_path: Path to the test file or folder.

    Returns:
        pytest execution summary.
    """
    p = Path(test_path).expanduser().resolve()
    scope_err = _workspace_scope_error(p, test_path, "searches")
    if scope_err:
        return scope_err

    if not p.exists():
        return f"Error: Test path not found: {test_path}"

    pytest_path = shutil.which("pytest")
    if not pytest_path:
        # Fallback: run via python module if pytest is in sys.executable context
        import sys
        cmd = [sys.executable, "-m", "pytest", str(p), "-v"]
    else:
        cmd = [pytest_path, str(p), "-v"]

    # Windows-specific: avoid permission locks by adding basetemp if running inside our project
    if "kazma" in str(p).lower() and "test" in str(p).lower():
        cmd.extend(["--basetemp", ".pytest_tmp_run_skills_tests"])

    try:
        # Limit test suite execution to 60 seconds to prevent hanging
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        output = res.stdout.strip()
        err = res.stderr.strip()
        combined = f"{output}\n{err}".strip()
        if len(combined) > 12000:
            combined = combined[:6000] + "\n\n[... TRUNCATED MIDDLE CONTENT ...]\n\n" + combined[-6000:]
        return f"Test results (Exit code {res.returncode}):\n{combined}"
    except subprocess.TimeoutExpired:
        return "Error: Test suite execution timed out (limit: 60s)."
    except Exception as e:
        logger.error("Error running pytest on %s: %s", test_path, e)
        return f"Error running tests: {e}"
