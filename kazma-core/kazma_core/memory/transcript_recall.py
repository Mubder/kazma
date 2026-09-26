"""Transcript recall fallback — search past chat sessions when memory is empty.

Born from the 2026-08-27 "green names" incident: facts that live ONLY in old
chat transcripts (a naming shortlist, a decision table) are invisible to V2
recall, so the supervisor burned 21 iterations hand-writing SQL against
``chat_sessions.db`` and grepping export files (plus a YOLO approval) just to
answer "what did we decide before?".

This module gives the supervisor a first-class, read-only fallback: when V2
recall returns nothing, search past WEB chat sessions (title + message text)
and return ranked hits with snippets. The supervisor injects them as a fenced
untrusted block next to the memory block — zero extra iterations, no danger
tools, no permissions.

Design notes:
  * Reads the chat store wherever it is -- Postgres ``kazma_chat_sessions``
    or SQLite ``chat_sessions.db`` -- read-only, through
    :mod:`kazma_core.memory.chat_history` (kazma-core must not import
    kazma-ui's SessionManager). Until 2026-09-26 it opened only the SQLite
    file, which on a Postgres install is a leftover from before the switch.
  * Best-effort and NEVER raises — a missing DB, a locked file, or a schema
    drift returns ``[]`` and the turn proceeds exactly as before.
  * Kill-switch: ``KAZMA_TRANSCRIPT_RECALL=0`` env or ConfigStore
    ``memory.transcript_fallback=false`` (live-read, no restart).
  * Transcript text is UNTRUSTED conversation data — callers must inject it
    via :func:`format_transcript_block` (prompt-fenced), never raw.
  * A session is a hit when it holds MORE THAN HALF of the question's content
    words (``memory/query_terms.py``: stopwords dropped, Arabic folded), each
    as a whole word. It fires whenever memory recall comes back empty, which
    since the relevance floor (2026-09-26) is every question memory has no
    answer for -- and it used to accept one substring ("size" in "sizeable")
    from a list that kept "what's", "is" and "my".
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "format_transcript_block",
    "search_transcripts",
    "transcript_fallback_enabled",
]

_MAX_HITS = 3
_SNIPPET_CHARS = 320
#: The store's substring ranking is a prefilter: it is asked for this many
#: times the hits wanted, and each row is then confirmed word by word.
_PREFILTER_FACTOR = 5


def transcript_fallback_enabled() -> bool:
    """Live-read kill-switch. Env wins, then ConfigStore
    ``memory.transcript_fallback`` (default ON). Never raises."""
    env = (os.getenv("KAZMA_TRANSCRIPT_RECALL") or "").strip().lower()
    if env in ("0", "false", "off"):
        return False
    if env in ("1", "true", "on"):
        return True
    try:
        from kazma_core.config_store import get_config_store

        val = get_config_store().get("memory.transcript_fallback")
        if isinstance(val, bool):
            return val
        if isinstance(val, str) and val.strip().lower() in ("0", "false", "off"):
            return False
    except Exception:
        pass
    return True


def _terms(query: str, max_terms: int = 6) -> list[str]:
    """The question's content words, most specific (longest) first."""
    from kazma_core.memory.query_terms import content_terms

    return sorted(content_terms(str(query or "")), key=len, reverse=True)[:max_terms]


def _present(terms: list[str], text: str) -> list[str]:
    """The *terms* that *text* holds as whole words (any written form)."""
    from kazma_core.memory.query_terms import mentions, search_terms

    return [t for t in terms if mentions(text, search_terms(t) or [t])]


def _snippet(text: str, terms: list[str]) -> str:
    """Window around the first term occurrence; JSON-unescape for display."""
    hay = text.lower()
    pos = -1
    for t in terms:
        pos = hay.find(t.lower())
        if pos >= 0:
            break
    if pos < 0:
        return text[:_SNIPPET_CHARS].strip()
    start = max(0, pos - _SNIPPET_CHARS // 2)
    end = min(len(text), pos + _SNIPPET_CHARS)
    out = text[start:end]
    for esc, real in (("\\n", "\n"), ("\\t", "\t"), ('\\"', '"')):
        out = out.replace(esc, real)
    return ("…" if start > 0 else "") + out.strip() + ("…" if end < len(text) else "")


def search_transcripts(
    query: str,
    *,
    tenant_id: str = "default",
    exclude_session_id: str | None = None,
    db_path: str | Path | None = None,
    limit: int = _MAX_HITS,
) -> list[dict[str, Any]]:
    """Rank past chat sessions by query-term overlap. Never raises.

    Every session of the tenant is ranked -- in whichever store holds the
    chats (:func:`kazma_core.memory.chat_history.search_sessions`) -- title
    matches 5, message occurrences up to 4 per term -- as a prefilter; a hit
    must hold more than half of the content words as whole words. ``db_path``
    searches that SQLite file instead. Returns up to ``limit`` hits:
    {session_id, thread_id, title, created_at, score, matched, snippet}; empty
    when the store is missing or no session is about the question.
    """
    try:
        terms = _terms(query)
        if not terms:
            return []
        from kazma_core.memory.chat_history import search_sessions
        from kazma_core.memory.query_terms import search_terms

        prefilter = list(dict.fromkeys(w for t in terms for w in (search_terms(t) or [t])))
        rows = search_sessions(
            prefilter,
            tenant_id=tenant_id,
            exclude_session_id=exclude_session_id,
            limit=max(1, limit) * _PREFILTER_FACTOR,
            sqlite_path=Path(db_path) if db_path is not None else None,
        )
        hits: list[dict[str, Any]] = []
        for r in rows:
            title = str(r.get("title") or "")
            messages = str(r.get("messages") or "")
            matched = _present(terms, f"{title}\n{messages}")
            if len(matched) * 2 <= len(terms):
                continue  # half the question or less: not about it
            hits.append(
                {
                    "session_id": str(r.get("session_id") or ""),
                    "thread_id": str(r.get("thread_id") or ""),
                    "title": title or "(untitled session)",
                    "created_at": str(r.get("created_at") or ""),
                    "score": int(r.get("score") or 0),
                    "matched": matched,
                    "snippet": _snippet(messages if len(messages) < 200000 else title, matched),
                }
            )
            if len(hits) >= max(1, limit):
                break
        return hits
    except Exception:
        logger.debug("[transcript-recall] search failed — returning no hits", exc_info=True)
        return []


def format_transcript_block(hits: list[dict[str, Any]]) -> str:
    """Prompt-fenced untrusted block for the supervisor context. Empty string
    when there is nothing to inject. Transcript text is conversation data —
    never instructions — so it MUST ride inside the kazma:data fence."""
    if not hits:
        return ""
    from kazma_core.safety.prompt_fence import format_untrusted_block

    lines: list[str] = [
        "Past-session transcript matches (memory had nothing for this query —",
        "these are excerpts from earlier chats, newest-relevance first):",
        "",
    ]
    for i, h in enumerate(hits, 1):
        when = str(h.get("created_at") or "")
        lines.append(
            f"{i}. \"{str(h.get('title') or '(untitled)')}\""
            f" (saved {when or 'unknown date'}, matched: {', '.join(h.get('matched') or [])})"
        )
        snip = str(h.get("snippet") or "").strip()
        if snip:
            lines.append(f"   > {snip}")
    lines.append("")
    lines.append(
        "If a hit is relevant, cite what it says; open the session only if the"
        " excerpt is insufficient."
    )
    return format_untrusted_block("\n".join(lines), source="chat_history")
