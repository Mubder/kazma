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
    "is_explicit_topic_shift",
    "is_memory_store_intent",
    "is_memory_graph_cleanup_intent",
    "is_multi_part_memory_work",
    "is_bulk_document_message",
    "extract_store_focus_query",
    "latest_turn_priority_note",
    "classify_turn_intent",
    "prior_substantive_user_texts",
    "stub_prior_tool_chains",
    "should_stub_prior_tools",
    "semantic_topic_drift",
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


# Explicit topic pivots — user is abandoning / suspending the open task.
# Keep patterns exclusive enough that "continue with the other step" still
# routes through is_short_continuation, not shift.
_TOPIC_SHIFT_RE = re.compile(
    r"(?is)"
    r"("
    r"\b(?:new\s+topic|different\s+(?:topic|question|subject)|change\s+(?:of\s+)?subject)\b"
    r"|"
    r"\b(?:forget\s+(?:that|this|it)|never\s*mind|nvm|unrelated|anyway)\b"
    r"|"
    r"\b(?:switch(?:ing)?\s+(?:to|topics?)|moving\s+on|side\s+question)\b"
    r"|"
    r"\b(?:by\s+the\s+way|btw)\b.{0,20}\b(?:unrelated|different|new)\b"
    r"|"
    # Arabic pivots (common Gulf/Levantine chat)
    r"(?:موضوع\s*ثاني|موضوع\s*جديد|غير\s*موضوع|خلنا\s*نغير|نغير\s*الموضوع|"
    r"اسأل\s*سؤال\s*ثاني|سؤال\s*ثاني|مو\s*مهم|خلاص\s*غير\s*هالموضوع)"
    r")"
)

# Casual off-task asks that should not inherit multi-step tool goals when
# there is a substantive prior user goal in history (heuristic shift).
_CASUAL_PIVOT_RE = re.compile(
    r"(?is)^(?:"
    r"(?:what(?:'s|\s+is)\s+the\s+weather|how(?:'s|\s+is)\s+the\s+weather)"
    r"|(?:what\s+time\s+is\s+it|what(?:'s|\s+is)\s+today(?:'s)?\s+date)"
    r"|(?:tell\s+me\s+a\s+joke|good\s+morning|good\s+night|hello|hi\s+there)"
    r"|(?:check\s+my\s+email|any\s+new\s+email|read\s+my\s+inbox)"
    r"|(?:who\s+are\s+you|what\s+model\s+are\s+you|what(?:'s|\s+is)\s+your\s+name)"
    r"|(?:الطقس|الجو\s*شلون|كم\s*الساعه|كم\s*الساعة|وش\s*الوقت|مرحبا|السلام\s*عليكم)"
    r")\b"
)


def is_explicit_topic_shift(text: str) -> bool:
    """True when the user explicitly abandons or suspends the current subject."""
    t = (text or "").strip()
    if not t:
        return False
    # Continuations win — "continue anyway" is not a pivot.
    if is_short_continuation(t):
        return False
    return bool(_TOPIC_SHIFT_RE.search(t))


def prior_substantive_user_texts(
    messages: list[dict[str, Any]] | None,
    *,
    exclude: str = "",
    min_chars: int = 24,
    limit: int = 3,
) -> list[str]:
    """Last N non-continuation user texts (oldest→newest among the window)."""
    if not messages:
        return []
    excl = (exclude or "").strip()
    found: list[str] = []
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        c = m.get("content")
        if not isinstance(c, str):
            continue
        s = c.strip()
        if not s or s == excl:
            continue
        if is_short_continuation(s):
            continue
        if len(s) < min_chars and not is_memory_store_intent(s):
            # Keep short-but-tasky lines only if they look like real asks
            if len(s) < 8:
                continue
        found.append(s)
    if limit > 0:
        return found[-limit:]
    return found


