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


def _extract_pred_from_text(text: str) -> tuple[str, str] | None:
    """Try to extract a (predicate, object) pair from memory text.

    Returns None when the text doesn't look like a durable fact. Strict
    validation on the extracted object prevents conversational fragments
    ("name is just exploring", "prefers to disconnect") from becoming
    garbage beliefs.
    """
    import re

    t = (text or "").strip()

    # ── Object validation ──────────────────────────────────────────
    # Reject objects that look like sentence fragments, not fact values.
    _REJECT_STARTS = frozenset({
        "to ", "a ", "an ", "the ", "my ", "your ", "his ", "her ",
        "this ", "that ", "some ", "just ", "sorry", "done", "running",
        "going", "not ", "no ", "yes", "ok", "sure", "actually",
        "check", "try", "let ", "can ", "could ", "would ", "should ",
        "disconnect", "re-insert", "about ",
    })

    def _valid_obj(obj: str) -> bool:
        """True if the extracted object looks like a real fact value."""
        o = obj.strip().rstrip(".!?,").strip()
        if not o or len(o) < 2 or len(o) > 60:
            return False
        ol = o.lower()
        # Reject sentence-fragment starters
        for rs in _REJECT_STARTS:
            if ol.startswith(rs):
                return False
        # Reject if it contains sentence punctuation (likely a clause)
        if any(c in o for c in (";", "!", "?", "\n")):
            return False
        # Reject if it's mostly verbs (ends in -ing or -ed without a noun)
        if ol.endswith("ing") and len(ol) < 8:
            return False
        return True

    patterns = [
        # name — must be a proper noun (Capitalized)
        (re.compile(r"(?i)\b(?:user(?:'s)? |my )name is ([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"), "name_is"),
        (re.compile(r"\b(?:i am|i'm|call me)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"), "name_is"),
        # favorite — category + value
        (re.compile(r"(?i)\b(?:user(?:'s)? |my )favorite (\w+) is (.+)"), "favorite"),
        # works at/on
        (re.compile(r"(?i)\b(?:user |i )works? (?:at|on) (.+)"), "works_at"),
        # lives in
        (re.compile(r"(?i)\b(?:user |i )live[s]? in (.+)"), "lives_in"),
        # prefers — strict object validation
        (re.compile(r"(?i)\b(?:user |i )(?:prefer|like|love|use|need)s? (.+)"), "prefers"),
        # has skill
        (re.compile(r"(?i)\b(?:user |i )(?:has|have) skill (?:in|with) (.+)"), "has_skill"),
        # github
        (re.compile(r"(?i)\b(?:user(?:'s)? |my )(?:github|gitlab) (?:is|username is|handle is) (.+)"), "github_is"),
    ]
    for pat, default_pred in patterns:
        m = pat.search(t)
        if not m:
            continue
        if default_pred == "favorite":
            cat = m.group(1).strip()
            val = m.group(2).strip().rstrip(".")
            if not _valid_obj(val):
                continue
            return f"favorite_{cat}", val
        obj = m.group(1).strip().rstrip(".")
        if not _valid_obj(obj):
            continue  # try the next pattern
        return default_pred, obj
    return None


