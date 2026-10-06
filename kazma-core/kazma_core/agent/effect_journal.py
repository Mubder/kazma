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
from contextlib import closing
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas

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


def effect_identity(state: dict[str, Any], call: dict[str, Any]) -> str:
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
    parts = [str(state.get("tenant_id") or "default"), thread, scope,
             int(state.get("iteration") or 0), call_id, str(call.get("name") or "")]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


class EffectJournal:
    """Short-lived connections; atomic insert admits exactly one caller."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            apply_sqlite_pragmas(conn)
            # The admission/result fence must survive a power loss after commit,
            # not only a process crash. Reject a volume that cannot provide WAL.
            if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
                raise sqlite3.OperationalError("Effect receipts require WAL support")
            conn.execute("PRAGMA synchronous=FULL")
            conn.executescript(_SCHEMA)
            return conn
        except BaseException:
            conn.close()
            raise

    def begin(self, effect_id: str, request_hash: str, thread_id: str, tool: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            with conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO tool_effects "
                    "(effect_id, request_hash, thread_id, tool, state, started_at) "
                    "VALUES (?, ?, ?, ?, 'started', ?)",
                    (effect_id, request_hash, thread_id, tool, time.time()),
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

    def inspect(self, thread_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        """Read receipt metadata without exposing results or changing state."""
        if not self.path.exists():
            return []
        uri = self.path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT effect_id, thread_id, tool, state, started_at, completed_at, "
                "json_extract(result_json, '$.effect_uncertain') AS result_uncertain "
                "FROM tool_effects WHERE thread_id = ? ORDER BY started_at DESC LIMIT ?",
                (thread_id, min(1000, max(1, limit))),
            ).fetchall()
            return [dict(row) for row in rows]


async def execute_effect(
    executor: Any, state: dict[str, Any], call: dict[str, Any], arguments: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch reads normally; fence checkpoint replays of mutating tools."""
    from kazma_core.paths import data_dir
    from kazma_core.safety.side_effects import is_read_only

    name = str(call.get("name") or "")
    try:
        read_only = is_read_only(name)
    except Exception as exc:
        raise EffectUncertain("The effect classifier is unavailable; dispatch was withheld.") from exc
    if read_only:
        return await executor.execute(name, arguments)
    identity = effect_identity(state, call)
    if not identity:
        return await executor.execute(name, arguments)
    try:
        journal = EffectJournal((await asyncio.to_thread(data_dir)) / "tool_effects.db")
        from kazma_core.workspace.binding import resolve_active_root

        root = await asyncio.to_thread(resolve_active_root)
        request_hash = hashlib.sha256(json.dumps(
            {"arguments": arguments, "workspace": str(root)},
            sort_keys=True, ensure_ascii=False, allow_nan=False,
        ).encode()).hexdigest()
        cached = await asyncio.to_thread(journal.begin, identity, request_hash,
                                         str(state.get("thread_id") or ""), name)
    except EffectUncertain:
        raise
    except Exception as exc:
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
        raise EffectUncertain(
            "The dispatch failed without a durable result. Its effects are unknown; "
            "verify the target before starting new work."
        ) from exc
    try:
        await asyncio.to_thread(journal.finish, identity, result)
    except Exception as exc:
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


def main() -> int:
    """Operator inspection only; unknown effects are never reset or replayed."""
    import argparse

    from kazma_core.env_files import load_env_files
    from kazma_core.paths import data_dir

    parser = argparse.ArgumentParser(description="Inspect graph tool receipt metadata without replaying effects.")
    parser.add_argument("--thread", required=True)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    load_env_files()
    print(json.dumps(EffectJournal(data_dir() / "tool_effects.db").inspect(
        args.thread, limit=args.limit,
    ), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
