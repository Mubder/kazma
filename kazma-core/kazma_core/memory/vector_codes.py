"""Narrowing meaning search past a size, with every answer re-scored exactly
(the memory framework audit's F15, part H).

Exact search is the truth: :mod:`kazma_core.memory.vector_engine` compares
the question with every comparable row, and recall's ranking (the evidence,
the background of meaning ranks 4-15) is built on those exact scores. It is
also what makes recall slow as memory grows: every vector of the space is
read for every question (Kazma's guide measured about 8 seconds a question at
170,000 memories). Past :data:`NARROW_FROM` coded rows of one kind and space,
the search narrows first and re-scores exactly:

1. **The approximate index.** Each vector has a sign code
   (``memory_vector_codes``: one bit per dimension, 128 bytes for bge-m3's
   1,024), made by the "memory vector codes" sweep (:func:`fill_codes`, a
   budget a pass). A code is a residue of its vector, so it goes with it:
   triggers delete the code when the row's vector changes, the row moves to
   another space, or the row is deleted (a forget drops the vector, and with
   it the code).
2. **The candidates**: the rows whose code is nearest the question's
   (Hamming distance: :data:`NARROW_MIN` of them, or :data:`NARROW_FACTOR`
   times what was asked for, whichever is more, and every row tied with the
   last one taken), and every comparable row that has no code yet (written
   since the last sweep: never lost to the narrowing).
3. **Re-scored exactly**: the engine's own exact query, over those rows
   only, in the order a full scan reads them (rowid), so equal scores keep
   the order a full scan gives them.

The results equal the full scan's whenever the true nearest rows are among
the candidates; the retrieval benchmark is held to equal results with
narrowing forced on (``tests/test_memory_narrowing.py``). Returning the
codes' estimates without the exact re-score changes them (the control).

The engine never writes; the sweep does. No numpy: no narrowing.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "FILL_PER_PASS",
    "KINDS",
    "NARROW_FACTOR",
    "NARROW_FROM",
    "NARROW_MIN",
    "candidates",
    "coded_count",
    "ensure_codes_schema",
    "fill_codes",
]

#: Past this many coded rows of one kind in one space, a search narrows.
NARROW_FROM = 20_000
#: At least this many candidates are re-scored exactly...
NARROW_MIN = 400
#: ...or this many times what the search asked for, whichever is more.
NARROW_FACTOR = 8
#: The codes the sweep makes in one pass, and its time.
FILL_PER_PASS = 20_000
FILL_SECONDS = 30.0
_FILL_BATCH = 2000

#: kind -> table. Facts and memories of conversations; an entity's name and
#: a summary are few, and are always searched in full.
KINDS: dict[str, str] = {"episode": "episodes", "belief": "beliefs"}


def ensure_codes_schema(conn: Any) -> None:
    """The code table and the triggers that take a code with its vector."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memory_vector_codes ("
        " kind TEXT NOT NULL, id TEXT NOT NULL, tenant_id TEXT NOT NULL,"
        " model TEXT NOT NULL DEFAULT '', dim INTEGER NOT NULL, code BLOB NOT NULL,"
        " PRIMARY KEY (kind, id))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vector_codes_kind_tenant ON memory_vector_codes(kind, tenant_id)")
    for kind, table in KINDS.items():
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS kazma_vector_code_au_{table} "
            f"AFTER UPDATE OF embedding, embedding_model_version, tenant_id ON {table} "
            "WHEN old.embedding IS NOT new.embedding OR old.embedding_model_version IS NOT "
            "new.embedding_model_version OR old.tenant_id IS NOT new.tenant_id BEGIN "
            f"DELETE FROM memory_vector_codes WHERE kind = '{kind}' AND id = old.id; END")
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS kazma_vector_code_ad_{table} AFTER DELETE ON {table} BEGIN "
            f"DELETE FROM memory_vector_codes WHERE kind = '{kind}' AND id = old.id; END")


def _numpy() -> Any | None:
    try:
        import numpy as np  # type: ignore[import-untyped]
    except ImportError:
        return None
    return np


def _sign_code(np: Any, vector: Any) -> bytes:
    """One bit per dimension: set where the value is above zero."""
    return np.packbits(np.asarray(vector, dtype=np.float32) > 0).tobytes()


