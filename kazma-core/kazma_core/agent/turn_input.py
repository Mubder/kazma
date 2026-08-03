"""Build agent turn input with checkpointer + session continuity.

Both the gateway and Web SSE paths previously diverged:

* Gateway restored full checkpoint history (including tool chains)
* SSE rebuilt from SessionManager text-only projection and overwrote
  the checkpointer (no ``add_messages`` reducer on SupervisorState)

That caused post-HITL "amnesia" on Web and any path that forgot restore.

After a **server restart**, the checkpointer can be thinner than the UI
session (or empty) while SessionStore still has the full chat — using only
the checkpoint then makes "Proceed" look like a blank conversation.

Use :func:`build_turn_messages` for every new user turn.
"""

from __future__ import annotations

import logging
import re
from typing import Any

__all__ = [
    "build_turn_messages",
    "load_checkpoint_messages",
    "contentful_turn_count",
    "normalize_history_messages",
    "is_short_continuation",
    "is_memory_store_intent",
    "is_memory_graph_cleanup_intent",
    "is_bulk_document_message",
    "extract_store_focus_query",
    "latest_turn_priority_note",
]

logger = logging.getLogger(__name__)

# Short follow-ups that inherit the prior task (same-session continuity).
_CONTINUATION_PHRASES = frozenset(
    {
        "proceed",
        "continue",
        "try now",
        "try again",
        "go",
        "go ahead",
        "do it",
        "yes",
        "yep",
        "ok",
        "okay",
        "same",
        "again",
        "retry",
        "keep going",
        "finish",
        "finish it",
        "pick up",
        "resume",
    }
)

# User wants the agent to *write* this turn into memory (not answer from memory).
_STORE_INTENT_RE = re.compile(
    r"(?is)"
    r"("
    r"\b(?:add|save|store|put)\b.{0,80}\b(?:to|into|in)\b.{0,40}\bmemory\b"
    r"|"
    r"\bremember\s+(?:this|that|the following)\b"
    r"|"
    r"\b(?:write|persist|ingest)\b.{0,40}\b(?:to|into)\b.{0,40}\bmemory\b"
    r"|"
    r"\badd\s+(?:this|it|these|the following)\s+to\b"
    r")"
)

# "… to the ShipX memory" / "ShipX memory:"
_STORE_SUBJECT_RE = re.compile(
    r"(?is)\b(?:to|into|in)\s+(?:the\s+)?([A-Za-z][\w .-]{1,40}?)\s+memory\b"
)
_NAMED_PROJECT_RE = re.compile(
    r"(?is)\b(?:overview|about|regarding)\s+(?:of\s+)?([A-Za-z][\w]{1,32})\b"
    r"|\b([A-Za-z][\w]{1,32})\s+is\s+an?\s+"
)


def is_short_continuation(text: str) -> bool:
    """True for short same-session continuations like 'Proceed' / 'try now'."""
    s = " ".join((text or "").strip().lower().split())
    if not s or len(s) > 48:
        return False
    if s in _CONTINUATION_PHRASES:
        return True
    # "proceed with cleanup", "continue please"
    head = s.split()[0] if s.split() else ""
    return head in ("proceed", "continue", "resume", "retry") and len(s) < 40


def is_bulk_document_message(text: str, *, min_chars: int = 600) -> bool:
    """True for long paste payloads (architecture dumps, handoffs, etc.)."""
    return len((text or "").strip()) >= min_chars


# User wants graph/entity hygiene: merge, link, delete junk — NOT memory_store.
_GRAPH_CLEANUP_RE = re.compile(
    r"(?is)"
    r"("
    r"\b(?:entities?|beliefs?|graph|nodes?)\b.{0,80}\b(?:messy|missy|clutter|cleanup|clean\s*up|align|restructur|organiz|organise|hierarchy|linked|duplicate)\b"
    r"|"
    r"\b(?:merge|link|align|restructur|cleanup|clean\s*up)\b.{0,80}\b(?:entities?|beliefs?|graph|nodes?|kazma|shipx)\b"
    r"|"
    r"\b(?:aligned|structure)\b.{0,40}\b(?:this way|like|as)\b"
    r"|"
    r"\b(?:mubder|user)\b.{0,20}(?:→|->|—>|>).{0,20}\bkazma\b"
    r"|"
    r"\bjunk\b.{0,40}\b(?:entities?|nodes?|true|false)\b"
    r"|"
    r"\bdelete\b.{0,40}\b(?:entities?|entity|true|false)\b"
    r")"
)


