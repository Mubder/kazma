#!/usr/bin/env python3
"""Re-embed all beliefs and episodes after an embedder model change.

Run AFTER updating ``memory.embedding`` in ``kazma.yaml`` (model + dim).
The script:
  1. Backs up ``memory_state.db`` to ``memory_state.db.pre_reembed``
  2. NULLs out existing 384-dim embedding BLOBs (preserves all source text)
  3. Re-encodes every belief and episode with the **new** embedder
  4. Deletes the ChromaDB ``vector_memory/`` directory (auto-recreates)
  5. Updates ``embedding_model_version`` on every row

Usage::

    python scripts/reembed.py

Requires ``kazma_core`` on ``sys.path`` (run from repo root).
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("reembed")

# ---------------------------------------------------------------------------
# ensure kazma_core is importable from the repo root
# ---------------------------------------------------------------------------
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)
if _root not in sys.path:
    sys.path.insert(0, _root)

BATCH_SIZE = 256  # commit every N rows


def _backup(src: str, dst: str) -> None:
    """sqlite3 native backup (safe, online, file-level copy)."""
    logger.info("Backing up %s → %s", os.path.basename(src), os.path.basename(dst))
    sconn = sqlite3.connect(src)
    dconn = sqlite3.connect(dst)
    try:
        sconn.backup(dconn)
    finally:
        dconn.close()
        sconn.close()


def _embed_text(emb, text: str) -> bytes | None:
    """Encode *text* → float32 BLOB, or None."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        vec = emb.encode(text)
        if not vec:
            return None
        import struct

        return struct.pack(f"<{len(vec)}f", *vec)
    except Exception:
        logger.debug("Embed failed for text[:80]=%r", text[:80], exc_info=True)
        return None


def main() -> int:
    from kazma_core.memory.embedder import get_embedder
    from kazma_core.paths import data_dir, primary_memory_db

    db_path = primary_memory_db()
    if not db_path or not os.path.isfile(db_path):
        logger.error("memory_state.db not found at %s", db_path)
        return 1

    # 1. Backup -----------------------------------------------------------------
    backup_path = db_path + ".pre_reembed"
    if os.path.exists(backup_path):
        logger.warning(
            "Backup %s already exists — skipping backup (delete it to force a fresh backup)",
            backup_path,
        )
    else:
        _backup(db_path, backup_path)

    # 2. Open DB ----------------------------------------------------------------
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    # 3. NULL out old embeddings ------------------------------------------------
    logger.info("NULLing existing embeddings (preserving source text) …")
    conn.execute("UPDATE episodes SET embedding = NULL")
    conn.execute("UPDATE beliefs SET embedding = NULL")
    conn.commit()

    # 4. Load the NEW embedder -------------------------------------------------
    logger.info("Loading embedder (this may download the model on first run) …")
    t0 = time.monotonic()
    emb = get_embedder()
    logger.info("Embedder ready in %.1fs", time.monotonic() - t0)

    model_name = os.environ.get(
        "KAZMA_EMBED_MODEL",
        "BAAI/bge-m3",  # fallback; actual value comes from yaml
    )
    dim = len(emb.encode("dimension probe"))
    logger.info("Embedder model=%s  dim=%d", model_name, dim)

    # 5. Re-embed EPISODES ------------------------------------------------------
    logger.info("Re-embedding episodes …")
    rows = conn.execute(
        "SELECT id, user_text, assistant_text, summary_text "
        "FROM episodes WHERE embedding IS NULL"
    ).fetchall()
    total = len(rows)
    done = 0
    for i, row in enumerate(rows):
        # pick the best text to embed: summary > user > assistant
        text = (row["summary_text"] or row["user_text"] or row["assistant_text"] or "").strip()
        if text:
            blob = _embed_text(emb, text)
            if blob:
                conn.execute(
                    "UPDATE episodes SET embedding = ?, embedding_model_version = ? WHERE id = ?",
                    (blob, model_name, row["id"]),
                )
        if (i + 1) % BATCH_SIZE == 0:
            conn.commit()
            delta = i + 1 - done
            done = i + 1
            logger.info("  episodes %d/%d", done, total)
    conn.commit()
    logger.info("Episodes complete: %d rows", total)

    # 6. Re-embed BELIEFS -------------------------------------------------------
    logger.info("Re-embedding beliefs …")
    rows = conn.execute(
        "SELECT id, subject, predicate, object "
        "FROM beliefs WHERE embedding IS NULL"
    ).fetchall()
    total = len(rows)
    done = 0
    for i, row in enumerate(rows):
        text = f"{row['subject']} {row['predicate']} {row['object']}"
        blob = _embed_text(emb, text)
        if blob:
            conn.execute(
                "UPDATE beliefs SET embedding = ?, embedding_model_version = ? WHERE id = ?",
                (blob, model_name, row["id"]),
            )
        if (i + 1) % BATCH_SIZE == 0:
            conn.commit()
            delta = i + 1 - done
            done = i + 1
            logger.info("  beliefs %d/%d", done, total)
    conn.commit()
    logger.info("Beliefs complete: %d rows", total)

    conn.close()

    # 7. Clear ChromaDB vector store (derived indexes, auto-recreate) -----------
    vec_dir = os.path.join(data_dir(), "vector_memory")
    if os.path.isdir(vec_dir):
        logger.info("Removing ChromaDB vector store: %s", vec_dir)
        shutil.rmtree(vec_dir)
        logger.info("  (will auto-recreate on next Kazma start)")

    # Done ----------------------------------------------------------------------
    elapsed = time.monotonic() - t0
    logger.info("Re-embed complete in %.1fs. Restart Kazma now.", elapsed)
    logger.info("If anything is broken, restore from: %s", backup_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
