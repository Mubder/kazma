"""What the tool registry hands the model for one direct tool call.

A tool refuses before acting by raising ``RefusedBeforeEffect``; the registry
turns that into an ordinary error (``tool_registry.execute``). A test that
calls a tool function directly reads the same text through ``answer``.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from kazma_core.agent.tool_refusal import RefusedBeforeEffect


async def answer(call: Awaitable[Any]) -> Any:
    try:
        return await call
    except RefusedBeforeEffect as exc:
        return exc.content()


def answer_now(tool: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """``answer`` for a call made outside an event loop, sync tool or async."""
    try:
        out = tool(*args, **kwargs)
        return asyncio.run(out) if inspect.isawaitable(out) else out
    except RefusedBeforeEffect as exc:
        return exc.content()
