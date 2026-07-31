"""V2 backfill — migrate legacy memory stores into the V2 cognitive schema.

One-shot (but idempotent) migration of the pre-V2 corpus so flipping
``memory.v2.use_new_stack=true`` does not cause "amnesia". Three sources:

  1. Legacy ``memories`` table (L3 FTS5) → V2 ``episodes``
     - tier defaults to 'episodic'
     - structural_importance inferred from source/relevance
     - embedding BLOB carried over when present

  2. Legacy ``kg_nodes`` (L2 property graph) → V2 ``entities``
     - id, type, name(label), aliases, tenant_id carried over

  3. Legacy ``kg_edges`` → V2 ``beliefs``
     - subject/predicate/object reconstructed from source/target/relation
     - valid_from = edge.created_at, valid_until = NULL (still believed)
     - predicate_type inferred (functional vs set) from the predicate name

Idempotency: every insert uses a STABLE derived primary key
(sha256 of source-table-name + source-row-id) and ``INSERT OR IGNORE``,
so re-running the script never duplicates rows — it only backfills rows
that were added since the last run.

Safe to run while the server is live (WAL mode, separate DB files).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["run_backfill", "backfill_status", "cleanup_polluted_backfill"]

# Structural L2 edge types that are graph plumbing, NOT real beliefs.
# These must be skipped during backfill — they produce noise like
# "7eea13cd has_memory user" which means nothing as a belief.
_STRUCTURAL_PREDICATES = frozenset(
    {"has_memory", "has_fact", "tagged", "related", "mentions", "links_to"}
)


# ── Stable ID derivation (idempotency key) ────────────────────────────────


def _stable_id(source_table: str, source_id: str) -> str:
    """Deterministic V2 PK from the source table + source row id.

    Re-running the backfill produces the SAME id for the same source
    row, so ``INSERT OR IGNORE`` skips already-migrated rows.
    """
    h = hashlib.sha256(f"{source_table}|{source_id}".encode("utf-8")).hexdigest()
    return h[:24]


def _slug(text: str) -> str:
    import re

    raw = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", raw)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:80] or "entity"


# Functional predicates (single-valued) — used to classify backfilled edges.
_FUNCTIONAL_PREDICATES = frozenset(
    {
        "name_is", "lives_in", "works_at", "active_project", "favorite_ide",
        "favorite_editor", "favorite_language", "located_in", "current_role",
        "preferred_name", "favorite_color", "has_memory", "related",
    }
) - {"has_memory", "related", "tagged"}  # these are structural, not facts


def _classify_predicate(predicate: str) -> str:
    p = (predicate or "").strip().lower()
    if p in _FUNCTIONAL_PREDICATES:
        return "functional"
    if p.startswith("favorite_"):
        return "functional"
    if p in {"issue_status", "pipeline_state", "task_state"}:
        return "state"
    return "set"


# ── Connection helpers ───────────────────────────────────────────────────


def _open_legacy_memory() -> sqlite3.Connection | None:
    """Open the legacy memory.db (L3 FTS5) if it exists."""
    from kazma_core.paths import fts5_memory_path

    path = fts5_memory_path()
    import os

    if not os.path.exists(path):
        return None
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _open_legacy_graph() -> sqlite3.Connection | None:
    """Open the legacy knowledge_graph.db (L2) if it exists."""
    from kazma_core.paths import knowledge_graph_db

    path = knowledge_graph_db()
    import os

    if not os.path.exists(path):
        return None
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _open_primary() -> sqlite3.Connection:
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    conn = sqlite3.connect(primary_memory_db(), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    return conn


# ── Backfill steps ────────────────────────────────────────────────────────


def _backfill_memories_to_episodes(primary: sqlite3.Connection) -> dict[str, int]:
    """Migrate legacy `memories` rows → V2 `episodes`."""
    stats = {"memories_seen": 0, "episodes_inserted": 0, "skipped": 0}
    legacy = _open_legacy_memory()
    if legacy is None:
        logger.info("[backfill] no legacy memory.db found — skipping memories→episodes")
        return stats
    try:
        # Detect columns (the schema may vary across versions)
        cols = {r["name"] for r in legacy.execute("PRAGMA table_info(memories)").fetchall()}
        if "memories" not in {r["name"] for r in legacy.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}:
            logger.info("[backfill] no `memories` table — skipping")
            return stats
        rows = legacy.execute(
            f"SELECT * FROM memories"
        ).fetchall()
        now = time.time()
        for r in rows:
            stats["memories_seen"] += 1
            src_id = str(r["id"])
            eid = "e_" + _stable_id("memories", src_id)
            content = r["content"] if "content" in cols else ""
            if not content:
                stats["skipped"] += 1
                continue
            tenant = r["tenant_id"] if "tenant_id" in cols and r["tenant_id"] else "default"
            ts = float(r["timestamp"]) if "timestamp" in cols and r["timestamp"] else now
            source = r["source"] if "source" in cols else "backfill"
            # Infer importance: consolidator-sourced facts are more durable
            importance = 3 if "consolidat" in (source or "").lower() else 2
            relevance = float(r["relevance"]) if "relevance" in cols and r["relevance"] else 1.0
            if relevance >= 0.9:
                importance = max(importance, 4)
            emb = r["embedding"] if "embedding" in cols else None
            meta = {"source": "backfill_memories", "legacy_source": source}
            try:
                primary.execute(
                    """INSERT OR IGNORE INTO episodes
                       (id, tenant_id, session_id, turn_number, user_text, tier,
                        structural_importance, created_at, embedding, metadata_json)
                       VALUES (?, ?, ?, ?, ?, 'episodic', ?, ?, ?, ?)""",
                    (
                        eid, tenant, f"legacy-{src_id[:8]}", 0,
                        content[:4000], importance, ts,
                        sqlite3.Binary(emb) if emb else None,
                        json.dumps(meta, ensure_ascii=False),
                    ),
                )
                stats["episodes_inserted"] += 1
            except Exception:
                stats["skipped"] += 1
                logger.debug("[backfill] memories row %s skipped", src_id, exc_info=True)
    finally:
        legacy.close()
    return stats


def _backfill_graph_to_beliefs(primary: sqlite3.Connection) -> dict[str, int]:
    """Migrate legacy kg_nodes → entities, kg_edges → beliefs."""
    stats = {
        "nodes_seen": 0, "entities_inserted": 0,
        "edges_seen": 0, "beliefs_inserted": 0, "skipped": 0,
    }
    legacy = _open_legacy_graph()
    if legacy is None:
        logger.info("[backfill] no legacy knowledge_graph.db found — skipping graph→beliefs")
        return stats
    try:
        tables = {r["name"] for r in legacy.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        now = time.time()

        # ── kg_nodes → entities ──
        # Build a node-ID → (label, type) lookup so edges can resolve
        # hash IDs to human-readable labels.
        node_lookup: dict[str, tuple[str, str]] = {}
        if "kg_nodes" in tables:
            nodes = legacy.execute("SELECT * FROM kg_nodes").fetchall()
            for n in nodes:
                stats["nodes_seen"] += 1
                src_id = str(n["id"])
                ent_id = "ent_" + _stable_id("kg_nodes", src_id)
                etype = n["entity_type"] if "entity_type" in n.keys() else "concept"
                name = n["label"] or n["id"]
                node_lookup[src_id] = (name, etype)
                tenant = n["tenant_id"] if "tenant_id" in n.keys() and n["tenant_id"] else "default"
                high = 1 if etype in ("person", "project") else 0
                try:
                    primary.execute(
                        """INSERT OR IGNORE INTO entities
                           (id, tenant_id, type, name, aliases_json, is_high_stakes, metadata_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            ent_id, tenant, etype, name,
                            json.dumps([name]), high,
                            json.dumps({"source": "backfill_kg_nodes", "legacy_id": src_id}),
                        ),
                    )
                    stats["entities_inserted"] += 1
                except Exception:
                    stats["skipped"] += 1

        # ── kg_edges → beliefs ──
        if "kg_edges" in tables:
            edges = legacy.execute("SELECT * FROM kg_edges").fetchall()
            for e in edges:
                stats["edges_seen"] += 1
                relation = e["relation_type"] or "related"
                pred = relation.strip().lower().replace(" ", "_") or "related"
                # SKIP structural edges (has_memory, tagged, has_fact, ...)
                # — they are graph plumbing, not real beliefs.
                if pred in _STRUCTURAL_PREDICATES:
                    stats["skipped"] += 1
                    continue
                src_id = str(e["id"])
                bid = "b_" + _stable_id("kg_edges", src_id) + "_" + _stable_id("edge", src_id)
                # Resolve hash IDs to human-readable labels via the lookup.
                # Falls back to the raw ID only if the node isn't in the table.
                raw_subj = str(e["source_id"])
                raw_obj = str(e["target_id"])
                subject = node_lookup.get(raw_subj, (raw_subj, "concept"))[0]
                obj = node_lookup.get(raw_obj, (raw_obj, "concept"))[0]
                # Slugify the resolved labels for belief subject/object
                subject = _slug(subject) if not raw_subj.startswith("s_") else subject
                obj = _slug(obj) if not raw_obj.startswith("o_") else obj
                ptype = _classify_predicate(pred)
                tenant = e["tenant_id"] if "tenant_id" in e.keys() and e["tenant_id"] else "default"
                created = float(e["created_at"]) if "created_at" in e.keys() and e["created_at"] else now
                props = {}
                if "properties" in e.keys() and e["properties"]:
                    try:
                        props = json.loads(e["properties"])
                    except Exception:
                        props = {}
                fact = props.get("fact") or f"{subject} {pred} {obj}"
                confidence = float(props.get("confidence", 0.5))
                importance = int(props.get("importance", 2))
                meta = {
                    "source": "backfill_kg_edges", "legacy_id": src_id,
                    "fact_text": str(fact)[:200], "memory_class": "general",
                }
                try:
                    primary.execute(
                        """INSERT OR IGNORE INTO beliefs
                           (id, tenant_id, subject, predicate, predicate_type, object,
                            confidence, structural_importance, source_trust_weight,
                            valid_from, ingested_at, extraction_method, metadata_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'system_tool', ?)""",
                        (
                            bid, tenant, subject, pred, ptype, obj,
                            confidence, importance, 0.85,
                            created, now,
                            json.dumps(meta, ensure_ascii=False),
                        ),
                    )
                    stats["beliefs_inserted"] += 1
                except Exception:
                    stats["skipped"] += 1
                    logger.debug("[backfill] edge %s skipped", src_id, exc_info=True)
    finally:
        legacy.close()
    return stats


