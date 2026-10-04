"""Export observed X cases for human annotation without inventing labels."""

from __future__ import annotations

from typing import Any


def collect_cases(*, limit: int = 500) -> list[dict[str, Any]]:
    """Tenant-scoped records; missing original context stays explicitly missing."""
    from kazma_core.x_api.ownership import x_tenant_id
    from kazma_core.x_api.reply_store import ReplyRecord, get_reply_store

    output = []
    store = get_reply_store()
    with store._lock:
        conn = store._connect()
        try:
            records = conn.execute("SELECT * FROM x_replies WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                                   (x_tenant_id(), max(1, min(limit, 5000)))).fetchall()
        finally:
            conn.close()
    for row in map(ReplyRecord, records):
        source = row.decision.get("context") or {
            "source_id": row.parent_id, "text": row.parent_text, "author_handle": row.target_handle,
            "verified_source": False, "author_resolved": False, "fallback_text": True,
        }
        authority = row.decision.get("summon_context") or {}
        output.append({"id": f"x:{row.tenant_id}:{row.summon_id}:{row.attempt_no}", "language": "",
                       "categories": [], "held_out": False, "human_reviewed": False, "labeler": "",
                       "expected": {"target": None, "auto": None, "evidence": None, "safety": None},
                       "context": source,
                       "summon": {"id": row.summon_id, "author": row.summoner, "text": row.summon_text,
                                  "conversation_id": authority.get("conversation_id", ""),
                                  "target_followers": authority.get("target_followers")},
                       "group": authority.get("conversation_id") or row.parent_id,
                       "observed_at": row.created_at, "observed_candidate": row.draft_text,
                       "observed_status": row.status, "observed_reason": row.reason,
                       "rationale": "", "review_notes": ""})
    return output
