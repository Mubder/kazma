"""Kazma Agent State — TypedDict definitions for the agent state machine."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, TypedDict


class Provenance(TypedDict, total=False):
    """Source tracking metadata."""

    source: str
    thread_id: str
    parent_checkpoint_id: str | None
    created_by: str


class AgentState(TypedDict, total=False):
    """Core agent state persisted across checkpoints.

    This schema defines what gets saved to SQLite on every checkpoint.
    Note: 'checkpoint_id' is reserved by LangGraph, so we use 'last_cp_id'.
    """

    messages: list[dict[str, Any]]
    tool_results: dict[str, Any]
    context_tokens: int
    last_cp_id: str
    created_at: str
    provenance: Provenance


def initial_state() -> AgentState:
    """Create a fresh initial state with sensible defaults."""
    now = datetime.now(timezone.utc).isoformat()
    return AgentState(
        messages=[],
        tool_results={},
        context_tokens=0,
        last_cp_id=str(uuid.uuid4()),
        created_at=now,
        provenance=Provenance(
            source="initial",
            thread_id=str(uuid.uuid4()),
            parent_checkpoint_id=None,
            created_by="kazma",
        ),
    )
