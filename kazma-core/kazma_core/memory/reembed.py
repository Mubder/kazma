"""Embedding rebuild + version-accounting helpers (in-process, UI-driven).

The Web UI Embedder settings page offers a one-click "Rebuild embeddings"
action, and ``scripts/reembed.py`` runs the same :func:`rebuild_embeddings`
from a shell (it was a second copy until 2026-10-14, and kept deleting the
Knowledge Library's vector store after this one had stopped).

* **Incremental**: only rows whose ``embedding_model_version`` differs from
  the currently configured model are re-encoded (after a fresh model switch
  that is every row; after a small drift it is only the stray rows — e.g.
  episodes written while a stale server was running).
* **Status-driven** (the page): progress is persisted to ConfigStore key
  ``embedding.rebuild_status`` so the UI can poll it. The rebuild runs on a
  background thread via ``asyncio.to_thread`` — never block the event loop.

Row accounting (:func:`embedding_version_counts`) reads the primary memory
DB read-only and groups episodes/beliefs by model version — the numbers the
UI shows in the "vector-space composition" card.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any, Callable

from kazma_core.memory.episode_text import embed_text

logger = logging.getLogger(__name__)

__all__ = [
    "REBUILD_STATUS_KEY",
    "embedding_version_counts",
    "get_rebuild_status",
    "rebuild_embeddings",
    "repair_unsearchable_vectors",
    "reset_rebuild_status",
    "run_vector_repair_pass",
    "vector_repair_counts",
]

REBUILD_STATUS_KEY = "embedding.rebuild_status"

ProgressCallback = Callable[[int, int], None]  # (done, total)

_IDLE_STATUS = {
    "state": "idle",
    "model": "",
    "total": 0,
    "done": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}

_BATCH_SIZE = 16  # status updates / commits every N rows


def _config_store():
    """Return the ConfigStore singleton without constructing it."""
    try:
        import kazma_core.config_store as _cs_mod

        return getattr(_cs_mod, "_config_store", None)
    except Exception:
        return None


def _set_status(status: dict[str, Any]) -> None:
    try:
        store = _config_store()
        if store is not None:
            store.set(REBUILD_STATUS_KEY, status, category="embedding")
    except Exception:
        logger.debug("[reembed] status write failed", exc_info=True)


def get_rebuild_status() -> dict[str, Any]:
    """Return the persisted rebuild status (never raises)."""
    try:
        store = _config_store()
        if store is not None:
            val = store.get(REBUILD_STATUS_KEY)
            if isinstance(val, dict):
                return {**_IDLE_STATUS, **val}
    except Exception:
        logger.debug("[reembed] status read failed", exc_info=True)
    return dict(_IDLE_STATUS)


def reset_rebuild_status() -> None:
    """Clear the persisted rebuild status (e.g. after a model change)."""
    try:
        store = _config_store()
        if store is not None:
            store.delete(REBUILD_STATUS_KEY)
    except Exception:
        logger.debug("[reembed] status reset failed", exc_info=True)


def embedding_version_counts() -> dict[str, dict[str, int]]:
    """Count episodes/beliefs per ``embedding_model_version`` (read-only).

    Returns ``{"episodes": {"BAAI/bge-m3": 435, ...}, "beliefs": {...}}``.
    Rows with a NULL version are grouped under ``"(none)"``. Never raises.
    """
    out: dict[str, dict[str, int]] = {"episodes": {}, "beliefs": {}}
    try:
        from kazma_core.paths import primary_memory_db

        db = primary_memory_db()
        if not db or not os.path.isfile(db):
            return out
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            for table in ("episodes", "beliefs"):
                rows = con.execute(
                    "SELECT COALESCE(embedding_model_version, '(none)') AS v, count(*) "
                    f"FROM {table} GROUP BY v"
                ).fetchall()
                out[table] = {str(r[0]): int(r[1]) for r in rows}
        finally:
            con.close()
    except Exception:
        logger.debug("[reembed] version counts failed", exc_info=True)
    return out


#: The row still holds what was embedded, and is no tombstone: an episode
#: with its three texts unchanged and not forgotten, a fact with its triple
#: unchanged and a value (a forget empties ``object``). Parameters: the texts
#: read with the row (audit 2026-10-26 AUD-028).
_EPISODE_STILL = (
    "tier != 'forgotten' AND user_text IS ? AND assistant_text IS ? AND summary_text IS ?"
)
_BELIEF_STILL = "subject IS ? AND predicate IS ? AND object IS ? AND object != ''"


def _reencode_row(
    conn: sqlite3.Connection,
    emb: Any,
    kind: str,
    rid: str,
    text: str,
    *,
    still_params: tuple[Any, ...],
    replacing: bytes | None,
    model: str,
    tenant_id: str,
    meta: dict[str, Any],
    dim: int | None = None,
) -> str:
    """Re-encode one row in place, the §15 D2 way. Returns ``"written"``,
    ``"skipped"`` (no text, no vector, or a vector of another size) or
    ``"changed"`` (the row changed while it was embedded: left as it is).

    The vector is computed once (:func:`encode_vector`) with no transaction
    open, written by ONE guarded ``UPDATE`` (:func:`write_local_vector`: the
    row must still hold the text that was embedded and the vector it had when
    read, and a tombstone never takes one), then sent to the remote index,
    which takes it back out if a forget landed meanwhile. The rebuild and the
    repair used to embed rows and write them inside an open batch
    transaction, unguarded: a forget between the read and the write got the
    forgotten text's vector back on its tombstone (and in the remote index),
    and the batch held the memory write lock while the model ran (AUD-028).
    """
    from kazma_core.memory.vector_write import encode_vector, index_remotely, write_local_vector

    still_sql = _EPISODE_STILL if kind == "episodes" else _BELIEF_STILL
    if conn.in_transaction:
        conn.commit()  # nothing of ours is held while the model runs
    vec = encode_vector(text, embedder=emb)
    if not vec or (dim is not None and len(vec) != dim):
        return "skipped"
    if not write_local_vector(conn, kind, rid, vec, still_sql=still_sql, still_params=still_params,
                              replace=True, replacing=replacing, model=model):
        return "changed"

    def _still_held() -> bool:
        return conn.execute(
            f"SELECT 1 FROM {kind} WHERE id = ? AND {still_sql}", (rid, *still_params),
        ).fetchone() is not None

    index_remotely(rid, vec, tenant_id=tenant_id, meta=meta, still_held=_still_held)
    return "written"


def _backup(db_path: str) -> None:
    """The copy kept before a rebuild, ``<db>.pre_reembed``: taken again at
    every rebuild and replacing the last (Memory audit M28 -- it was written
    once and kept forever, outside backup retention, with text forgotten since;
    the erasure horizon says "until the next rebuild")."""
    from kazma_core.db.sqlite_snapshot import snapshot_copy

    backup_path = db_path + ".pre_reembed"
    tmp = backup_path + ".tmp"
    try:
        snapshot_copy(db_path, tmp)
        os.replace(tmp, backup_path)
        logger.info("[reembed] pre-rebuild copy -> %s", backup_path)
    except (sqlite3.Error, OSError):
        logger.warning("[reembed] pre-rebuild copy failed (continuing)", exc_info=True)
        with contextlib.suppress(OSError):
            os.remove(tmp)


def rebuild_embeddings(
    progress: ProgressCallback | None = None,
    *,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Re-embed every row whose model version ≠ the configured model.

    Runs synchronously — call via ``asyncio.to_thread`` from the server.
    Returns a summary dict. *progress* is invoked as ``(done, total)``.

    Safe to re-run: rows already stamped with the current model are skipped.
    """
    from kazma_core.memory.embedder import get_embedder, get_embedding_model_name
    from kazma_core.paths import primary_memory_db

    target_model = model_name or get_embedding_model_name()
    db_path = primary_memory_db()
    if not db_path or not os.path.isfile(db_path):
        raise FileNotFoundError(f"memory_state.db not found at {db_path}")

    # The model first: a rebuild that cannot encode changes nothing (Memory
    # audit M28 -- every old vector was dropped and committed before the model
    # loaded, so a failed load left memory with no meaning search at all).
    logger.info("[reembed] loading embedder (model=%s) ...", target_model)
    t0 = time.monotonic()
    emb = get_embedder()
    if emb is None:
        raise RuntimeError("Embedder failed to initialize — check server logs.")
    logger.info("[reembed] embedder ready in %.1fs", time.monotonic() - t0)
    _backup(db_path)

    started = datetime.now(UTC).isoformat()
    summary: dict[str, Any] = {"episodes": 0, "beliefs": 0, "skipped_rows": 0}
    # Rows NOT already in the target vector space (NULL version: legacy or
    # unknown), re-encoded IN PLACE: a row's old vector stays until its new
    # one is written, as in the repair pass.
    stale = "embedding IS NULL OR embedding_model_version IS NULL OR embedding_model_version != ?"

    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")

        # ── Episodes ────────────────────────────────────────────────────
        rows = conn.execute(
            "SELECT id, tenant_id, summary_text, user_text, assistant_text, tier, embedding "
            f"FROM episodes WHERE {stale}", (target_model,),
        ).fetchall()
        total = len(rows)
        for i, row in enumerate(rows, 1):
            text = embed_text(row["user_text"], row["assistant_text"], row["summary_text"])
            if text and row["tier"] != "forgotten":
                outcome = _reencode_row(
                    conn, emb, "episodes", str(row["id"]), text,
                    still_params=(row["user_text"], row["assistant_text"], row["summary_text"]),
                    replacing=row["embedding"], model=target_model,
                    tenant_id=str(row["tenant_id"] or "default"),
                    meta={"kind": "episode", "tier": row["tier"] or "episodic"},
                )
                if outcome != "written":
                    summary["skipped_rows"] += 1
            if i % _BATCH_SIZE == 0 and progress:
                progress(i, total)
        summary["episodes"] = total
        if progress:
            progress(total, total)

        # ── Beliefs ─────────────────────────────────────────────────────
        rows = conn.execute(
            f"SELECT id, tenant_id, subject, predicate, object, embedding FROM beliefs WHERE {stale}",
            (target_model,),
        ).fetchall()
        total = len(rows)
        for i, row in enumerate(rows, 1):
            # A forgotten fact has no value left to embed (object = '').
            if row["object"]:
                text = f"{row['subject']} {row['predicate']} {row['object']}"
                outcome = _reencode_row(
                    conn, emb, "beliefs", str(row["id"]), text,
                    still_params=(row["subject"], row["predicate"], row["object"]),
                    replacing=row["embedding"], model=target_model,
                    tenant_id=str(row["tenant_id"] or "default"),
                    meta={"kind": "belief", "tier": "semantic"},
                )
                if outcome != "written":
                    summary["skipped_rows"] += 1
            if i % _BATCH_SIZE == 0 and progress:
                progress(i, total)
        summary["beliefs"] = total
        if progress:
            progress(total, total)

        # The Chroma folder (data_dir/vector_memory) is NOT touched: it held
        # the retired V1 chat index when this deleted it after every rebuild,
        # and since 2026-09-26 it is the Knowledge Library's live store --
        # a rebuild would have removed it under the running server. The
        # Knowledge Library re-embeds another model's vectors itself
        # (KnowledgeIndex.backfill_vectors).
    finally:
        conn.close()

    summary["model"] = target_model
    summary["started_at"] = started
    summary["finished_at"] = datetime.now(UTC).isoformat()
    return summary


