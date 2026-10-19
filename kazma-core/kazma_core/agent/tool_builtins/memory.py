"""Memory V2 tools — store, search, beliefs, entities, scratchpad.

Extracted from the former ``tool_builtins.py`` god module (2,833
lines) — audit O5. Tool bodies are unchanged; registration order
within this group is preserved.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from kazma_core.agent.tool_refusal import RefusedBeforeEffect

logger = logging.getLogger(__name__)


def _qnorm(q: str) -> str:
    """Normalize a memory q-filter: underscores/hyphens -> single spaces,
    lowercased. Paired with REPLACE(...) in SQL so 'memory system' matches
    user_memory_system (2026-08-27 report — the literal LIKE filter missed
    it while FTS memory_search matched fine)."""
    import re as _re

    return _re.sub(r"[_\-\s]+", " ", str(q or "").strip().lower()).strip()


#: What a memory tool answers in a chat the user keeps out of memory.
_KEPT_OUT = (
    "Not stored: the user keeps this chat out of memory, so nothing from it is "
    "saved. They can let it back in from the chat's Memory menu, or with /memory on."
)


def _chat_of_call() -> tuple[str, int]:
    """The chat and turn a memory tool is called in: the thread id and the
    turn index its memory records (``consolidator.user_turn_index`` over the
    turn's messages), or ``("", 0)`` outside a chat.

    Every note and fact a memory tool writes names them (2026-09-27): without
    that, "forget this chat" and "don't remember this chat" missed what the
    agent stored with its tools -- live, a test chat's "pebble-live" token
    outlived the chat as a current fact.
    """
    from kazma_core.memory.consolidator import user_turn_index
    from kazma_core.safety.hitl import get_current_thread_id
    from kazma_core.tools.export_session import get_current_session_messages

    thread = str(get_current_thread_id() or "")
    if not thread:
        return "", 0
    return thread, user_turn_index(get_current_session_messages() or [])


def _agent_write(*texts: str) -> tuple[str | None, str]:
    """``(refusal, extraction method)`` for a memory tool's write (Memory
    audit M03, ``memory.provenance``).

    The tools wrote ``user_explicit`` -- the user's gold label, which blocks
    the user's own later correction -- for whatever the model chose to store,
    a fetched page or an email included, and screened nothing. A write is
    refused when it reads as an instruction; it is the user's own only when
    their latest message asks to remember and holds the words, else
    ``agent_asserted``."""
    from kazma_core.memory.provenance import agent_write_source, latest_user_message, screen_agent_text
    from kazma_core.tools.export_session import get_current_session_messages

    refusal = screen_agent_text(*texts)
    if refusal:
        return f"Not stored: {refusal}.", ""
    latest = latest_user_message(get_current_session_messages() or [])
    return None, agent_write_source(" ".join(t for t in texts if t), latest)


#: What a memory tool answers when its write would replace the user's own fact.
_USER_FACT_STANDS = (
    "The user's own fact stands: a memory tool cannot replace what the user said. "
    "If it changed, ask the user; their own words correct it."
)


#: What memory_search answers when recall itself failed: never "no memories",
#: which reads as a fact (Memory audit M11; the recall deadline is M16).
_RECALL_DEGRADED = (
    "Memory search is unavailable right now (recall failed). This is NOT an empty result: "
    "do not tell the user nothing is remembered."
)


def _fence_result(text: str, *, own: bool) -> str:
    """Memory read back to the model is data, never instructions (Memory audit
    M11): what a turn, a page or a tool once wrote reaches the model inside
    the untrusted-data fence, as the per-turn recall block always did. *own*
    is this tool's own message (an error, "nothing found"), left as it is."""
    from kazma_core.safety.prompt_fence import fence_untrusted

    return text if own else fence_untrusted(text, source="memory_search")


def _shown(hit: Any) -> str:
    """What a recalled memory shows: its display text (question AND answer),
    never only the match text (``recall._shown``'s rule)."""
    return str((hit.metadata or {}).get("display") or hit.content or "")


def _chat_kept_out(thread: str, tenant_id: str) -> bool:
    """True when the user keeps the chat *thread* out of memory (plan U1)."""
    if not thread:
        return False
    from kazma_core.memory.forget import chat_remembered

    return not chat_remembered(thread, tenant_id=tenant_id)


