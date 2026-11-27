"""Durable invocation receipts for graph tool effects.

The receipt is written before dispatch and the returned result before the
graph checkpoints it. A replay returns that result; an interrupted dispatch
is held for reconciliation. This cannot make an external API atomic and
does not authorize whole-agent retries or repeat an unknown effect.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas
from kazma_core.spaces.scope import require_space_id, space_of_state

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_effects (
    effect_id TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('started', 'completed')),
    result_json TEXT,
    started_at REAL NOT NULL,
    completed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tool_effects_thread ON tool_effects(thread_id);
"""


class EffectUncertain(RuntimeError):
    """An effect cannot be safely dispatched or repeated."""


def _effect_identity(state: dict[str, Any], call: dict[str, Any]) -> str:
    """Stable across checkpoint resumes, distinct across turns/iterations.

    Production entry points initialize ``created_at`` for every new turn.
    A legacy/direct node invocation without that scope has no replay receipt;
    it must not fabricate an identity from model arguments alone.
    """
    scope = str(state.get("created_at") or "")
    thread = str(state.get("thread_id") or "")
    if not (scope and thread):
        return ""
    call_id = str(call.get("id") or "")
    if not call_id:
        raise EffectUncertain("The effect has no tool-call identity; dispatch was withheld.")
    parts = [space_of_state(state), thread, scope,
             int(state.get("iteration") or 0), call_id, str(call.get("name") or "")]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


