"""Kazma Recovery Hook — Startup recovery from last checkpoint.

When the agent restarts (after SIGKILL, crash, or normal restart),
this module loads the last checkpoint and resumes execution.
"""

from __future__ import annotations

import logging
from typing import Any

from kazma_core.checkpoint import CheckpointManager
from kazma_core.state import AgentState, initial_state

logger = logging.getLogger(__name__)


async def recover_on_startup(
    db_path: str | None = None,
) -> AgentState:
    """Load last checkpoint on agent startup.

    If no checkpoint exists, returns a fresh initial state.
    This is the entry point for crash recovery — call this when
    the agent process starts to resume from where it left off.

    Args:
        db_path: Path to the SQLite checkpoint database.
                 Defaults to kazma-data/checkpoints.db.

    Returns:
        The recovered AgentState or a fresh initial state.
    """
    manager = CheckpointManager(db_path=db_path)

    try:
        last = await manager.load_latest()
        if last is not None:
            logger.info(
                "Recovered checkpoint: %s (created=%s, tokens=%d)",
                last.get("last_cp_id"),
                last.get("created_at"),
                last.get("context_tokens", 0),
            )
            return last
        else:
            logger.info("No checkpoint found, starting fresh")
            return initial_state()
    except Exception:
        logger.exception("Failed to recover checkpoint, starting fresh")
        return initial_state()
    finally:
        await manager.close()


async def resume_agent(
    db_path: str | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Resume the agent from its last checkpoint.

    Loads the checkpoint, rebuilds the LangGraph app, and continues
    execution from the saved state.

    Args:
        db_path: Path to the SQLite checkpoint database.
        thread_id: Thread ID to resume. If None, uses the thread_id
                   from the recovered checkpoint.

    Returns:
        Dict with 'state' (recovered AgentState) and 'app' (compiled graph).
    """

    from kazma_core.agent import create_app

    state = await recover_on_startup(db_path=db_path)
    actual_db = db_path or "kazma-data/checkpoints.db"

    # Create the app with checkpointer
    app, saver = await create_app(actual_db)

    # Get the thread_id from recovered state
    tid = thread_id or state.get("provenance", {}).get("thread_id")
    if tid is None:
        from uuid import uuid4
        tid = str(uuid4())

    config: dict[str, Any] = {"configurable": {"thread_id": tid}}

    return {
        "state": state,
        "app": app,
        "saver": saver,
        "config": config,
        "thread_id": tid,
    }
