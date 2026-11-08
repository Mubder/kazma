"""Per-LLM-call ledger — durable, queryable record of every model call.

Closes the observability gap from the long-horizon audit: previously token
usage/cost/latency only existed as optional in-process Prometheus counters,
with no persisted per-call history to inspect after a multi-day run.

One row per supervisor LLM call: thread, iteration, model, provider, token
usage, cost, latency, status, error kind, and whether the call was served by
a failover model. SQLite WAL (``kazma-data/llm_calls.db``), module singleton,
thread-safe, and strictly best-effort — a ledger failure must never affect
the turn it describes.

Async code records through :func:`arecord_llm_call`, never
:func:`record_llm_call` itself: the write is SQLite, and on the event loop it
stalls every chat stream -- on a busy disk for the whole busy timeout
(AGENTS §26 E, §35). ``resilient_chat`` (swarm workers, research) wrote its
rows on the loop until 2026-10-15.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "arecord_llm_call",
    "close_llm_ledger",
    "query_recent",
    "record_llm_call",
    "thread_usage",
    "turn_usage",
    "usage_series",
    "usage_since",
]

logger = logging.getLogger(__name__)

from kazma_core.paths import data_dir as _data_dir

#: Resolved via paths so KAZMA_DATA_DIR is honoured.
_DEFAULT_DB = str(_data_dir() / "llm_calls.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    thread_id TEXT DEFAULT '',
    iteration INTEGER DEFAULT 0,
    provider TEXT DEFAULT '',
    model TEXT DEFAULT '',
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    duration_ms REAL DEFAULT 0.0,
    status TEXT DEFAULT 'ok',
    error_kind TEXT DEFAULT '',
    failover_from TEXT DEFAULT '',
    turn_id TEXT DEFAULT '',
    priced INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_ts ON llm_calls(ts);
CREATE INDEX IF NOT EXISTS idx_llm_calls_thread ON llm_calls(thread_id);
"""

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()


def _get_conn(db_path: str = _DEFAULT_DB) -> sqlite3.Connection:
    global _conn
    if _conn is None:
        from pathlib import Path

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(db_path, check_same_thread=False)
        from kazma_core.config_store import apply_sqlite_pragmas

        apply_sqlite_pragmas(_conn)
        _conn.executescript(_SCHEMA)
        _migrate(_conn)
    return _conn


#: Providers whose clients priced calls from their own tables before the
#: ``priced`` column: their old rows keep their cost.
_TABLE_PRICED_PROVIDERS = ("anthropic", "bedrock", "anthropicprovider", "bedrockprovider")
#: Local servers: their calls cost nothing, and the old rows say so.
_LOCAL_PROVIDERS = ("ollama", "lm-studio", "lmstudio", "local")