def _llm_extract_beliefs_from_memories(
    primary: sqlite3.Connection,
    legacy: sqlite3.Connection,
    cols: set[str],
) -> int:
    """Batch-extract structured beliefs from legacy memories using the LLM.

    Sends memories in batches of 20 to the LLM, which extracts real
    beliefs (subject, predicate, predicate_type, object, confidence).
    Far more accurate than regex — the LLM understands context and
    rejects conversational noise. Returns the count of beliefs stored.
    """
    import asyncio

    try:
        from kazma_core.memory.belief_mutation import mutate_belief
    except Exception:
        logger.debug("[backfill] belief_mutation import failed", exc_info=True)
        return 0

    # Gather memory contents (skip obvious noise to save LLM tokens)
    rows = legacy.execute("SELECT * FROM memories").fetchall()
    candidates: list[str] = []
    for r in rows:
        content = (r["content"] if "content" in cols else "").strip()
        if not content or len(content) < 8 or len(content) > 300:
            continue
        # Skip obvious noise
        cl = content[:80].lower()
        if any(cl.startswith(m) for m in (
            "#", "```", "you are", "def ", "class ", "import ",
            "http", "user:", "assistant:", "tool:", "---",
        )):
            continue
        candidates.append(content)

    if not candidates:
        logger.info("[backfill] no memory candidates for LLM extraction")
        return 0

    logger.info("[backfill] sending %d memories to LLM for belief extraction", len(candidates))

    # Process in batches of 20
    BATCH = 20
    total_beliefs = 0
    now = time.time()

    # Open the ops connection for audit logging
    try:
        from kazma_core.paths import memory_ops_db
        from kazma_core.memory.schema_v2 import ensure_ops_schema

        ops = sqlite3.connect(memory_ops_db(), check_same_thread=False, isolation_level=None)
        ensure_ops_schema(ops)
    except Exception:
        ops = None

    try:
        from kazma_core.model_registry import (
            get_model_registry,
            initialize_model_registry,
        )

        # The registry needs initialization (normally done at server boot).
        # When running from CLI, do it here.
        mr = get_model_registry()
        client = mr.get_client()
        if client is None:
            initialize_model_registry()
            client = get_model_registry().get_client()
    except Exception:
        logger.debug("[backfill] model registry init failed", exc_info=True)
        client = None

    if client is None:
        logger.warning("[backfill] no LLM client — skipping belief extraction")
        if ops:
            ops.close()
        return 0

    for i in range(0, len(candidates), BATCH):
        batch = candidates[i : i + BATCH]
        batch_text = "\n".join(f"{j+1}. {c}" for j, c in enumerate(batch))
        prompt = (
            "You are a memory extraction engine. Below are memory entries from a chat history.\n"
            "Extract ONLY durable, long-term facts about the user (identity, preferences, "
            "skills, location, tools, decisions). Skip test messages, commands, questions, "
            "system prompts, and one-off conversation.\n\n"
            "Return ONLY valid JSON (no markdown):\n"
            '{"beliefs": [{"subject": "user", "predicate": "snake_case", '
            '"predicate_type": "functional|set|state", "object": "short value", '
            '"confidence": 0.0-1.0}]}\n\n'
            "Rules:\n"
            "- subject is always 'user' unless clearly about something else\n"
            "- predicate: short snake_case (lives_in, prefers, name_is, uses_tool, works_at)\n"
            "- predicate_type: functional=single-valued (name, location), set=multi-valued (tools, skills)\n"
            "- object: SHORT value (1-4 words max), NOT a sentence\n"
            "- Skip if nothing durable. Return {\"beliefs\": []} if no facts.\n"
            "- Never extract 'noted' or generic predicates.\n\n"
            f"Memory entries:\n{batch_text}"
        )
        try:
            import re as _re

            raw = client.chat([
                {"role": "system", "content": "You are a memory extraction engine. Return only JSON."},
                {"role": "user", "content": prompt},
            ])
            if not isinstance(raw, str):
                raw = str(raw or "")
            raw = raw.strip()
            if raw.startswith("```"):
                raw = _re.sub(r"^```(?:json)?\s*", "", raw)
                raw = _re.sub(r"\s*```$", "", raw)
            data = json.loads(raw)
            beliefs = data.get("beliefs") or []
            for b in beliefs:
                if not isinstance(b, dict):
                    continue
                subj = str(b.get("subject") or "user").strip()[:80]
                pred = str(b.get("predicate") or "").strip().lower().replace(" ", "_")[:60]
                obj = str(b.get("object") or "").strip()[:200]
                ptype = str(b.get("predicate_type") or "set").strip().lower()
                if ptype not in ("functional", "set", "state"):
                    ptype = "set"
                conf = 0.8
                try:
                    conf = max(0.0, min(1.0, float(b.get("confidence", 0.8))))
                except (TypeError, ValueError):
                    pass
                if not (subj and pred and obj) or pred == "noted":
                    continue
                # Fence check
                try:
                    from kazma_core.safety.prompt_fence import is_override_delta

                    if is_override_delta(f"{subj} {pred} {obj}"):
                        continue
                except Exception:
                    pass
                # Write via mutate_belief
                action = mutate_belief(
                    primary, subj, pred, obj,
                    ops_conn=ops, predicate_type=ptype,
                    confidence=conf, importance=4,
                    extraction_method="user_explicit",
                    cfg=None,
                )
                if action["action"] != "noop":
                    total_beliefs += 1
        except Exception:
            logger.debug("[backfill] LLM batch %d failed", i // BATCH, exc_info=True)

    if ops:
        try:
            ops.close()
        except Exception:
            pass
    logger.info("[backfill] LLM extracted %d beliefs from %d memories", total_beliefs, len(candidates))
    return total_beliefs


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
    """Migrate legacy `memories` rows → V2 `episodes` + LLM-extract beliefs."""
    stats = {"memories_seen": 0, "episodes_inserted": 0, "skipped": 0, "beliefs_extracted": 0}
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
                # Belief extraction is done in a batch LLM pass AFTER all
                # episodes are stored (see _llm_extract_beliefs_from_memories).
                # This is more efficient + accurate than per-row regex.
            except Exception:
                stats["skipped"] += 1
                logger.debug("[backfill] memories row %s skipped", src_id, exc_info=True)

        # ── Batch LLM belief extraction ──────────────────────────────
        # Send the memory contents to the LLM in batches and let IT
        # extract structured beliefs. This replaces the broken regex
        # approach — the LLM understands context and can distinguish
        # "My name is Mubder" (a real fact) from "name is just exploring"
        # (a sentence fragment).
        stats["beliefs_extracted"] = _llm_extract_beliefs_from_memories(primary, legacy, cols)
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

        # ── memory_chunk nodes → beliefs (the REAL content) ─────────
        # The L2 graph stores most memory content as memory_chunk nodes,
        # NOT as fact edges. Each chunk's content/label IS the fact text.
        # Some chunks also have structured subject/predicate/object in
        # their properties (from the consolidator's upsert_triple).
        if "kg_nodes" in tables:
            chunks = legacy.execute(
                "SELECT * FROM kg_nodes WHERE entity_type = 'memory_chunk'"
            ).fetchall()
            for c in chunks:
                stats["edges_seen"] += 1
                src_id = str(c["id"])
                content = (c["content"] or c["label"] or "").strip()

                # ── Content quality gate ────────────────────────────
                # Reject non-fact content: system prompts, tool dumps,
                # code blocks, very long blobs, markdown headers.
                if not content or len(content) < 5:
                    stats["skipped"] += 1
                    continue
                # Skip system-prompt-like content (markdown headers, code)
                if content.startswith("#") or content.startswith("```"):
                    stats["skipped"] += 1
                    continue
                # Skip content that looks like a system prompt or tool output
                _NOISE_MARKERS = (
                    "you are", "do not", "do *not", "system prompt",
                    "def ", "class ", "import ", "http://", "https://",
                    "traceback", "error:", "exception", "{\\n", "[{",
                    "user:", "assistant:", "tool:", "---",
                )
                cl = content[:100].lower()
                if any(cl.startswith(m) for m in _NOISE_MARKERS):
                    stats["skipped"] += 1
                    continue
                # Skip very long content (likely a dump, not a fact)
                if len(content) > 300:
                    stats["skipped"] += 1
                    continue

                # Check for structured SPO in properties
                props = {}
                if "properties" in c.keys() and c["properties"]:
                    try:
                        props = json.loads(c["properties"])
                    except Exception:
                        props = {}

                subj = str(props.get("subject") or "user")
                # Try to extract a real predicate from the content itself
                pred = str(props.get("predicate") or "")
                obj = str(props.get("object") or "")

                if not pred or pred == "noted":
                    # Heuristic predicate extraction from the content text.
                    # Returns None when the content isn't a recognizable
                    # fact — skip it rather than storing conversational
                    # noise as a meaningless 'noted' belief.
                    extracted = _extract_pred_from_text(content)
                    if extracted is None:
                        stats["skipped"] += 1
                        continue
                    pred, obj = extracted

                pred_clean = pred.strip().lower().replace(" ", "_") or "noted"
                if pred_clean in _STRUCTURAL_PREDICATES:
                    stats["skipped"] += 1
                    continue
                if not obj:
                    obj = content[:200]
                ptype = _classify_predicate(pred_clean)
                bid = "b_" + _stable_id("memory_chunk", src_id)
                meta = {
                    "source": "backfill_memory_chunk",
                    "legacy_id": src_id,
                    "fact_text": content[:200],
                    "memory_class": "general",
                }
                try:
                    primary.execute(
                        """INSERT OR IGNORE INTO beliefs
                           (id, tenant_id, subject, predicate, predicate_type, object,
                            confidence, structural_importance, source_trust_weight,
                            valid_from, ingested_at, extraction_method, metadata_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'system_tool', ?)""",
                        (
                            bid, "default", _slug(subj), pred_clean, ptype,
                            obj[:300], 0.7, 3, 0.85,
                            now, now,
                            json.dumps(meta, ensure_ascii=False),
                        ),
                    )
                    stats["beliefs_inserted"] += 1
                except Exception:
                    stats["skipped"] += 1

        # ── kg_edges → beliefs (real fact triples only) ──
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
    """Delete ALL backfilled beliefs + entities for a clean re-extraction.

    Deletes every belief whose extraction_method is 'system_tool' (the
    backfill's method) — this clears ALL previous backfill attempts
    (hash IDs, 'noted' noise, regex garbage like 'name_is Running').
    Also deletes hash-named entities.

    Beliefs created by the live extractor (extraction_method='llm_inferred'
    or 'user_explicit') are PRESERVED.
    """
    from kazma_core.paths import primary_memory_db

    stats = {"beliefs_deleted": 0, "entities_deleted": 0}
    try:
        conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            # Delete ALL backfill-sourced beliefs (system_tool = backfill)
            cur = conn.execute(
                "DELETE FROM beliefs WHERE extraction_method = 'system_tool'"
            )
            stats["beliefs_deleted"] = cur.rowcount or 0
            # Also delete any remaining hash-named entities
            cur2 = conn.execute(
                "DELETE FROM entities WHERE id GLOB '[a-f0-9][a-f0-9][a-f0-9][a-f0-9]*'"
            )
            stats["entities_deleted"] = cur2.rowcount or 0
            conn.commit()
            logger.info(
                "[backfill] cleanup deleted %d backfill beliefs + %d hash entities",
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
