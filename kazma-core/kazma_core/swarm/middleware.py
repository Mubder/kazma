"""Swarm middleware — tool output truncation.

TruncationMiddleware: caps tool outputs at ``max_tokens`` characters
with a ``[...truncated N chars]`` indicator.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

__all__ = ["MAX_TOKENS", "ToolOutput", "TruncationMiddleware"]

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