def classify_turn_intent(
    text: str,
    *,
    messages: list[dict[str, Any]] | None = None,
    task_status: str = "",
    task_goal_summary: str = "",
    use_embedding_drift: bool = True,
) -> str:
    """Classify this user turn for focus / recall policy.

    Returns one of:
      ``continue`` | ``store`` | ``cleanup`` | ``multi_part`` | ``shift`` | ``normal``

    Priority (first match wins after mutual exclusions in helpers):
      cleanup > multi_part/store > continue > explicit/heuristic shift
      > embedding drift (optional) > normal
    """
    t = (text or "").strip()
    if not t:
        return "normal"

    # Specialized memory work — already has focused recall paths.
    if is_memory_graph_cleanup_intent(t):
        return "cleanup"
    if is_multi_part_memory_work(t):
        return "multi_part"
    if is_memory_store_intent(t):
        return "store"

    # True same-session continuations only when an open task is plausible.
    if is_short_continuation(t):
        status = (task_status or "").strip().lower()
        # If the prior task was already completed/superseded, treat bare
        # "ok"/"yes" as normal chat unless history still has an open ask —
        # still allow "proceed"/"continue"/"retry" heads as continue.
        s = " ".join(t.lower().split())
        hard_continue = s in {
            "proceed",
            "continue",
            "try now",
            "try again",
            "retry",
            "keep going",
            "finish",
            "finish it",
            "resume",
            "go ahead",
            "do it",
            "pick up",
        } or s.split()[0] in ("proceed", "continue", "resume", "retry")
        if hard_continue or status in ("", "idle", "in_progress"):
            return "continue"
        if status in ("completed", "superseded") and not hard_continue:
            return "normal"
        return "continue"

    if is_explicit_topic_shift(t):
        return "shift"

    # Heuristic: casual pivot after a substantive multi-step prior goal.
    priors = prior_substantive_user_texts(messages, exclude=t, min_chars=40, limit=2)
    if priors and _CASUAL_PIVOT_RE.search(t):
        # Only when the prior goal looks like multi-step work (not prior small talk).
        prior_blob = " ".join(priors).lower()
        multi_markers = (
            "memory",
            "github",
            "graph",
            "entity",
            "entities",
            "shipx",
            "kazma",
            "repo",
            "pipeline",
            "pat",
            "token",
            "ذاكر",
            "جراف",
            "مشروع",
        )
        if any(m in prior_blob for m in multi_markers) or any(
            len(p) >= 120 for p in priors
        ):
            return "shift"

    # Semantic embedding drift vs open goal / last substantive user ask.
    # Fail-open inside semantic_topic_drift — never forces shift on errors.
    if use_embedding_drift:
        ref = (task_goal_summary or "").strip()
        if not ref and priors:
            ref = priors[-1]
        if not ref:
            # Fall back to any recent substantive line (including shorter)
            short_priors = prior_substantive_user_texts(
                messages, exclude=t, min_chars=16, limit=1
            )
            ref = short_priors[-1] if short_priors else ""
        status = (task_status or "").strip().lower()
        # Only run embed when focus is open (or just completed with a real goal).
        # Skip when already superseded — soft-reset already applied.
        _run_embed = bool(ref) and status in ("", "idle", "in_progress", "completed")
        if _run_embed:
            try:
                from kazma_core.agent.topic_drift import semantic_topic_drift

                if semantic_topic_drift(t, ref):
                    return "shift"
            except Exception:
                logger.debug("[turn_input] embedding drift skipped", exc_info=True)

    return "normal"


def stub_prior_tool_chains(
    messages: list[dict[str, Any]] | None,
    *,
    keep_last_n_user_turns: int = 1,
) -> list[dict[str, Any]]:
    """Re-export — collapse prior-turn tool chains into short stubs."""
    from kazma_core.agent.topic_drift import stub_prior_tool_chains as _stub

    return _stub(messages, keep_last_n_user_turns=keep_last_n_user_turns)


def should_stub_prior_tools(
    *,
    intent_mode: str = "",
    prev_task_status: str = "",
) -> bool:
    """Re-export — whether this turn should stub prior tool history."""
    from kazma_core.agent.topic_drift import should_stub_prior_tools as _should

    return _should(intent_mode=intent_mode, prev_task_status=prev_task_status)


def semantic_topic_drift(
    current: str,
    reference: str,
    *,
    threshold: float | None = None,
    enabled: bool | None = None,
) -> bool:
    """Re-export — embedding cosine-distance topic drift (fail-open)."""
    from kazma_core.agent.topic_drift import semantic_topic_drift as _drift

    return _drift(current, reference, threshold=threshold, enabled=enabled)


def is_bulk_document_message(text: str, *, min_chars: int = 600) -> bool:
    """True for long paste payloads (architecture dumps, handoffs, etc.)."""
    return len((text or "").strip()) >= min_chars


# User wants graph/entity hygiene: merge, link, delete junk — NOT memory_store.
# Keep patterns *exclusive* enough that multi-part tasks mentioning hierarchy
# as a *storage shape* (Mubder → kazma → facts) are not forced into cleanup-only.
_GRAPH_CLEANUP_RE = re.compile(
    r"(?is)"
    r"("
    r"\b(?:entities?|beliefs?|graph|nodes?)\b.{0,80}\b(?:messy|missy|clutter|cleanup|clean\s*up|align|restructur|organiz|organise|duplicate)\b"
    r"|"
    r"\b(?:merge|align|restructur|cleanup|clean\s*up)\b.{0,80}\b(?:entities?|beliefs?|graph|nodes?|kazma|shipx)\b"
    r"|"
    r"\b(?:aligned|structure)\b.{0,40}\b(?:this way|like|as)\b.{0,40}\b(?:entities?|graph|nodes?)\b"
    r"|"
    # Hierarchy alone is not cleanup — require hygiene verbs nearby
    r"\b(?:mubder|user)\b.{0,20}(?:→|->|—>|>).{0,20}\bkazma\b.{0,120}\b(?:merge|cleanup|clean\s*up|delete|junk|messy|duplicate)\b"
    r"|"
    r"\b(?:merge|cleanup|clean\s*up|delete|junk|messy|duplicate)\b.{0,120}\b(?:mubder|user)\b.{0,20}(?:→|->|—>|>).{0,20}\bkazma\b"
    r"|"
    r"\bjunk\b.{0,40}\b(?:entities?|nodes?|true|false|graph)\b"
    r"|"
    r"\bdelete\b.{0,40}\b(?:entities?|entity|true|false)\b"
    r")"
)