def is_memory_graph_cleanup_intent(text: str) -> bool:
    """True when the user wants to restructure/clean the belief graph."""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_GRAPH_CLEANUP_RE.search(t))


def is_memory_store_intent(text: str) -> bool:
    """True when the user is asking to *save* content into memory this turn.

    Distinguishes store tasks from "what do you remember about X?" questions so
    we do not let session-boosted prior topics (e.g. reminders) hijack a bulk
    "add this to ShipX memory" paste.
    """
    t = (text or "").strip()
    if not t:
        return False
    # Graph cleanup wins over store (users say "memory is messy" without meaning store)
    if is_memory_graph_cleanup_intent(t):
        return False
    if _STORE_INTENT_RE.search(t):
        return True
    # Long document with an explicit project memory target in the first lines
    head = t[:400]
    if is_bulk_document_message(t) and re.search(
        r"(?i)\b(?:memory|remember|save|store)\b", head
    ):
        return True
    return False


def extract_store_focus_query(text: str) -> str:
    """Build a short recall query for store intents (subject, not full paste).

    Full multi-KB pastes match unrelated FTS terms poorly and still get
    *session_boost* on the prior reminder thread — so we prefer the named
    subject (ShipX) + first heading line.
    """
    t = (text or "").strip()
    if not t:
        return ""
    parts: list[str] = []
    m = _STORE_SUBJECT_RE.search(t)
    if m:
        subj = (m.group(1) or "").strip(" .:-")
        if subj and subj.lower() not in ("your", "the", "my", "our"):
            parts.append(subj)
    # First non-empty line often "Now read this…" or "Overview of ShipX"
    for line in t.splitlines():
        line = line.strip()
        if not line:
            continue
        parts.append(line[:120])
        break
    m2 = _NAMED_PROJECT_RE.search(t[:800])
    if m2:
        name = (m2.group(1) or m2.group(2) or "").strip()
        if name and name not in parts:
            parts.append(name)
    # Unique preserve order
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        k = p.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    q = " ".join(out).strip()
    return q[:240] if q else t[:200]


def latest_turn_priority_note(
    *,
    store_intent: bool = False,
    graph_cleanup: bool = False,
    focus: str = "",
) -> str:
    """System note so the model does not pivot to an old recalled topic."""
    base = (
        "LATEST USER MESSAGE PRIORITY: Your job this turn is to fulfill the "
        "user's *most recent* message. Recalled memory and earlier chat are "
        "supporting context only. Do NOT change subject to an unrelated "
        "recalled topic (e.g. reminders, quota resets, prior tools) when the "
        "latest message is about something else."
    )
    if graph_cleanup:
        base += (
            " MEMORY GRAPH CLEANUP TASK: The user wants the entity/belief graph "
            "restructured or cleaned — NOT a new free-text memory_store note. "
            "Use memory_list_entities / memory_list_beliefs, then "
            "memory_merge_entities (collapse duplicates into one canonical id), "
            "memory_link_entities (hierarchy edges e.g. user has_project kazma; "
            "kazma has_part …), memory_delete_entity (junk shells like true/false), "
            "memory_invalidate (bad beliefs). Target shape often: "
            "Mubder(user) → has_project → kazma → has_part → related entities. "
            "Do NOT invent ShipX notes or re-save FILE_INDEX unless they asked."
        )
        if focus:
            base += f" Focus keywords: {focus}."
    elif store_intent:
        focus_bit = f" Subject focus: {focus}." if focus else ""
        base += (
            " MEMORY STORE TASK: The user wants the content of their latest "
            "message saved into long-term memory (use memory_store / belief "
            "tools as appropriate). Extract structured facts from *that* "
            f"payload{focus_bit} Prefer structured links "
            "(memory_link_entities) for project trees over one giant noted blob. "
            "Do not answer as if they asked about unrelated prior conversation topics."
        )
    return base


