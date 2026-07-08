"""Environment Bootstrapper Native Skill — tools for package installation and diagnostics."""

from __future__ import annotations

import logging
import os
import shutil
import sys
import subprocess
from pathlib import Path
from kazma_core.tools.file_write import _get_workspace

logger = logging.getLogger(__name__)


async def install_python_packages(packages: list[str]) -> str:
    """Install Python packages safely inside the runtime virtual environment using uv or pip.

    Args:
        packages: A list of packages to install (e.g. ["pandas", "requests"]).

    Returns:
        Status message of the installation command.
    """
    if not packages:
        return "Error: No packages specified."

    # Validate package names to avoid shell injection
    for pkg in packages:
        if not pkg or any(char in pkg for char in ";|&<>`$()"):
            return f"Error: Invalid package name: {pkg}"

    cwd = _get_workspace()

    # Try uv first
    uv_path = shutil.which("uv")
    if uv_path:
        cmd = [uv_path, "pip", "install"] + packages
    else:
        # Fallback to current virtualenv interpreter if available, or sys.executable
        venv_python = Path(sys.prefix) / "Scripts" / "python.exe" if os.name == "nt" else Path(sys.prefix) / "bin" / "python"
        if venv_python.exists():
            cmd = [str(venv_python), "-m", "pip", "install"] + packages
        else:
            cmd = [sys.executable, "-m", "pip", "install"] + packages

    try:
        logger.info("Executing Python package install: %s", " ".join(cmd))
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=90)
        output = res.stdout.strip() or res.stderr.strip()
        if res.returncode == 0:
            return f"Successfully installed packages: {', '.join(packages)}\n\nOutput:\n{output}"
        return f"Failed to install packages (Exit code {res.returncode}):\n{output}"
    except Exception as e:
        logger.error("Error installing Python packages: %s", e)
        return f"Error installing Python packages: {e}"


async def install_npm_packages(packages: list[str]) -> str:
    """Install Node/npm packages inside the active workspace.

    Args:
        packages: A list of npm packages to install (e.g. ["lodash", "typescript"]).

    Returns:
        Status message of the installation.
    """
    if not packages:
        return "Error: No packages specified."

    for pkg in packages:
        if not pkg or any(char in pkg for char in ";|&<>`$()"):
            return f"Error: Invalid package name: {pkg}"

    cwd = _get_workspace()
    npm_path = shutil.which("npm")
    if not npm_path:
        return "Error: npm command not found on PATH."

    cmd = [npm_path, "install"] + packages
    try:
        logger.info("Executing npm install: %s", " ".join(cmd))
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=90)
        output = res.stdout.strip() or res.stderr.strip()
        if res.returncode == 0:
            return f"Successfully installed npm packages: {', '.join(packages)}\n\nOutput:\n{output}"
        return f"Failed to install npm packages (Exit code {res.returncode}):\n{output}"
    except Exception as e:
        logger.error("Error installing npm packages: %s", e)
        return f"Error installing npm packages: {e}"


async def check_environment() -> str:
    """Diagnose system binaries, active Python interpreter, PATH variables, and compile resources.

    Returns:
        Structured diagnostics report.
    """
    python_version = sys.version
    python_path = sys.executable
    os_name = os.name
    platform_system = sys.platform

    binaries = ["git", "uv", "node", "npm", "python", "pytest", "ruff", "mypy", "gcc"]
    binary_paths = {}
    for bin_name in binaries:
        binary_paths[bin_name] = shutil.which(bin_name) or "Not Found"

    cwd = _get_workspace()

    report = [
        "=== Kazma Environment Diagnostic Report ===",
        f"OS Architecture: {os_name} ({platform_system})",
        f"Active Python: {python_path}",
        f"Python Version: {python_version}",
        f"Current Workspace CWD: {cwd}",
        "\n--- Binary Discovery ---",
    ]

    for bin_name, path in binary_paths.items():
        report.append(f"  {bin_name:<8}: {path}")

    report.append("\n--- Path Inspection ---")
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    report.append(f"Total PATH entries: {len(path_entries)}")
    for p in path_entries[:5]:  # List first 5 paths
        report.append(f"  - {p}")
    if len(path_entries) > 5:
        report.append("  - ... (truncated)")

    return "\n".join(report)