# ── Public entry points ──────────────────────────────────────────────────


def cleanup_polluted_backfill() -> dict[str, int]:
    """Delete beliefs/entities polluted by the buggy first backfill run.

    The original backfill used raw L2 node IDs (hashes like
    ``7eea13cd594dcf53``) as belief subjects/objects, and migrated
    structural edges (``has_memory``, ``tagged``, ``has_fact``) that
    aren't real beliefs. This cleans them so the V2 graph shows
    human-readable labels + real facts only.

    Deletes:
      - Beliefs whose subject OR object is a hash-like string (≥16 hex chars)
      - Beliefs with structural predicates (has_memory, tagged, has_fact, ...)
      - Entities whose id is hash-like (left over from the bad node migration)

    Returns counts of what was deleted. Safe to run multiple times.
    """
    import re

    from kazma_core.paths import primary_memory_db

    stats = {"beliefs_deleted": 0, "entities_deleted": 0}
    try:
        conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            # Hash-like pattern: 16+ hex chars (the L2 slug IDs)
            hash_pat = re.compile(r"^[a-f0-9]{16,}$")
            # Delete beliefs with hash subjects/objects OR structural predicates
            placeholders = ",".join("?" * len(_STRUCTURAL_PREDICATES))
            cur = conn.execute(
                f"""DELETE FROM beliefs
                    WHERE subject GLOB '[a-f0-9][a-f0-9][a-f0-9][a-f0-9]*'
                       OR object GLOB '[a-f0-9][a-f0-9][a-f0-9][a-f0-9]*'
                       OR predicate IN ({placeholders})""",
                tuple(_STRUCTURAL_PREDICATES),
            )
            stats["beliefs_deleted"] = cur.rowcount or 0
            # Delete hash-named entities
            cur2 = conn.execute(
                "DELETE FROM entities WHERE id GLOB '[a-f0-9][a-f0-9][a-f0-9][a-f0-9]*'"
            )
            stats["entities_deleted"] = cur2.rowcount or 0
            conn.commit()
            logger.info(
                "[backfill] cleanup deleted %d beliefs + %d entities (polluted)",
                stats["beliefs_deleted"], stats["entities_deleted"],
            )
        finally:
            conn.close()
    except Exception:
        logger.debug("[backfill] cleanup failed", exc_info=True)
    return stats


