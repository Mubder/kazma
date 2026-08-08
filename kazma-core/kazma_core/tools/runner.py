"""Non-Blocking Async Tool Execution Runner (Task 3).

Wraps blocking file I/O and CPU-bound operations in worker threads
with explicit timeout boundaries to prevent event-loop freezing and 120s overrides.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

__all__ = ["execute_workspace_tool_safe", "TOOL_EXECUTION_TIMEOUT"]

TOOL_EXECUTION_TIMEOUT = 30.0  # Seconds


async def execute_workspace_tool_safe(tool_func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Executes workspace tools in worker threads with non-blocking timeout handling."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(tool_func, *args, **kwargs),
            timeout=TOOL_EXECUTION_TIMEOUT,
        )
    except asyncio.TimeoutError:
        func_name = getattr(tool_func, "__name__", str(tool_func))
        logger.error(f"Tool execution timed out after {TOOL_EXECUTION_TIMEOUT}s: {func_name}")
        return {
            "status": "error",
            "message": "Tool execution timed out. Switching to native single-pass fallback.",
        }