class _EffectJournal:
    """Short-lived connections; atomic insert admits exactly one caller."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        ready = False
        try:
            apply_sqlite_pragmas(conn)
            # The admission/result fence must survive a power loss after commit,
            # not only a process crash. Reject a volume that cannot provide WAL.
            if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
                raise sqlite3.OperationalError("Effect receipts require WAL support")
            conn.execute("PRAGMA synchronous=FULL")
            conn.executescript(_SCHEMA)
            from kazma_core.db.sqlite_columns import add_missing_columns

            # The turn and the space are stamped on the write. A read never
            # adds them: a file that lacks them is not readable as one turn.
            add_missing_columns(conn, "tool_effects", (
                ("turn_id", "TEXT NOT NULL DEFAULT ''"),
                ("space", "TEXT NOT NULL DEFAULT ''"),
            ))
            ready = True
            return conn
        finally:
            if not ready:
                conn.close()

    def prune_completed(self, *, days: int, now: float | None = None) -> int:
        """Remove completed receipts past *days* (2026-11, FA-141). A started
        receipt -- an effect whose outcome is unknown -- is never removed."""
        cutoff = (now if now is not None else time.time()) - days * 86400
        with closing(self._connect()) as conn:
            with conn:
                cur = conn.execute(
                    "DELETE FROM tool_effects WHERE state = 'completed' AND completed_at < ?", (cutoff,)
                )
                return int(cur.rowcount or 0)

    def forget_thread(self, thread_id: str) -> int:
        """Remove the completed receipts of a thread that never runs again.
        A started one -- an effect whose outcome is unknown -- stays."""
        if not self.path.exists():
            return 0
        with closing(self._connect()) as conn:
            with conn:
                cur = conn.execute(
                    "DELETE FROM tool_effects WHERE state = 'completed' AND thread_id = ?", (thread_id,))
                return int(cur.rowcount or 0)

    def begin(
        self, effect_id: str, request_hash: str, thread_id: str, tool: str,
        *, turn_id: str = "", space: str = "",
    ) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            with conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO tool_effects "
                    "(effect_id, request_hash, thread_id, tool, state, started_at, turn_id, space) "
                    "VALUES (?, ?, ?, ?, 'started', ?, ?, ?)",
                    (effect_id, request_hash, thread_id, tool, time.time(), turn_id or "", space or ""),
                )
                if cur.rowcount == 1:
                    return None
                row = conn.execute(
                    "SELECT request_hash, state, result_json FROM tool_effects WHERE effect_id = ?",
                    (effect_id,),
                ).fetchone()
                if row is None or row[0] != request_hash:
                    raise EffectUncertain(
                        "The tool-call identity is bound to a different request; dispatch was withheld."
                    )
                if row[1] != "completed" or row[2] is None:
                    raise EffectUncertain(
                        "A previous dispatch has no durable result. Its effects are unknown; "
                        "verify the target before starting new work. This call was not repeated."
                    )
                result = json.loads(row[2])
                if not isinstance(result, dict):
                    raise EffectUncertain("The saved effect result is invalid; dispatch was withheld.")
                return result

    def finish(self, effect_id: str, result: dict[str, Any]) -> None:
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
        with closing(self._connect()) as conn:
            with conn:
                cur = conn.execute(
                    "UPDATE tool_effects SET state = 'completed', result_json = ?, completed_at = ? "
                    "WHERE effect_id = ? AND state = 'started'",
                    (encoded, time.time(), effect_id),
                )
                if cur.rowcount != 1:
                    raise EffectUncertain(
                        "The effect result could not be committed; verify its target before retrying."
                    )

    def inspect(
        self, thread_id: str, *, limit: int = 200,
        turn_ids: list[str] | None = None, space: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read receipt metadata without exposing results or changing state.

        No schema step and no new ``-wal`` or ``-shm``. A log already beside
        the file is read, so a committed row still in it counts. *turn_ids*
        or *space* on a file that has no such column raises
        :class:`~kazma_core.db.sqlite_session.NotReadableError`. With neither, an
        older file is read as it stands.
        """
        if not self.path.exists():
            return []
        from kazma_core.db.sqlite_session import NotReadableError, connect_immutable
        from kazma_core.spaces.scope import OWNER_SPACE_ID

        conn = connect_immutable(self.path)
        try:
            conn.row_factory = sqlite3.Row
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(tool_effects)")}
            if "effect_id" not in columns:
                raise NotReadableError("tool_effects")
            where = "thread_id = ?"
            args: list[Any] = [thread_id]
            if turn_ids is not None:
                if "turn_id" not in columns:
                    raise NotReadableError("tool_effects")
                if not turn_ids:
                    return []
                where += " AND turn_id IN (" + ", ".join("?" for _ in turn_ids) + ")"
                args.extend(turn_ids)
            if space is not None:
                if "space" not in columns:
                    raise NotReadableError("tool_effects")
                if space == OWNER_SPACE_ID:
                    where += " AND (space = ? OR space = '' OR space IS NULL)"
                else:
                    where += " AND space = ?"
                args.append(space)
            args.append(min(1000, max(1, limit)))
            rows = conn.execute(
                "SELECT effect_id, thread_id, tool, state, started_at, completed_at, "
                "json_extract(result_json, '$.effect_uncertain') AS result_uncertain "
                "FROM tool_effects WHERE " + where + " ORDER BY started_at DESC LIMIT ?",
                args,
            ).fetchall()
            return [dict(row) for row in rows]
        except NotReadableError:
            raise
        except sqlite3.Error as exc:
            raise NotReadableError("tool_effects") from exc
        finally:
            conn.close()


async def execute_operation(
    namespace: str, operation_id: str, tool: str, arguments: dict[str, Any],
    dispatch: Callable[[], Awaitable[dict[str, Any]]], *, actor: str = "operator",
) -> dict[str, Any]:
    """Fence a non-graph operation; callers keep its ID for retries of that operation.

    A new deliberate operation needs a new ID. Neither this receipt nor a
    completed replay grants permission to execute a new action.
    """

    class Executor:
        async def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            return await dispatch()

    state = {
        "thread_id": namespace + ":" + actor,
        "tenant_id": require_space_id(),
        "created_at": namespace, "iteration": 0,
    }
    return await execute_effect(Executor(), state, {"id": operation_id, "name": tool}, arguments)