# ── Continuous repair (the 15-minute maintenance cadence) ──────────────────

#: Whether an episode has any text to embed. The text itself is
#: ``episode_text.embed_text``, computed in Python -- the function every writer
#: uses; a repair with another text would put its vector apart from a fresh one.
_EPISODE_TEXT_SQL = (
    "COALESCE(NULLIF(TRIM(summary_text), ''), NULLIF(TRIM(user_text), ''), "
    "NULLIF(TRIM(assistant_text), ''))"
)

#: Vector size each embedding model produced when probed in this process, so
#: a pass with nothing to repair never loads the model or pays for a call.
_MODEL_DIMS: dict[str, int] = {}
_BELIEF_TEXT_SQL = "TRIM(subject || ' ' || predicate || ' ' || object)"


def _unsearchable_sql(kind: str, model: str) -> str:
    """``FROM ... WHERE`` for the rows meaning search cannot compare.

    Parameters, in order: tenant, vector byte length, then *model* when set.
    """
    stale = "embedding IS NULL OR length(embedding) != ?"
    if model:
        stale += (
            " OR (embedding_model_version IS NOT NULL AND embedding_model_version != ''"
            " AND embedding_model_version != ?)"
        )
    if kind == "episodes":
        return f"episodes WHERE tenant_id = ? AND ({stale}) AND {_EPISODE_TEXT_SQL} IS NOT NULL"
    return (
        "beliefs WHERE tenant_id = ? AND valid_until IS NULL AND invalidated_at IS NULL"
        f" AND ({stale}) AND {_BELIEF_TEXT_SQL} != ''"
    )