def _migrate(conn: sqlite3.Connection) -> None:
    """Columns added after the ledger was first made: ``turn_id``
    (2026-09-26) and ``priced`` (2026-11-08).

    Rows written before ``priced`` existed were priced at gpt-4o-mini's
    $0.15 / $0.60 whatever the model (kazma_core.model_prices). Once, as the
    column is added: a local server's rows cost nothing (and say so), the
    rows of a provider priced from its own table keep their cost, and every
    other row's cost becomes unknown -- the number was never its own.
    """
    from kazma_core.db.sqlite_columns import add_missing_columns

    added = add_missing_columns(
        conn, "llm_calls", (("turn_id", "TEXT DEFAULT ''"), ("priced", "INTEGER DEFAULT 1")),
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_calls_turn ON llm_calls(turn_id)")
    if "priced" in added:
        conn.execute(
            f"UPDATE llm_calls SET cost_usd = 0.0 WHERE lower(provider) IN ({_marks(_LOCAL_PROVIDERS)})",
            _LOCAL_PROVIDERS,
        )
        kept = (*_TABLE_PRICED_PROVIDERS, *_LOCAL_PROVIDERS)
        conn.execute(
            f"UPDATE llm_calls SET priced = 0, cost_usd = 0.0 WHERE lower(provider) NOT IN ({_marks(kept)})",
            kept,
        )
        logger.info("[llm-ledger] calls recorded before prices were checked: cost marked unknown")
    conn.commit()


def provider_of(client: Any) -> str:
    """The provider a client's calls go to -- its name in Settings, from the
    model registry that built it -- else the client's class. Every row said
    ``LLMProvider`` (the class), so the ledger could not tell DeepSeek from
    OpenRouter (8,839 such rows on the live install, 2026-11-08). No I/O:
    the registry is only peeked at."""
    try:
        from kazma_core.model_registry import peek_model_registry

        registry = peek_model_registry()
        name = registry.provider_for_client(client) if registry is not None else ""
    except Exception:  # noqa: BLE001 - a label must never fail a call
        name = ""
    return str(name or type(client).__name__)


def _marks(values: tuple[str, ...]) -> str:
    return ", ".join("?" for _ in values)


#: The SELECT list every sum reads: calls, tokens, dollars, and how many of
#: the calls had no known price (their dollars are not in the sum).
_SUMS = ("COUNT(*), COALESCE(SUM(total_tokens), 0), COALESCE(SUM(cost_usd), 0.0), "
         "COALESCE(SUM(CASE WHEN priced = 0 THEN 1 ELSE 0 END), 0)")


def _usage(calls: Any, tokens: Any, cost: Any, unpriced: Any) -> dict[str, Any]:
    return {"calls": int(calls or 0), "tokens": int(tokens or 0), "cost": float(cost or 0.0),
            "unpriced": int(unpriced or 0)}


def record_llm_call(
    *,
    thread_id: str = "",
    iteration: int = 0,
    provider: str = "",
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost_usd: float = 0.0,
    duration_ms: float = 0.0,
    status: str = "ok",
    error_kind: str = "",
    failover_from: str = "",
    turn_id: str | None = None,
    priced: bool = True,
) -> None:
    """Record one LLM call. Best-effort: never raises, never blocks the turn.

    ``priced`` False: no price was known for the call (``LLMResponse.
    cost_known``); its ``cost_usd`` is 0.0 and every sum counts it apart.

    ``turn_id`` defaults to the turn bound in this context
    (``observability.correlation``): the SSE path binds the reply turn id,
    which survives an approval pause, so every segment's calls sum to one
    turn (:func:`turn_usage`).
    """
    try:
        if turn_id is None:
            from kazma_core.observability.correlation import current_turn_id

            turn_id = current_turn_id()
        with _lock:
            conn = _get_conn()
            # Commits, or rolls back when the insert raises: an open
            # transaction on this shared connection would hold the ledger's
            # write lock until the next call.
            with conn:
                conn.execute(
                    """INSERT INTO llm_calls
                       (ts, thread_id, iteration, provider, model,
                        prompt_tokens, completion_tokens, total_tokens,
                        cost_usd, duration_ms, status, error_kind, failover_from,
                        turn_id, priced)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        datetime.now(UTC).isoformat(),
                        thread_id,
                        int(iteration or 0),
                        provider,
                        model,
                        int(prompt_tokens or 0),
                        int(completion_tokens or 0),
                        int(prompt_tokens or 0) + int(completion_tokens or 0),
                        float(cost_usd or 0.0),
                        float(duration_ms or 0.0),
                        status,
                        error_kind,
                        failover_from,
                        str(turn_id or ""),
                        1 if priced else 0,
                    ),
                )
    except Exception as exc:  # noqa: BLE001 — observability must be invisible
        logger.debug("[llm-ledger] record failed (non-fatal): %s", exc)


def _record_if_enabled(**fields: Any) -> None:
    """:func:`record_llm_call`, when the ledger is switched on.

    ``agent.nonstop.ledger.enabled`` is a settings read (a Postgres round trip
    on a cache miss), so the check runs where the write runs: in a worker
    thread, never on the loop. Never raises: neither does the switch's
    reader (``get_nonstop_config``) nor the write.
    """
    from kazma_core.agent.nonstop import get_nonstop_config

    if get_nonstop_config().ledger_enabled:
        record_llm_call(**fields)


#: How long a cancelled caller waits for its row before its cancellation goes
#: on. The write normally takes milliseconds; this bounds a locked disk.
_CANCEL_JOIN_SECONDS = 10.0


async def arecord_llm_call(**fields: Any) -> None:
    """Record one LLM call from async code, off the event loop.

    The write -- and the setting that switches the ledger on -- runs in a
    worker thread (``asyncio.to_thread``, which carries the caller's
    ContextVars). The caller awaits it, so its row is in the ledger before
    the caller goes on: a turn's calls are written in the order they were
    made, and a turn's totals (:func:`turn_usage`), read after its calls
    returned, include every one of them. The turn id is read here, from the
    caller's own context.

    A cancelled caller does not lose its row: the write is shielded (a write
    still queued for a thread would otherwise be dropped with the
    cancellation), and the cancellation waits for it, bounded, before it
    goes on. Never raises, except the caller's own cancellation.
    """
    if fields.get("turn_id") is None:
        from kazma_core.observability.correlation import current_turn_id

        fields["turn_id"] = current_turn_id()
    if not fields.get("thread_id"):
        # The work the call belongs to, from the caller's context when it did
        # not say: ``run_agent_turn`` binds its thread there, and a graph
        # whose state holds no thread (Diagnose, a cron task) wrote rows no
        # one could attribute (2026-11, FA-260).
        from kazma_core.safety.hitl import get_current_thread_id

        fields["thread_id"] = get_current_thread_id() or ""
    from kazma_core.background import spawn_background

    write = spawn_background(asyncio.to_thread(_record_if_enabled, **fields), name="llm-ledger-write")
    try:
        await asyncio.shield(write)
    except asyncio.CancelledError:
        try:
            await asyncio.wait({write}, timeout=_CANCEL_JOIN_SECONDS)
        except asyncio.CancelledError:
            pass  # cancelled again: stop waiting; the thread still writes the row
        raise
    except RuntimeError as exc:  # the thread pool is shut down (interpreter exit)
        logger.debug("[llm-ledger] record not written (non-fatal): %s", exc)


def query_recent(limit: int = 100, thread_id: str | None = None) -> list[dict[str, Any]]:
    """Return the most recent ledger rows (newest first)."""
    with _lock:
        conn = _get_conn()
        conn.row_factory = sqlite3.Row
        if thread_id:
            rows = conn.execute(
                "SELECT * FROM llm_calls WHERE thread_id = ? ORDER BY id DESC LIMIT ?",
                (thread_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM llm_calls ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def turn_usage(turn_id: str, *, since: str = "") -> dict[str, Any]:
    """Tokens, cost and call count of one turn's LLM calls. Never raises.

    ``since`` (an ISO timestamp as the rows store it) limits the sum to calls
    recorded after it -- one segment of a turn that paused for approval.
    Zeros when the turn has no rows (or the ledger is unreadable): callers
    tell "no calls" from "no ledger" by ``calls``.
    """
    empty = _usage(0, 0, 0.0, 0)
    if not turn_id:
        return empty
    try:
        with _lock:
            conn = _get_conn()
            sql = f"SELECT {_SUMS} FROM llm_calls WHERE turn_id = ?"
            args: list[Any] = [turn_id]
            if since:
                # Strictly after: the clock is coarse (~0.5 ms on Windows), and a
                # call stamped in the same tick as the segment's start belongs
                # to what came before it.
                sql += " AND ts > ?"
                args.append(since)
            row = conn.execute(sql, args).fetchone()
    except sqlite3.Error as exc:
        logger.debug("[llm-ledger] turn usage unreadable: %s", exc)
        return empty
    return _usage(*row)


def thread_usage(thread_id: str) -> dict[str, Any] | None:
    """Tokens, cost and call count of every LLM call one chat has made.

    What a chat app's ``/cost`` shows (it showed a hard-coded zero until
    2026-10-01). ``None`` when the ledger cannot be read, so an unreadable
    ledger is never reported as nothing spent.
    """
    if not thread_id:
        return _usage(0, 0, 0.0, 0)
    try:
        with _lock:
            conn = _get_conn()
            row = conn.execute(f"SELECT {_SUMS} FROM llm_calls WHERE thread_id = ?", (thread_id,)).fetchone()
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[llm-ledger] a chat's usage is unreadable: %s", exc)
        return None
    return _usage(*row)


def usage_since(since: datetime) -> dict[str, Any] | None:
    """Tokens, cost and call count of every LLM call recorded since *since*.

    What the daily cost limit counts (``agent.turn_token_budget``'s module,
    plan E1): *since* is the operator's local midnight, compared in UTC as
    the rows store it. ``None`` when the ledger cannot be read, so an
    unreadable ledger is never taken for nothing spent. Blocking: callers
    run it off the loop.
    """
    stamp = (since if since.tzinfo else since.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    try:
        with _lock:
            conn = _get_conn()
            row = conn.execute(f"SELECT {_SUMS} FROM llm_calls WHERE ts >= ?", (stamp,)).fetchone()
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[llm-ledger] today's usage is unreadable: %s", exc)
        return None
    return _usage(*row)


def unpriced_models(days: int = 30) -> list[dict[str, Any]] | None:
    """The models called in the last *days* with no known price, most calls
    first: what Settings -> Providers & Connectors -> Models -> Model prices offers to
    price. ``None`` when the ledger cannot be read. Blocking."""
    from datetime import timedelta

    since = (datetime.now(UTC) - timedelta(days=max(1, int(days)))).isoformat()
    try:
        with _lock:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT model, COUNT(*), MAX(ts) FROM llm_calls "
                "WHERE priced = 0 AND ts >= ? AND model != '' GROUP BY model ORDER BY 2 DESC LIMIT 50",
                (since,),
            ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[llm-ledger] models without a price are unreadable: %s", exc)
        return None
    return [{"model": str(m), "calls": int(n or 0), "last": str(last or "")} for m, n, last in rows]


def used_models(days: int = 30) -> dict[str, int] | None:
    """Every model called in the last *days*, priced or not, with its number
    of calls: the models Settings' Model details lists first (2026-11-08:
    the card listed every model of every working provider, OpenRouter's 425
    among them, and was most of the page). ``None`` when the ledger cannot be
    read. Blocking."""
    from datetime import timedelta

    since = (datetime.now(UTC) - timedelta(days=max(1, int(days)))).isoformat()
    try:
        with _lock:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT model, COUNT(*) FROM llm_calls WHERE ts >= ? AND model != '' GROUP BY model",
                (since,),
            ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[llm-ledger] models used are unreadable: %s", exc)
        return None
    return {str(m): int(n or 0) for m, n in rows}


def usage_series(
    since: datetime, until: datetime, bucket_seconds: int,
) -> list[dict[str, Any]] | None:
    """Tokens, cost and calls of the LLM calls in ``[since, until)``, per bucket.

    What the Dashboard's "Tokens Over Time" and "Cost Over Time" draw. They
    drew the process's running totals, sampled by the open page every 10 s
    and kept only in that page's memory (60 samples): bars that only grew,
    reset by a restart or a reload, and "Day" / "Week" never more than the
    tab had seen (2026-10-22). Every bucket is returned, empty ones as
    zeros. ``None`` when the ledger cannot be read, never an empty chart.
    Blocking: callers run it off the loop.
    """
    if bucket_seconds <= 0 or until <= since:
        return []
    count = int((until - since).total_seconds() // bucket_seconds)
    buckets = [{"start": since.timestamp() + i * bucket_seconds,
                "tokens": 0, "cost": 0.0, "calls": 0, "unpriced": 0} for i in range(count)]
    try:
        with _lock:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT ts, total_tokens, cost_usd, priced FROM llm_calls "
                "WHERE ts >= ? AND ts < ?",
                (since.isoformat(), until.isoformat()),
            ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[llm-ledger] usage over time is unreadable: %s", exc)
        return None
    start = since.timestamp()
    for ts, tokens, cost, priced in rows:
        try:
            at = datetime.fromisoformat(ts).timestamp()
        except (TypeError, ValueError):
            continue
        index = int((at - start) // bucket_seconds)
        if 0 <= index < count:
            bucket = buckets[index]
            bucket["tokens"] += int(tokens or 0)
            bucket["cost"] += float(cost or 0.0)
            bucket["calls"] += 1
            bucket["unpriced"] += 1 if priced == 0 else 0
    return buckets


#: How long a model call's row is kept (2026-11, FA-141): a chat's ``/cost``
#: and the usage charts read it, so a year; the ledger kept every call.
CALL_RETENTION_DAYS = 365
_PRUNE_BATCH = 20000


def prune_old_calls(*, days: int = CALL_RETENTION_DAYS) -> int:
    """Remove model-call rows past *days*; how many were removed."""
    from datetime import UTC, datetime, timedelta

    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with _lock:
        conn = _get_conn()
        with conn:
            cur = conn.execute(
                "DELETE FROM llm_calls WHERE id IN (SELECT id FROM llm_calls WHERE ts < ? LIMIT ?)",
                (cutoff, _PRUNE_BATCH),
            )
            return int(cur.rowcount or 0)


def close_llm_ledger() -> None:
    """Close the ledger connection (process shutdown)."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:  # noqa: BLE001
                pass
            try:
                _conn.close()
            except Exception:  # noqa: BLE001
                pass
            _conn = None
