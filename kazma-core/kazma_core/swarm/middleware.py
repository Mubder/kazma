"""Swarm middleware — tool output truncation + graceful error handling.

TruncationMiddleware: caps tool outputs at ``max_tokens`` characters
with a ``[...truncated N chars]`` indicator.

GracefulErrorFallback: wraps tool execution so a single broken tool
never crashes the entire pipeline — returns a structured error ToolResult.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

__all__ = ["GracefulErrorFallback", "MAX_TOKENS", "ToolOutput", "TruncationMiddleware"]

logger = logging.getLogger(__name__)

MAX_TOKENS = 2000


@dataclass(slots=True)
class ToolOutput:
    """Tool execution result after middleware processing."""
    tool_name: str
    success: bool
    output: str = ""
    truncated: bool = False
    original_length: int = 0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TruncationMiddleware:
    """Caps tool output at max_tokens with truncation indicator."""

    def __init__(self, max_tokens: int = MAX_TOKENS) -> None:
        self.max_tokens = max_tokens

    def process(self, tool_name: str, output: str) -> ToolOutput:
        """Truncate output if it exceeds max_tokens."""
        original_len = len(output)
        if len(output) <= self.max_tokens:
            return ToolOutput(
                tool_name=tool_name,
                success=True,
                output=output,
                original_length=original_len,
            )
        truncated = output[:self.max_tokens] + f"\n\n[...truncated {original_len - self.max_tokens} chars]"
        logger.info("[Truncation] %s: %d → %d chars", tool_name, original_len, self.max_tokens)
        return ToolOutput(
            tool_name=tool_name,
            success=True,
            output=truncated,
            truncated=True,
            original_length=original_len,
        )


class GracefulErrorFallback:
    """Wraps tool execution so a broken tool never crashes the pipeline.

    Usage in SwarmEngine or worker dispatch:
        try:
            result = await tool.execute(...)
        except Exception:
            result = GracefulErrorFallback.tool_error(tool_name, exc)
    """

    @staticmethod
    def tool_error(tool_name: str, exc: Exception) -> ToolOutput:
        """Return a structured error ToolOutput that won't crash the pipeline."""
        logger.warning("[GracefulError] Tool '%s' failed: %s", tool_name, exc)
        return ToolOutput(
            tool_name=tool_name,
            success=False,
            output="",
            error=f"{type(exc).__name__}: {exc!s}"[:500],
        )

    @staticmethod
    def to_json_error(exc: Exception) -> dict[str, Any]:
        """Serialize an exception into a human-readable JSON error state."""
        return {
            "success": False,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc)[:500],
                "recoverable": isinstance(exc, (ValueError, KeyError, TimeoutError)),
            },
        }