def _tenants(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT tenant_id FROM episodes UNION SELECT tenant_id FROM beliefs"
    ).fetchall()
    return sorted({str(r[0] or "default") for r in rows})


def _embed(emb: Any, text: str) -> list[float] | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        vec = emb.encode(text)
    except Exception:  # noqa: BLE001 -- one row that will not encode is skipped
        logger.debug("[reembed] encode failed for %r", text[:80], exc_info=True)
        return None
    return [float(x) for x in vec] if vec is not None and len(vec) else None


def _unsearchable_counts(
    conn: sqlite3.Connection, *, dim: int, model: str, tenant_id: str | None = None
) -> dict[str, int]:
    """Memories meaning search cannot compare right now, per kind."""
    out = {"episodes": 0, "beliefs": 0}
    for tenant in [tenant_id] if tenant_id else _tenants(conn):
        for kind in out:
            params: list[Any] = [tenant, int(dim) * 4] + ([model] if model else [])
            out[kind] += int(
                conn.execute(
                    f"SELECT count(*) FROM {_unsearchable_sql(kind, model)}", params
                ).fetchone()[0]
            )
    return out


def repair_unsearchable_vectors(
    conn: sqlite3.Connection,
    *,
    tenant_id: str | None = None,
    time_budget_s: float = 20.0,
    batch: int = 32,
    embedder: Any = None,
    model: str | None = None,
) -> dict[str, int]:
    """Re-encode, in place, every memory meaning search cannot compare.

    Unsearchable: no vector, a vector of another size, or one stamped with
    another embedding model -- episodes of every tier and every current
    belief. Until 2026-09-26 only missing vectors were repaired, 100 a day,
    and a model switch left every older memory in the old vector space until
    someone clicked Rebuild. The old vector stays until its replacement is
    written, so a switch never leaves a memory with nothing. Newest first,
    bounded by *time_budget_s* per call -- the next pass continues. A remote
    index gets each new vector too. Returns ``{"episodes", "beliefs",
    "remaining"}`` (``remaining`` is -1 when no embedder is available).
    """
    from kazma_core.memory.embedder import (
        get_embedder,
        get_embedding_dim,
        get_embedding_model_name,
    )

    model = get_embedding_model_name() if model is None else model
    if embedder is None:
        # Nothing to repair: no model load, no paid embedding call. Until the
        # model has been probed the size is the configured one; a wrong
        # configured size only makes this say "maybe", and the probe settles it.
        known = _MODEL_DIMS.get(model) or int(get_embedding_dim() or 0)
        if known and not any(
            _unsearchable_counts(conn, dim=known, model=model, tenant_id=tenant_id).values()
        ):
            return {"episodes": 0, "beliefs": 0, "remaining": 0}
    emb = embedder if embedder is not None else get_embedder()
    if emb is None:
        return {"episodes": 0, "beliefs": 0, "remaining": -1}
    dim = _MODEL_DIMS.get(model) if embedder is None else None
    if not dim:
        probe = _embed(emb, "dimension probe")
        if not probe:
            return {"episodes": 0, "beliefs": 0, "remaining": -1}
        dim = len(probe)  # what the model produces, not what the config claims
        if embedder is None:
            _MODEL_DIMS[model] = dim
    tenants = [tenant_id] if tenant_id else _tenants(conn)
    deadline = time.monotonic() + max(0.0, float(time_budget_s))
    done = {"episodes": 0, "beliefs": 0}
    # Rows that would not encode, or changed while they were embedded: this
    # pass only (the next one reads them again).
    skipped: set[str] = set()
    for kind in ("episodes", "beliefs"):
        source = _unsearchable_sql(kind, model)
        columns = ("id, user_text, assistant_text, summary_text, tier, session_id, embedding"
                   if kind == "episodes" else "id, subject, predicate, object, '', '', embedding")
        order = "created_at" if kind == "episodes" else "ingested_at"
        for tenant in tenants:
            while time.monotonic() < deadline:
                params: list[Any] = [tenant, dim * 4] + ([model] if model else [])
                exclude = ""
                if skipped:
                    exclude = f" AND id NOT IN ({','.join('?' for _ in skipped)})"
                    params.extend(sorted(skipped))
                rows = conn.execute(
                    f"SELECT {columns} FROM {source}{exclude} ORDER BY {order} DESC LIMIT ?",
                    [*params, int(batch)],
                ).fetchall()
                if not rows:
                    break
                for rid, a, b, c, tier, session_id, old in rows:
                    if kind == "episodes":
                        text = embed_text(a, b, c)
                        meta = {"kind": "episode", "tier": tier or "episodic", "session_id": session_id}
                    else:
                        text = f"{a} {b} {c}".strip()
                        meta = {"kind": "belief", "tier": "semantic"}
                    outcome = _reencode_row(
                        conn, emb, kind, str(rid), text, still_params=(a, b, c),
                        replacing=old, model=model or "", tenant_id=tenant, meta=meta, dim=dim,
                    )
                    if outcome != "written":
                        skipped.add(str(rid))
                        continue
                    done[kind] += 1
    remaining = sum(_unsearchable_counts(conn, dim=dim, model=model).values())
    if done["episodes"] or done["beliefs"]:
        logger.info(
            "[memory] re-encoded %d episode and %d belief vector(s); %d still unsearchable",
            done["episodes"], done["beliefs"], remaining,
        )
    return {**done, "remaining": remaining}