def register_memory_tools(registry: Any) -> None:
    """Register the memory tools onto *registry*."""
    from kazma_core.db.sql_patterns import like_literal
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
                ql = f"%{like_literal(_qnorm(q))}%"
                sql += (
                    " AND (LOWER(REPLACE(REPLACE(subject,'_',' '),'-',' ')) LIKE ? ESCAPE '!' "
                    "OR LOWER(REPLACE(REPLACE(predicate,'_',' '),'-',' ')) LIKE ? ESCAPE '!' "
                    "OR LOWER(REPLACE(REPLACE(object,'_',' '),'-',' ')) LIKE ? ESCAPE '!')"
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
                ql = f"%{like_literal(_qnorm(q))}%"
                sql += (
                    " AND (LOWER(REPLACE(REPLACE(e.id,'_',' '),'-',' ')) LIKE ? ESCAPE '!' "
                    "OR LOWER(REPLACE(REPLACE(e.name,'_',' '),'-',' ')) LIKE ? ESCAPE '!' "
                    "OR LOWER(REPLACE(REPLACE(e.type,'_',' '),'-',' ')) LIKE ? ESCAPE '!')"
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
    def _mem_invalidate(belief_id: str, forget_source_turn: bool = False) -> str:
        from kazma_core.memory.hygiene import invalidate_belief
        from kazma_core.safety.hitl import get_current_tenant_id

        bid = (belief_id or "").strip()
        if not bid:
            raise RefusedBeforeEffect("Error: belief_id required (from list_beliefs)")
        tenant = get_current_tenant_id()
        if bid.startswith("e_"):
            # A memory of a conversation turn (memory_search's id): only the
            # user forgets those, so this is the Memory page's Forget. Live
            # 2026-10-19 the model passed two; "not found" from the facts
            # table was recorded as an unknown effect and failed the turn.
            return _forget_memory(bid, tenant or "default")
        try:
            # The turn's tenant, like the list tools above: an id from another
            # tenant reads as not found.
            result = invalidate_belief(bid, remove_graph=True, tenant_id=tenant)
            if not result.get("ok") and result.get("error") == "not found":
                raise RefusedBeforeEffect(
                    f"Error: fact {bid} not found. Fact ids (b_...) come from memory_list_beliefs; "
                    "a memory of a conversation (e_..., from memory_search) is forgotten by "
                    "passing its id here.")
            if forget_source_turn and result.get("ok"):
                # The user asked to forget: the turn the fact came from goes
                # too, or recall finds it and the next turn learns it again
                # (memory.forget.forget_belief_source, live 2026-10-19).
                from kazma_core.memory.forget import forget_belief_source

                result["source_turn"] = forget_belief_source(bid, tenant_id=tenant or "default")
                result["this_turn_kept_out"] = _keep_this_turn_out()
            return json.dumps(result, ensure_ascii=False, indent=2)
        except RefusedBeforeEffect:
            raise
        except Exception as exc:
            logger.warning("[memory_invalidate] failed: %s", exc)
            return f"Error: memory_invalidate failed — {exc}"

    def _forget_mentions(text: str) -> str:
        import sqlite3

        from kazma_core.memory.forget import MENTION_MIN_CHARS, forget_mentions
        from kazma_core.safety.hitl import get_current_tenant_id

        needle = (text or "").strip()
        if len(needle) < MENTION_MIN_CHARS:
            raise RefusedBeforeEffect(f"Error: text must be at least {MENTION_MIN_CHARS} characters")
        try:
            result = forget_mentions(needle, tenant_id=get_current_tenant_id() or "default")
        except (sqlite3.Error, OSError) as exc:
            logger.warning("[memory_forget_mentions] failed: %s", exc, exc_info=True)
            return f"Error: memory_forget_mentions failed — {exc}"
        if result.get("error") == "nothing_holds_it":
            raise RefusedBeforeEffect(f"Error: no memory or current fact holds {needle!r}")
        result["this_turn_kept_out"] = _keep_this_turn_out()
        return json.dumps(result, ensure_ascii=False, indent=2)

    def _keep_this_turn_out() -> bool:
        """The turn asking to forget names what it forgets: keep it out of
        memory (``forget.keep_turn_out``)."""
        import sqlite3

        from kazma_core.memory.forget import keep_turn_out
        from kazma_core.memory.provenance import latest_user_message
        from kazma_core.safety.hitl import get_current_tenant_id
        from kazma_core.tools.export_session import get_current_session_messages

        chat, turn = _chat_of_call()
        question = latest_user_message(get_current_session_messages() or [])
        try:
            return keep_turn_out(chat, turn, question, tenant_id=get_current_tenant_id() or "default")
        except (sqlite3.Error, OSError):
            logger.warning("[memory] could not keep the forgetting turn out of memory", exc_info=True)
            return False

    def _forget_memory(episode_id: str, tenant: str) -> str:
        import sqlite3

        from kazma_core.memory.forget import forget_episode

        try:
            result = forget_episode(episode_id, tenant_id=tenant, by="user")
        except (sqlite3.Error, OSError) as exc:
            logger.warning("[memory_invalidate] forgetting %s failed: %s", episode_id, exc, exc_info=True)
            return f"Error: forgetting {episode_id} failed — {exc}"
        if result.get("error") == "not_found":
            raise RefusedBeforeEffect(f"Error: memory {episode_id} not found (ids come from memory_search)")
        result["this_turn_kept_out"] = _keep_this_turn_out()
        return json.dumps(result, ensure_ascii=False, indent=2)
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
            raise RefusedBeforeEffect("Error: source_id and target_id required")
        if src_id == tgt_id:
            raise RefusedBeforeEffect("Error: source and target must differ")
        try:
            conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            ensure_primary_schema(conn)
            # The routes' rule (memory/entity_key.entity_owner): a tenant acts on
            # its own entities, the install's own tenant on the one tenant's
            # that holds the id -- this tool read both rows without asking
            # (Memory audit M21). Source and target are one tenant's: entities
            # are keyed by (tenant_id, id).
            from kazma_core.memory.entity_key import entity_owner
            from kazma_core.memory.entity_protection import merge_refusal
            from kazma_core.safety.hitl import get_current_tenant_id

            caller = get_current_tenant_id() or "default"
            tenant = entity_owner(conn, src_id, caller)
            # The page's rule (memory/entity_protection.py): the hub, the
            # agent, the owner's own names (which may go INTO the hub) and the
            # operator's flag. This tool kept a list of its own, user and
            # assistant only.
            if merge_refusal(conn, src_id, tgt_id, tenant_id=tenant or caller):
                conn.close()
                raise RefusedBeforeEffect(f"Error: cannot merge protected source {src_id}")
            src = conn.execute(
                "SELECT id, name, aliases_json FROM entities WHERE id=? AND tenant_id=?", (src_id, tenant)
            ).fetchone() if tenant else None
            tgt = conn.execute(
                "SELECT id, name, aliases_json FROM entities WHERE id=? AND tenant_id=?", (tgt_id, tenant)
            ).fetchone() if tenant else None
            if not src or not tgt:
                conn.close()
                raise RefusedBeforeEffect(json.dumps(
                    {"ok": False, "error": "source or target not found"},
                    ensure_ascii=False,
                ))
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
                "UPDATE entities SET aliases_json=? WHERE id=? AND tenant_id=?",
                (_json.dumps(tgt_aliases, ensure_ascii=False), tgt_id, tenant),
            )
            # The belief redirects below are the entities' tenant's: unscoped,
            # they rewrote every tenant's beliefs naming the id, and the merge
            # audit row was misattributed to the 'default' tenant (audit
            # finding).
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
                   WHERE id = ? AND tenant_id = ?""",
                (tgt_id, src_id, tenant),
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

                recompute_entity_counts(conn, [src_id, tgt_id], tenant_id=tenant, neighbours=True)
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
        except RefusedBeforeEffect:
            raise
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
            raise RefusedBeforeEffect("Error: subject and object required")
        thread, turn = _chat_of_call()
        if _chat_kept_out(thread, get_current_tenant_id()):
            return _KEPT_OUT
        refusal, method = _agent_write(sub, pred, object_)
        if refusal:
            raise RefusedBeforeEffect(refusal)
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
                    "SELECT id FROM entities WHERE id=? AND tenant_id=?", (eid, tenant)
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
                extraction_method=method,
                tenant_id=tenant,
                source_session=thread or None,
                source_turn=turn or None,
            )
            ops.close()
            primary.close()
            if result.get("blocked") == "lower_trust_source":
                raise RefusedBeforeEffect(json.dumps({"ok": False, "error": "user_fact_stands", "hint": _USER_FACT_STANDS}))
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
        except RefusedBeforeEffect:
            raise
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
            raise RefusedBeforeEffect("Error: entity_id required")
        try:
            conn = sqlite3.connect(
                primary_memory_db(), check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            ensure_primary_schema(conn)
            tenant = get_current_tenant_id()
            # The page's rule (memory/entity_protection.py): the hub, the
            # agent, the owner's own names and the operator's flag.
            from kazma_core.memory.entity_protection import protection

            if protection(conn, eid, tenant_id=tenant):
                conn.close()
                raise RefusedBeforeEffect(f"Error: refusing to delete protected entity '{eid}'")
            row = conn.execute(
                "SELECT id, type, name FROM entities WHERE id=? AND tenant_id=?",
                (eid, tenant),
            ).fetchone()
            if not row:
                conn.close()
                raise RefusedBeforeEffect(json.dumps(
                    {"ok": False, "error": "not_found", "entity_id": eid}
                ))
            try:
                from kazma_core.memory.entity_resolution import preserve_merge_ledger

                preserve_merge_ledger(conn, eid, tenant_id=tenant, reason="entity_delete")
            except Exception:
                logger.debug("[memory_delete_entity] merge-ledger archive skipped", exc_info=True)
            conn.execute(
                "DELETE FROM entity_merges WHERE tenant_id=? AND (source_entity_id=? OR target_entity_id=?)",
                (tenant, eid, eid),
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
        except RefusedBeforeEffect:
            raise
        except Exception as exc:
            logger.warning("[memory_delete_entity] failed: %s", exc)
            return f"Error: memory_delete_entity failed — {exc}"
    def _mem_purge_empty_entities(*, confirm: bool = False) -> str:
        """Delete entity shells that hold nothing (safe clutter).

        The rule the automatic retire and the Memory page's Hygiene use
        (``entity_retire.plain_empty_shells``): no live fact and nothing but
        the entity's own name. It deleted every entity with no live fact,
        merge redirects and the owner's other names included.
        """
        import sqlite3

        from kazma_core.memory.entity_retire import plain_empty_shells, retire_empty_entities
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
            ids = [eid for _tenant, eid in plain_empty_shells(conn, tenant_id=tenant)]
            if not confirm:
                empty = [
                    {**dict(r), "belief_count": 0}
                    for eid in ids
                    for r in conn.execute(
                        "SELECT id, type, name FROM entities WHERE id=? AND tenant_id=?", (eid, tenant)
                    ).fetchall()
                ]
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
            deleted = retire_empty_entities(conn, ids, tenant_id=tenant)
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
            # The chat this is stored from: named on the note and every fact,
            # so forgetting the chat (or turn) forgets them too.
            thread, turn = _chat_of_call()
            if _chat_kept_out(thread, _tenant):
                return _KEPT_OUT
            refusal, method = _agent_write(text)
            if refusal:
                return refusal
            # Episode (raw text snapshot) — always keep diary trail
            eid = mirror_episode(
                session_id=str(meta.get("session_id", "memory_store")),
                turn_number=int(meta.get("turn", 0)),
                user_text=text,
                source="memory_store_tool",
                tenant_id=_tenant,
                metadata={"chat": thread, "chat_turn": turn} if thread else None,
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
                            extraction_method=method,
                            tenant_id=_tenant,
                            source_session=thread or None,
                            source_turn=turn or None,
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
                        extraction_method=method,
                        tenant_id=_tenant,
                        source_session=thread or None,
                        source_turn=turn or None,
                        cfg=None,
                    )
                )
            bids = [a.get("belief_id", "") for a in actions if a.get("belief_id")]
            supersedes = sum(1 for a in actions if a.get("action") == "supersede")
            stands = sum(1 for a in actions if a.get("blocked") == "lower_trust_source")
            if eid or bids:
                detail = f"beliefs={','.join(bids) or 'n/a'}"
                if supersedes:
                    detail += f", superseded={supersedes}"
                note = f" {_USER_FACT_STANDS}" if stands else ""
                return f"Stored memory (v2 episode={eid or 'n/a'}, {detail}).{note}"
            return "Error: memory store failed — V2 write returned no ids."
        except Exception as exc:
            logger.warning("[memory_store] V2 write failed: %s", exc)
            return f"Error: memory store failed — {exc}"
        finally:
            primary.close()
            ops.close()


    @registry.register(
        description=(
            "Search long-term memory for relevant past conversations, facts, or preferences. "
            "Use this before answering questions that may require context from earlier sessions."
        ),
        category="memory",
    )
    async def memory_search(query: str, limit: int = 5) -> str:
        # Recall reads SQLite and embeds the query: in a thread, never on the
        # loop that serves every chat stream (AGENTS §35). An async tool runs
        # on the loop; the registry threads only sync ones.
        body, own = await asyncio.to_thread(_memory_search_sync, query, limit)
        return _fence_result(body, own=own)

    def _memory_search_sync(query: str, limit: int) -> tuple[str, bool]:
        # V2 cognitive recall — the single memory read path (V1 removed).
        # Returns its results even when empty: an empty result is a real
        # "no memories match", not a signal to consult a legacy store.
        try:
            from kazma_core.memory.recall import recall as v2_recall
            from kazma_core.safety.hitl import get_current_tenant_id

            result = v2_recall(query, limit=limit, tenant_id=get_current_tenant_id())
            out: list[dict[str, Any]] = []
            # A week's topic summary (plan C2) shows its week, title and text;
            # a memory, its question and its answer.
            for kind, hits in (("belief", result.beliefs), ("weekly_summary", result.summaries),
                               ("episode", result.episodes)):
                for h in hits:
                    out.append({
                        "id": h.id, "content": _shown(h), "score": h.score,
                        "kind": kind, "source": h.source, "metadata": h.metadata,
                    })
            if result.degraded:
                # Not "nothing remembered": a search that could not look by
                # meaning says so (Memory audit M16).
                note = f"Memory search was incomplete ({result.degraded}); keyword matches only."
                if out:
                    return note + "\n" + json.dumps(out, ensure_ascii=False, indent=2), False
                return note + " " + _RECALL_DEGRADED, True
            if out:
                return json.dumps(out, ensure_ascii=False, indent=2), False
            return "No relevant memories found.", True
        except Exception as exc:
            logger.warning("[memory_search] V2 recall failed: %s", exc)
            return _RECALL_DEGRADED, True
    @registry.register(
        description=(
            "MEMORY ADMIN (read+write). Prefer this over SQL for all memory maintenance. "
            "action=list_beliefs|list_entities|invalidate|delete_entity|purge_empty_entities|"
            "merge|link|help. "
            "Graph cleanup: merge (id=source, target=keep), link (subject, predicate, object). "
            "Example hierarchy: link subject=user predicate=has_project object=kazma; "
            "link subject=kazma predicate=has_part object=kazma_framework. "
            "Merge duplicate shells into one: merge id=kazma_app target=kazma. "
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
                        {"action": "merge", "id": "kazma_app", "target": "kazma"},
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
                    "graph_shape_goal": "user → has_project → kazma → has_part → …",
                },
                ensure_ascii=False,
                indent=2,
            )
        # Every action is SQLite: in a thread, like memory_store.
        if act in ("list_beliefs", "beliefs"):
            text = await asyncio.to_thread(_mem_list_beliefs, q=q, limit=limit)
            return _fence_result(text, own=text.startswith("Error:"))
        if act in ("list_entities", "entities"):
            text = await asyncio.to_thread(_mem_list_entities, q=q, limit=limit)
            return _fence_result(text, own=text.startswith("Error:"))
        if act in ("invalidate", "invalidate_belief"):
            return await asyncio.to_thread(_mem_invalidate, id)
        if act in ("delete_entity", "delete"):
            return await asyncio.to_thread(_mem_delete_entity, id)
        if act in ("purge_empty_entities", "purge_empty", "purge"):
            return await asyncio.to_thread(_mem_purge_empty_entities, confirm=bool(confirm))
        if act in ("merge", "merge_entities"):
            return await asyncio.to_thread(_mem_merge_entities, id or subject, target or object)
        if act in ("link", "link_entities", "edge"):
            return await asyncio.to_thread(
                _mem_link_entities, subject or id, predicate, object or target
            )
        raise RefusedBeforeEffect(f"Error: unknown action {action!r}. "
            "Use action=help for the list.")
    @registry.register(
        description=(
            "WRITE: Merge memory entity source into target. Beliefs rewired; "
            "use for duplicate shells (kazma_app → kazma, acme_project → acme). "
            "Protected: cannot merge away the hub (user), the agent, the owner's own "
            "names or an entity the operator protected. Prefer over memory_store for cleanup."
        ),
        category="memory",
    )
    async def memory_merge_entities(source_id: str, target_id: str) -> str:
        return await asyncio.to_thread(_mem_merge_entities, source_id, target_id)
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
        return await asyncio.to_thread(
            _mem_link_entities, subject, predicate, object, predicate_type=predicate_type
        )
    @registry.register(
        description=(
            "List active long-term memory beliefs (V2). Optional q filter. "
            "For deletes use memory_admin action=invalidate. Not SQL."
        ),
        category="memory",
    )
    async def memory_list_beliefs(q: str = "", limit: int = 30) -> str:
        text = await asyncio.to_thread(_mem_list_beliefs, q=q, limit=limit)
        return _fence_result(text, own=text.startswith("Error:"))
    @registry.register(
        description=(
            "WRITE: Soft-invalidate one belief by id (from memory_list_beliefs). "
            "Removes stale/duplicate facts. Also: memory_admin action=invalidate id=… "
            "When the USER asks you to forget something they told you, pass "
            "forget_source_turn=true: the conversation turn the fact came from is "
            "forgotten too (as the Memory page's Forget does), so it is neither "
            "recalled nor learned again. Leave it false for clean-up of stale or "
            "duplicate facts. A memory id (e_..., from memory_search) forgets that "
            "conversation memory and the facts its turn produced (the user's Forget)."
        ),
        category="memory",
    )
    async def memory_invalidate(belief_id: str, forget_source_turn: bool = False) -> str:
        return await asyncio.to_thread(_mem_invalidate, belief_id, bool(forget_source_turn))
    @registry.register(
        description=(
            "WRITE: Forget EVERY memory of a conversation and every current fact that "
            "contains this exact text (any case, at least 4 characters). Use when the user "
            "asks to forget everything that mentions something (a codeword, a number, a "
            "name): memory_search shows only the best few matches, so forgetting its hits "
            "one by one leaves the rest. Each memory is forgotten as the Memory page's "
            "Forget does. Returns the ids forgotten."
        ),
        category="memory",
    )
    async def memory_forget_mentions(text: str) -> str:
        return await asyncio.to_thread(_forget_mentions, text)
    @registry.register(
        description=(
            "List memory entities with belief counts. "
            "To delete empty shells: memory_admin action=purge_empty_entities confirm=true. "
            "To delete one: memory_delete_entity or memory_admin action=delete_entity."
        ),
        category="memory",
    )
    async def memory_list_entities(q: str = "", limit: int = 40) -> str:
        text = await asyncio.to_thread(_mem_list_entities, q=q, limit=limit)
        return _fence_result(text, own=text.startswith("Error:"))
    @registry.register(
        description=(
            "WRITE: Delete one memory entity by id (e.g. empty shell). "
            "Protected: user/assistant/kazma. Also memory_admin action=delete_entity."
        ),
        category="memory",
    )
    async def memory_delete_entity(entity_id: str) -> str:
        return await asyncio.to_thread(_mem_delete_entity, entity_id)
    @registry.register(
        description=(
            "WRITE: Purge entity shells with zero active beliefs (safe clutter cleanup). "
            "Dry-run by default (confirm=false). Set confirm=true to delete. "
            "Also: memory_admin action=purge_empty_entities confirm=true."
        ),
        category="memory",
    )
    async def memory_purge_empty_entities(confirm: bool = False) -> str:
        return await asyncio.to_thread(_mem_purge_empty_entities, confirm=bool(confirm))
    @registry.register(
        description=(
            "Store a fact, preference, or conversation fragment in long-term memory. "
            "Use when the user shares personal info, preferences, or important context "
            "that should be remembered across sessions. "
            "DO NOT use this to restructure/clean the entity graph — for merge shells, "
            "link user→kazma→parts, or delete junk nodes (true/false) use "
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
