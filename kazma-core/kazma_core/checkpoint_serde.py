"""The one checkpoint serializer every Kazma checkpointer uses.

LangGraph's ``JsonPlusSerializer`` rebuilds typed values (enums,
dataclasses, pydantic models) from a checkpoint by importing and calling the
class the checkpoint names. Its default is PERMISSIVE: any class, with a
warning -- so a checkpoint row decides what gets constructed. Given an
allowlist it is strict: LangGraph's safe types plus the ones listed here,
and anything else comes back as its raw data.

Kazma built its savers in six places and passed an allowlist in two
(2026-09-26). The shared SQLite saver took its serializer from whichever
caller opened it first -- the server's CheckpointManager (strict) or
KazmaAgent (LangGraph's permissive default) -- and KazmaAgent's own Postgres
and SQLite savers always used the default. The strict list also missed
``TaskStatus`` and ``RouteKind``, which the supervisor writes into graph
state, so every SQLite round trip handed them back as plain strings with a
warning. (Postgres stores ``str`` values inline, so it never showed.)

Every saver takes :func:`kazma_checkpoint_serde`.
``tests/test_checkpoint_serde.py`` finds each saver construction in the
product source and requires it, and round-trips every enum the graph-state
modules define.
"""

from __future__ import annotations

from typing import Any

#: Kazma's own types a checkpoint may hold, beyond LangGraph's safe list:
#: every Enum the graph-state modules define (the supervisor stores their
#: members in ``SupervisorState``).
KAZMA_MSGPACK_TYPES: tuple[tuple[str, str], ...] = (
    ("kazma_core.agent.state", "NodeName"),
    ("kazma_core.agent.state", "TaskStatus"),
    ("kazma_core.agent.intent.types", "RouteKind"),
    ("kazma_core.agent.intent.types", "ActKind"),
)


def kazma_checkpoint_serde() -> Any:
    """A strict ``JsonPlusSerializer``: LangGraph's safe types plus Kazma's."""
    from langgraph.checkpoint.serde._msgpack import SAFE_MSGPACK_TYPES
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    return JsonPlusSerializer(
        allowed_msgpack_modules=[*SAFE_MSGPACK_TYPES, *KAZMA_MSGPACK_TYPES],
    )