def vector_repair_counts(conn: sqlite3.Connection) -> dict[str, Any]:
    """How many memories meaning search can compare now, and how many wait
    for the repair pass, per kind (every tenant). No model load."""
    from kazma_core.memory.embedder import get_embedding_dim, get_embedding_model_name

    model = get_embedding_model_name()
    dim = _MODEL_DIMS.get(model) or int(get_embedding_dim() or 0)
    pending = _unsearchable_counts(conn, dim=dim, model=model)
    totals = {
        "episodes": int(
            conn.execute(
                f"SELECT count(*) FROM episodes WHERE {_EPISODE_TEXT_SQL} IS NOT NULL"
            ).fetchone()[0]
        ),
        "beliefs": int(
            conn.execute(
                "SELECT count(*) FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL"
                f" AND {_BELIEF_TEXT_SQL} != ''"
            ).fetchone()[0]
        ),
    }
    out: dict[str, Any] = {"model": model, "dim": dim}
    for kind, total in totals.items():
        out[kind] = {"total": total, "searchable": max(0, total - pending[kind]), "pending": pending[kind]}
    return out


def run_vector_repair_pass(*, time_budget_s: float = 20.0) -> dict[str, int]:
    """One repair pass over the primary memory database (15-minute cadence).

    Returns :func:`repair_unsearchable_vectors`'s counts, or zeros when there
    is no memory database yet.
    """
    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.paths import primary_memory_db

    zero = {"episodes": 0, "beliefs": 0, "remaining": 0}
    db = primary_memory_db()
    if not db or not os.path.isfile(db):
        return zero
    conn = sqlite3.connect(db, timeout=15)
    try:
        apply_sqlite_pragmas(conn, busy_timeout=15000)
        out = repair_unsearchable_vectors(conn, time_budget_s=time_budget_s)
        if out.get("remaining", 0) != -1:  # an embedder is there
            from kazma_core.memory.entity_resolution import repair_entity_name_vectors

            out["entities"] = repair_entity_name_vectors(conn)
        return out
    except sqlite3.OperationalError as exc:
        if "no such" not in str(exc).lower():
            raise  # locked or busy: the maintenance runner counts and reports it (M20)
        logger.debug("[reembed] memory database not ready for repair", exc_info=True)
        return zero
    finally:
        conn.close()