async def execute_effect(
    executor: Any, state: dict[str, Any], call: dict[str, Any], arguments: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch reads normally; fence checkpoint replays of mutating tools."""
    from kazma_core.safety.side_effects import is_read_only

    name = str(call.get("name") or "")
    read_only = is_read_only(name)
    if read_only:
        return await executor.execute(name, arguments)
    identity = _effect_identity(state, call)
    if not identity:
        return await executor.execute(name, arguments)
    try:
        from kazma_core.observability.correlation import current_turn_id
        from kazma_core.paths import tool_effects_db

        journal = _EffectJournal(await asyncio.to_thread(tool_effects_db))
        from kazma_core.workspace.binding import resolve_active_root

        root = await asyncio.to_thread(resolve_active_root)
        request_hash = hashlib.sha256(json.dumps(
            {"arguments": arguments, "workspace": str(root)},
            sort_keys=True, ensure_ascii=False, allow_nan=False,
        ).encode()).hexdigest()
        # The turn id is a ContextVar: read it here, not in the worker thread.
        turn_id = current_turn_id()
        cached = await asyncio.to_thread(
            journal.begin, identity, request_hash,
            str(state.get("thread_id") or ""), name,
            turn_id=turn_id, space=space_of_state(state),
        )
    except EffectUncertain:
        raise
    except (sqlite3.Error, OSError, TypeError, ValueError, OverflowError) as exc:
        raise EffectUncertain("The effect receipt is unavailable; dispatch was withheld.") from exc
    if cached is not None:
        logger.info("[effects] using recorded result tool=%s effect=%s", name, identity[:12])
        if cached.get("effect_uncertain"):
            raise EffectUncertain(
                "The recorded tool failure does not confirm its effects. "
                "Verify the target before starting new work; this call was not repeated."
            )
        return cached
    # Exceptions, cancellation and process death deliberately leave 'started'.
    try:
        result = await executor.execute(name, arguments)
    except Exception as exc:
        # Tool implementations may raise arbitrary application exceptions.
        # Keep the admission receipt and terminate this turn rather than retry.
        raise EffectUncertain(
            "The dispatch failed without a durable result. Its effects are unknown; "
            "verify the target before starting new work."
        ) from exc
    try:
        await asyncio.to_thread(journal.finish, identity, result)
    except (sqlite3.Error, OSError, TypeError, ValueError, OverflowError) as exc:
        raise EffectUncertain(
            "The tool returned but its result receipt was not committed. "
            "Its effects need reconciliation; this call must not be repeated."
        ) from exc
    if result.get("effect_uncertain"):
        raise EffectUncertain(
            "The tool reported a failure after invocation. Its effects are unknown; "
            "verify the target before starting new work. The failure was recorded "
            "and this call must not be repeated."
        )
    return result


#: How long a completed receipt is kept (2026-11, FA-141): a resume replays
#: a receipt only for a turn its checkpoint still holds, and an idle chat's
#: checkpoints are thinned after ``checkpoints.retention_days`` (30).
COMPLETED_RETENTION_DAYS = 90


def prune_completed_receipts(*, days: int = COMPLETED_RETENTION_DAYS) -> int:
    """The maintenance sweep's prune of the install's receipts."""
    from pathlib import Path

    from kazma_core.paths import tool_effects_db

    path = Path(tool_effects_db(create=False))
    if not path.exists():
        return 0
    return _EffectJournal(path).prune_completed(days=days)


def forget_thread_receipts(thread_id: str) -> int:
    """Remove the completed receipts of a chat that ended (its thread never
    runs again). Blocking."""
    from kazma_core.paths import tool_effects_db

    return _EffectJournal(tool_effects_db(create=False)).forget_thread(thread_id)


def inspect_effects(
    thread_id: str, *, limit: int = 200,
    turn_ids: list[str] | None = None, space: str | None = None,
) -> list[dict[str, Any]]:
    """Receipt metadata for one thread: no tool results, and no change of state.

    The same read :meth:`_EffectJournal.inspect` does. An absent journal file
    is an empty list (the file is created by the first effect, not by a read).
    *turn_ids* and *space* narrow that read; a file that cannot be narrowed
    without a migration raises ``NotReadableError``.
    """
    from kazma_core.paths import tool_effects_db

    return _EffectJournal(tool_effects_db(create=False)).inspect(
        thread_id, limit=limit, turn_ids=turn_ids, space=space,
    )


def main() -> int:
    """Operator inspection only; unknown effects are never reset or replayed."""
    import argparse

    from kazma_core.env_files import load_env_files

    parser = argparse.ArgumentParser(description="Inspect graph tool receipt metadata without replaying effects.")
    parser.add_argument("--thread", required=True)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    load_env_files()
    print(json.dumps(inspect_effects(args.thread, limit=args.limit), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