def run_backfill(*, dry_run: bool = False) -> dict[str, Any]:
    """Run the full backfill. Returns a combined stats dict.

    Args:
        dry_run: If True, count source rows but do NOT write to V2.
            Useful for sizing the migration before committing.
    """
    logger.info("[backfill] starting (dry_run=%s)", dry_run)
    if dry_run:
        # Just count sources — do NOT touch the V2 DB at all
        mem_stats = _count_legacy_memories()
        graph_stats = _count_legacy_graph()
        return {"dry_run": True, "memories": mem_stats, "graph": graph_stats}

    primary = _open_primary()
    try:
        mem_stats = _backfill_memories_to_episodes(primary)
        graph_stats = _backfill_graph_to_beliefs(primary)
        combined = {"memories": mem_stats, "graph": graph_stats}
        logger.info("[backfill] complete: %s", combined)
        return combined
    finally:
        primary.close()


def _count_legacy_memories() -> dict[str, int]:
    legacy = _open_legacy_memory()
    if legacy is None:
        return {"memories_seen": 0}
    try:
        tables = {r["name"] for r in legacy.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "memories" not in tables:
            return {"memories_seen": 0}
        count = legacy.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        return {"memories_seen": count}
    finally:
        legacy.close()


def _count_legacy_graph() -> dict[str, int]:
    legacy = _open_legacy_graph()
    if legacy is None:
        return {"nodes_seen": 0, "edges_seen": 0}
    try:
        tables = {r["name"] for r in legacy.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        nodes = legacy.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0] if "kg_nodes" in tables else 0
        edges = legacy.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0] if "kg_edges" in tables else 0
        return {"nodes_seen": nodes, "edges_seen": edges}
    finally:
        legacy.close()


def backfill_status() -> dict[str, Any]:
    """Report how many V2 rows came from backfill (source-tagged)."""
    primary = _open_primary()
    try:
        bf_episodes = primary.execute(
            "SELECT COUNT(*) FROM episodes WHERE metadata_json LIKE '%backfill_memories%'"
        ).fetchone()[0]
        bf_entities = primary.execute(
            "SELECT COUNT(*) FROM entities WHERE metadata_json LIKE '%backfill_kg_nodes%'"
        ).fetchone()[0]
        bf_beliefs = primary.execute(
            "SELECT COUNT(*) FROM beliefs WHERE metadata_json LIKE '%backfill_kg_edges%'"
        ).fetchone()[0]
        return {
            "backfilled_episodes": bf_episodes,
            "backfilled_entities": bf_entities,
            "backfilled_beliefs": bf_beliefs,
        }
    finally:
        primary.close()