def fill_codes(conn: sqlite3.Connection, *, budget: int = FILL_PER_PASS,
               seconds: float = FILL_SECONDS) -> dict[str, int]:
    """Make the codes rows lack (the "memory vector codes" sweep): at most
    *budget* rows and about *seconds* a pass, newest rows first; the rows
    still without one are the next pass's (the anti-join is the cursor: a
    code is never made twice, nor kept for a vector that changed). Each batch
    is read and written in one write transaction, so a vector cannot change
    between its read and its code (the trigger would have nothing to delete
    yet). Returns how many were made and how many still wait."""
    np = _numpy()
    out = {"made": 0, "waiting": 0}
    if np is None:
        return out
    deadline = time.monotonic() + max(0.1, float(seconds))
    if conn.in_transaction:
        conn.commit()
    for kind, table in KINDS.items():
        while out["made"] < budget and time.monotonic() <= deadline:
            conn.execute("BEGIN IMMEDIATE")
            committed = False
            try:
                rows = conn.execute(
                    f"SELECT t.id, t.tenant_id, COALESCE(t.embedding_model_version, ''), t.embedding "  # nosec B608 - fixed tables
                    f"FROM {table} t LEFT JOIN memory_vector_codes c ON c.kind = ? AND c.id = t.id "
                    "WHERE c.id IS NULL AND t.embedding IS NOT NULL AND length(t.embedding) > 0 "
                    "AND length(t.embedding) % 4 = 0 ORDER BY t.rowid DESC LIMIT ?",
                    (kind, min(_FILL_BATCH, budget - out["made"]))).fetchall()
                batch = []
                for rid, tenant, model, blob in rows:
                    vec = np.frombuffer(bytes(blob), dtype=np.float32)
                    batch.append((kind, str(rid), str(tenant), str(model), int(vec.shape[0]), _sign_code(np, vec)))
                conn.executemany(
                    "INSERT OR REPLACE INTO memory_vector_codes (kind, id, tenant_id, model, dim, code) "
                    "VALUES (?, ?, ?, ?, ?, ?)", batch)
                conn.commit()
                committed = True
            finally:
                # A failure leaves nothing written and the write lock released.
                if not committed and conn.in_transaction:
                    conn.rollback()
            out["made"] += len(batch)
            if len(rows) < _FILL_BATCH:
                break
    for kind, table in KINDS.items():
        out["waiting"] += int(conn.execute(
            f"SELECT COUNT(*) FROM {table} t LEFT JOIN memory_vector_codes c ON c.kind = ? AND c.id = t.id "  # nosec B608 - fixed tables
            "WHERE c.id IS NULL AND t.embedding IS NOT NULL AND length(t.embedding) > 0", (kind,)).fetchone()[0])
    return out


def coded_count(conn: sqlite3.Connection, kind: str, tenant_id: str) -> int:
    """How many rows of *kind* in *tenant_id*'s space have a code."""
    try:
        return int(conn.execute("SELECT COUNT(*) FROM memory_vector_codes WHERE kind = ? AND tenant_id = ?",
                                (kind, tenant_id)).fetchone()[0])
    except sqlite3.OperationalError:
        return 0  # a database from before the codes


def candidates(
    conn: sqlite3.Connection, np: Any, *, kind: str, predicate: str, params: list[Any],
    query_vec: list[float], limit: int,
) -> list[int]:
    """The rowids to re-score exactly (the module docstring's step 2): the
    rows meeting *predicate* (the engine's own, on the table's columns) whose
    code is nearest the question's, ties at the cut included, and every one
    with no usable code; ascending."""
    table = KINDS[kind]
    want = max(NARROW_MIN, NARROW_FACTOR * int(limit))
    q = _sign_code(np, query_vec)
    rows = f"(SELECT rowid AS rid, id, COALESCE(embedding_model_version, '') AS v FROM {table} WHERE {predicate})"  # nosec B608 - fixed tables; the predicate is the engine's own
    coded = conn.execute(
        f"SELECT r.rid, c.code FROM {rows} r JOIN memory_vector_codes c ON c.kind = ? AND c.id = r.id "  # nosec B608 - see above
        "AND c.dim = ? AND c.model = r.v", [*params, kind, len(query_vec)]).fetchall()
    out: list[int] = []
    if coded:
        rowids = np.fromiter((r[0] for r in coded), dtype=np.int64, count=len(coded))
        codes = np.frombuffer(b"".join(bytes(r[1]) for r in coded), dtype=np.uint8).reshape(len(coded), len(q))
        distance = np.unpackbits(np.bitwise_xor(codes, np.frombuffer(q, dtype=np.uint8)), axis=1).sum(axis=1)
        if len(coded) <= want:
            out = rowids.tolist()
        else:
            cut = np.partition(distance, want - 1)[want - 1]
            out = rowids[distance <= cut].tolist()
    uncoded = conn.execute(
        f"SELECT r.rid FROM {rows} r LEFT JOIN memory_vector_codes c ON c.kind = ? AND c.id = r.id "  # nosec B608 - see above
        "AND c.dim = ? AND c.model = r.v WHERE c.id IS NULL", [*params, kind, len(query_vec)]).fetchall()
    out.extend(int(r[0]) for r in uncoded)
    return sorted(set(out))