# Multi-part research / ingest tasks that *also* mention graph shape or junk.
# These must NOT be treated as exclusive MEMORY GRAPH CLEANUP (which disables
# store intent and steers the model away from GitHub/read/compare work).
_MULTI_PART_WORK_RE = re.compile(
    r"(?is)"
    r"("
    r"\b(?:github|ghp_|pat\b|personal\s+access\s+token)\b"
    r"|"
    r"\b(?:read|fetch|clone|pull)\b.{0,40}\b(?:repo|repos|repositories|projects?)\b"
    r"|"
    r"\b(?:compare|analy[sz]e|analysis)\b.{0,60}\b(?:projects?|repos?|jobs?|email)\b"
    r"|"
    r"\b(?:job|career|resume|cv|hiring)\b"
    r"|"
    # Arabic cues used in multi-step PAT / read / save / compare asks
    r"(?:تقرأ|تقرا|قارن|تقارن|تحفظ|تحفض|احفظ|جيت\s*هب|غيت\s*هب|ايميل|إيميل|توكن)"
    r"|"
    r"\b(?:save|store|remember)\b.{0,40}\b(?:memory|graph)\b"
    r"|"
    r"\b(?:read|fetch).{0,40}(?:github|repo)\b"
    r")"
)


def is_multi_part_memory_work(text: str) -> bool:
    """True for compound tasks (read GitHub + store + shape graph + analyze)."""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_MULTI_PART_WORK_RE.search(t))


def is_memory_graph_cleanup_intent(text: str) -> bool:
    """True when the user wants *primarily* to restructure/clean the belief graph.

    Returns False for multi-part work that only *mentions* hierarchy as the
    desired storage shape (e.g. read PAT repos then save under Mubder→kazma).
    """
    t = (text or "").strip()
    if not t:
        return False
    if not _GRAPH_CLEANUP_RE.search(t):
        return False
    # Compound research/store tasks win — inject store + structure notes instead
    if is_multi_part_memory_work(t):
        return False
    return True


def is_memory_store_intent(text: str) -> bool:
    """True when the user is asking to *save* content into memory this turn.

    Distinguishes store tasks from "what do you remember about X?" questions so
    we do not let session-boosted prior topics (e.g. reminders) hijack a bulk
    "add this to ShipX memory" paste.
    """
    t = (text or "").strip()
    if not t:
        return False
    # Pure graph cleanup wins over store (users say "memory is messy" without meaning store)
    if is_memory_graph_cleanup_intent(t):
        return False
    if _STORE_INTENT_RE.search(t):
        return True
    # Arabic / multi-part "save to memory" without English store phrasing
    if re.search(
        r"(?is)(?:تحفظ|تحفض|احفظ|بالذاكر|بالذاكرة|في الذاكر|في الذاكرة|save|store).{0,40}"
        r"(?:memory|ذاكر|graph|جراف)",
        t,
    ):
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
    multi_part: bool = False,
    topic_shift: bool = False,
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
    if topic_shift:
        base += (
            " TOPIC SHIFT: The user changed subject or suspended the prior "
            "open task. Treat any previous multi-step goal as SUPERSEDED for "
            "this turn — do NOT resume unfinished tool steps, GitHub/memory "
            "cleanup, or auto-continue the old task unless they explicitly "
            "ask to resume. Answer only the latest message."
        )
        return base
    if multi_part and not graph_cleanup:
        focus_bit = f" Focus: {focus}." if focus else ""
        base += (
            " MULTI-PART TASK — complete *every* step the user asked, in order; "
            "do not stop after only graph cleanup or only listing entities."
            f"{focus_bit} Typical steps may include: (1) use the stored GitHub "
            "PAT/token to read the named repos/projects; (2) save *new* facts "
            "into memory with clean hierarchy "
            "Mubder(user) → has_project → {kazma|shipx|kca} → has_part → details "
            "(no junk true/false shells); (3) compare projects and answer any "
            "analysis/job/email question. Prefer memory_link_entities + "
            "memory_store over endless list/merge loops. If a step fails "
            "(e.g. missing token), report it and continue the rest."
        )
    elif graph_cleanup:
        base += (
            " MEMORY GRAPH CLEANUP TASK: The user wants the entity/belief graph "
            "restructured or cleaned — NOT a new free-text memory_store note. "
            "Use memory_list_entities / memory_list_beliefs, then "
            "memory_merge_entities (collapse duplicates into one canonical id), "
            "memory_link_entities (hierarchy edges e.g. user has_project kazma; "
            "kazma has_part …), memory_delete_entity (junk shells like true/false), "
            "memory_invalidate (bad beliefs). Target shape often: "
            "Mubder(user) → has_project → kazma → has_part → related entities. "
            "Do NOT invent ShipX notes or re-save FILE_INDEX unless they asked. "
            "Finish in a bounded number of tool rounds — do not loop list→merge forever."
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
