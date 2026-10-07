"""Keep a turn's workspace through interrupts and process restarts."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from langgraph.types import Command

from kazma_core.exceptions import ConfigError
from kazma_core.ide.workspace_scope import workspace_path_scope
from kazma_core.workspace import binding


@asynccontextmanager
async def turn_workspace(
    graph: Any, input_state: Any, config: dict[str, Any] | None,
) -> AsyncIterator[Any]:
    """Capture the trusted transport's root; restore it on Command resume.

    The checkpoint owns the root, rather than the approval HTTP request or
    the process's currently active workspace. New turns capture a fresh
    binding, so an intentional workspace switch applies on the next turn.
    Legacy checkpoints without this field cannot recover a scope that was
    never saved. Refuse to resume them; a fresh turn captures a safe binding.
    """
    maintenance = isinstance(input_state, dict) and input_state.get("needs_compaction") is True
    if isinstance(input_state, dict) and not maintenance:
        # Ignore a supplied state value: the actual execution scope is the
        # authority at the start of a new turn, including task-specific pins.
        root = str(await asyncio.to_thread(binding.resolve_active_root))
        prepared = {**input_state, "workspace_root": root}
    elif isinstance(input_state, Command) or maintenance:
        snapshot = await graph.aget_state(config)
        values = getattr(snapshot, "values", {})
        if not isinstance(values, dict) or "workspace_root" not in values:
            message = (
                "⚠️ Cannot resume: this older checkpoint did not save its workspace. "
                "Cancel the paused turn and start a fresh turn in the intended workspace."
            )
            raise ConfigError(message, user_message=message)
        root = values["workspace_root"]
        if not isinstance(root, str) or not root or not Path(root).is_absolute():
            message = "⚠️ Cannot resume: checkpointed workspace root is invalid. Cancel and start a fresh turn."
            raise ConfigError(message, user_message=message)
        if not await asyncio.to_thread(Path(root).is_dir):
            message = "⚠️ Cannot resume: checkpointed workspace directory is unavailable. Restore it or start a fresh turn."
            raise ConfigError(message, user_message=message)
        prepared = {**input_state, "workspace_root": root} if maintenance else input_state
    else:
        yield input_state
        return
    async with workspace_path_scope(root):
        yield prepared
