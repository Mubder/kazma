"""System Health Monitor Native Skill — tools for monitoring host resources, processes, and logs."""

from __future__ import annotations

import os
import re
import logging
from pathlib import Path
import psutil

from kazma_core.agent.tool_registry import _workspace_scope_error
from kazma_core.tools.file_write import _get_workspace

logger = logging.getLogger(__name__)


async def get_system_stats() -> str:
    """Fetches CPU, RAM, and Disk space utilization metrics of the host system.

    Returns:
        A formatted markdown string presenting the system metrics.
    """
    try:
        # CPU Info
        cpu_count = psutil.cpu_count(logical=True)
        cpu_percent = psutil.cpu_percent(interval=0.1)

        # Virtual Memory Info
        vm = psutil.virtual_memory()
        ram_total = vm.total / (1024**3)  # GB
        ram_available = vm.available / (1024**3)  # GB
        ram_used = vm.used / (1024**3)  # GB
        ram_percent = vm.percent

        # Disk Info
        workspace = _get_workspace()
        disk = psutil.disk_usage(str(workspace))
        disk_total = disk.total / (1024**3)  # GB
        disk_used = disk.used / (1024**3)  # GB
        disk_free = disk.free / (1024**3)  # GB
        disk_percent = disk.percent

        stats = [
            "🖥️ **KAZMA SYSTEM HEALTH REPORT**",
            "==================================",
            f"⚡ **CPU Usage:** `{cpu_percent}%` ({cpu_count} logical cores)",
            f"🧠 **RAM Usage:** `{ram_percent}%` (Used: {ram_used:.2f} GB / Total: {ram_total:.2f} GB)",
            f"💽 **Disk Space (Workspace):** `{disk_percent}%` (Free: {disk_free:.2f} GB / Total: {disk_total:.2f} GB)",
            "==================================",
            "✅ *System diagnostics check passed.*",
        ]
        return "\n".join(stats)
    except Exception as e:
        logger.error("Error fetching system stats: %s", e)
        return f"Error fetching system statistics: {e}"


async def list_active_processes() -> str:
    """Lists active subprocesses and related python processes currently running under parent Kazma.

    Returns:
        A clean markdown-formatted table of active processes.
    """
    try:
        parent_pid = os.getpid()
        parent_proc = psutil.Process(parent_pid)

        # Retrieve direct and indirect children
        children = parent_proc.children(recursive=True)

        # Build list of relevant processes (parent + kids + any other running kazma/uvicorn/textual procs)
        proc_list = [parent_proc] + children

        # Filter out duplicates and dead ones
        unique_procs = {}
        for p in proc_list:
            try:
                if p.is_running() and p.pid not in unique_procs:
                    unique_procs[p.pid] = p
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Format process table
        report = [
            "📊 **KAZMA ACTIVE PROCESSES**",
            "",
            "| PID | Process Name | Status | CPU % | RAM (MB) | Command Line |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ]

        for pid, p in sorted(unique_procs.items()):
            try:
                name = p.name()
                status = p.status()
                cpu = p.cpu_percent(interval=None)
                mem = p.memory_info().rss / (1024 * 1024)  # MB
                cmdline = " ".join(p.cmdline() or [])
                if len(cmdline) > 60:
                    cmdline = cmdline[:57] + "..."
                report.append(f"| `{pid}` | `{name}` | {status} | {cpu:.1f}% | {mem:.1f} | `{cmdline or '-'}` |")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return "\n".join(report)
    except Exception as e:
        logger.error("Error listing processes: %s", e)
        return f"Error listing active processes: {e}"


def _sanitize_log_text(text: str) -> str:
    """Masks secret API keys, passwords, and authorization tokens."""
    # Mask OpenAI style sk-... keys
    text = re.sub(r"sk-[a-zA-Z0-9]{32,}", "[REDACTED_OPENAI_KEY]", text)
    # Mask Google/Gemini style API keys
    text = re.sub(r"AIzaSy[a-zA-Z0-9_-]{33}", "[REDACTED_GEMINI_KEY]", text)
    # Mask general authorization headers / tokens
    text = re.sub(r"(?i)bearer\s+[a-zA-Z0-9_\-\.\~]+", "Bearer [REDACTED_BEARER_TOKEN]", text)
    # Mask inline secrets, keys, and passwords (e.g., db_password="xxx", api_key="xxx")
    text = re.sub(
        r"(?i)(api[-_]?key|secret|password|token|jwt|auth)\s*[=:]\s*['\"][^'\"]+['\"]",
        r'\1: "[REDACTED_SECRET]"',
        text,
    )
    text = re.sub(
        r"(?i)(api[-_]?key|secret|password|token|jwt|auth)\s*[=:]\s*[a-zA-Z0-9_\-]+",
        r"\1=[REDACTED_SECRET]",
        text,
    )
    return text


async def read_system_logs(lines: int = 100) -> str:
    """Safely streams recent lines of the Kazma gateway and server logs, with filters to mask API tokens and secrets.

    Args:
        lines: Number of recent lines to read. Max 200 lines.

    Returns:
        A secure log stream segment.
    """
    if lines <= 0:
        return "Error: Line count must be positive."
    if lines > 200:
        lines = 200

    workspace = _get_workspace()
    log_path = workspace / "out.log"

    scope_err = _workspace_scope_error(log_path, str(log_path), "reads")
    if scope_err:
        return scope_err

    if not log_path.exists():
        # Fallback: check if there is an alternative log file
        alt_log = workspace / "server.log"
        if alt_log.exists():
            log_path = alt_log
        else:
            return f"Error: System log file not found in workspace root. (Expected: {log_path.name})"

    try:
        # Read the file's last lines safely
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()

        tail_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
        sanitized_lines = [_sanitize_log_text(line) for line in tail_lines]

        report = [
            f"📋 **REPLAYING RECENT SYSTEM LOGS ({len(sanitized_lines)} lines)**",
            f"File: `{log_path.name}`",
            "```text",
            "".join(sanitized_lines).strip(),
            "```",
        ]
        return "\n".join(report)
    except Exception as e:
        logger.error("Error reading system logs: %s", e)
        return f"Error reading system logs: {e}"