def contentful_turn_count(msgs: list[dict[str, Any]]) -> int:
    """Count user/assistant turns that carry real text (not empty tool shells)."""
    n = 0
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        if m.get("tool_calls"):
            continue
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            n += 1
        elif isinstance(content, list) and content:
            n += 1
    return n


def normalize_history_messages(raw: list[Any] | None) -> list[dict[str, Any]]:
    """Project UI/session rows into graph-safe message dicts."""
    if not raw:
        return []
    out: list[dict[str, Any]] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("system", "user", "assistant", "tool"):
            continue
        # Skip empty pending assistant placeholders
        content = m.get("content")
        if (
            role == "assistant"
            and not m.get("tool_calls")
            and not (isinstance(content, str) and content.strip())
            and m.get("pending")
        ):
            continue
        row = {
            k: v
            for k, v in m.items()
            if k in ("role", "content", "tool_calls", "tool_call_id", "name")
        }
        out.append(row)
    try:
        from kazma_core.agent.graph_builder import sanitize_tool_chains

        return sanitize_tool_chains(out)
    except Exception:
        return out


async def load_checkpoint_messages(
    graph: Any,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Load and sanitize messages from the LangGraph checkpointer."""
    if graph is None or getattr(graph, "checkpointer", None) is None:
        return []
    try:
        snap = await graph.aget_state(config)
        prior = list((snap.values or {}).get("messages") or []) if snap else []
    except Exception as exc:
        logger.debug("[turn_input] aget_state failed: %s", exc)
        return []

    if not prior:
        return []

    try:
        from kazma_core.agent.graph_builder import sanitize_tool_chains

        return sanitize_tool_chains(prior)
    except Exception:
        return prior


async def build_turn_messages(
    graph: Any,
    config: dict[str, Any],
    *,
    user_text: str,
    system_messages: list[dict[str, Any]] | None = None,
    fallback_history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Assemble the full message list for one new user turn.

    Priority:
      1. Checkpoint history when it is the richest source (tool-aware)
      2. Session/UI fallback when checkpoint is empty OR thinner (restart gap)
      3. Optional system_messages prepended only when not already present
      4. The new user message always last

    SessionManager is the UI projection; after restart it often has more
    conversational turns than a thin/empty checkpoint — that continuity
    must win for "Proceed" / same-session work.
    """
    ckpt = await load_checkpoint_messages(graph, config)
    session_hist = normalize_history_messages(fallback_history)

    ckpt_n = contentful_turn_count(ckpt)
    sess_n = contentful_turn_count(session_hist)

    if not ckpt:
        prior = session_hist
        if prior:
            logger.info(
                "[turn_input] checkpoint empty — using session history (%d contentful turns)",
                sess_n,
            )
    elif sess_n > ckpt_n + 1:
        # Session is meaningfully richer (common after restart / failed turns
        # that never wrote full checkpoint state). Prefer session text history
        # so "Proceed" still sees the task the user already gave.
        prior = session_hist
        logger.warning(
            "[turn_input] session history richer than checkpoint "
            "(session=%d contentful, checkpoint=%d) — using session for continuity",
            sess_n,
            ckpt_n,
        )
    else:
        prior = ckpt

    out: list[dict[str, Any]] = list(prior)

    # Prepend system messages that are not already at the head (env refresh etc.)
    if system_messages:
        existing_sys = {
            (m.get("content") or "")[:80]
            for m in out
            if isinstance(m, dict) and m.get("role") == "system"
        }
        to_prepend: list[dict[str, Any]] = []
        for sm in system_messages:
            if not isinstance(sm, dict) or sm.get("role") != "system":
                continue
            key = (sm.get("content") or "")[:80]
            if key and key not in existing_sys:
                to_prepend.append(sm)
                existing_sys.add(key)
        if to_prepend:
            # Keep base system first if present in out
            if out and isinstance(out[0], dict) and out[0].get("role") == "system":
                out = [out[0], *to_prepend, *out[1:]]
            else:
                out = [*to_prepend, *out]

    # Avoid double-appending the same user text if last message is identical
    if out:
        last = out[-1]
        if (
            isinstance(last, dict)
            and last.get("role") == "user"
            and (last.get("content") or "").strip() == (user_text or "").strip()
        ):
            return out

    out.append({"role": "user", "content": user_text})
    return out
