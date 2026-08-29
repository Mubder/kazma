"""Memory V2 tools — store, search, beliefs, entities, scratchpad.

Extracted from the former ``tool_builtins.py`` god module (2,833
lines) — audit O5. Tool bodies are unchanged; registration order
within this group is preserved.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass

def _qnorm(q: str) -> str:
    """Normalize a memory q-filter: underscores/hyphens -> single spaces,
    lowercased. Paired with REPLACE(...) in SQL so 'memory system' matches
    user_memory_system (2026-08-27 report — the literal LIKE filter missed
    it while FTS memory_search matched fine)."""
    import re as _re

    return _re.sub(r"[_\-\s]+", " ", str(q or "").strip().lower()).strip()




def register_memory_tools(registry: Any) -> None:
    """Register the memory tools onto *registry*."""
    # Helper closures used by the memory tools below. They carry no
    # @registry.register decorator, so they moved with their callers when
    # this module was split out of tool_builtins.py (audit O5).
    # ── Memory admin helpers (shared by thin tools + memory_admin) ──
    def _mem_list_beliefs(q: str = "", limit: int = 30) -> str:
        import sqlite3

        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db
        from kazma_core.safety.hitl import get_current_tenant_id

        try:
            conn = sqlite3.connect(
                primary_memory_db(), check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            ensure_primary_schema(conn)
            tenant = get_current_tenant_id()
            lim = max(1, min(int(limit or 30), 100))
            sql = (
                "SELECT id, subject, predicate, predicate_type, "
                "substr(object,1,240) AS object, confidence, structural_importance "
                "FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL "
                "AND tenant_id=?"
            )
            params: list[Any] = [tenant]
            if (q or "").strip():
                # Word-boundary friendly: normalize _/- to spaces on BOTH
                # sides so 'memory system' matches user_memory_system.
                ql = f"%{_qnorm(q)}%"
                sql += (
                    " AND (LOWER(REPLACE(REPLACE(subject,'_',' '),'-',' ')) LIKE ? "
                    "OR LOWER(REPLACE(REPLACE(predicate,'_',' '),'-',' ')) LIKE ? "
                    "OR LOWER(REPLACE(REPLACE(object,'_',' '),'-',' ')) LIKE ?)"
                )
                params.extend([ql, ql, ql])
            sql += (
                " ORDER BY (structural_importance * confidence) DESC LIMIT ?"
            )
            params.append(lim)
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
            conn.close()
            return json.dumps(
                {"count": len(rows), "beliefs": rows},
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            logger.warning("[memory_list_beliefs] failed: %s", exc)
            return f"Error: memory_list_beliefs failed — {exc}"
    def _mem_list_entities(q: str = "", limit: int = 40) -> str:
        import sqlite3

        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db
        from kazma_core.safety.hitl import get_current_tenant_id

        try:
            conn = sqlite3.connect(
                primary_memory_db(), check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            ensure_primary_schema(conn)
            tenant = get_current_tenant_id()
            lim = max(1, min(int(limit or 40), 100))
            sql = """
                SELECT e.id, e.type, e.name, e.is_high_stakes,
                       (
                         SELECT COUNT(*) FROM beliefs b
                         WHERE b.tenant_id = e.tenant_id
                           AND b.valid_until IS NULL AND b.invalidated_at IS NULL
                           AND (b.subject = e.id OR b.object = e.name
                                OR b.object = e.id OR b.subject = e.name)
                       ) AS belief_count
                FROM entities e
                WHERE e.tenant_id = ?
            """
            params: list[Any] = [tenant]
            if (q or "").strip():
                ql = f"%{_qnorm(q)}%"
                sql += (
                    " AND (LOWER(REPLACE(REPLACE(e.id,'_',' '),'-',' ')) LIKE ? "
                    "OR LOWER(REPLACE(REPLACE(e.name,'_',' '),'-',' ')) LIKE ? "
                    "OR LOWER(REPLACE(REPLACE(e.type,'_',' '),'-',' ')) LIKE ?)"
                )
                params.extend([ql, ql, ql])
            sql += " ORDER BY belief_count DESC, e.name ASC LIMIT ?"
            params.append(lim)
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
            conn.close()
            return json.dumps(
                {"count": len(rows), "entities": rows},
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            logger.warning("[memory_list_entities] failed: %s", exc)
            return f"Error: memory_list_entities failed — {exc}"
    def _mem_invalidate(belief_id: str) -> str:
        from kazma_core.memory.hygiene import invalidate_belief

        bid = (belief_id or "").strip()
        if not bid:
            return "Error: belief_id required (from list_beliefs)"
        try:
            result = invalidate_belief(bid, remove_graph=True)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("[memory_invalidate] failed: %s", exc)
            return f"Error: memory_invalidate failed — {exc}"
    def _mem_merge_entities(source_id: str, target_id: str) -> str:
        """Merge source into target (beliefs rewired, aliases union)."""
        import json as _json
        import sqlite3
        import time as _time
        import uuid as _uuid

        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        src_id = (source_id or "").strip()
        tgt_id = (target_id or "").strip()
        if not src_id or not tgt_id:
            return "Error: source_id and target_id required"
        if src_id == tgt_id:
            return "Error: source and target must differ"
        protected = {"user", "assistant"}
        if src_id.lower() in protected:
            return f"Error: cannot merge protected source {src_id}"
        try:
            conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            ensure_primary_schema(conn)
            src = conn.execute(
                "SELECT id, name, aliases_json FROM entities WHERE id=?", (src_id,)
            ).fetchone()
            tgt = conn.execute(
                "SELECT id, name, aliases_json FROM entities WHERE id=?", (tgt_id,)
            ).fetchone()
            if not src or not tgt:
                conn.close()
                return json.dumps(
                    {"ok": False, "error": "source or target not found"},
                    ensure_ascii=False,
                )
            try:
                src_aliases = _json.loads(src["aliases_json"] or "[]")
            except Exception:
                src_aliases = []
            try:
                tgt_aliases = _json.loads(tgt["aliases_json"] or "[]")
            except Exception:
                tgt_aliases = []
            if not isinstance(src_aliases, list):
                src_aliases = []
            if not isinstance(tgt_aliases, list):
                tgt_aliases = []
            for a in list(src_aliases) + [src["name"], src_id]:
                if a and a not in tgt_aliases:
                    tgt_aliases.append(a)
            conn.execute(
                "UPDATE entities SET aliases_json=? WHERE id=?",
                (_json.dumps(tgt_aliases, ensure_ascii=False), tgt_id),
            )
            # Resolve the request-scoped tenant (matching the sibling
            # memory helpers). ``entities.id`` is a GLOBAL primary key
            # (AGENTS.md §16), so the belief redirects below MUST be scoped
            # to this tenant — otherwise they rewrite every tenant's beliefs
            # pointing at the entity, and the merge audit row was previously
            # misattributed to the 'default' tenant (audit finding).
            from kazma_core.safety.hitl import get_current_tenant_id

            tenant = get_current_tenant_id()
            for old in {src_id, src["name"]}:
                if not old:
                    continue
                conn.execute(
                    "UPDATE beliefs SET subject=? WHERE subject=? AND tenant_id=?",
                    (tgt_id, old, tenant),
                )
                conn.execute(
                    "UPDATE beliefs SET object=? WHERE object=? AND tenant_id=?",
                    (tgt_id, old, tenant),
                )
            conn.execute(
                """UPDATE entities
                   SET metadata_json = json_set(
                     COALESCE(NULLIF(metadata_json,''), '{}'),
                     '$.merged_into', ?
                   )
                   WHERE id = ?""",
                (tgt_id, src_id),
            )
            mid = "m_" + _uuid.uuid4().hex[:16]
            now = _time.time()
            conn.execute(
                """INSERT OR IGNORE INTO entity_merges
                   (id, tenant_id, source_entity_id, target_entity_id, status,
                    merge_tier, confidence, requested_at, resolved_at, metadata_json)
                   VALUES (?, ?, ?, ?, 'approved', 'agent_tool', 1.0, ?, ?, ?)""",
                (
                    mid,
                    tenant,
                    src_id,
                    tgt_id,
                    now,
                    now,
                    _json.dumps({"via": "memory_merge_entities"}),
                ),
            )
            # M-06: rewire invalidated the source's counts — refresh both.
            try:
                from kazma_core.memory.entity_counts import recompute_entity_counts

                recompute_entity_counts(conn, [src_id, tgt_id], tenant_id=tenant)
            except Exception:
                logger.debug("[memory_merge] count recompute skipped", exc_info=True)
            conn.commit()
            conn.close()
            return json.dumps(
                {
                    "ok": True,
                    "merge_id": mid,
                    "source_id": src_id,
                    "target_id": tgt_id,
                    "status": "approved",
                    "hint": "Beliefs rewired; refresh /memory graph",
                },
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            logger.warning("[memory_merge_entities] failed: %s", exc)
            return f"Error: memory_merge_entities failed — {exc}"
    def _mem_link_entities(
        subject: str, predicate: str, obj: str, *, predicate_type: str = "set"
    ) -> str:
        """Create subject --predicate--> object belief (graph edge)."""
        import sqlite3

        from kazma_core.memory.belief_mutation import mutate_belief
        from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
        from kazma_core.paths import memory_ops_db, primary_memory_db
        from kazma_core.safety.hitl import get_current_tenant_id

        sub = (subject or "").strip()
        pred = (predicate or "related_to").strip() or "related_to"
        object_ = (obj or "").strip()
        if not sub or not object_:
            return "Error: subject and object required"
        try:
            primary = sqlite3.connect(
                primary_memory_db(), check_same_thread=False
            )
            primary.row_factory = sqlite3.Row
            ensure_primary_schema(primary)
            ops = sqlite3.connect(
                memory_ops_db(), check_same_thread=False
            )
            ensure_ops_schema(ops)
            tenant = get_current_tenant_id()
            for eid, etype in ((sub, "concept"), (object_, "concept")):
                if eid.lower() == "user":
                    etype = "person"
                row = primary.execute(
                    "SELECT id FROM entities WHERE id=?", (eid,)
                ).fetchone()
                if not row:
                    primary.execute(
                        """INSERT OR IGNORE INTO entities
                           (id, tenant_id, type, name, aliases_json, is_high_stakes, metadata_json)
                           VALUES (?, ?, ?, ?, '[]', 0, '{}')""",
                        (eid, tenant, etype, eid.replace("_", " ")),
                    )
            primary.commit()
            result = mutate_belief(
                primary,
                sub,
                pred,
                object_,
                ops_conn=ops,
                predicate_type=predicate_type if predicate_type in ("functional", "set", "state") else "set",
                confidence=0.9,
                importance=4,
                extraction_method="user_explicit",
                tenant_id=tenant,
            )
            ops.close()
            primary.close()
            return json.dumps(
                {
                    "ok": True,
                    "link": result,
                    "subject": sub,
                    "predicate": pred,
                    "object": object_,
                    "hint": "Edge created; use has_project / part_of / owns for hierarchy",
                },
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            logger.warning("[memory_link_entities] failed: %s", exc)
            return f"Error: memory_link_entities failed — {exc}"
    def _mem_delete_entity(entity_id: str) -> str:
        import sqlite3

        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db
        from kazma_core.safety.hitl import get_current_tenant_id

        eid = (entity_id or "").strip()
        if not eid:
            return "Error: entity_id required"
        blocked = {"user", "assistant", "kazma", "mubder"}
        if eid.lower() in blocked:
            return f"Error: refusing to delete protected entity '{eid}'"
        try:
            conn = sqlite3.connect(
                primary_memory_db(), check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            ensure_primary_schema(conn)
            tenant = get_current_tenant_id()
            row = conn.execute(
                "SELECT id, type, name FROM entities WHERE id=? AND tenant_id=?",
                (eid, tenant),
            ).fetchone()
            if not row:
                conn.close()
                return json.dumps(
                    {"ok": False, "error": "not_found", "entity_id": eid}
                )
            try:
                from kazma_core.memory.entity_resolution import preserve_merge_ledger

                preserve_merge_ledger(conn, eid, reason="entity_delete")
            except Exception:
                logger.debug("[memory_delete_entity] merge-ledger archive skipped", exc_info=True)
            conn.execute(
                "DELETE FROM entity_merges WHERE source_entity_id=? OR target_entity_id=?",
                (eid, eid),
            )
            conn.execute(
                "DELETE FROM entities WHERE id=? AND tenant_id=?",
                (eid, tenant),
            )
            conn.commit()
            conn.close()
            return json.dumps(
                {
                    "ok": True,
                    "deleted": eid,
                    "type": row["type"],
                    "name": row["name"],
                },
                ensure_ascii=False,
            )
        except Exception as exc:
            logger.warning("[memory_delete_entity] failed: %s", exc)
            return f"Error: memory_delete_entity failed — {exc}"
    def _mem_purge_empty_entities(*, confirm: bool = False) -> str:
        """Delete entity shells with zero active beliefs (safe clutter)."""
        import sqlite3

        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db
        from kazma_core.safety.hitl import get_current_tenant_id

        protected = {"user", "assistant", "kazma", "mubder"}
        try:
            conn = sqlite3.connect(
                primary_memory_db(), check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            ensure_primary_schema(conn)
            tenant = get_current_tenant_id()
            rows = conn.execute(
                """
                SELECT e.id, e.type, e.name,
                       (
                         SELECT COUNT(*) FROM beliefs b
                         WHERE b.tenant_id = e.tenant_id
                           AND b.valid_until IS NULL AND b.invalidated_at IS NULL
                           AND (b.subject = e.id OR b.object = e.name
                                OR b.object = e.id OR b.subject = e.name)
                       ) AS belief_count
                FROM entities e
                WHERE e.tenant_id = ?
                """,
                (tenant,),
            ).fetchall()
            empty = [
                dict(r)
                for r in rows
                if int(r["belief_count"] or 0) == 0
                and str(r["id"] or "").lower() not in protected
            ]
            if not confirm:
                conn.close()
                return json.dumps(
                    {
                        "ok": True,
                        "dry_run": True,
                        "would_delete": len(empty),
                        "entities": empty,
                        "hint": "Call again with confirm=true to delete these shells.",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            deleted: list[str] = []
            for r in empty:
                eid = r["id"]
                try:
                    from kazma_core.memory.entity_resolution import preserve_merge_ledger

                    preserve_merge_ledger(conn, eid, reason="purge_empty")
                except Exception:
                    logger.debug(
                        "[memory_purge_empty] merge-ledger archive skipped",
                        exc_info=True,
                    )
                conn.execute(
                    "DELETE FROM entity_merges WHERE source_entity_id=? OR target_entity_id=?",
                    (eid, eid),
                )
                conn.execute(
                    "DELETE FROM entities WHERE id=? AND tenant_id=?",
                    (eid, tenant),
                )
                deleted.append(eid)
            conn.commit()
            conn.close()
            return json.dumps(
                {
                    "ok": True,
                    "dry_run": False,
                    "deleted": deleted,
                    "count": len(deleted),
                },
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            logger.warning("[memory_purge_empty_entities] failed: %s", exc)
            return f"Error: memory_purge_empty_entities failed — {exc}"
    def _memory_store_sync(text: str, metadata: str = "{}") -> str:
        try:
            meta = json.loads(metadata) if isinstance(metadata, str) else metadata
        except json.JSONDecodeError:
            meta = {"raw": metadata}
        if not isinstance(meta, dict):
            meta = {"raw": meta}
        # V2 native write — the single memory write path (V1 removed).
        # Episode = raw text snapshot. Beliefs: rotating current facts
        # (grok_next_reset, …) use functional supersede; everything else
        # still lands as additive ``noted`` set-beliefs.
        import sqlite3

        from kazma_core.memory.belief_mutation import mutate_belief
        from kazma_core.memory.current_facts import parse_current_facts
        from kazma_core.memory.dual_write import mirror_episode
        from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
        from kazma_core.paths import memory_ops_db, primary_memory_db

        primary = sqlite3.connect(
            primary_memory_db(), check_same_thread=False, isolation_level=None
        )
        primary.row_factory = sqlite3.Row
        ops = sqlite3.connect(
            memory_ops_db(), check_same_thread=False, isolation_level=None
        )
        try:
            ensure_primary_schema(primary)
            ensure_ops_schema(ops)
            from kazma_core.safety.hitl import get_current_tenant_id

            _tenant = get_current_tenant_id()
            # Episode (raw text snapshot) — always keep diary trail
            eid = mirror_episode(
                session_id=str(meta.get("session_id", "memory_store")),
                turn_number=int(meta.get("turn", 0)),
                user_text=text,
                source="memory_store_tool",
                tenant_id=_tenant,
            )
            current = parse_current_facts(text, meta)
            actions: list[dict] = []
            if current:
                for fact in current:
                    actions.append(
                        mutate_belief(
                            primary,
                            fact.get("subject") or "user",
                            fact["predicate"],
                            fact["object"],
                            ops_conn=ops,
                            predicate_type=fact.get("predicate_type") or "functional",
                            confidence=float(fact.get("confidence") or 1.0),
                            importance=int(fact.get("importance") or 5),
                            extraction_method="user_explicit",
                            tenant_id=_tenant,
                            cfg=None,
                        )
                    )
            else:
                # Generic free-text remember — additive diary belief
                actions.append(
                    mutate_belief(
                        primary,
                        "user",
                        "noted",
                        text[:1000],
                        ops_conn=ops,
                        predicate_type="set",
                        confidence=1.0,
                        importance=5,
                        extraction_method="user_explicit",
                        tenant_id=_tenant,
                        cfg=None,
                    )
                )
            bids = [a.get("belief_id", "") for a in actions if a.get("belief_id")]
            supersedes = sum(1 for a in actions if a.get("action") == "supersede")
            if eid or bids:
                detail = f"beliefs={','.join(bids) or 'n/a'}"
                if supersedes:
                    detail += f", superseded={supersedes}"
                return f"Stored memory (v2 episode={eid or 'n/a'}, {detail})"
            return "Error: memory store failed — V2 write returned no ids."
        except Exception as exc:
            logger.warning("[memory_store] V2 write failed: %s", exc)
            return f"Error: memory store failed — {exc}"
        finally:
            primary.close()
            ops.close()

    from kazma_core.agent.tool_registry import _pending_dispatch_tasks  # noqa: F401


    @registry.register(
        description=(
            "Search long-term memory for relevant past conversations, facts, or preferences. "
            "Use this before answering questions that may require context from earlier sessions."
        ),
        category="memory",
    )
    async def memory_search(query: str, limit: int = 5) -> str:
        # V2 cognitive recall — the single memory read path (V1 removed).
        # Returns its results even when empty: an empty result is a real
        # "no memories match", not a signal to consult a legacy store.
        try:
            from kazma_core.memory.recall import recall as v2_recall
            from kazma_core.safety.hitl import get_current_tenant_id

            result = v2_recall(query, limit=limit, tenant_id=get_current_tenant_id())
            out: list[dict[str, Any]] = []
            for h in result.beliefs:
                out.append({
                    "id": h.id, "content": h.content, "score": h.score,
                    "kind": "belief", "source": h.source, "metadata": h.metadata,
                })
            for h in result.episodes:
                out.append({
                    "id": h.id, "content": h.content, "score": h.score,
                    "kind": "episode", "source": h.source, "metadata": h.metadata,
                })
            if out:
                return json.dumps(out, ensure_ascii=False, indent=2)
            return "No relevant memories found."
        except Exception as exc:
            logger.warning("[memory_search] V2 recall failed: %s", exc)
            return "No relevant memories found."
    @registry.register(
        description=(
            "MEMORY ADMIN (read+write). Prefer this over SQL for all memory maintenance. "
            "action=list_beliefs|list_entities|invalidate|delete_entity|purge_empty_entities|"
            "merge|link|help. "
            "Graph cleanup: merge (id=source, target=keep), link (subject, predicate, object). "
            "Example hierarchy: link subject=user predicate=has_project object=kazma; "
            "link subject=kazma predicate=has_part object=kazma_framework. "
            "Merge duplicate shells into one: merge id=mubder_kazma target=kazma. "
            "Delete junk entity true/false: delete_entity id=true. "
            "DO NOT use memory_store to restructure the graph — store only adds notes. "
            "Only for explicit user requests to maintain/clean memory — never "
            "because a text you are rewriting or composing should mention memory."
        ),
        category="memory",
    )
    async def memory_admin(
        action: str = "help",
        id: str = "",
        q: str = "",
        limit: int = 40,
        confirm: bool = False,
        target: str = "",
        subject: str = "",
        predicate: str = "related_to",
        object: str = "",
    ) -> str:
        # Models often call memory_admin with {} — never require action.
        act = (action or "help").strip().lower().replace("-", "_")
        if act in ("help", "", "actions", "none", "null"):
            return json.dumps(
                {
                    "actions": [
                        "list_beliefs",
                        "list_entities",
                        "invalidate",
                        "delete_entity",
                        "purge_empty_entities",
                        "merge",
                        "link",
                        "help",
                    ],
                    "writes": [
                        "invalidate",
                        "delete_entity",
                        "purge_empty_entities",
                        "merge",
                        "link",
                    ],
                    "examples": [
                        {"action": "list_entities", "q": "kazma"},
                        {"action": "merge", "id": "mubder_kazma", "target": "kazma"},
                        {
                            "action": "link",
                            "subject": "user",
                            "predicate": "has_project",
                            "object": "kazma",
                        },
                        {
                            "action": "link",
                            "subject": "kazma",
                            "predicate": "has_part",
                            "object": "kazma_framework",
                        },
                        {"action": "delete_entity", "id": "true"},
                        {"action": "purge_empty_entities", "confirm": True},
                    ],
                    "graph_shape_goal": "user(Mubder) → has_project → kazma → has_part → …",
                },
                ensure_ascii=False,
                indent=2,
            )
        if act in ("list_beliefs", "beliefs"):
            return _mem_list_beliefs(q=q, limit=limit)
        if act in ("list_entities", "entities"):
            return _mem_list_entities(q=q, limit=limit)
        if act in ("invalidate", "invalidate_belief"):
            return _mem_invalidate(id)
        if act in ("delete_entity", "delete"):
            return _mem_delete_entity(id)
        if act in ("purge_empty_entities", "purge_empty", "purge"):
            return _mem_purge_empty_entities(confirm=bool(confirm))
        if act in ("merge", "merge_entities"):
            return _mem_merge_entities(id or subject, target or object)
        if act in ("link", "link_entities", "edge"):
            return _mem_link_entities(
                subject or id, predicate, object or target
            )
        return (
            f"Error: unknown action {action!r}. "
            "Use action=help for the list."
        )
    @registry.register(
        description=(
            "WRITE: Merge memory entity source into target. Beliefs rewired; "
            "use for duplicate shells (mubder_kazma → kazma, kazma_framework → kazma). "
            "Protected: cannot merge away user. Prefer over memory_store for cleanup."
        ),
        category="memory",
    )
    async def memory_merge_entities(source_id: str, target_id: str) -> str:
        return _mem_merge_entities(source_id, target_id)
    @registry.register(
        description=(
            "WRITE: Link two entities with a belief edge subject--predicate-->object. "
            "Use for graph hierarchy e.g. user has_project kazma; kazma has_part "
            "kazma_file_index. Creates missing entity rows. Not for free-text notes "
            "(use memory_store for notes)."
        ),
        category="memory",
    )
    async def memory_link_entities(
        subject: str,
        object: str,
        predicate: str = "related_to",
        predicate_type: str = "set",
    ) -> str:
        return _mem_link_entities(
            subject, predicate, object, predicate_type=predicate_type
        )
    @registry.register(
        description=(
            "List active long-term memory beliefs (V2). Optional q filter. "
            "For deletes use memory_admin action=invalidate. Not SQL."
        ),
        category="memory",
    )
    async def memory_list_beliefs(q: str = "", limit: int = 30) -> str:
        return _mem_list_beliefs(q=q, limit=limit)
    @registry.register(
        description=(
            "WRITE: Soft-invalidate one belief by id (from memory_list_beliefs). "
            "Removes stale/duplicate facts. Also: memory_admin action=invalidate id=…"
        ),
        category="memory",
    )
    async def memory_invalidate(belief_id: str) -> str:
        return _mem_invalidate(belief_id)
    @registry.register(
        description=(
            "List memory entities with belief counts. "
            "To delete empty shells: memory_admin action=purge_empty_entities confirm=true. "
            "To delete one: memory_delete_entity or memory_admin action=delete_entity."
        ),
        category="memory",
    )
    async def memory_list_entities(q: str = "", limit: int = 40) -> str:
        return _mem_list_entities(q=q, limit=limit)
    @registry.register(
        description=(
            "WRITE: Delete one memory entity by id (e.g. empty shell). "
            "Protected: user/assistant/kazma. Also memory_admin action=delete_entity."
        ),
        category="memory",
    )
    async def memory_delete_entity(entity_id: str) -> str:
        return _mem_delete_entity(entity_id)
    @registry.register(
        description=(
            "WRITE: Purge entity shells with zero active beliefs (safe clutter cleanup). "
            "Dry-run by default (confirm=false). Set confirm=true to delete. "
            "Also: memory_admin action=purge_empty_entities confirm=true."
        ),
        category="memory",
    )
    async def memory_purge_empty_entities(confirm: bool = False) -> str:
        return _mem_purge_empty_entities(confirm=bool(confirm))
    @registry.register(
        description=(
            "Store a fact, preference, or conversation fragment in long-term memory. "
            "Use when the user shares personal info, preferences, or important context "
            "that should be remembered across sessions. "
            "DO NOT use this to restructure/clean the entity graph — for merge shells, "
            "link Mubder→Kazma→parts, or delete junk nodes (true/false) use "
            "memory_merge_entities / memory_link_entities / memory_admin / memory_delete_entity. "
            "For rotating single-valued facts (e.g. 'my Grok next weekly reset is …', "
            "'ZCode next reset is …'), pass metadata JSON with "
            '{"predicate":"grok_next_reset","object":"<when>"} or '
            '{"service":"grok","next_reset":"<when>"} so the new value SUPERSEDES '
            "the previous one instead of stacking duplicates. Free text that mentions "
            "a product next/weekly reset is auto-classified the same way. "
            "To remove stale beliefs use memory_invalidate — never raw SQL. "
            "Only use this tool when the user EXPLICITLY asks to save/remember "
            "something — never because a reply you are writing should mention "
            "or reference memory."
        ),
        category="memory",
    )
    async def memory_store(text: str, metadata: str = "{}") -> str:
        # Two SQLite writers with schema-ensure and belief mutation — off the
        # event loop so a lock wait cannot stall SSE/WebSocket streams (F-06).
        return await asyncio.to_thread(_memory_store_sync, text, metadata)
