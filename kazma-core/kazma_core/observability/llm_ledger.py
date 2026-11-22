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

Every row names the space and the person the call was made for (Spaces
S1-22, ``space``, ``asker``): the scope bound where the call was made -- a
turn's, a scheduled fire's, a swarm worker's -- read in the caller's own
context. Kazma's own work (``install_work``) and a call with nothing bound
name nobody. :func:`spend_by_person` is the admin's view (N11: spend, never
text).

Every row also counts the prompt tokens the provider served from its cache
(``cached_tokens``, 2026-11-22, the memory plan's finding 9), read from the
call's own usage whatever its shape (:func:`token_counts`): DeepSeek's
``prompt_cache_hit_tokens``, OpenAI's and Qwen's
``prompt_tokens_details.cached_tokens``, Anthropic's
``cache_read_input_tokens``. A caller hands the usage over (``usage=``) and
the ledger reads it; every sum gives the prompt tokens and their cached
share beside each other.

Memory's own model calls -- the deep pass, the weekly summaries and
roll-ups, the lesson refiner, the V1 backfill -- go through
:func:`ledgered_chat` (finding 6): they were in no ledger, so no spend
report, product measure or person's daily limit counted them.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import sys
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from kazma_core.token_usage import token_counts

__all__ = [
    "arecord_llm_call",
    "close_llm_ledger",
    "ledgered_chat",
    "query_recent",
    "record_llm_call",
    "spend_by_person",
    "thread_usage",
    "usage_of",
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
    priced INTEGER DEFAULT 1,
    space TEXT DEFAULT '',
    asker TEXT DEFAULT '',
    cached_tokens INTEGER DEFAULT 0,
    own_key INTEGER DEFAULT 0
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
    (2026-09-26), ``priced`` (2026-11-08), ``space`` and ``asker``
    (2026-11-20, Spaces S1-22: a row from before names nobody),
    ``cached_tokens`` (2026-11-22: a row from before counts none),
    ``own_key`` (2026-11-23, Spaces S2-04: a call made with the person's own
    key; a row from before was made with the install's).

    Rows written before ``priced`` existed were priced at gpt-4o-mini's
    $0.15 / $0.60 whatever the model (kazma_core.model_prices). Once, as the
    column is added: a local server's rows cost nothing (and say so), the
    rows of a provider priced from its own table keep their cost, and every
    other row's cost becomes unknown -- the number was never its own.
    """
    from kazma_core.db.sqlite_columns import add_missing_columns

    added = add_missing_columns(
        conn, "llm_calls", (("turn_id", "TEXT DEFAULT ''"), ("priced", "INTEGER DEFAULT 1"),
                            ("space", "TEXT DEFAULT ''"), ("asker", "TEXT DEFAULT ''"),
                            ("cached_tokens", "INTEGER DEFAULT 0"), ("own_key", "INTEGER DEFAULT 0")),
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_calls_turn ON llm_calls(turn_id)")
    # One person's day (Spaces S2-02): a person's limit is checked when their
    # turn starts, by account and time.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_calls_asker_ts ON llm_calls(asker, ts)")
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


#: The SELECT list every sum reads: calls, tokens, dollars, how many of the
#: calls had no known price (their dollars are not in the sum), and the
#: prompt tokens with the share of them the provider served from its cache.
_SUMS = ("COUNT(*), COALESCE(SUM(total_tokens), 0), COALESCE(SUM(cost_usd), 0.0), "
         "COALESCE(SUM(CASE WHEN priced = 0 THEN 1 ELSE 0 END), 0), "
         "COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(cached_tokens), 0)")
#: How many columns :data:`_SUMS` reads.
_SUMS_WIDTH = 6


def _usage(calls: Any, tokens: Any, cost: Any, unpriced: Any, prompt: Any = 0,
           cached: Any = 0) -> dict[str, Any]:
    return {"calls": int(calls or 0), "tokens": int(tokens or 0), "cost": float(cost or 0.0),
            "unpriced": int(unpriced or 0), "prompt_tokens": int(prompt or 0),
            "cached_tokens": int(cached or 0)}


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
    space: str | None = None,
    asker: str | None = None,
    cached_tokens: int = 0,
    usage: Mapping[str, Any] | None = None,
    own_key: bool | None = None,
) -> None:
    """Record one LLM call. Best-effort: never raises, never blocks the turn.

    ``usage``: the provider's usage of an answered call, read with
    :func:`token_counts` for the prompt, completion and cached tokens a
    caller did not give (finding 9: the cache's share was in no row).

    ``priced`` False: no price was known for the call (``LLMResponse.
    cost_known``); its ``cost_usd`` is 0.0 and every sum counts it apart.

    ``turn_id`` defaults to the turn bound in this context
    (``observability.correlation``): the SSE path binds the reply turn id,
    which survives an approval pause, so every segment's calls sum to one
    turn (:func:`turn_usage`).

    ``own_key`` defaults to whether the call was made with the person's own
    key for its provider (Spaces S2-04, ``spaces.own_keys.uses_own_key``:
    the answer the call itself was given). Such a call is the person's to
    pay: no limit counts it (:func:`usage_of`, :func:`usage_since`).
    """
    try:
        if usage is not None:
            counts = token_counts(usage)
            prompt_tokens = int(prompt_tokens or 0) or counts.prompt
            completion_tokens = int(completion_tokens or 0) or counts.completion
            cached_tokens = int(cached_tokens or 0) or counts.cached
        cached_tokens = min(max(0, int(cached_tokens or 0)), max(0, int(prompt_tokens or 0)))
        if turn_id is None:
            from kazma_core.observability.correlation import current_turn_id

            turn_id = current_turn_id()
        if space is None or asker is None:
            bound_space, bound_asker = _whose()
            space = bound_space if space is None else space
            asker = bound_asker if asker is None else asker
        if own_key is None:
            from kazma_core.spaces.own_keys import uses_own_key

            own_key = uses_own_key(str(asker or ""), str(provider or ""))
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
                        turn_id, priced, space, asker, cached_tokens, own_key)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        str(space or "")[:120],
                        str(asker or "")[:120],
                        cached_tokens,
                        1 if own_key else 0,
                    ),
                )
    except Exception as exc:  # noqa: BLE001 — observability must be invisible
        logger.debug("[llm-ledger] record failed (non-fatal): %s", exc)


def _whose() -> tuple[str, str]:
    """(space, asker) of the scope bound where the call is made, ("", "")
    with none (Kazma's own work, or nothing bound). No I/O."""
    from kazma_core.spaces.scope import current_scope

    bound = current_scope()
    return (bound.space_id, bound.asker) if bound is not None else ("", "")


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
    if fields.get("space") is None or fields.get("asker") is None:
        # Whose call it is, read here in the caller's own context (S1-22).
        space, asker = _whose()
        if fields.get("space") is None:
            fields["space"] = space
        if fields.get("asker") is None:
            fields["asker"] = asker
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


async def ledgered_chat(client: Any, messages: Any, *, work: str, **kwargs: Any) -> Any:
    """``client.chat(messages, **kwargs)`` with its row in this ledger.

    For model calls made outside a turn's supervisor and outside
    ``resilient_chat`` -- memory's own work (the deep pass, the weekly
    summaries and roll-ups, the lesson refiner, the V1 backfill): none of
    them was in the ledger (the memory plan's finding 6), so no spend
    report, product measure or person's daily limit counted them. Whose
    call it is comes from the scope bound where it is made
    (:func:`arecord_llm_call`): the person the work acts for, nobody for
    Kazma's own sweeps. ``work`` names the call in ``thread_id`` when no
    thread is bound (``memory:<work>``).

    An answered call's row carries its usage (prompt, completion and the
    cache's share, :func:`token_counts`); a call that raised gets an
    ``error`` row and its exception goes on. A cancelled call writes no row
    (nothing is known of it).
    """
    from kazma_core.safety.hitl import get_current_thread_id

    thread_id = get_current_thread_id() or f"memory:{work}"
    started = time.monotonic()
    answered = False
    try:
        response = await client.chat(messages, **kwargs)
        answered = True
    finally:
        # The failure's row, while the exception goes on (no handler
        # catches it here); a cancellation writes none.
        failure = None if answered else sys.exc_info()[1]
        if failure is not None and not isinstance(failure, asyncio.CancelledError):
            await arecord_llm_call(
                thread_id=thread_id, provider=provider_of(client), model=_model_of(client, None),
                duration_ms=(time.monotonic() - started) * 1000, status="error",
                error_kind=str(getattr(failure, "kind", "") or type(failure).__name__),
            )
    usage = getattr(response, "usage", None)
    await arecord_llm_call(
        thread_id=thread_id, provider=provider_of(client), model=_model_of(client, response),
        cost_usd=float(getattr(response, "cost_usd", 0.0) or 0.0),
        duration_ms=(time.monotonic() - started) * 1000, status="ok",
        priced=bool(getattr(response, "cost_known", False)),
        usage=usage if isinstance(usage, Mapping) else {},
    )
    return response


def _model_of(client: Any, response: Any) -> str:
    """The model that answered, else the one the client asks for."""
    return str(getattr(response, "model", "") or getattr(getattr(client, "config", None), "model", "") or "")


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


def spend_by_person(since: datetime) -> list[dict[str, Any]] | None:
    """What each person's model calls cost since *since*: one entry per
    account the calls were made for (``asker``), with its calls, tokens,
    dollars and the calls of unknown price, the most first. The calls that
    name nobody -- Kazma's own work, and rows from before Spaces S1-22 --
    are the entry with an empty ``asker``. Spend and counts only, never a
    chat or a model's text (N11). A call made with the person's own key
    (Spaces S2-04) is theirs to pay and is not in it. None when the ledger
    cannot be read. Blocking (SQLite)."""
    stamp = (since if since.tzinfo else since.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    try:
        with _lock:
            conn = _get_conn()
            rows = conn.execute(
                f"SELECT asker, {_SUMS}, COUNT(DISTINCT space) FROM llm_calls WHERE ts >= ? AND own_key = 0 "
                "GROUP BY asker ORDER BY SUM(cost_usd) DESC, SUM(total_tokens) DESC",
                (stamp,),
            ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[llm-ledger] spend per person could not be read: %s", exc)
        return None
    return [{"asker": str(row[0] or ""), **_usage(*row[1:1 + _SUMS_WIDTH]),
             "spaces": int(row[1 + _SUMS_WIDTH] or 0)} for row in rows]


def usage_of(asker: str, since: datetime) -> dict[str, Any] | None:
    """Tokens, cost and call count of the model calls made for *asker* since
    *since*: what a person's daily limit counts (``spaces.daily_limits``,
    S2-02). ``cost`` holds only the calls of known price, ``unpriced`` counts
    the rest; their tokens are in ``tokens``. A call made with the person's
    own key (Spaces S2-04) is not counted: it is theirs to pay. None when the
    ledger cannot be read. Blocking (SQLite)."""
    stamp = (since if since.tzinfo else since.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    try:
        with _lock:
            conn = _get_conn()
            row = conn.execute(
                f"SELECT {_SUMS} FROM llm_calls WHERE asker = ? AND ts >= ? AND own_key = 0", (str(asker), stamp),
            ).fetchone()
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[llm-ledger] a person's usage is unreadable: %s", exc)
        return None
    return _usage(*row)


def usage_since(since: datetime) -> dict[str, Any] | None:
    """Tokens, cost and call count of every LLM call recorded since *since*.

    What the daily cost limit counts (``agent.turn_token_budget``'s module,
    plan E1): *since* is the operator's local midnight, compared in UTC as
    the rows store it. ``None`` when the ledger cannot be read, so an
    unreadable ledger is never taken for nothing spent. A call made with a
    person's own key (Spaces S2-04) is not the install's to pay and is not
    counted. Blocking: callers run it off the loop.
    """
    stamp = (since if since.tzinfo else since.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    try:
        with _lock:
            conn = _get_conn()
            row = conn.execute(f"SELECT {_SUMS} FROM llm_calls WHERE ts >= ? AND own_key = 0", (stamp,)).fetchone()
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
